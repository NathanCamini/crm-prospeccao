"""Orquestra a busca complementar: site oficial, API de busca e registro manual.

Regras de confiança (seção 4.5 do escopo, "regra simples"):
- ALTA: contato lido do site cujo domínio é o do e-mail da Receita E o nome da empresa
  aparece no site. Também: site indicado pela API com o mesmo domínio do e-mail da Receita.
- MÉDIA: site informado pelo usuário ou indicado pela API cujo título/domínio contém o nome
  da empresa; ou domínio do e-mail da Receita sem o nome no site (pode ser do contador).
- BAIXA: site indicado pela API sem relação visível com o nome da empresa.
- Registro manual: o usuário escolhe (padrão: média).

O contato original da Receita nunca é tocado: tudo aqui vai para a tabela contato_complementar.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from prospeccao.app_store import AppStore, ContatoNovo
from prospeccao.config import Config
from prospeccao.enrich.api_busca import ApiBuscaError, ProvedorBusca, criar_provedor, site_candidato
from prospeccao.enrich.extracao import (
    dominio,
    dominio_do_email,
    formatar_telefone,
    nome_aparece,
    normalizar_email,
    normalizar_telefone,
    normalizar_url,
    tokens_nome,
)
from prospeccao.enrich.site import LeitorSite, LimitadorFrequencia, SiteBloqueadoError

log = logging.getLogger("prospeccao.enrich")

METODO_SITE = "site_oficial"
METODO_API = "api_busca"
TIPOS = ("telefone", "email", "site", "rede_social")
CONFIANCAS = ("alta", "media", "baixa")
LOTE_MAXIMO = 100


@dataclass(frozen=True)
class ResultadoBuscaContato:
    cnpj: str
    metodo: str
    situacao: str  # ok | sem_resultado | bloqueado | cache | sem_dominio | sem_api | limite | erro
    novos: int
    mensagem: str


def normalizar_manual(tipo: str, valor: str) -> tuple[str, str]:
    """Valida e normaliza um contato digitado. Devolve (valor_exibicao, valor_normalizado)."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo inválido: {tipo}")
    if tipo == "email":
        norm = normalizar_email(valor)
        if not norm:
            raise ValueError("E-mail inválido")
        return norm, norm
    if tipo == "telefone":
        norm = normalizar_telefone(valor)
        if not norm:
            raise ValueError("Telefone inválido: informe DDD + número")
        return formatar_telefone(norm), norm
    norm = normalizar_url(valor)
    if not norm:
        raise ValueError("Endereço (URL) inválido")
    return valor.strip(), norm


