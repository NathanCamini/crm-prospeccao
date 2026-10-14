from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import auditoria
from .config import Config
from .db import migrar
from .modulo3 import Modulo3
from .rotas import apoio, auth, clientes, equipe, leads, operacao, produtos
from .sincronizacao import sincronizar_periodicamente

METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
log = logging.getLogger("crm")


def criar_app(cfg: Config | None = None, m3: Modulo3 | None = None) -> FastAPI:
    cfg = cfg or Config()
    migrar(cfg.db_path)
    m3 = m3 or Modulo3(cfg.modulo3_url, cfg.modulo3_timeout, cfg.agente_timeout)

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        parar = threading.Event()
        if cfg.sincronizar_minutos > 0:
            threading.Thread(
                target=sincronizar_periodicamente, args=(cfg, m3, parar), name="sincronizacao", daemon=True
            ).start()
        yield
        parar.set()

    app = FastAPI(title="CRM de prospecção", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
                  lifespan=ciclo)
    app.state.config = cfg
    app.state.modulo3 = m3

    @app.middleware("http")
    async def protecao_e_auditoria(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        requisicao = uuid.uuid4().hex[:16]
        auditoria.ator(request)
        detalhe_erro = None
        # O cookie é SameSite=Strict; o cabeçalho customizado é uma segunda barreira,
        # porque formulários de outros sites não conseguem enviá-lo.
        if request.method not in METODOS_SEGUROS and request.headers.get("x-crm") != "1":
            resposta = JSONResponse({"detail": "Requisição recusada."}, status_code=403)
            detalhe_erro = "sem o cabeçalho X-CRM (possível requisição de outro site)"
        else:
            try:
                resposta = await call_next(request)
            except Exception:
                log.exception("Erro não tratado em %s %s", request.method, request.url.path)
                resposta = JSONResponse({"detail": "Erro interno. O registro foi guardado."}, status_code=500)
                detalhe_erro = "erro interno"
        resposta.headers.setdefault("Cache-Control", "no-store")
        resposta.headers["X-Requisicao"] = requisicao
        silenciosa = request.url.path in auditoria.SILENCIOSAS and resposta.status_code in (200, 401)
        if not silenciosa:
            linhas = auditoria.linhas_da_requisicao(request, resposta.status_code, requisicao, cfg.confiar_proxy, detalhe_erro)
            try:
                await run_in_threadpool(auditoria.gravar, cfg.db_path, linhas)
            except Exception:  # a auditoria nunca derruba a resposta, mas o erro fica no log do servidor
                log.exception("Falha ao gravar auditoria da requisição %s", requisicao)
        return resposta

    app.include_router(auth.router)
    app.include_router(leads.router)
    app.include_router(apoio.router)
    app.include_router(equipe.router)
    app.include_router(clientes.router)
    app.include_router(operacao.router)
    app.include_router(produtos.router)
    return app
