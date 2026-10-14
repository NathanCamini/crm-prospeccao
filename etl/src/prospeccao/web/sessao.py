"""Login que sobrevive a recarregar a página: cookie com um código aleatório de sessão.

- O navegador guarda só um código aleatório (não a senha nem o nome do usuário).
- O banco do usuário guarda apenas o hash SHA-256 desse código, com validade (SESSAO_HORAS).
- Sair, trocar a senha ou remover o usuário invalida a sessão no servidor.
- Cookie com SameSite=Strict e, quando a página está em HTTPS, Secure.
"""

from __future__ import annotations

import json
import re

import streamlit as st

NOME_COOKIE = "prospeccao_sessao"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,100}")


def ler_cookie() -> str | None:
    """Código de sessão enviado pelo navegador ao abrir a página (None se não houver ou for inválido)."""
    try:
        valor = st.context.cookies.get(NOME_COOKIE)
    except Exception:  # noqa: BLE001 — sem contexto de navegador (ex.: testes)
        return None
    return valor if isinstance(valor, str) and _TOKEN.fullmatch(valor) else None


def _script(conteudo_cookie: str) -> str:
    return (
        "<script>(function(){var c=" + json.dumps(conteudo_cookie) + ";"
        "if(location.protocol==='https:'){c+='; Secure';}document.cookie=c;})();</script>"
    )


def gravar_cookie(token: str, horas: float) -> None:
    if not _TOKEN.fullmatch(token):
        raise ValueError("Código de sessão inválido")
    segundos = int(horas * 3600)
    st.html(_script(f"{NOME_COOKIE}={token}; Max-Age={segundos}; Path=/; SameSite=Strict"),
            unsafe_allow_javascript=True)


def apagar_cookie() -> None:
    st.html(_script(f"{NOME_COOKIE}=; Max-Age=0; Path=/; SameSite=Strict"), unsafe_allow_javascript=True)
