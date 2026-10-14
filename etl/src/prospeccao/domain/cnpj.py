"""Normalização, validação e formatação de CNPJ.

O CNPJ é sempre tratado como texto de 14 caracteres. Desde julho de 2026 a
Receita emite CNPJs alfanuméricos: as 12 primeiras posições aceitam [0-9A-Z]
e os 2 dígitos verificadores continuam numéricos. O cálculo do DV usa o valor
ASCII do caractere menos 48, o que mantém o resultado idêntico para CNPJs
somente numéricos.
"""

from __future__ import annotations

import re

_REMOVER = re.compile(r"[\s./-]")
_VALIDO_BRUTO = re.compile(r"[0-9A-Z]{1,14}")
_FORMATO = re.compile(r"[0-9A-Z]{12}[0-9]{2}")
_PESOS_DV1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_DV2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


class CNPJInvalidoError(ValueError):
    """CNPJ com caracteres ou tamanho inválidos."""


def normalizar_cnpj(valor: str) -> str:
    """Remove pontuação, converte para maiúsculas e completa com zeros à esquerda.

    Aceita apenas texto: números inteiros perdem zeros à esquerda e são rejeitados.
    """
    if not isinstance(valor, str):
        raise TypeError("CNPJ deve ser texto (str), nunca número")
    limpo = _REMOVER.sub("", valor).upper()
    if not _VALIDO_BRUTO.fullmatch(limpo):
        raise CNPJInvalidoError(f"CNPJ inválido: {valor!r}")
    return limpo.zfill(14)


def montar_cnpj(basico: str, ordem: str, dv: str) -> str:
    """Monta o CNPJ completo a partir das três partes da base da Receita."""
    return basico.strip().upper().zfill(8) + ordem.strip().upper().zfill(4) + dv.strip().zfill(2)


def _digito(base: str, pesos: tuple[int, ...]) -> str:
    soma = sum((ord(c) - 48) * p for c, p in zip(base, pesos, strict=True))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def calcular_dv(primeiros_12: str) -> str:
    """Calcula os dois dígitos verificadores a partir das 12 primeiras posições."""
    base = primeiros_12.upper()
    if not re.fullmatch(r"[0-9A-Z]{12}", base):
        raise CNPJInvalidoError(f"Base de CNPJ inválida: {primeiros_12!r}")
    dv1 = _digito(base, _PESOS_DV1)
    dv2 = _digito(base + dv1, _PESOS_DV2)
    return dv1 + dv2


def cnpj_valido(valor: str) -> bool:
    """True se o CNPJ tem formato correto e dígitos verificadores válidos."""
    try:
        cnpj = normalizar_cnpj(valor)
    except (CNPJInvalidoError, TypeError):
        return False
    if not _FORMATO.fullmatch(cnpj) or len(set(cnpj)) == 1:
        return False
    return calcular_dv(cnpj[:12]) == cnpj[12:]


def formatar_cnpj(valor: str) -> str:
    """Formata como XX.XXX.XXX/XXXX-XX."""
    c = normalizar_cnpj(valor)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
