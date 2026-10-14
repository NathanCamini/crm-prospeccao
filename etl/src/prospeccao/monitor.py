"""Log padronizado de contagens por camada e medição de tempo e memória."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psutil

log = logging.getLogger("prospeccao")


def configurar_log(nivel: int = logging.INFO) -> None:
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def pico_memoria_mb() -> float:
    """Pico de memória residente do processo (inclui o DuckDB), em MB."""
    try:
        import resource

        pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux devolve KB; macOS devolve bytes.
        return pico / (1024 * 1024) if sys.platform == "darwin" else pico / 1024
    except ImportError:  # Windows
        info = psutil.Process().memory_info()
        return getattr(info, "peak_wset", info.rss) / (1024 * 1024)


def registrar_contagem(camada: str, tabela: str, entrada: int, saida: int) -> dict[str, Any]:
    log.info("camada=%s tabela=%s entrada=%d saida=%d", camada, tabela, entrada, saida)
    return {"camada": camada, "tabela": tabela, "entrada": entrada, "saida": saida}


@contextmanager
def medir(etapa: str) -> Iterator[dict[str, Any]]:
    """Mede duração e pico de memória de uma etapa; o dict é preenchido ao sair."""
    metrica: dict[str, Any] = {"etapa": etapa}
    inicio = time.perf_counter()
    try:
        yield metrica
    finally:
        metrica["segundos"] = round(time.perf_counter() - inicio, 2)
        metrica["pico_memoria_mb"] = round(pico_memoria_mb(), 1)
        log.info(
            "etapa=%s segundos=%.2f pico_memoria_processo_mb=%.1f",
            etapa,
            metrica["segundos"],
            metrica["pico_memoria_mb"],
        )