class ServicoContatos:
    def __init__(
        self,
        store: AppStore,
        leitor: LeitorSite,
        provedor: ProvedorBusca | None,
        cache_dias: int = 90,
        limite_diario_api: int = 100,
        limitador_api: LimitadorFrequencia | None = None,
    ) -> None:
        self.store = store
        self.leitor = leitor
        self.provedor = provedor
        self.cache_dias = cache_dias
        self.limite_diario_api = limite_diario_api
        self.limitador_api = limitador_api or LimitadorFrequencia(1.0)

    # ---------------------------------------------------------------- utilidades
    def em_cache(self, cnpj: str, metodo: str) -> dict | None:
        """Última busca bem-sucedida dentro da janela de cache (None se precisa buscar de novo)."""
        ultima = self.store.ultima_busca(cnpj, metodo)
        if not ultima or ultima["resultado"] in ("erro", "cache"):
            return None
        limite = (datetime.now() - timedelta(days=self.cache_dias)).isoformat(timespec="seconds")
        return ultima if ultima["executado_em"] >= limite else None

    def _gravar(self, empresa: dict[str, Any], tipo: str, valor: str, normalizado: str, fonte_nome: str,
                fonte_url: str | None, confianca: str, observacao: str | None, usuario: str | None) -> bool:
        # não duplica o que já está no contato original da Receita
        if tipo == "email" and normalizado == (normalizar_email(empresa.get("email_receita") or "") or ""):
            return False
        if tipo == "telefone":
            originais = {
                normalizar_telefone(f"{empresa.get(f'ddd_receita_{i}') or ''}{empresa.get(f'telefone_receita_{i}') or ''}")
                for i in (1, 2)
            }
            if normalizado in originais:
                return False
        return self.store.adicionar_contato(ContatoNovo(
            cnpj=empresa["cnpj"], tipo=tipo, valor=valor, valor_normalizado=normalizado, fonte_nome=fonte_nome,
            fonte_url=fonte_url, confianca=confianca, observacao=observacao, criado_por=usuario,
        ))

    # ---------------------------------------------------------------- site oficial
    def buscar_site(self, empresa: dict[str, Any], url_base: str | None = None, forcar: bool = False,
                    usuario: str | None = None) -> ResultadoBuscaContato:
        """Lê o site oficial. Sem `url_base`, usa o domínio do e-mail da Receita (se não for gmail, hotmail...)."""
        cnpj = empresa["cnpj"]
        dominio_receita = dominio_do_email(empresa.get("email_receita"))
        if url_base is None and dominio_receita is None:
            return ResultadoBuscaContato(cnpj, METODO_SITE, "sem_dominio", 0,
                                         "O e-mail da Receita não indica um domínio próprio da empresa.")
        if not forcar and url_base is None and (ultima := self.em_cache(cnpj, METODO_SITE)):
            return ResultadoBuscaContato(cnpj, METODO_SITE, "cache", 0,
                                         f"Já buscado em {ultima['executado_em'][:10]}; use 'buscar de novo' para repetir.")
        alvo = url_base or f"https://{dominio_receita}"
        try:
            try:
                resultado = self.leitor.ler(alvo)
            except SiteBloqueadoError:
                if url_base is None and alvo.startswith("https://"):
                    resultado = self.leitor.ler("http://" + alvo[8:])
                else:
                    raise
        except SiteBloqueadoError as exc:
            self.store.registrar_busca(cnpj, METODO_SITE, "bloqueado", str(exc))
            return ResultadoBuscaContato(cnpj, METODO_SITE, "bloqueado", 0, str(exc))

        tokens = tokens_nome(empresa.get("razao_social"), empresa.get("nome_fantasia"))
        nome_no_site = nome_aparece(resultado.texto, tokens)
        do_email_receita = dominio_receita is not None and dominio(resultado.url_base) == dominio_receita
        if do_email_receita and nome_no_site:
            confianca, obs, fonte = "alta", None, "site oficial (domínio do e-mail da Receita)"
        elif do_email_receita:
            confianca = "media"
            obs = "Domínio do e-mail da Receita, mas o nome da empresa não aparece no site (pode ser do contador)."
            fonte = "site do domínio do e-mail da Receita"
        else:
            confianca = "media" if nome_no_site else "baixa"
            obs = None if nome_no_site else "O nome da empresa não aparece no site."
            fonte = "site informado"

        novos = 0
        site_norm = normalizar_url(resultado.url_base) or dominio(resultado.url_base)
        novos += self._gravar(empresa, "site", resultado.url_base.rstrip("/"), site_norm, fonte,
                              resultado.url_base, confianca, obs, usuario)
        for pagina in resultado.paginas:
            for c in pagina.contatos:
                novos += self._gravar(empresa, c.tipo, c.valor, c.valor_normalizado, fonte, c.fonte_url,
                                      confianca, obs, usuario)
        situacao = "ok" if novos else "sem_resultado"
        self.store.registrar_busca(cnpj, METODO_SITE, situacao, f"{len(resultado.paginas)} página(s), {novos} novo(s)")
        return ResultadoBuscaContato(cnpj, METODO_SITE, situacao, novos,
                                     f"{len(resultado.paginas)} página(s) lida(s), {novos} contato(s) novo(s).")

    # ---------------------------------------------------------------- API de busca
    def buscar_api(self, empresa: dict[str, Any], forcar: bool = False, usuario: str | None = None) -> ResultadoBuscaContato:
        """Pede à API oficial o site provável da empresa e grava só a URL do site (não grava trechos)."""
        cnpj = empresa["cnpj"]
        if self.provedor is None:
            return ResultadoBuscaContato(cnpj, METODO_API, "sem_api", 0,
                                         "Nenhuma API de busca configurada (BUSCA_API_PROVEDOR no .env).")
        if not forcar and (ultima := self.em_cache(cnpj, METODO_API)):
            return ResultadoBuscaContato(cnpj, METODO_API, "cache", 0, f"Já buscado em {ultima['executado_em'][:10]}.")
        hoje = datetime.now().strftime("%Y-%m-%dT00:00:00")
        if self.store.buscas_desde(METODO_API, hoje) >= self.limite_diario_api:
            return ResultadoBuscaContato(cnpj, METODO_API, "limite", 0,
                                         f"Limite diário de {self.limite_diario_api} consultas à API atingido.")
        nome = empresa.get("nome_fantasia") or empresa.get("razao_social") or ""
        consulta = f'"{nome}" {empresa.get("municipio") or ""} {empresa.get("uf") or ""}'.strip()
        self.limitador_api.aguardar("api")
        try:
            resultados = self.provedor.buscar(consulta)
        except ApiBuscaError as exc:
            self.store.registrar_busca(cnpj, METODO_API, "erro", str(exc))
            return ResultadoBuscaContato(cnpj, METODO_API, "erro", 0, str(exc))
        candidato = site_candidato(resultados)
        if candidato is None:
            self.store.registrar_busca(cnpj, METODO_API, "sem_resultado", consulta)
            return ResultadoBuscaContato(cnpj, METODO_API, "sem_resultado", 0, "A API não indicou um site provável.")

        partes = urlparse(candidato.url)
        home = f"{partes.scheme}://{partes.netloc}"
        tokens = tokens_nome(empresa.get("razao_social"), empresa.get("nome_fantasia"))
        if dominio(home) == dominio_do_email(empresa.get("email_receita")):
            confianca = "alta"
        elif nome_aparece(candidato.titulo + " " + dominio(home), tokens):
            confianca = "media"
        else:
            confianca = "baixa"
        novo = self._gravar(
            empresa, "site", home, normalizar_url(home) or dominio(home), self.provedor.nome, candidato.url,
            confianca, "Site indicado pela API de busca. Use 'Ler site' para extrair telefone e e-mail.", usuario,
        )
        self.store.registrar_busca(cnpj, METODO_API, "ok", home)
        return ResultadoBuscaContato(cnpj, METODO_API, "ok", int(novo), f"Site provável: {home} (confiança {confianca}).")

    # ---------------------------------------------------------------- manual
    def registrar_manual(self, empresa: dict[str, Any], tipo: str, valor: str, fonte_url: str | None,
                         confianca: str = "media", observacao: str | None = None, usuario: str | None = None) -> bool:
        if confianca not in CONFIANCAS:
            raise ValueError(f"Confiança inválida: {confianca}")
        exibicao, normalizado = normalizar_manual(tipo, valor)
        return self.store.adicionar_contato(ContatoNovo(
            cnpj=empresa["cnpj"], tipo=tipo, valor=exibicao, valor_normalizado=normalizado,
            fonte_nome="busca manual", fonte_url=(fonte_url or None), confianca=confianca,
            observacao=(observacao or None), criado_por=usuario,
        ))

    # ---------------------------------------------------------------- lote
    def em_lote(self, empresas: list[dict[str, Any]], metodo: str, forcar: bool = False, usuario: str | None = None,
                progresso: Callable[[int, int, ResultadoBuscaContato], None] | None = None) -> list[ResultadoBuscaContato]:
        if len(empresas) > LOTE_MAXIMO:
            raise ValueError(f"Lote máximo de {LOTE_MAXIMO} empresas por vez")
        resultados = []
        for i, empresa in enumerate(empresas, start=1):
            if metodo == METODO_SITE:
                r = self.buscar_site(empresa, forcar=forcar, usuario=usuario)
            elif metodo == METODO_API:
                r = self.buscar_api(empresa, forcar=forcar, usuario=usuario)
            else:
                raise ValueError(f"Método inválido: {metodo}")
            resultados.append(r)
            if progresso:
                progresso(i, len(empresas), r)
            if r.situacao == "limite":
                break
        return resultados


def criar_servico(cfg: Config, store: AppStore) -> ServicoContatos:
    leitor = LeitorSite(LimitadorFrequencia(cfg.site_intervalo_segundos))
    provedor = criar_provedor(cfg.busca_api_provedor, cfg.google_cse_chave, cfg.google_cse_cx, cfg.brave_chave)
    return ServicoContatos(store, leitor, provedor, cfg.cache_busca_dias, cfg.api_limite_diario,
                           LimitadorFrequencia(cfg.api_intervalo_segundos))
