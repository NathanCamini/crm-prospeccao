"""Datas no fuso da operação (padrão: America/Sao_Paulo). O banco guarda horários em UTC."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def hoje(fuso: str) -> date:
    return datetime.now(ZoneInfo(fuso)).date()


def inicio_do_dia_utc(dia: date, fuso: str) -> str:
    """Instante UTC (ISO) em que começa o dia local `dia`."""
    return datetime.combine(dia, time.min, ZoneInfo(fuso)).astimezone(timezone.utc).isoformat(timespec="seconds")


def intervalo_utc(de: date, ate: date, fuso: str) -> tuple[str, str]:
    """[início de `de`, início do dia seguinte a `ate`) em UTC, para comparar com criado_em."""
    return inicio_do_dia_utc(de, fuso), inicio_do_dia_utc(ate + timedelta(days=1), fuso)
