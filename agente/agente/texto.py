"""Normalização de nomes e e-mails (as mesmas regras do módulo 1)."""

from __future__ import annotations

import unicodedata

PROVEDORES_GENERICOS = frozenset({
    "gmail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br", "live.com", "msn.com",
    "yahoo.com", "yahoo.com.br", "ymail.com", "icloud.com", "me.com", "bol.com.br", "uol.com.br",
    "terra.com.br", "ig.com.br", "globo.com", "globomail.com", "r7.com", "zipmail.com.br", "oi.com.br",
    "protonmail.com", "proton.me", "aol.com", "gmx.com", "mail.com",
})


def normalizar_texto(valor: str) -> str:
    """Minúsculas, sem acento e sem espaços nas pontas."""
    sem_acento = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


def dominio_do_email(email: str | None) -> str | None:
    """Domínio do e-mail da Receita, se não for de provedor genérico (gmail, hotmail...)."""
    if not email or "@" not in email:
        return None
    dom = email.strip().lower().rsplit("@", 1)[1]
    if dom in PROVEDORES_GENERICOS or "." not in dom:
        return None
    return dom
