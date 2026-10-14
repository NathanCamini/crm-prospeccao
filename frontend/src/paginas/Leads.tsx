import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiErro } from "../api";
import Estagio, { Pontuacao } from "../componentes/Estagio";
import Funil from "../componentes/Funil";
import { useAviso } from "../componentes/Aviso";
import { cnpj, diasDesde, mesReferencia, nomeEmpresa, quandoVence, relativo, titulo } from "../formato";
import { useSessao } from "../sessao";
import { ESTAGIOS, type Estagio as E, type Filtro, type PaginaLeads, type Perfil, type Resumo } from "../tipos";

const UFS = ["RS", "SC", "PR", "SP", "RJ", "MG"];
const POR_PAGINA = 25;

function lerFiltro(p: URLSearchParams): Filtro {
  const estagio = p.get("estagio") as E | null;
  return {
    texto: p.get("q") || undefined,
    perfil_id: p.get("perfil") || undefined,
    uf: p.get("uf") ? [p.get("uf")!] : [],
    municipio: p.get("municipio") || undefined,
    score_min: Number(p.get("score") || 0),
    ordenar: (p.get("ordenar") as Filtro["ordenar"]) || "score",
    estagio: estagio && (ESTAGIOS as readonly string[]).includes(estagio) ? estagio : undefined,
    responsavel: p.get("responsavel") || undefined,
    origem: (p.get("origem") as Filtro["origem"]) || undefined,
    pagina: Number(p.get("pagina") || 1),
    por_pagina: POR_PAGINA,
  };
}

