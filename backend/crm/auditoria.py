"""Auditoria: quem fez o quê, quando, de onde, e o que mudou.

Toda requisição à API gera ao menos uma linha:
- as rotas que alteram dados anotam o que mudou (`anotar`), com antes e depois
  só dos campos alterados;
- as rotas de leitura sensível (ficha de lead, exportação, relatórios) anotam o acesso;
- qualquer outra requisição vira uma linha genérica com rota e resultado;
- recusas (sem login, sem permissão, bloqueio) são gravadas como "negado" ou
  "recusado", mesmo que a rota tenha desfeito tudo.

As linhas são encadeadas: cada uma guarda o hash da anterior. O banco recusa
UPDATE e DELETE na tabela, e `verificar` aponta qualquer adulteração feita
direto no arquivo.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request

from .db import agora, conectar

GENESIS = "0" * 64
CAMPOS_HASH = (
    "quando", "requisicao", "usuario", "papel", "empresa_id", "tipo", "acao", "entidade", "entidade_id",
    "antes", "depois", "detalhe", "resultado", "status", "metodo", "rota", "ip", "agente",
)
# Rotas de rotina que não dizem nada sobre o que a pessoa fez (a sessão é conferida
# a cada página; a contagem da agenda é consultada sozinha).
SILENCIOSAS = {"/api/saude", "/api/auth/eu", "/api/equipe/nomes", "/api/tarefas/contagem"}

# Eventos que só acontecem na tela e que a interface informa por /api/auditoria/evento.
EVENTOS_DE_TELA = {
    "ui.copiar_mensagem": "Copiou o texto de uma mensagem",
    "ui.abrir_site": "Abriu o site ou rede social da empresa",
    "ui.ligar": "Clicou para ligar",
    "ui.abrir_link_contato": "Abriu WhatsApp, e-mail ou formulário",
    "ui.copiar_credencial": "Copiou dados de acesso de uma pessoa",
}


@dataclass
class Anotacao:
    tipo: str
    acao: str
    entidade: str | None = None
    entidade_id: str | None = None
    antes: dict | None = None
    depois: dict | None = None
    detalhe: Any = None
    usuario: str | None = None
    empresa_id: int | None = None
    sempre: bool = False  # grava mesmo se a requisição terminar em erro (ex.: senha errada)


@dataclass
class Ator:
    usuario: str | None = None
    papel: str | None = None
    empresa_id: int | None = None
    anotacoes: list[Anotacao] = field(default_factory=list)


def ator(request: Request) -> Ator:
    if not hasattr(request.state, "ator"):
        request.state.ator = Ator()
    return request.state.ator


def anotar(request: Request, tipo: str, acao: str, *, entidade: str | None = None, entidade_id: Any = None,
           antes: dict | None = None, depois: dict | None = None, detalhe: Any = None,
           usuario: str | None = None, empresa_id: int | None = None, sempre: bool = False) -> None:
    ator(request).anotacoes.append(
        Anotacao(tipo, acao, entidade, None if entidade_id is None else str(entidade_id), antes, depois, detalhe,
                 usuario, empresa_id, sempre)
    )


def diferencas(antes: dict | None, depois: dict | None) -> tuple[dict | None, dict | None]:
    """Só os campos que mudaram, para o registro mostrar exatamente o que foi alterado."""
    antes, depois = antes or {}, depois or {}
    chaves = [k for k in dict.fromkeys([*antes, *depois]) if antes.get(k) != depois.get(k)]
    if not chaves:
        return None, None
    return ({k: antes.get(k) for k in chaves if k in antes} or None, {k: depois.get(k) for k in chaves if k in depois} or None)


def _json(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)


def calcular_hash(linha: dict, anterior: str) -> str:
    conteudo = json.dumps({k: linha.get(k) for k in CAMPOS_HASH}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256((anterior + conteudo).encode()).hexdigest()


def gravar(caminho: Path, linhas: list[dict]) -> None:
    """Grava as linhas em ordem, encadeadas, numa transação exclusiva."""
    if not linhas:
        return
    conn = conectar(caminho)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ultima = conn.execute("SELECT hash FROM auditoria ORDER BY id DESC LIMIT 1").fetchone()
        anterior = ultima["hash"] if ultima else GENESIS
        for linha in linhas:
            linha = {k: linha.get(k) for k in CAMPOS_HASH}
            linha["antes"], linha["depois"], linha["detalhe"] = _json(linha["antes"]), _json(linha["depois"]), _json(linha["detalhe"])
            linha["hash_anterior"] = anterior
            linha["hash"] = anterior = calcular_hash(linha, anterior)
            colunas = ", ".join(linha)
            conn.execute(f"INSERT INTO auditoria ({colunas}) VALUES ({', '.join('?' * len(linha))})", list(linha.values()))
        conn.commit()
    finally:
        conn.close()


def registrar_sistema(caminho: Path, acao: str, *, detalhe: Any = None, resultado: str = "ok",
                      empresa_id: int | None = None, usuario: str = "sistema") -> None:
    """Para o que o próprio CRM faz sozinho (sincronização) ou o terminal."""
    gravar(caminho, [{
        "quando": agora(), "requisicao": uuid.uuid4().hex[:16], "usuario": usuario, "papel": "sistema",
        "empresa_id": empresa_id, "tipo": "sistema", "acao": acao, "detalhe": detalhe, "resultado": resultado,
    }])


def resultado_do_status(status: int) -> str:
    if status < 400:
        return "ok"
    if status in (401, 403):
        return "negado"
    if status >= 500:
        return "erro"
    return "recusado"


def ip_do_cliente(request: Request, confiar_proxy: bool) -> str | None:
    if confiar_proxy:
        encaminhado = request.headers.get("x-forwarded-for")
        if encaminhado:
            # O último endereço é o que o nosso nginx acrescentou; os anteriores vêm do cliente e podem ser forjados.
            return encaminhado.split(",")[-1].strip()
    return request.client.host if request.client else None


def linhas_da_requisicao(request: Request, status: int, requisicao: str, confiar_proxy: bool, detalhe_erro: str | None) -> list[dict]:
    a = ator(request)
    rota = request.scope.get("route")
    base = {
        "quando": agora(),
        "requisicao": requisicao,
        "usuario": a.usuario,
        "papel": a.papel,
        "empresa_id": a.empresa_id,
        "resultado": resultado_do_status(status),
        "status": status,
        "metodo": request.method,
        "rota": request.url.path,
        "ip": ip_do_cliente(request, confiar_proxy),
        "agente": (request.headers.get("user-agent") or "")[:200] or None,
    }
    sucesso = status < 400
    linhas = []
    for an in a.anotacoes:
        if not (sucesso or an.sempre):
            continue
        linhas.append(base | {
            "usuario": an.usuario or a.usuario,
            "empresa_id": an.empresa_id if an.empresa_id is not None else a.empresa_id,
            "tipo": an.tipo, "acao": an.acao, "entidade": an.entidade, "entidade_id": an.entidade_id,
            "antes": an.antes, "depois": an.depois, "detalhe": an.detalhe,
        })
    if not linhas:
        nome = getattr(rota, "name", None) or "desconhecida"
        if sucesso:
            tipo, acao = ("acesso" if request.method == "GET" else "alteracao"), f"rota.{nome}"
        else:
            tipo, acao = "seguranca", "acesso.negado" if status in (401, 403) else f"rota.{nome}"
        linhas.append(base | {"tipo": tipo, "acao": acao, "detalhe": detalhe_erro})
    return linhas


def verificar(caminho: Path) -> tuple[int, int | None]:
    """Refaz a cadeia. Devolve (linhas conferidas, id da primeira linha adulterada ou None)."""
    conn = conectar(caminho)
    try:
        anterior = GENESIS
        n = 0
        for linha in conn.execute("SELECT * FROM auditoria ORDER BY id"):
            n += 1
            d = dict(linha)
            if d["hash_anterior"] != anterior or calcular_hash(d, anterior) != d["hash"]:
                return n, d["id"]
            anterior = d["hash"]
        return n, None
    finally:
        conn.close()


def consultar(conn: sqlite3.Connection, *, empresa_id: int | None, de: str | None, ate: str | None,
              usuario: str | None, acao: str | None, resultado: str | None, entidade_id: str | None,
              tipo: str | None, limite: int, deslocamento: int) -> tuple[int, list[dict]]:
    filtros, params = [], []
    if empresa_id is not None:
        filtros.append("empresa_id = ?")
        params.append(empresa_id)
    if de:
        filtros.append("quando >= ?")
        params.append(de)
    if ate:
        filtros.append("quando < ?")
        params.append(ate)
    for coluna, valor in (("usuario", usuario), ("resultado", resultado), ("entidade_id", entidade_id), ("tipo", tipo)):
        if valor:
            filtros.append(f"{coluna} = ?")
            params.append(valor)
    if acao:
        filtros.append("acao LIKE ?")
        params.append(acao.rstrip("*") + "%")
    onde = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    total = conn.execute(f"SELECT COUNT(*) FROM auditoria {onde}", params).fetchone()[0]
    linhas = []
    for r in conn.execute(
        f"""SELECT id, quando, requisicao, usuario, papel, empresa_id, tipo, acao, entidade, entidade_id, antes, depois,
                   detalhe, resultado, status, metodo, rota, ip, agente FROM auditoria {onde} ORDER BY id DESC LIMIT ? OFFSET ?""",
        [*params, limite, deslocamento],
    ):
        d = dict(r)
        for k in ("antes", "depois", "detalhe"):
            if d[k] and d[k][:1] in "[{":
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        linhas.append(d)
    return total, linhas
