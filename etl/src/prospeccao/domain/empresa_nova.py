"""Regra de "empresa nova": data de início de atividade dentro da janela, limites inclusivos."""

from __future__ import annotations

from datetime import date, timedelta

JANELAS_PADRAO: tuple[int, ...] = (30, 60, 90, 180, 365)
JANELA_PADRAO_DIAS = 90


def intervalo_empresa_nova(referencia: date, dias: int = JANELA_PADRAO_DIAS) -> tuple[date, date]:
    """Devolve (inicio, fim), ambos inclusivos. Ex.: 90 dias até 09/10 = 11/07 a 09/10."""
    if dias <= 0:
        raise ValueError("A janela deve ter pelo menos 1 dia")
    return referencia - timedelta(days=dias), referencia


def e_empresa_nova(
    data_inicio_atividade: date | None,
    referencia: date,
    dias: int = JANELA_PADRAO_DIAS,
) -> bool:
    """True se a data de início de atividade está no intervalo [referencia - dias, referencia]."""
    if data_inicio_atividade is None:
        return False
    inicio, fim = intervalo_empresa_nova(referencia, dias)
    return inicio <= data_inicio_atividade <= fim
