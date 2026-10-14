"""Interface Streamlit: login, filtros, tabela paginada, ficha da empresa e indicadores.

Rodar: `prospeccao web` (ou `streamlit run src/prospeccao/web/app.py`).
Toda regra de negócio fica em `prospeccao.domain` e `prospeccao.consulta`; aqui só há tela.
"""

from __future__ import annotations

import io
import threading
import time
from datetime import datetime
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from prospeccao.app_store import AppStore, ConsultaExistenteError
from prospeccao.domain.status import STATUS, STATUS_PADRAO
from prospeccao.reports.descricao import descrever_filtros
from prospeccao.reports.gerar import gerar_arquivo
from prospeccao.config import Config, carregar_config
from prospeccao.consulta import COLUNAS_DISPONIVEIS, COLUNAS_PADRAO, Pagina, Repositorio
from prospeccao.domain.cnpj import CNPJInvalidoError, formatar_cnpj, normalizar_cnpj
from prospeccao.enrich.extracao import dominio_do_email, links_busca_manual
from prospeccao.enrich.servico import (
    CONFIANCAS,
    LOTE_MAXIMO,
    METODO_API,
    METODO_SITE,
    TIPOS,
    ServicoContatos,
    criar_servico,
)
from prospeccao.domain.filtros import MATRIZ_FILIAL, PORTES, SITUACOES, Filtros, FiltroInvalidoError
from prospeccao.web import sessao as sessao_web
from prospeccao.web.rotulos import (
    CAMPOS_CONTATO_RECEITA,
    formatar_data,
    formatar_mes_referencia,
    formatar_valor,
    rotulo,
)
from prospeccao.web.tela_filtros import (
    OPCOES_PERIODO,
    PERIODO_PADRAO,
    TRES_ESTADOS,
    EstadoTela,
    filtros_da_tela,
)

TAMANHOS_PAGINA = (25, 50, 100)
ORDENACOES = ("data_inicio_atividade", "razao_social", "nome_fantasia", "municipio", "capital_social", "cnae_principal")


# ---------------------------------------------------------------- recursos compartilhados
@st.cache_resource
def _store(caminho: str) -> AppStore:
    return AppStore(Path(caminho))


@st.cache_resource
def _conexoes() -> dict[str, Any]:
    """Guarda a conexão com a base entre execuções da tela (uma por processo)."""
    return {"trava": threading.Lock(), "chave": None, "con": None}


def _conexao_base(caminho: str, versao: int) -> duckdb.DuckDBPyConnection:
    """Conexão somente leitura, trocada quando o arquivo da base muda (nova carga).

    Abre um banco em memória e anexa o arquivo (ATTACH) em vez de `duckdb.connect(caminho)`:
    o DuckDB reaproveita o banco já aberto pelo mesmo caminho, e depois de uma carga nova a
    interface continuaria lendo o arquivo antigo até ser reiniciada.
    """
    estado = _conexoes()
    with estado["trava"]:
        if estado["chave"] != (caminho, versao):
            antiga = estado["con"]
            nova = duckdb.connect()
            nova.execute(f"ATTACH '{caminho.replace(chr(39), chr(39) * 2)}' AS gold (READ_ONLY)")
            estado["con"], estado["chave"] = nova, (caminho, versao)
            if antiga is not None:
                try:
                    antiga.close()
                except duckdb.Error:
                    pass
        return estado["con"]


@st.cache_resource
def _servico(caminho_app: str) -> ServicoContatos:
    """Um serviço por processo: o limitador de frequência vale para todas as sessões."""
    cfg = carregar_config()
    return criar_servico(cfg, _store(caminho_app))


def _versao_base(cfg: Config) -> int:
    return cfg.gold_db.stat().st_mtime_ns


def _repositorio(cfg: Config) -> Repositorio | None:
    if not cfg.gold_db.exists():
        return None
    con = _conexao_base(str(cfg.gold_db), _versao_base(cfg))
    return Repositorio(conexao=con, app=_store(str(cfg.app_db)), banco="gold")


@st.cache_data(max_entries=64, show_spinner="Consultando…")
def _buscar(_repo: Repositorio, versao: str, filtros: Filtros, hoje: date, pagina: int, tamanho: int,
            ordenar: str, desc: bool, colunas: tuple[str, ...], somente: tuple[str, ...] | None = None) -> Pagina:
    return _repo.buscar(filtros, pagina, tamanho, ordenar, desc, colunas, referencia=hoje,
                        somente_cnpjs=list(somente) if somente is not None else None)


@st.cache_data(max_entries=32, show_spinner="Calculando indicadores…")
def _indicadores(_repo: Repositorio, versao: str, filtros: Filtros, hoje: date,
                 somente: tuple[str, ...] | None = None) -> dict[str, Any]:
    return _repo.indicadores(filtros, referencia=hoje, somente_cnpjs=list(somente) if somente is not None else None)


def _somente() -> tuple[str, ...] | None:
    """CNPJs a que a tela está restrita ('mostrar só as novas' de uma consulta salva)."""
    lista = st.session_state.get("somente_cnpjs")
    return tuple(lista) if lista is not None else None


