"""Servidor HTTP do agente (`agente servir`), chamado só pelo módulo 3.

    GET    /saude
    POST   /perguntar        {"pergunta", "contexto": Lead v1 | null, "perfis": [ids] | null} → {"resposta"}
    GET    /perfis?dono=X                                      → produtos descritos pela empresa cliente X
    POST   /perfis/sugerir   {"dono", "descricao", "perfil_id"?} → perfil proposto e prévia (nada é gravado)
    POST   /perfis           {"dono", "descricao", "perfil", "perfil_id"?} → grava e classifica o último mês
    DELETE /perfis/{id}?dono=X                                 → desativa

`dono` é o identificador da empresa cliente no CRM; o agente não conhece pessoas nem permissões, só
garante que um perfil é lido, alterado e desativado apenas pelo próprio dono.

Servidor da biblioteca padrão (ThreadingHTTPServer): o agente não ganha dependência nova. Fica só na
rede interna do Docker; não publique a porta.
"""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from agente.armazenamento import ClassificacaoStore
from agente.config import AgenteConfig
from agente.perfis_cliente import PerfilNaoEncontradoError, PerfisClientes, PropostaInvalidaError
from agente.perguntas import Assistente, PerguntaInvalidaError, validar_corpo

log = logging.getLogger("agente.api")
MAXIMO_CORPO = 1_000_000


class Servicos:
    """O que o servidor usa. Sem chave do Gemini, fica vazio e as rotas respondem 503 com a explicação."""

    def __init__(self, assistente: Assistente | None = None, perfis: PerfisClientes | None = None,
                 erro: str | None = None) -> None:
        self.assistente, self.perfis, self.erro = assistente, perfis, erro


def montar_servicos(cfg: AgenteConfig, fabrica_cliente: Callable[[AgenteConfig], Any],
                    fabrica_classificador: Callable[[AgenteConfig, ClassificacaoStore, Any], Any]) -> Servicos:
    try:
        cliente = fabrica_cliente(cfg)
    except RuntimeError as exc:
        log.error("%s", exc)
        return Servicos(erro=str(exc))
    store = ClassificacaoStore(cfg.banco)
    return Servicos(Assistente(cfg, cliente),
                    PerfisClientes(cfg, store, cliente, fabrica_classificador(cfg, store, cliente)))


def _handler(servicos: Servicos) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agente"

        def log_message(self, formato: str, *args: Any) -> None:  # noqa: A002 — assinatura da biblioteca
            log.info("%s %s", self.address_string(), formato % args)

        def _json(self, status: int, dados: Any) -> None:
            corpo = json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _corpo(self) -> Any:
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > MAXIMO_CORPO:
                raise PerguntaInvalidaError("Corpo grande demais.")
            try:
                return json.loads(self.rfile.read(tamanho) or b"null")
            except ValueError:
                raise PerguntaInvalidaError("JSON inválido.") from None

        def _rota(self, metodo: str) -> None:
            url = urlparse(self.path)
            caminho = url.path.rstrip("/") or "/"
            consulta = {k: v[0] for k, v in parse_qs(url.query).items()}
            if metodo == "GET" and caminho == "/saude":
                self._json(HTTPStatus.OK, {"ok": servicos.erro is None, "erro": servicos.erro,
                                           "modelo": servicos.assistente.modelo if servicos.assistente else None})
                return
            if servicos.erro is not None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"detail": servicos.erro})
                return
            try:
                self._json(*self._despachar(metodo, caminho, consulta))
            except (PerguntaInvalidaError, PropostaInvalidaError) as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            except PerfilNaoEncontradoError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"detail": str(exc)})
            except Exception as exc:  # noqa: BLE001 — erro do LLM ou da base vira 502, sem derrubar o servidor
                log.exception("Falha em %s %s", metodo, caminho)
                self._json(HTTPStatus.BAD_GATEWAY, {"detail": f"O agente não conseguiu responder: {exc}"})

        def _despachar(self, metodo: str, caminho: str, consulta: dict[str, str]) -> tuple[int, Any]:
            perfis = servicos.perfis
            if metodo == "POST" and caminho == "/perguntar":
                pergunta, contexto, ids = validar_corpo(self._corpo())
                return HTTPStatus.OK, {"resposta": servicos.assistente.responder(pergunta, contexto, ids)}
            if caminho == "/perfis" and metodo == "GET":
                return HTTPStatus.OK, perfis.listar(_dono(consulta.get("dono")))
            if caminho == "/perfis/sugerir" and metodo == "POST":
                c = _objeto(self._corpo())
                return HTTPStatus.OK, perfis.propor(_dono(c.get("dono")), c.get("descricao"), c.get("perfil_id"))
            if caminho == "/perfis" and metodo == "POST":
                c = _objeto(self._corpo())
                return HTTPStatus.ACCEPTED, perfis.salvar(_dono(c.get("dono")), c.get("descricao"),
                                                          c.get("perfil") or {}, c.get("perfil_id"))
            if caminho.startswith("/perfis/") and metodo == "DELETE":
                perfis.desativar(_dono(consulta.get("dono")), caminho.removeprefix("/perfis/"))
                return HTTPStatus.OK, {"ok": True}
            return HTTPStatus.NOT_FOUND, {"detail": f"Rota inexistente: {metodo} {caminho}"}

        def do_GET(self) -> None:  # noqa: N802
            self._rota("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._rota("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._rota("DELETE")

    return Handler


def _objeto(corpo: Any) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise PerguntaInvalidaError("Envie um objeto JSON.")
    return corpo


def _dono(valor: Any) -> str:
    texto = str(valor or "").strip()
    if not texto or len(texto) > 60:
        raise PerguntaInvalidaError("'dono' (a empresa cliente) é obrigatório.")
    return texto


def criar_servidor(servicos: Servicos, endereco: str, porta: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((endereco, porta), _handler(servicos))


def servir(servicos: Servicos, endereco: str = "0.0.0.0", porta: int = 9000) -> None:
    servidor = criar_servidor(servicos, endereco, porta)
    log.info("Agente em http://%s:%d (perguntas e perfis das empresas clientes)", endereco, porta)
    try:
        servidor.serve_forever()
    finally:
        servidor.server_close()
