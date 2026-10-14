"""Módulo 3: entrega ao CRM os leads do agente (módulo 2) com contatos complementares, no contrato v1.

Sem `MODULO2_CSV`, serve os dados fictícios de `seed.py` (fonte "simulado").
Com o CSV, busca em segundo plano os contatos das empresas-alvo (ver enriquecimento.py).

Endpoints:
  GET  /saude
  GET  /v1/perfis
  POST /v1/leads/busca
  GET  /v1/leads/{cnpj}
  POST /v1/perguntar      (encaminha ao agente do módulo 2 se AGENTE_URL estiver definido)
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from .dados import Fonte, Repositorio, caminho_csv
from .modelos import VERSAO_CONTRATO, Filtro, Lead, Pagina, Perfil, Pergunta, Resposta

log = logging.getLogger("modulo3")


def _resposta_simulada(repo: Repositorio, lead: Lead | None) -> str:
    if lead:
        c = lead.classificacao
        nome = lead.nome_fantasia or lead.razao_social
        franquia = ""
        if c.e_franquia:
            franquia = f" Consta como unidade da {c.marca}; confirme quem decide as compras."
        return (
            f"(Resposta simulada) {nome} recebeu score {c.score} no perfil {c.perfil_id}. "
            f"Justificativa registrada pelo agente: {c.justificativa}{franquia} "
            "Para respostas de verdade, conecte o agente do módulo 2 definindo AGENTE_URL no módulo 3."
        )
    linhas = [f"- {p.nome}: {p.total_alvos} empresas-alvo" for p in repo.perfis()]
    return (
        "(Resposta simulada) O agente do módulo 2 ainda não está conectado, então não consigo "
        "interpretar a pergunta. Resumo da base atual:\n" + "\n".join(linhas) +
        "\nDefina AGENTE_URL no módulo 3 para encaminhar as perguntas ao agente."
    )


def _ligado(nome: str, padrao: bool = True) -> bool:
    valor = os.environ.get(nome)
    return padrao if valor is None else valor.strip().lower() in {"1", "true", "sim", "yes"}


def criar_app(repo: Repositorio | None = None, fonte: Fonte | None = None, enriquecedor=None) -> FastAPI:
    """`repo` fixo (testes) ou `fonte`; sem nenhum dos dois, lê o ambiente."""
    if repo is None and fonte is None:
        csv = caminho_csv()
        if csv is not None and enriquecedor is None and _ligado("MODULO3_ENRIQUECER"):
            from .enriquecimento import ConfigEnriquecimento, Enriquecedor

            enriquecedor = Enriquecedor(ConfigEnriquecimento.do_ambiente())
        fonte = Fonte(csv, enriquecedor.contatos if enriquecedor else None)
    atual = (lambda: repo) if repo is not None else fonte.repo

    @asynccontextmanager
    async def ciclo(_: FastAPI) -> AsyncIterator[None]:
        parar = threading.Event()
        if enriquecedor is not None and enriquecedor.cfg.minutos > 0:
            from .enriquecimento import enriquecer_periodicamente

            threading.Thread(target=enriquecer_periodicamente, args=(enriquecedor, lambda: atual().leads, parar),
                             name="enriquecimento", daemon=True).start()
        yield
        parar.set()

    app = FastAPI(title="Módulo 3 – contatos e entrega ao CRM", version=VERSAO_CONTRATO, lifespan=ciclo)
    app.state.atual = atual
    app.state.enriquecedor = enriquecedor
    agente_url = os.environ.get("AGENTE_URL", "").strip()
    agente_timeout = float(os.environ.get("AGENTE_TIMEOUT_SEGUNDOS", "120"))

    @app.get("/saude")
    def saude() -> dict:
        r = atual()
        return {
            "ok": True,
            "contrato": VERSAO_CONTRATO,
            "fonte": r.fonte,
            "mes_referencia": r.mes_referencia,
            "total": len(r.leads),
            "agente_conectado": bool(agente_url),
            "enriquecimento": enriquecedor.resumo() if enriquecedor else {"ativo": False},
        }

    @app.get("/v1/perfis", response_model=list[Perfil])
    def perfis() -> list[Perfil]:
        return atual().perfis()

    @app.post("/v1/leads/busca", response_model=Pagina)
    def buscar(filtro: Filtro) -> Pagina:
        return atual().buscar(filtro)

    @app.get("/v1/leads/{cnpj}", response_model=Lead)
    def obter(cnpj: str) -> Lead:
        lead = atual().obter(cnpj.upper())
        if not lead:
            raise HTTPException(404, "Empresa não encontrada no módulo 3.")
        return lead

    @app.post("/v1/perguntar", response_model=Resposta)
    def perguntar(p: Pergunta) -> Resposta:
        r = atual()
        lead = r.obter(p.cnpj.upper()) if p.cnpj else None
        if p.cnpj and not lead and p.contexto is None:
            raise HTTPException(404, "Empresa não encontrada no módulo 3.")
        contexto = lead.model_dump(mode="json") if lead else p.contexto
        if not agente_url:
            if lead is None and contexto:
                nome = contexto.get("nome_fantasia") or contexto.get("razao_social") or "esta empresa"
                return Resposta(resposta=f"(Resposta simulada) {nome} foi cadastrada à mão e ainda não passou pelo agente. "
                                         "Conecte o agente do módulo 2 (AGENTE_URL) para respostas reais.", fonte="simulado")
            return Resposta(resposta=_resposta_simulada(r, lead), fonte="simulado")
        try:
            resp = httpx.post(
                agente_url,
                json={"pergunta": p.pergunta, "contexto": contexto},
                timeout=agente_timeout,
            )
            resp.raise_for_status()
            return Resposta(resposta=str(resp.json()["resposta"]), fonte="modulo2")
        except (httpx.HTTPError, KeyError, ValueError) as erro:
            log.warning("Falha ao consultar o agente: %s", erro)
            raise HTTPException(502, "O agente do módulo 2 não respondeu. Tente de novo em instantes.") from erro

    return app


app = criar_app()