@st.cache_data(max_entries=4)
def _listas(_repo: Repositorio, versao: str) -> dict[str, Any]:
    return {
        "ufs": _repo.ufs(),
        "municipios": _repo.todos_municipios(),
        "cnaes": _repo.cnaes("", limite=10_000),
        "naturezas": _repo.naturezas(),
        "carga": _repo.ultima_carga(),
    }


@st.cache_data(max_entries=256)
def _bairros(_repo: Repositorio, versao: str, codigo: str) -> list[str]:
    return _repo.bairros(codigo)


# ---------------------------------------------------------------- login
def restaurar_sessao(store: AppStore, cfg: Config) -> None:
    """Se o navegador trouxe um código de sessão válido, entra direto (recarregar a página não pede senha)."""
    if "usuario" in st.session_state or cfg.sessao_horas <= 0 or st.session_state.get("saiu"):
        return
    token = sessao_web.ler_cookie()
    usuario = store.usuario_da_sessao(token)
    if usuario:
        st.session_state["usuario"] = usuario
        st.session_state["token_sessao"] = token


def tela_login(store: AppStore, cfg: Config) -> None:
    st.title("Prospecção de empresas novas")
    if st.session_state.pop("apagar_cookie", False):
        sessao_web.apagar_cookie()
    if not store.listar_usuarios():
        st.warning(
            "Nenhum usuário cadastrado. Crie o primeiro no terminal:\n\n"
            "`prospeccao usuario criar SEU_NOME`\n\n"
            "(com Docker: `docker compose run --rm prospeccao usuario criar SEU_NOME`)"
        )
        st.stop()
    with st.form("login"):
        usuario = st.text_input("Usuário", key="login_usuario")
        senha = st.text_input("Senha", type="password", key="login_senha")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        if store.autenticar(usuario, senha):
            nome = usuario.strip().lower()
            st.session_state["usuario"] = nome
            st.session_state.pop("login_senha", None)
            st.session_state.pop("saiu", None)
            if cfg.sessao_horas > 0:
                token = store.criar_sessao(nome, cfg.sessao_horas)
                st.session_state["token_sessao"] = token
                st.session_state["gravar_cookie"] = token
            st.rerun()
        tentativas = st.session_state.get("falhas_login", 0) + 1
        st.session_state["falhas_login"] = tentativas
        time.sleep(min(tentativas, 5))  # atraso crescente contra tentativa e erro
        st.error("Usuário ou senha inválidos.")
    st.stop()


# ---------------------------------------------------------------- filtros
def _limpar_filtros() -> None:
    for chave in list(st.session_state):
        if chave.startswith("f_"):
            del st.session_state[chave]
    st.session_state["filtros_aplicados"] = Filtros()
    st.session_state["pagina"] = 1
    for chave in ("cnpj_ficha", "novidades", "somente_cnpjs"):
        st.session_state.pop(chave, None)


