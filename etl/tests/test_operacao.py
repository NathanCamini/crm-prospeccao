"""Operação: trava de carga, carga sob demanda, agendador e limpeza de meses antigos."""

from __future__ import annotations

from datetime import timedelta

import pytest

from prospeccao.config import Config
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.pipeline import (
    CargaEmAndamentoError,
    carregar_se_houver_novo,
    executar_carga,
    limpar_meses_antigos,
    mes_carregado,
    rodar_agendador,
    trava_carga,
)
from tests.conftest import MES, REFERENCIA


def _cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path, duckdb_memory_limit="512MB", duckdb_threads=2)


def test_trava_impede_duas_cargas(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    with trava_carga(cfg):
        with pytest.raises(CargaEmAndamentoError, match="em andamento"):
            with trava_carga(cfg):
                pass
    with trava_carga(cfg):  # liberada ao sair
        pass


def test_trava_entre_processos(tmp_path) -> None:
    """Outro processo segurando a trava bloqueia a carga; quando ele termina, a trava é liberada."""
    import subprocess
    import sys
    import textwrap

    cfg = _cfg(tmp_path)
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from prospeccao.config import Config
        from prospeccao.pipeline import trava_carga
        with trava_carga(Config(data_dir=Path({str(tmp_path)!r}))):
            print("travado", flush=True)
            time.sleep(30)
    """)
    filho = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert filho.stdout.readline().strip() == "travado"
        with pytest.raises(CargaEmAndamentoError, match=str(filho.pid)):
            with trava_carga(cfg):
                pass
    finally:
        filho.kill()  # simula processo que morreu no meio da carga
        filho.wait()
    with trava_carga(cfg):  # o sistema soltou a trava do processo morto
        pass


def test_arquivo_de_trava_antigo_nao_bloqueia(tmp_path) -> None:
    """Arquivo deixado por uma carga interrompida (sem processo segurando) não impede nova carga."""
    (tmp_path / ".carga.lock").write_text("1")
    with trava_carga(_cfg(tmp_path)):
        pass


class _ClienteFalso:
    def __init__(self, meses: list[str]) -> None:
        self.meses = meses

    def listar_meses(self) -> list[str]:
        return self.meses


def test_carga_sob_demanda_so_quando_ha_mes_novo(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    gerar_amostra(cfg.bronze_dir, MES, REFERENCIA)
    executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)
    assert mes_carregado(cfg) == MES
    assert carregar_se_houver_novo(cfg, _ClienteFalso(["2026-08", MES])) is None

    chamadas = []
    monkeypatch.setattr("prospeccao.pipeline.executar_carga", lambda c, m: chamadas.append(m) or {"mes_referencia": m})
    assert carregar_se_houver_novo(cfg, _ClienteFalso([MES, "2026-10"])) == {"mes_referencia": "2026-10"}
    assert chamadas == ["2026-10"]


def test_agendador_nao_cai_com_erro(tmp_path, monkeypatch, caplog) -> None:
    def falha(_cfg):
        raise RuntimeError("Receita fora do ar")

    monkeypatch.setattr("prospeccao.pipeline.carregar_se_houver_novo", falha)
    rodar_agendador(_cfg(tmp_path), intervalo_horas=24, uma_vez=True)
    assert "Falha na verificação/carga agendada" in caplog.text


def test_limpeza_mantem_os_meses_mais_recentes(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    for mes in ("2026-07", "2026-08", "2026-09"):
        (cfg.bronze_dir / mes).mkdir(parents=True)
        (cfg.silver_dir / mes).mkdir(parents=True)
    removidos = limpar_meses_antigos(cfg, manter=2)
    assert removidos == ["bronze/2026-07", "silver/2026-07"]
    assert sorted(p.name for p in cfg.bronze_dir.iterdir()) == ["2026-08", "2026-09"]


def test_carga_apaga_zips_e_silver_e_deixa_so_a_gold(tmp_path) -> None:
    from dataclasses import replace

    cfg = replace(_cfg(tmp_path), manter_arquivos_carga=False)
    gerar_amostra(cfg.bronze_dir, "2026-08", REFERENCIA - timedelta(days=30))
    executar_carga(cfg, "2026-08", baixar=False, data_referencia=REFERENCIA - timedelta(days=30))
    gerar_amostra(cfg.bronze_dir, MES, REFERENCIA)
    executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)
    assert list(cfg.bronze_dir.iterdir()) == [] and list(cfg.silver_dir.iterdir()) == []
    assert mes_carregado(cfg) == MES and cfg.gold_db.exists()
    novas = cfg.data_dir / "exportacoes" / "novas"
    assert sorted(p.name for p in novas.iterdir()) == [MES, "_ultimo_corte.json"]   # o arquivo de agosto saiu


