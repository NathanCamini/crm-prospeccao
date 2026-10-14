"""Testes de integração: amostra fictícia -> bronze -> silver -> gold."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import timedelta

import duckdb

from prospeccao.config import Config
from prospeccao.domain.cnpj import cnpj_valido
from prospeccao.domain.empresa_nova import e_empresa_nova
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.pipeline import executar_carga
from tests.conftest import MES, REFERENCIA


def _estabelecimentos_brutos(cfg: Config) -> list[list[str]]:
    linhas: list[list[str]] = []
    for zp in sorted((cfg.bronze_dir / MES / "zip").glob("Estabelecimentos*.zip")):
        with zipfile.ZipFile(zp) as zf:
            texto = zf.read(zf.namelist()[0]).decode("latin-1")
        linhas += list(csv.reader(io.StringIO(texto), delimiter=";"))
    return linhas


def test_contagens_registradas_por_camada(carga_amostra) -> None:
    _, resumo = carga_amostra
    por_chave = {(c["camada"], c["tabela"]): c for c in resumo["contagens"]}
    assert por_chave[("bronze", "estabelecimentos")]["saida"] == 8
    assert por_chave[("silver", "estabelecimentos")] == {
        "camada": "silver", "tabela": "estabelecimentos", "entrada": 8, "saida": 7,  # 1 duplicata removida
    }
    assert por_chave[("gold", "empresa")]["entrada"] == por_chave[("gold", "empresa")]["saida"] == 7
    assert {m["etapa"] for m in resumo["metricas"]} == {"bronze", "silver", "gold"}


def test_cnpj_14_caracteres_texto_com_zeros(gold: duckdb.DuckDBPyConnection) -> None:
    cnpjs = [r[0] for r in gold.execute("SELECT cnpj FROM empresa").fetchall()]
    assert all(len(c) == 14 for c in cnpjs)
    assert "00012345000165" in cnpjs
    assert all(cnpj_valido(c) for c in cnpjs)
    assert gold.execute("SELECT typeof(cnpj) FROM empresa LIMIT 1").fetchone()[0] == "VARCHAR"


def test_cnpj_alfanumerico_preservado(gold: duckdb.DuckDBPyConnection) -> None:
    assert gold.execute("SELECT count(*) FROM empresa WHERE cnpj LIKE 'A1B2C3D4%'").fetchone()[0] == 1


def test_resolucao_de_municipio(gold: duckdb.DuckDBPyConnection) -> None:
    linhas = dict(gold.execute("SELECT cnpj, municipio || '/' || uf FROM empresa").fetchall())
    assert linhas["00012345000165"] == "CIDADE ALFA/RS"
    assert linhas["00012345000246"] == "SÃO JOÃO DO TESTE/RS"  # acentos convertidos de Latin-1
    assert gold.execute("SELECT count(*) FROM empresa WHERE municipio IS NULL").fetchone()[0] == 0


def test_flag_empresa_nova_igual_a_regra_de_dominio(gold: duckdb.DuckDBPyConnection) -> None:
    for cnpj, inicio, is_nova in gold.execute("SELECT cnpj, data_inicio_atividade, is_nova FROM empresa").fetchall():
        assert is_nova == e_empresa_nova(inicio, REFERENCIA, 90), cnpj
    flags = dict(gold.execute("SELECT cnpj, is_nova FROM empresa").fetchall())
    assert flags["00012345000246"] is True    # exatamente 90 dias: inclusivo
    assert flags["11222333000181"] is False   # 91 dias
    assert flags["55666777000181"] is False   # data futura


def test_contato_original_da_receita_identico_a_fonte(carga_amostra, gold: duckdb.DuckDBPyConnection) -> None:
    cfg, _ = carga_amostra
    gold_contatos = {
        r[0]: r[1:]
        for r in gold.execute(
            "SELECT cnpj, ddd_receita_1, telefone_receita_1, ddd_receita_2, telefone_receita_2, email_receita "
            "FROM empresa"
        ).fetchall()
    }
    def vazio_para_none(v: str) -> str | None:
        return v if v != "" else None

    for linha in _estabelecimentos_brutos(cfg):
        cnpj = linha[0] + linha[1] + linha[2]
        fonte = tuple(vazio_para_none(linha[i]) for i in (21, 22, 23, 24, 27))
        assert gold_contatos[cnpj] == fonte, cnpj
    # casos específicos: espaço inicial e maiúsculas/minúsculas mantidos
    assert gold_contatos["44555666000181"][1] == " 99999000"
    assert gold_contatos["44555666000181"][4] == "Joao.Silva@Exemplo.com"


def test_porte_mei_e_simples(gold: duckdb.DuckDBPyConnection) -> None:
    porte = dict(gold.execute("SELECT cnpj, porte FROM empresa").fetchall())
    assert porte["44555666000181"] == "MEI"
    assert porte["00012345000165"] == "ME"
    assert porte["77888999000181"] == "EPP"
    assert porte["11222333000181"] == "DEMAIS"


def test_bairro_vazio_vira_nulo(gold: duckdb.DuckDBPyConnection) -> None:
    assert gold.execute("SELECT bairro FROM empresa WHERE cnpj='00012345000246'").fetchone()[0] is None


def test_socios_minimizados_lgpd(gold: duckdb.DuckDBPyConnection) -> None:
    colunas = {r[0] for r in gold.execute("DESCRIBE socio").fetchall()}
    assert colunas == {"cnpj_basico", "nome_socio", "qualificacao", "data_entrada", "tipo_socio"}


def test_nova_carga_preserva_historico(tmp_path) -> None:
    cfg = Config(data_dir=tmp_path, duckdb_memory_limit="512MB", duckdb_threads=2)
    gerar_amostra(cfg.bronze_dir, "2026-08", REFERENCIA - timedelta(days=30))
    executar_carga(cfg, "2026-08", baixar=False, data_referencia=REFERENCIA - timedelta(days=30))
    gerar_amostra(cfg.bronze_dir, MES, REFERENCIA)
    executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)
    with duckdb.connect(str(cfg.gold_db), read_only=True) as con:
        meses = [r[0] for r in con.execute("SELECT mes_referencia FROM carga ORDER BY data_ingestao, mes_referencia").fetchall()]
        assert sorted(meses) == ["2026-08", "2026-09"]
        assert con.execute("SELECT count(*) FROM carga WHERE contagens IS NULL").fetchone()[0] == 0
    assert not (cfg.bronze_dir / MES / "csv").exists()  # CSV extraído apagado; zips mantidos
    assert any((cfg.bronze_dir / MES / "zip").glob("*.zip"))


def test_carga_recusa_comecar_sem_espaco_em_disco(tmp_path) -> None:
    import pytest

    from prospeccao.pipeline import EspacoInsuficienteError

    cfg = Config(data_dir=tmp_path, espaco_minimo_gb=10**9)
    with pytest.raises(EspacoInsuficienteError, match="ESPACO_MINIMO_GB"):
        executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)


def test_silver_particionada_em_baldes(carga_amostra) -> None:
    cfg, _ = carga_amostra
    pasta = cfg.silver_dir / MES / "estabelecimentos"
    assert len(list(pasta.glob("balde=*"))) == 32


def test_cli_exportar_consulta_salva(carga_amostra, tmp_path, monkeypatch, capsys) -> None:
    import shutil

    from prospeccao.app_store import AppStore
    from prospeccao.cli import main
    from prospeccao.domain.filtros import Filtros

    cfg, _ = carga_amostra
    pasta = tmp_path / "dados"
    shutil.copytree(cfg.data_dir / "gold", pasta / "gold")
    AppStore(pasta / "app" / "app.sqlite").salvar_consulta("Tudo", Filtros(periodo=None, situacoes=()).para_dict())
    monkeypatch.setenv("PROSPECCAO_DATA_DIR", str(pasta))
    saida = tmp_path / "saida.xlsx"
    assert main(["exportar", "--consulta", "Tudo", "--formato", "xlsx", "--saida", str(saida)]) == 0
    assert "7 empresas exportadas" in capsys.readouterr().out
    assert saida.stat().st_size > 0
    assert main(["exportar", "--consulta", "Inexistente"]) == 2
