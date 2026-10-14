"""Senhas (scrypt) e sessões persistentes por cookie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import agora

N, R, P = 2**14, 8, 1
SENHA_MINIMA = 8
COOKIE = "crm_sessao"
NOME_VALIDO = re.compile(r"[a-z0-9._-]{2,64}")


def gerar_hash(senha: str) -> str:
    sal = secrets.token_bytes(16)
    chave = hashlib.scrypt(senha.encode(), salt=sal, n=N, r=R, p=P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${b(sal)}${b(chave)}"


def conferir(senha: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, chave = guardado.split("$")
        calculada = hashlib.scrypt(
            senha.encode(), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(calculada, base64.b64decode(chave))
    except (ValueError, TypeError):
        return False


# Usado quando o usuário não existe, para o tempo de resposta ser igual.
HASH_FALSO = gerar_hash(secrets.token_urlsafe(12))


def validar_senha(senha: str) -> str | None:
    if len(senha) < SENHA_MINIMA:
        return f"A senha precisa de pelo menos {SENHA_MINIMA} caracteres."
    return None


def validar_nome(nome: str) -> str | None:
    if not NOME_VALIDO.fullmatch(nome):
        return "Nome de usuário inválido: use de 2 a 64 letras minúsculas, números, ponto, hífen ou sublinhado."
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def criar_sessao(conn: sqlite3.Connection, usuario: str, dias: int, empresa_ativa: int | None) -> str:
    token = secrets.token_urlsafe(32)
    expira = (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessao (token_hash, usuario, empresa_ativa, criada_em, expira_em, ultimo_uso) VALUES (?, ?, ?, ?, ?, ?)",
        (hash_token(token), usuario, empresa_ativa, agora(), expira, agora()),
    )
    conn.execute("DELETE FROM sessao WHERE expira_em < ?", (agora(),))
    return token


def usuario_da_sessao(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """Sessão válida de usuário ativo, de empresa ativa (o superadmin não tem empresa)."""
    if not token:
        return None
    linha = conn.execute(
        """SELECT u.nome, u.nome_exibicao, u.papel, u.empresa_id, u.deve_trocar_senha,
                  s.token_hash, s.empresa_ativa
           FROM sessao s
           JOIN usuario u ON u.nome = s.usuario
           LEFT JOIN empresa e ON e.id = u.empresa_id
           WHERE s.token_hash = ? AND s.expira_em > ? AND u.ativo = 1
             AND (u.papel = 'superadmin' OR e.ativa = 1)""",
        (hash_token(token), agora()),
    ).fetchone()
    if linha:
        conn.execute("UPDATE sessao SET ultimo_uso = ? WHERE token_hash = ?", (agora(), linha["token_hash"]))
    return linha


def encerrar_sessao(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessao WHERE token_hash = ?", (hash_token(token),))


def encerrar_sessoes_do_usuario(conn: sqlite3.Connection, usuario: str, exceto_hash: str | None = None) -> None:
    conn.execute("DELETE FROM sessao WHERE usuario = ? AND token_hash IS NOT ?", (usuario, exceto_hash))
