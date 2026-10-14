from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from prospeccao.config import Config
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.pipeline import executar_carga

REFERENCIA = date(2026, 10, 9)
MES = "2026-09"


def config_teste(pasta: Path) -> Config:
    return Config(data_dir=pasta, duckdb_memory_limit="512MB", duckdb_threads=2, apagar_csv_extraido=False)


@pytest.fixture(scope="session")
def carga_amostra(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict]:
    cfg = config_teste(tmp_path_factory.mktemp("dados"))
    gerar_amostra(cfg.bronze_dir, MES, REFERENCIA)
    resumo = executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)
    return cfg, resumo


@pytest.fixture()
def gold(carga_amostra: tuple[Config, dict]) -> duckdb.DuckDBPyConnection:
    cfg, _ = carga_amostra
    con = duckdb.connect(str(cfg.gold_db), read_only=True)
    yield con
    con.close()


@pytest.fixture()
def cfg_vazio(tmp_path: Path) -> Config:
    return replace(config_teste(tmp_path))