def barra_filtros(repo: Repositorio, listas: dict[str, Any], versao: str) -> None:
    ss = st.session_state
    sb = st.sidebar
    sb.header("Filtros")

    sb.subheader("Localidade")
    ufs = sb.multiselect("UF", listas["ufs"], key="f_ufs", placeholder="Escolha…")
    por_codigo = {m["codigo"]: m for m in listas["municipios"]}
    escolhidos = ss.get("f_municipios", [])
    opcoes = [m["codigo"] for m in listas["municipios"] if not ufs or m["uf"] in ufs or m["codigo"] in escolhidos]
    municipios = sb.multiselect(
        "Município (digite para buscar)", opcoes, key="f_municipios", placeholder="Escolha…",
        format_func=lambda c: f"{por_codigo[c]['nome']}/{por_codigo[c]['uf']}",
        help="Sem município escolhido, vale a UF inteira. Com UF e município, vale só o município.",
    )
    bairros: dict[str, list[str]] = {}
    for codigo in municipios:
        lista = _bairros(repo, versao, codigo) if por_codigo[codigo]["tem_bairro"] else []
        if lista:  # filtro de bairro só aparece quando a base tem bairro para o município
            bairros[codigo] = sb.multiselect(f"Bairros em {por_codigo[codigo]['nome']}", lista, key=f"f_bairros_{codigo}", placeholder="Escolha…")

    sb.subheader("Abertura")
    periodo = sb.selectbox("Período de abertura", list(OPCOES_PERIODO), index=list(OPCOES_PERIODO).index(PERIODO_PADRAO), key="f_periodo")
    datas = None
    if OPCOES_PERIODO[periodo] is None:
        hoje = date.today()
        datas = sb.date_input("De / até", (hoje - timedelta(days=90), hoje), format="DD/MM/YYYY", key="f_datas")

    sb.subheader("Atividade (CNAE)")
    cnaes = {c["codigo"]: c["descricao"] for c in listas["cnaes"]}
    cnae_sel = sb.multiselect("CNAEs", list(cnaes), key="f_cnaes", placeholder="Escolha…", format_func=lambda c: f"{c} – {cnaes[c]}")
    cnae_texto = sb.text_input("Códigos ou prefixos", key="f_cnae_texto", placeholder="ex.: 4399, 43, 10",
                               help="7 dígitos = código exato; menos dígitos = prefixo.")
    cnae_palavra = sb.text_input("Palavra na descrição do CNAE", key="f_cnae_palavra")
    secundarios = sb.checkbox("Incluir CNAEs secundários", key="f_secundarios")

    sb.subheader("Empresa")
    portes = sb.multiselect("Porte", PORTES, key="f_portes", placeholder="Escolha…")
    naturezas = {n["codigo"]: n["descricao"] for n in listas["naturezas"]}
    nat_sel = sb.multiselect("Natureza jurídica", list(naturezas), key="f_naturezas", placeholder="Escolha…", format_func=lambda c: naturezas[c])
    situacoes = sb.multiselect("Situação cadastral", SITUACOES, default=["ATIVA"], key="f_situacoes", placeholder="Escolha…")
    col1, col2 = sb.columns(2)
    cap_min = col1.number_input("Capital mín. (R$)", min_value=0.0, value=None, step=1000.0, key="f_cap_min")
    cap_max = col2.number_input("Capital máx. (R$)", min_value=0.0, value=None, step=1000.0, key="f_cap_max")
    matriz = sb.radio("Matriz ou filial", ("Indiferente", *[m.capitalize() for m in MATRIZ_FILIAL]), horizontal=True, key="f_matriz")
    simples = sb.radio("Optante pelo Simples", TRES_ESTADOS, horizontal=True, key="f_simples")

    sb.subheader("Contato")
    tel = sb.radio("Tem telefone (Receita)", TRES_ESTADOS, horizontal=True, key="f_tel")
    email = sb.radio("Tem e-mail (Receita)", TRES_ESTADOS, horizontal=True, key="f_email")
    compl = sb.radio("Tem contato complementar", TRES_ESTADOS, horizontal=True, key="f_compl")
    palavra = sb.text_input("Palavra no nome (razão social ou fantasia)", key="f_palavra")

    sb.subheader("Prospecção")
    status = sb.multiselect("Status de prospecção", list(STATUS), format_func=STATUS.get, key="f_status", placeholder="Todos")

    col_a, col_b = sb.columns(2)
    if col_a.button("Aplicar filtros", type="primary", width="stretch", key="f_aplicar"):
        estado = EstadoTela(
            ufs=ufs, municipios=[por_codigo[c] for c in municipios], bairros=bairros, periodo=periodo,
            datas=tuple(datas) if datas else None, cnae_selecionados=cnae_sel, cnae_texto=cnae_texto,
            cnae_palavra=cnae_palavra, incluir_secundarios=secundarios, portes=portes, naturezas=nat_sel,
            situacoes=situacoes, capital_min=cap_min, capital_max=cap_max, matriz_filial=matriz,
            simples=simples, tem_telefone=tel, tem_email=email, tem_contato_complementar=compl, palavra_chave=palavra,
            status=status,
        )
        try:
            ss["filtros_aplicados"] = filtros_da_tela(estado)
            ss["pagina"] = 1
            for chave in ("cnpj_ficha", "novidades", "somente_cnpjs"):
                ss.pop(chave, None)
        except FiltroInvalidoError as exc:
            sb.error(str(exc))
    col_b.button("Limpar", width="stretch", on_click=_limpar_filtros, key="f_limpar")


# ---------------------------------------------------------------- ficha
ROTULO_TIPO = {"telefone": "Telefone", "email": "E-mail", "site": "Site", "rede_social": "Rede social"}
ROTULO_CONFIANCA = {"alta": "Alta", "media": "Média", "baixa": "Baixa"}
ROTULO_SITUACAO = {
    "ok": "contatos encontrados", "sem_resultado": "nada novo", "bloqueado": "site bloqueado ou fora do ar",
    "cache": "já buscado recentemente", "sem_dominio": "sem domínio próprio", "sem_api": "API não configurada",
    "limite": "limite diário da API", "erro": "erro",
}


def secao_acompanhamento(cnpj: str, store: AppStore) -> None:
    st.markdown("**Acompanhamento da prospecção**")
    atual = store.status_de([cnpj]).get(cnpj)
    chave_atual = atual["status"] if atual else STATUS_PADRAO
    with st.form(f"status_{cnpj}"):
        c1, c2 = st.columns([1, 3])
        novo = c1.selectbox("Status", list(STATUS), index=list(STATUS).index(chave_atual), format_func=STATUS.get)
        obs = c2.text_input("Observação", value=(atual or {}).get("observacao") or "")
        if st.form_submit_button("Salvar status"):
            store.definir_status(cnpj, novo, obs, st.session_state.get("usuario"))
            st.session_state["msg_status"] = f"Status salvo: {STATUS[novo]}."
            st.rerun()
    if msg := st.session_state.pop("msg_status", None):
        st.success(msg)
    if atual:
        st.caption(f"Última alteração em {atual['atualizado_em'][:16].replace('T', ' ')} por {atual['atualizado_por'] or '—'}. "
                   "O status fica ligado ao CNPJ e é mantido nas próximas cargas da base.")
    historico = store.historico_status(cnpj)
    if historico:
        with st.expander(f"Histórico ({len(historico)})"):
            df = pd.DataFrame(historico)
            df["status"] = df["status"].map(STATUS)
            df["alterado_em"] = df["alterado_em"].str[:16].str.replace("T", " ")
            st.dataframe(df.rename(columns={"status": "Status", "observacao": "Observação", "alterado_em": "Quando",
                                            "alterado_por": "Por"}), hide_index=True, width="stretch")