export default function Leads() {
  const [params, setParams] = useSearchParams();
  const filtro = useMemo(() => lerFiltro(params), [params]);
  const navegar = useNavigate();
  const { eAdmin, pessoas, nomeDe, usuario } = useSessao();
  const aviso = useAviso();
  const [exportando, setExportando] = useState(false);

  async function exportar() {
    setExportando(true);
    try {
      const { pagina: _p, por_pagina: _pp, ...tudo } = filtro;
      await api.exportarLeads(tudo);
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível exportar.", "erro");
    } finally {
      setExportando(false);
    }
  }

  const [perfis, setPerfis] = useState<Perfil[]>([]);
  const [pagina, setPagina] = useState<PaginaLeads | null>(null);
  const [resumo, setResumo] = useState<Resumo | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [texto, setTexto] = useState(filtro.texto ?? "");
  const [municipio, setMunicipio] = useState(filtro.municipio ?? "");

  function definir(mudancas: Record<string, string | undefined>, manterPagina = false) {
    const novo = new URLSearchParams(params);
    for (const [k, v] of Object.entries(mudancas)) {
      if (v === undefined || v === "" || v === "0") novo.delete(k);
      else novo.set(k, v);
    }
    if (!manterPagina) novo.delete("pagina");
    setParams(novo, { replace: true });
  }

  useEffect(() => {
    api.perfis().then(setPerfis, () => undefined);
  }, []);

  // Busca e resumo seguem a URL; o texto livre espera a pessoa parar de digitar.
  useEffect(() => {
    const t = window.setTimeout(() => {
      const mudancas: Record<string, string> = {};
      if ((filtro.texto ?? "") !== texto) mudancas.q = texto;
      if ((filtro.municipio ?? "") !== municipio) mudancas.municipio = municipio;
      if (Object.keys(mudancas).length) definir(mudancas);
    }, 350);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [texto, municipio]);

  useEffect(() => {
    let vivo = true;
    setCarregando(true);
    setErro(null);
    const { estagio: _e, pagina: _p, ...semEstagio } = filtro;
    Promise.all([api.buscar(filtro), api.resumo(semEstagio)])
      .then(([p, r]) => {
        if (!vivo) return;
        setPagina(p);
        setResumo(r);
      })
      .catch((err) => vivo && setErro(err instanceof ApiErro ? err.message : "Não foi possível carregar os leads."))
      .finally(() => vivo && setCarregando(false));
    return () => {
      vivo = false;
    };
  }, [filtro]);

  const totalPaginas = pagina ? Math.max(1, Math.ceil(pagina.total / POR_PAGINA)) : 1;
  const temFiltro = Boolean(
    filtro.texto || filtro.perfil_id || filtro.uf?.length || filtro.municipio || filtro.score_min || filtro.responsavel || filtro.origem,
  );

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Leads</h1>
          <p className="sub">
            {resumo ? (
              <>
                Empresas abertas e classificadas na base de {mesReferencia(resumo.mes_referencia)}.
                {!eAdmin && " Você vê os leads livres e os seus."}
              </>
            ) : (
              "Carregando a base…"
            )}
          </p>
        </div>
        <div className="cabecalho-acoes">
          {resumo?.fonte === "simulado" && (
            <span className="selo-simulado" title="O módulo 3 ainda não está conectado ao agente real. Os links de WhatsApp, e-mail e formulário não são abertos.">
              Dados simulados
            </span>
          )}
          <button type="button" className="botao" onClick={exportar} disabled={exportando || !pagina?.total}>
            {exportando ? "Exportando…" : "Exportar CSV"}
          </button>
          <Link to="/leads/novo" className="botao botao-primario">
            Cadastrar lead
          </Link>
        </div>
      </header>

      <div className="origens" role="group" aria-label="Origem dos leads">
        {(
          [
            [undefined, "Todos"],
            ["agente", "Do agente"],
            ["manual", "Cadastrados pela equipe"],
          ] as const
        ).map(([valor, rotulo]) => (
          <button
            key={rotulo}
            type="button"
            aria-pressed={filtro.origem === valor}
            className={filtro.origem === valor ? "ativa" : ""}
            onClick={() => definir({ origem: valor })}
          >
            {rotulo}
          </button>
        ))}
      </div>

      <Funil resumo={resumo} ativo={filtro.estagio} aoEscolher={(e) => definir({ estagio: e })} />

      <div className="filtros">
        <label className="campo campo-busca">
          <span className="oculto">Buscar</span>
          <input
            type="search"
            placeholder="Nome, CNPJ ou bairro"
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
          />
        </label>
        <label className="campo">
          <span>Responsável</span>
          <select value={filtro.responsavel ?? ""} onChange={(e) => definir({ responsavel: e.target.value })}>
            <option value="">{eAdmin ? "Qualquer" : "Livres e meus"}</option>
            <option value="eu">Meus</option>
            <option value="ninguem">Livres</option>
            {eAdmin &&
              pessoas
                .filter((p) => p.usuario !== usuario.usuario && p.ativo)
                .map((p) => (
                  <option key={p.usuario} value={p.usuario}>
                    {p.nome_exibicao}
                  </option>
                ))}
          </select>
        </label>
        <label className="campo">
          <span>Perfil</span>
          <select value={filtro.perfil_id ?? ""} onChange={(e) => definir({ perfil: e.target.value })}>
            <option value="">Todos</option>
            {perfis.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nome}
              </option>
            ))}
          </select>
        </label>
        <label className="campo campo-curto">
          <span>UF</span>
          <select value={filtro.uf?.[0] ?? ""} onChange={(e) => definir({ uf: e.target.value })}>
            <option value="">Todas</option>
            {UFS.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </label>
        <label className="campo">
          <span>Município</span>
          <input value={municipio} onChange={(e) => setMunicipio(e.target.value)} placeholder="Qualquer" />
        </label>
        <label className="campo campo-curto">
          <span>Pontuação mín.</span>
          <select value={String(filtro.score_min ?? 0)} onChange={(e) => definir({ score: e.target.value })}>
            {[0, 60, 70, 80, 90].map((s) => (
              <option key={s} value={s}>
                {s === 0 ? "Qualquer" : s}
              </option>
            ))}
          </select>
        </label>
        <label className="campo">
          <span>Ordenar por</span>
          <select value={filtro.ordenar} onChange={(e) => definir({ ordenar: e.target.value === "score" ? undefined : e.target.value })}>
            <option value="score">Pontuação</option>
            <option value="proximo_passo">Próximo passo</option>
            <option value="atividade">Última atividade</option>
            <option value="cadastro">Cadastro mais recente</option>
            <option value="abertura">Mais recentes</option>
            <option value="nome">Nome</option>
          </select>
        </label>
        {temFiltro && (
          <button
            type="button"
            className="botao-texto"
            onClick={() => {
              setTexto("");
              setMunicipio("");
              setParams(filtro.estagio ? { estagio: filtro.estagio } : {}, { replace: true });
            }}
          >
            Limpar filtros
          </button>
        )}
      </div>

      {erro && (
        <div className="faixa faixa-erro" role="alert">
          {erro}
        </div>
      )}

      <div className={`tabela-area${carregando ? " carregando" : ""}`}>
        <table className="tabela">
          <thead>
            <tr>
              <th>Empresa</th>
              <th>Local</th>
              <th>Abertura</th>
              <th className="num">Pontuação</th>
              <th>Estágio</th>
              <th>Responsável</th>
              <th>Próximo passo</th>
              <th>Última atividade</th>
            </tr>
          </thead>
          <tbody>
            {pagina?.itens.map((l) => {
              const dias = diasDesde(l.data_inicio_atividade);
              const prox = l.crm.proximo_passo_em ? quandoVence(l.crm.proximo_passo_em) : null;
              return (
                <tr key={l.chave} onClick={() => navegar(`/leads/${l.chave}`)} className={l.crm.suprimido ? "suprimida" : ""}>
                  <td>
                    <Link to={`/leads/${l.chave}`} className="empresa-nome" onClick={(e) => e.stopPropagation()}>
                      {nomeEmpresa(l)}
                    </Link>
                    <span className="empresa-meta">
                      {l.cnpj ? cnpj(l.cnpj) : "sem CNPJ"}
                      {l.origem === "manual" && <span className="marcador marcador-papel">equipe</span>}
                      {l.ausente_desde && <span className="marcador">fora da base</span>}
                      {l.matriz_filial === "FILIAL" && <span className="marcador">filial</span>}
                      {l.classificacao?.e_franquia && <span className="marcador">rede</span>}
                      {l.crm.suprimido && <span className="marcador marcador-alerta">não contatar</span>}
                    </span>
                  </td>
                  <td>
                    {l.municipio ? `${titulo(l.municipio)}${l.uf ? `/${l.uf}` : ""}` : <span className="mudo">—</span>}
                    <span className="empresa-meta">{titulo(l.bairro)}</span>
                  </td>
                  <td className="nowrap">{dias === null ? <span className="mudo">—</span> : dias === 0 ? "hoje" : `há ${dias} dias`}</td>
                  <td className="num">{l.classificacao ? <Pontuacao valor={l.classificacao.score} /> : <span className="mudo">—</span>}</td>
                  <td>
                    <Estagio valor={l.crm.estagio} />
                  </td>
                  <td className={l.crm.responsavel ? "nowrap" : "mudo nowrap"}>
                    {l.crm.responsavel === usuario.usuario ? "Você" : l.crm.responsavel ? nomeDe(l.crm.responsavel) : "Livre"}
                  </td>
                  <td className={prox ? "" : "mudo"}>
                    {prox ? (
                      <span className="proximo" title={l.crm.proximo_passo_titulo ?? ""}>
                        <span className={`vence${prox.atrasada ? " atrasada" : prox.hoje ? " hoje" : ""}`}>{prox.texto}</span>
                        <span className="empresa-meta proximo-titulo">{l.crm.proximo_passo_titulo}</span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="mudo nowrap">{relativo(l.crm.ultima_atividade_em)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {pagina && pagina.itens.length === 0 && !carregando && (
          <div className="vazio-tabela">
            {filtro.estagio || temFiltro ? (
              <>
                <p>Nenhum lead com esses filtros.</p>
                <button type="button" className="botao" onClick={() => setParams({}, { replace: true })}>
                  Ver todos os leads
                </button>
              </>
            ) : (
              <>
                <p>Ainda não há leads para esta empresa. Os do agente aparecem depois que ele classificar o mês.</p>
                <Link to="/leads/novo" className="botao">
                  Cadastrar o primeiro lead
                </Link>
              </>
            )}
          </div>
        )}
      </div>

      {pagina && pagina.total > POR_PAGINA && (
        <nav className="paginacao" aria-label="Páginas">
          <button
            type="button"
            className="botao"
            disabled={(filtro.pagina ?? 1) <= 1}
            onClick={() => definir({ pagina: String((filtro.pagina ?? 1) - 1) }, true)}
          >
            Anterior
          </button>
          <span>
            Página {filtro.pagina} de {totalPaginas}, {pagina.total} leads
          </span>
          <button
            type="button"
            className="botao"
            disabled={(filtro.pagina ?? 1) >= totalPaginas}
            onClick={() => definir({ pagina: String((filtro.pagina ?? 1) + 1) }, true)}
          >
            Próxima
          </button>
        </nav>
      )}
    </div>
  );
}
