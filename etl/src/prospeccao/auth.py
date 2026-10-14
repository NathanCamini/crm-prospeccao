"""Hash e verificação de senhas com scrypt (biblioteca padrão), sal aleatório por senha."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _TAMANHO = 2**14, 8, 1, 32
SENHA_MINIMA = 8


class SenhaFracaError(ValueError):
    """Senha não atende ao tamanho mínimo."""


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


def gerar_hash(senha: str) -> str:
    """Devolve 'scrypt$n$r$p$sal$hash' (nunca a senha)."""
    if len(senha) < SENHA_MINIMA:
        raise SenhaFracaError(f"A senha deve ter pelo menos {SENHA_MINIMA} caracteres")
    sal = secrets.token_bytes(16)
    chave = hashlib.scrypt(senha.encode("utf-8"), salt=sal, n=_N, r=_R, p=_P, dklen=_TAMANHO)
    return f"scrypt${_N}${_R}${_P}${_b64(sal)}${_b64(chave)}"


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    """Compara em tempo constante. Hash malformado = senha inválida."""
    try:
        algoritmo, n, r, p, sal, chave = hash_armazenado.split("$")
        if algoritmo != "scrypt":
            return False
        calculada = hashlib.scrypt(
            senha.encode("utf-8"), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p),
            dklen=len(base64.b64decode(chave)),
        )
        return hmac.compare_digest(calculada, base64.b64decode(chave))
    except (ValueError, TypeError):
        return False