def secao_contatos_complementares(dados: dict[str, Any], store: AppStore, servico: ServicoContatos) -> None:
    usuario = st.session_state.get("usuario")
    cnpj = dados["cnpj"]
    st.markdown("**Contatos complementares**")
    st.caption("Encontrados fora da Receita. Ficam separados e nunca substituem o contato original acima.")

    contatos = store.listar_contatos(cnpj)
    if contatos:
        df = pd.DataFrame(contatos)
        df["tipo"] = df["tipo"].map(ROTULO_TIPO)
        df["confianca"] = df["confianca"].map(ROTULO_CONFIANCA)
        df["data_busca"] = df["data_busca"].map(lambda d: f"{d[8:10]}/{d[5:7]}/{d[:4]}" if d else "")
        df["observacao"] = df["observacao"].fillna("")
        st.dataframe(
            df[["tipo", "valor", "confianca", "fonte_nome", "fonte_url", "data_busca", "observacao"]],
            hide_index=True, width="stretch",
            column_config={
                "tipo": "Tipo", "valor": "Valor", "confianca": "Confiança", "fonte_nome": "Fonte",
                "fonte_url": st.column_config.LinkColumn("Endereço da fonte"), "data_busca": "Data",
                "observacao": "Observação",
            },
        )
        with st.expander("Remover um contato complementar"):
            opcoes = {c["id"]: f"{ROTULO_TIPO[c['tipo']]}: {c['valor']}" for c in contatos}
            escolhido = st.selectbox("Contato", list(opcoes), format_func=opcoes.get, key=f"rm_sel_{cnpj}")
            if st.button("Remover", key=f"rm_btn_{cnpj}"):
                store.remover_contato(escolhido)
                st.rerun()
    else:
        st.caption("Nenhum contato complementar registrado ainda.")

    forcar = st.checkbox("Buscar de novo mesmo se já buscado recentemente", key=f"forcar_{cnpj}")
    c1, c2 = st.columns(2)
    dominio = dominio_do_email(dados.get("email_receita"))
    if c1.button("Ler site oficial", key=f"site_{cnpj}", disabled=dominio is None, width="stretch",
                 help=f"Lê {dominio} (domínio do e-mail da Receita)." if dominio else
                 "O e-mail da Receita não indica site próprio (ex.: gmail). Informe um site abaixo."):
        with st.spinner(f"Lendo {dominio}…"):
            r = servico.buscar_site(dados, forcar=forcar, usuario=usuario)
        st.session_state["msg_contato"] = f"Site oficial: {ROTULO_SITUACAO[r.situacao]}. {r.mensagem}"
        st.rerun()
    if c2.button("Indicar site pela API de busca", key=f"api_{cnpj}", disabled=servico.provedor is None,
                 width="stretch",
                 help="Configure BUSCA_API_PROVEDOR no .env." if servico.provedor is None else servico.provedor.nome):
        with st.spinner("Consultando a API de busca…"):
            r = servico.buscar_api(dados, forcar=forcar, usuario=usuario)
        st.session_state["msg_contato"] = f"API de busca: {ROTULO_SITUACAO[r.situacao]}. {r.mensagem}"
        st.rerun()
    if msg := st.session_state.pop("msg_contato", None):
        st.info(msg)

    sites = [c for c in contatos if c["tipo"] == "site"]
    with st.expander("Ler outro site (informado por você ou indicado pela API)"):
        sugestao = sites[0]["valor"] if sites else ""
        url = st.text_input("Endereço do site", value=sugestao, key=f"url_{cnpj}", placeholder="https://www.exemplo.com.br")
        if st.button("Ler este site", key=f"ler_url_{cnpj}") and url:
            alvo = url if url.startswith("http") else "https://" + url
            with st.spinner(f"Lendo {alvo}…"):
                r = servico.buscar_site(dados, url_base=alvo, forcar=True, usuario=usuario)
            st.session_state["msg_contato"] = f"Site: {ROTULO_SITUACAO[r.situacao]}. {r.mensagem}"
            st.rerun()

    with st.expander("Busca manual assistida"):
        st.caption("Abra os links no seu navegador e registre abaixo o que encontrar. A ferramenta não lê o Google.")
        for rotulo_link, link in links_busca_manual(dados["razao_social"], dados["nome_fantasia"], dados["municipio"], dados["uf"]):
            st.link_button(rotulo_link, link)
        with st.form(f"manual_{cnpj}", clear_on_submit=True):
            m1, m2, m3 = st.columns([1, 2, 1])
            tipo = m1.selectbox("Tipo", TIPOS, format_func=ROTULO_TIPO.get)
            valor = m2.text_input("Valor encontrado")
            confianca = m3.selectbox("Confiança", CONFIANCAS, index=1, format_func=ROTULO_CONFIANCA.get)
            fonte = st.text_input("Onde encontrou (URL)", placeholder="https://…")
            obs = st.text_input("Observação")
            if st.form_submit_button("Registrar contato"):
                try:
                    novo = servico.registrar_manual(dados, tipo, valor, fonte, confianca, obs, usuario)
                    st.session_state["msg_contato"] = "Contato registrado." if novo else "Esse contato já estava registrado."
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def lote_contatos(repo: Repositorio, pagina: Pagina, servico: ServicoContatos) -> None:
    with st.expander(f"Buscar contatos complementares das empresas desta página (até {LOTE_MAXIMO})"):
        st.caption(
            "Lê o site oficial (domínio do e-mail da Receita) ou pede à API o site provável. "
            "Respeita robots.txt, intervalo entre requisições e não repete empresas buscadas recentemente."
        )
        metodos = {METODO_SITE: "Site oficial", METODO_API: "API de busca (só indica o site)"}
        metodo = st.radio("Método", list(metodos), format_func=metodos.get, horizontal=True, key="lote_metodo")
        forcar = st.checkbox("Repetir mesmo se buscado recentemente", key="lote_forcar")
        if metodo == METODO_API and servico.provedor is None:
            st.warning("Nenhuma API de busca configurada (BUSCA_API_PROVEDOR no .env).")
            return
        if st.button("Iniciar busca em lote", key="lote_iniciar"):
            empresas = repo.empresas_por_cnpjs([l["cnpj"] for l in pagina.linhas][:LOTE_MAXIMO])
            barra = st.progress(0.0, text="Iniciando…")

            def avancar(i: int, total: int, r: Any) -> None:
                barra.progress(i / total, text=f"{i}/{total} · {ROTULO_SITUACAO[r.situacao]}")

            resultados = servico.em_lote(empresas, metodo, forcar, st.session_state.get("usuario"), avancar)
            resumo: dict[str, int] = {}
            for r in resultados:
                resumo[ROTULO_SITUACAO[r.situacao]] = resumo.get(ROTULO_SITUACAO[r.situacao], 0) + 1
            novos = sum(r.novos for r in resultados)
            st.success(f"{novos} contato(s) novo(s). " + "; ".join(f"{k}: {v}" for k, v in resumo.items()))


def ficha_empresa(repo: Repositorio, cnpj: str, store: AppStore | None = None, servico: ServicoContatos | None = None) -> None:
    dados = repo.ficha(cnpj)
    if dados is None:
        st.warning(f"CNPJ {formatar_cnpj(cnpj)} não encontrado na base carregada.")
        return
    nome = dados["nome_fantasia"] or dados["razao_social"]
    st.subheader(f"{nome} · {formatar_cnpj(dados['cnpj'])}")

    with st.container(border=True):
        st.markdown("**Contato original da Receita Federal**")
        st.caption(
            f"Exatamente como consta na base da Receita (referência {formatar_mes_referencia(dados['mes_referencia_base'])}). "
            "Pode estar desatualizado; este campo nunca é alterado pela ferramenta."
        )
        contato = {rotulo(c): formatar_valor(c, dados[c]) for c in CAMPOS_CONTATO_RECEITA}
        st.dataframe(pd.DataFrame([contato]), hide_index=True, width="stretch")

    blocos = {
        "Identificação": ("cnpj", "razao_social", "nome_fantasia", "matriz_filial", "natureza_juridica", "porte",
                          "capital_social", "opcao_simples", "opcao_mei"),
        "Situação e abertura": ("situacao_cadastral", "data_situacao", "motivo_situacao", "data_inicio_atividade"),
        "Atividade": ("cnae_principal", "cnae_principal_descricao", "cnaes_secundarios"),
        "Endereço": ("tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep", "municipio", "uf", "pais"),
    }
    colunas = st.columns(2)
    for i, (titulo, campos) in enumerate(blocos.items()):
        with colunas[i % 2].container(border=True):
            st.markdown(f"**{titulo}**")
            for campo in campos:
                st.markdown(f"{rotulo(campo)}: {formatar_valor(campo, dados[campo])}")
    st.caption(
        "A data de início de atividade é a da empresa, não a do cadastro atual: em mudanças de sede ou de "
        "município ela pode ser anterior. Compare com a data da situação cadastral."
    )

    with st.container(border=True):
        st.markdown("**Sócios**")
        if dados["socios"]:
            socios = pd.DataFrame(dados["socios"]).rename(columns={
                "nome_socio": "Nome", "qualificacao": "Qualificação", "data_entrada": "Entrada", "tipo_socio": "Tipo"})
            socios["Entrada"] = socios["Entrada"].map(formatar_data)
            st.dataframe(socios, hide_index=True, width="stretch")
        else:
            st.caption("Nenhum sócio informado na base.")

    if dados["outros_estabelecimentos"]:
        with st.expander(f"Outros estabelecimentos da mesma empresa ({len(dados['outros_estabelecimentos'])})"):
            outros = pd.DataFrame(dados["outros_estabelecimentos"])
            outros["cnpj"] = outros["cnpj"].map(formatar_cnpj)
            st.dataframe(outros.rename(columns=rotulo), hide_index=True, width="stretch")

    if store is not None:
        with st.container(border=True):
            secao_acompanhamento(dados["cnpj"], store)
    if store is not None and servico is not None:
        with st.container(border=True):
            secao_contatos_complementares(dados, store, servico)


# ---------------------------------------------------------------- resultados
def _tabela(pagina: Pagina, status: dict[str, dict] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(pagina.linhas, columns=pagina.colunas)
    if status is not None:
        df.insert(1, "status_prospeccao", [STATUS[status[c]["status"]] if c in status else STATUS[STATUS_PADRAO]
                                           for c in df["cnpj"]])
    if "cnpj" in df:
        df["cnpj"] = df["cnpj"].map(formatar_cnpj)
    if "capital_social" in df:
        df["capital_social"] = df["capital_social"].astype(float)
    return df.rename(columns=lambda c: "Status" if c == "status_prospeccao" else rotulo(c))


LIMITE_EXPORTACAO_TELA = 500_000


def exportar(repo: Repositorio, store: AppStore, filtros: Filtros, colunas: list[str], total: int) -> None:
    with st.expander("Exportar resultado (CSV, Excel ou PDF)"):
        formatos = {"csv": "CSV (abre no Excel)", "xlsx": "Excel (.xlsx)", "pdf": "Relatório PDF"}
        formato = st.radio("Formato", list(formatos), format_func=formatos.get, horizontal=True, key="exp_formato")
        separador, top_n = ";", 50
        if formato == "csv":
            seps = {";": "Ponto e vírgula (Excel em português)", ",": "Vírgula", "\t": "Tabulação"}
            separador = st.selectbox("Separador", list(seps), format_func=seps.get, key="exp_sep")
            separador = "\t" if separador == "\t" else separador
        if formato == "pdf":
            top_n = st.number_input("Quantidade de empresas na tabela do PDF", 10, 500, 50, step=10, key="exp_top")
        st.caption("Inclui as colunas visíveis, o contato original da Receita, os contatos complementares "
                   "(em colunas separadas) e o status de prospecção.")
        if formato != "pdf" and total > LIMITE_EXPORTACAO_TELA:
            st.warning(f"Resultado com mais de {LIMITE_EXPORTACAO_TELA:,} empresas. Refine os filtros ou use "
                       "o terminal: `prospeccao exportar --consulta NOME --formato csv`.".replace(",", "."))
            return
        if st.button("Gerar arquivo", key="exp_gerar"):
            buffer = io.BytesIO()
            with st.spinner("Gerando arquivo…"):
                n = gerar_arquivo(formato, buffer, repo, store, filtros, tuple(colunas), date.today(),
                                  st.session_state.get("usuario"), list(_somente()) if _somente() is not None else None,
                                  separador, int(top_n))
            st.session_state["arquivo_exportado"] = (formato, buffer.getvalue(), n)
        if arq := st.session_state.get("arquivo_exportado"):
            fmt, dados, n = arq
            mime = {"csv": "text/csv", "pdf": "application/pdf",
                    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[fmt]
            nome = f"prospeccao_{datetime.now():%Y%m%d_%H%M}.{fmt}"
            st.download_button(f"Baixar {nome} ({n} empresas)", dados, file_name=nome, mime=mime,
                               on_click="ignore", type="primary", key="exp_baixar")


def aba_empresas(repo: Repositorio, versao: str, filtros: Filtros, servico: ServicoContatos | None = None,
                 store: AppStore | None = None) -> None:
    ss = st.session_state
    c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
    ordenar = c1.selectbox("Ordenar por", ORDENACOES, format_func=rotulo, key="t_ordenar")
    desc = c2.toggle("Decrescente", value=True, key="t_desc")
    tamanho = c3.selectbox("Por página", TAMANHOS_PAGINA, index=1, key="t_tamanho")
    colunas = c4.multiselect("Colunas", COLUNAS_DISPONIVEIS, default=list(COLUNAS_PADRAO), format_func=rotulo, key="t_colunas", placeholder="Escolha…")
    if not colunas:
        st.info("Escolha ao menos uma coluna.")
        return
    if "cnpj" not in colunas:
        colunas = ["cnpj", *colunas]

    hoje = date.today()
    pagina_atual = ss.get("pagina", 1)
    somente = _somente()
    pagina = _buscar(repo, versao, filtros, hoje, pagina_atual, tamanho, ordenar, desc, tuple(colunas), somente)
    if pagina_atual > pagina.total_paginas:
        ss["pagina"] = 1
        pagina = _buscar(repo, versao, filtros, hoje, 1, tamanho, ordenar, desc, tuple(colunas), somente)

    st.markdown(f"**{pagina.total:,}** empresas encontradas".replace(",", "."))
    if pagina.total == 0:
        st.info("Nenhuma empresa com esses filtros. Tente ampliar o período ou a localidade.")
        return
    status = store.status_de([l["cnpj"] for l in pagina.linhas]) if store is not None else None
    evento = st.dataframe(
        _tabela(pagina, status), hide_index=True, width="stretch", on_select="rerun",
        selection_mode="single-row", key="t_tabela",
        column_config={
            rotulo("data_inicio_atividade"): st.column_config.DateColumn(format="DD/MM/YYYY"),
            rotulo("data_situacao"): st.column_config.DateColumn(format="DD/MM/YYYY"),
            rotulo("capital_social"): st.column_config.NumberColumn(format="R$ %.2f"),
        },
    )
    n1, n2, n3 = st.columns([1, 2, 1])
    if n1.button("◀ Anterior", disabled=pagina.pagina <= 1, key="t_anterior"):
        ss["pagina"] = pagina.pagina - 1
        st.rerun()
    n2.markdown(f"<div style='text-align:center'>Página {pagina.pagina} de {pagina.total_paginas}</div>", unsafe_allow_html=True)
    if n3.button("Próxima ▶", disabled=pagina.pagina >= pagina.total_paginas, key="t_proxima"):
        ss["pagina"] = pagina.pagina + 1
        st.rerun()

    linhas = evento.selection.rows if evento and evento.selection else []
    if linhas:
        ss["cnpj_ficha"] = pagina.linhas[linhas[0]]["cnpj"]
    st.caption("Clique em uma linha para abrir a ficha.")
    if servico is not None:
        lote_contatos(repo, pagina, servico)
    if store is not None:
        exportar(repo, store, filtros, colunas, pagina.total)


def aba_indicadores(repo: Repositorio, versao: str, filtros: Filtros) -> None:
    ind = _indicadores(repo, versao, filtros, date.today(), _somente())
    st.metric("Empresas no resultado", f"{ind['total']:,}".replace(",", "."))
    if ind["total"] == 0:
        return
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Top 10 atividades (CNAE principal)**")
        df = pd.DataFrame(ind["top_cnae"])
        df["atividade"] = df["codigo"] + " – " + df["descricao"].fillna("")
        st.altair_chart(
            alt.Chart(df).mark_bar().encode(
                x=alt.X("total:Q", title="Empresas", axis=alt.Axis(format="d", tickMinStep=1)), y=alt.Y("atividade:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
                tooltip=["codigo", "descricao", "total"]),
            width="stretch",
        )
    with c2:
        st.markdown("**Por porte**")
        df = pd.DataFrame(ind["por_porte"])
        st.altair_chart(
            alt.Chart(df).mark_bar().encode(
                x=alt.X("total:Q", title="Empresas", axis=alt.Axis(format="d", tickMinStep=1)), y=alt.Y("porte:N", sort="-x", title=None), tooltip=["porte", "total"]),
            width="stretch",
        )
    st.markdown("**Por mês de abertura**")
    df = pd.DataFrame(ind["por_mes"])
    if not df.empty:
        st.altair_chart(
            alt.Chart(df).mark_bar().encode(
                x=alt.X("mes:O", title="Mês"), y=alt.Y("total:Q", title="Empresas", axis=alt.Axis(format="d", tickMinStep=1)), tooltip=["mes", "total"]),
            width="stretch",
        )
    if ind["por_municipio"]:
        st.markdown("**Por município** (20 maiores)")
        df = pd.DataFrame(ind["por_municipio"])
        df["local"] = df["municipio"] + "/" + df["uf"]
        st.altair_chart(
            alt.Chart(df).mark_bar().encode(
                x=alt.X("total:Q", title="Empresas", axis=alt.Axis(format="d", tickMinStep=1)), y=alt.Y("local:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)), tooltip=["local", "total"]),
            width="stretch",
        )


def consultas_salvas(repo: Repositorio, store: AppStore, filtros: Filtros, carga: dict | None) -> None:
    ss = st.session_state
    with st.sidebar.expander("Consultas salvas", expanded=False):
        nome = st.text_input("Nome para salvar os filtros aplicados", key="cs_nome",
                             placeholder="ex.: Alimentos em Canoas, 90 dias")
        substituir = st.checkbox("Substituir se já existir", key="cs_substituir")
        if st.button("Salvar consulta", key="cs_salvar", width="stretch"):
            try:
                store.salvar_consulta(nome, filtros.para_dict(), ss.get("usuario"), substituir)
                st.success("Consulta salva.")
            except (ConsultaExistenteError, ValueError) as exc:
                st.error(str(exc))
        consultas = store.listar_consultas()
        if not consultas:
            st.caption("Nenhuma consulta salva.")
            return
        por_id = {c["id"]: c for c in consultas}
        escolhida = st.selectbox("Consulta", list(por_id), key="cs_escolhida",
                                 format_func=lambda i: por_id[i]["nome"])
        ultima = por_id[escolhida]["ultima_execucao"]
        st.caption(f"Última execução: {ultima[:16].replace('T', ' ')}" if ultima else "Ainda não executada.")
        c1, c2 = st.columns(2)
        if c1.button("Executar", key="cs_executar", type="primary", width="stretch"):
            filtros_salvos = Filtros.de_dict(por_id[escolhida]["filtros"])
            with st.spinner("Executando e comparando com a última execução…"):
                cnpjs = repo.cnpjs(filtros_salvos, date.today())
                execucao = store.registrar_execucao(escolhida, cnpjs, (carga or {}).get("mes_referencia"))
            ss["filtros_aplicados"] = filtros_salvos
            ss["pagina"] = 1
            ss["novidades"] = {"nome": por_id[escolhida]["nome"], "total": execucao.total,
                               "anterior": execucao.anterior_em, "novos": execucao.novos}
            ss.pop("somente_cnpjs", None)
            st.rerun()
        if c2.button("Excluir", key="cs_excluir", width="stretch"):
            store.excluir_consulta(escolhida)
            st.rerun()


def faixa_novidades() -> None:
    ss = st.session_state
    nov = ss.get("novidades")
    if not nov:
        return
    if nov["anterior"] is None:
        st.info(f"Consulta salva **{nov['nome']}**: primeira execução, {nov['total']} empresas registradas. "
                "Na próxima execução você verá só o que for novo.")
        return
    quando = nov["anterior"][:16].replace("T", " ")
    st.info(f"Consulta salva **{nov['nome']}**: **{len(nov['novos'])}** empresa(s) nova(s) desde a última "
            f"execução ({quando}), de {nov['total']} no total.")
    so_novas = st.toggle("Mostrar só as novas", key="so_novas", value=ss.get("somente_cnpjs") is not None)
    desejado = list(nov["novos"]) if so_novas else None
    if desejado != ss.get("somente_cnpjs"):
        ss["somente_cnpjs"] = desejado
        ss["pagina"] = 1
        st.rerun()


def resumo_filtros(filtros: Filtros, listas: dict[str, Any]) -> list[tuple[str, str]]:
    municipios = {m["codigo"]: f"{m['nome']}/{m['uf']}" for m in listas["municipios"]}
    naturezas = {n["codigo"]: n["descricao"] for n in listas["naturezas"]}
    return descrever_filtros(filtros, date.today(), municipios, naturezas)


# ---------------------------------------------------------------- principal
def main() -> None:
    st.set_page_config(page_title="Prospecção de empresas novas", page_icon="🏢", layout="wide")
    cfg = carregar_config()
    store = _store(str(cfg.app_db))
    restaurar_sessao(store, cfg)
    if "usuario" not in st.session_state:
        tela_login(store, cfg)
    if token := st.session_state.pop("gravar_cookie", None):
        sessao_web.gravar_cookie(token, cfg.sessao_horas)

    st.sidebar.caption(f"Usuário: **{st.session_state['usuario']}**")
    if st.sidebar.button("Sair", key="sair"):
        store.encerrar_sessao(st.session_state.get("token_sessao"))
        st.session_state.clear()
        st.session_state["saiu"] = True  # nesta aba, não reaproveita o cookie antigo
        st.session_state["apagar_cookie"] = True
        st.rerun()

    repo = _repositorio(cfg)
    if repo is None:
        st.title("Prospecção de empresas novas")
        st.warning("Nenhuma base carregada ainda. Rode `prospeccao carga` (ou `prospeccao amostra` para testar).")
        return
    try:
        _tela_principal(cfg, store, repo)
    finally:
        repo.fechar()  # fecha o cursor desta execução (a conexão compartilhada continua aberta)


def _tela_principal(cfg: Config, store: AppStore, repo: Repositorio) -> None:
    servico = _servico(str(cfg.app_db))
    versao = f"{_versao_base(cfg)}:{store.versao_contatos()}:{store.versao_status()}"
    listas = _listas(repo, versao)
    st.session_state.setdefault("filtros_aplicados", Filtros())
    consultas_salvas(repo, store, st.session_state["filtros_aplicados"], listas["carga"])
    barra_filtros(repo, listas, versao)
    filtros: Filtros = st.session_state["filtros_aplicados"]

    st.title("Prospecção de empresas novas")
    carga = listas["carga"]
    if carga:
        st.caption(
            f"Base: referência {formatar_mes_referencia(carga['mes_referencia'])}, "
            f"carregada em {carga['data_ingestao']:%d/%m/%Y %H:%M}"
        )
    with st.expander("Filtros aplicados"):
        for criterio, valor in resumo_filtros(filtros, listas):
            st.markdown(f"- **{criterio}:** {valor}")
        st.caption("A data de início de atividade é a da empresa; em mudanças de sede ou município pode ser "
                   "anterior ao cadastro atual.")
    faixa_novidades()

    with st.expander("Abrir ficha pelo CNPJ"):
        texto = st.text_input("CNPJ", key="busca_cnpj", placeholder="00.000.000/0000-00")
        if texto:
            try:
                st.session_state["cnpj_ficha"] = normalizar_cnpj(texto)
            except (CNPJInvalidoError, TypeError):
                st.error("CNPJ inválido.")

    aba1, aba2 = st.tabs(["Empresas", "Indicadores"])
    try:
        with aba1:
            aba_empresas(repo, versao, filtros, servico, store)
        with aba2:
            aba_indicadores(repo, versao, filtros)
    except FiltroInvalidoError as exc:
        st.error(str(exc))

    if st.session_state.get("cnpj_ficha"):
        st.divider()
        ficha_empresa(repo, st.session_state["cnpj_ficha"], store, servico)

    st.sidebar.divider()
    st.sidebar.caption(
        "Dados públicos da Receita Federal para prospecção B2B. Ofereça sempre um canal de descadastro "
        "e atenda pedidos de exclusão (LGPD)."
    )


main()
