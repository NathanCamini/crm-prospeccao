"""Arquivo mensal de empresas novas para o agente classificador (módulo 2)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from prospeccao.config import Config
from prospeccao.transform.novas import ExportacaoNovasError, exportar_novas, mes_anterior, pasta_novas
from tests.conftest import MES

SCHEMA = Path(__file__).resolve().parents[2] / "agente" / "schema" / "empresas_novas.v1.json"
_TIPOS = {
    "VARCHAR": r"VARCHAR", "DATE": r"DATE", "BOOLEAN": r"BOOLEAN", "VARCHAR[]": r"VARCHAR\[\]",
    "NUMERIC": r"DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|INTEGER|BIGINT|HUGEINT",
}


@pytest.fixture()
def cfg_copia(carga_amostra: tuple[Config, dict], tmp_path: Path) -> Config:
    """Cópia da base da amostra, para cada teste mexer à vontade."""
    cfg, _ = carga_amostra
    copia = replace(cfg, data_dir=tmp_path)
    copia.gold_dir.mkdir(parents=True)
    shutil.copy(cfg.gold_db, copia.gold_db)
    shutil.copytree(cfg.silver_dir / MES, copia.silver_dir / MES)
    return copia


def _ler(cfg: Config, mes: str = MES) -> dict[str, dict]:
    parquet = pasta_novas(cfg, mes) / "empresas_novas.parquet"
    with duckdb.connect() as con:
        cur = con.execute(f"SELECT * FROM read_parquet('{parquet.as_posix()}')")
        nomes = [d[0] for d in cur.description]
        return {linha[0]: dict(zip(nomes, linha)) for linha in cur.fetchall()}


def _silver_anterior(cfg: Config, sem: set[str]) -> None:
    """Silver do mês anterior com todos os estabelecimentos de hoje, menos os de `sem`."""
    destino = cfg.silver_dir / mes_anterior(MES) / "estabelecimentos" / "balde=0"
    destino.mkdir(parents=True)
    origem = (cfg.silver_dir / MES / "estabelecimentos").as_posix() + "/*/*.parquet"
    lista = ", ".join(f"'{c}'" for c in sem)
    with duckdb.connect() as con:
        con.execute(f"""COPY (SELECT * FROM read_parquet('{origem}', hive_partitioning=false)
                              WHERE cnpj NOT IN ({lista}))
                        TO '{(destino / 'dados.parquet').as_posix()}' (FORMAT parquet)""")


def test_carga_ja_grava_o_arquivo_do_mes(carga_amostra: tuple[Config, dict]) -> None:
    cfg, resumo = carga_amostra
    assert resumo["empresas_novas"]["total"] == 4
    manifesto = json.loads((pasta_novas(cfg, MES) / "_manifest.json").read_text(encoding="utf-8"))
    parquet = pasta_novas(cfg, MES) / "empresas_novas.parquet"
    assert manifesto["mes"] == MES and manifesto["versao_schema"] == 1 and manifesto["total_linhas"] == 4
    assert manifesto["sha256"] == hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert not list(pasta_novas(cfg, MES).glob("*.tmp"))


def test_sem_mes_anterior_usa_a_data_de_abertura(carga_amostra: tuple[Config, dict]) -> None:
    cfg, _ = carga_amostra
    linhas = _ler(cfg)
    # desde 01/08: entram as de 10, 5 e 30 dias e a de hoje; ficam fora a de 90/91 dias e a com data futura
    assert {c[:8] for c in linhas} == {"00012345", "44555666", "77888999", "A1B2C3D4"}
    assert linhas["00012345000165"]["matriz_filial"] == "MATRIZ"
    manifesto = json.loads((pasta_novas(cfg, MES) / "_manifest.json").read_text(encoding="utf-8"))
    assert manifesto["criterio"] == "abertura a partir de 2026-08-01"


def test_contato_original_e_marcacoes(carga_amostra: tuple[Config, dict]) -> None:
    cfg, _ = carga_amostra
    linhas = _ler(cfg)
    mei = next(v for c, v in linhas.items() if c.startswith("44555666"))
    assert mei["telefone_receita_1"] == " 99999000"            # sem alteração
    assert mei["email_receita"] == "Joao.Silva@Exemplo.com"
    assert mei["pessoa_fisica_identificavel"] is True          # empresário individual / MEI
    alfa = linhas["00012345000165"]
    assert alfa["pessoa_fisica_identificavel"] is False
    assert alfa["filial_de_empresa_existente"] is None          # matriz: não se aplica
    assert alfa["cnaes_secundarios"] == ["4399199", "4330404"]
    assert all(v["contato_compartilhado"] is False for v in linhas.values())
    assert all(v["mes_referencia_base"] == MES for v in linhas.values())


def test_atende_ao_contrato_do_agente(carga_amostra: tuple[Config, dict]) -> None:
    if not SCHEMA.exists():
        pytest.skip("schema do agente fora deste checkout")
    cfg, _ = carga_amostra
    parquet = pasta_novas(cfg, MES) / "empresas_novas.parquet"
    with duckdb.connect() as con:
        tipos = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM "
                                 f"read_parquet('{parquet.as_posix()}'))").fetchall())
    contrato = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for coluna in contrato["colunas"]:
        assert coluna["nome"] in tipos, f"falta {coluna['nome']}"
        assert re.fullmatch(_TIPOS[coluna["tipo"]], tipos[coluna["nome"]]), (coluna["nome"], tipos[coluna["nome"]])
    assert set(tipos) == {c["nome"] for c in contrato["colunas"]}


def test_validado_pelo_proprio_agente(carga_amostra: tuple[Config, dict]) -> None:
    contrato = pytest.importorskip("agente.contrato")
    cfg, _ = carga_amostra
    arquivo = contrato.validar_mes(cfg.data_dir / "exportacoes" / "novas", MES, SCHEMA.parent)
    assert arquivo.total_linhas == 4
    assert arquivo.tem("contato_compartilhado") and arquivo.tem("filial_de_empresa_existente")


def test_com_mes_anterior_compara_com_a_silver(cfg_copia: Config) -> None:
    with duckdb.connect(str(cfg_copia.gold_db), read_only=True) as con:
        tech = con.execute("SELECT cnpj FROM empresa WHERE cnpj LIKE 'A1B2C3D4%'").fetchone()[0]
    filial = "00012345000246"
    _silver_anterior(cfg_copia, {filial, tech})
    r = exportar_novas(cfg_copia)
    assert r["criterio"] == f"ausente na silver de {mes_anterior(MES)}"
    linhas = _ler(cfg_copia)
    assert set(linhas) == {filial, tech}                         # aberta há 90 dias, mas só agora apareceu
    assert linhas[filial]["filial_de_empresa_existente"] is True  # a matriz já estava no mês anterior
    assert linhas[tech]["filial_de_empresa_existente"] is None


def test_contato_compartilhado(cfg_copia: Config) -> None:
    with duckdb.connect(str(cfg_copia.gold_db)) as con:  # 6 outros CNPJs com o e-mail da ALFA OBRAS
        for i in range(6):
            con.execute("""INSERT INTO empresa SELECT * REPLACE ('9999999' || $i || '000100' AS cnpj,
                                  DATE '2000-01-01' AS data_inicio_atividade, NULL AS ddd_receita_1,
                                  NULL AS telefone_receita_1, 'contato@alfa-obras.com.br ' AS email_receita)
                           FROM empresa WHERE cnpj = '00012345000165'""", {"i": str(i)})
    exportar_novas(cfg_copia)
    linhas = _ler(cfg_copia)
    assert linhas["00012345000165"]["contato_compartilhado"] is True
    assert next(v for c, v in linhas.items() if c.startswith("44555666"))["contato_compartilhado"] is False


def test_recusa_mes_diferente_da_base(cfg_copia: Config) -> None:
    with pytest.raises(ExportacaoNovasError, match="é de 2026-09"):
        exportar_novas(cfg_copia, "2026-10")


def test_desde_explicito(cfg_copia: Config) -> None:
    r = exportar_novas(cfg_copia, desde=date(2026, 10, 1))
    assert r["total_linhas"] == 2  # a de hoje e a de 5 dias
    assert r["criterio"] == "abertura a partir de 2026-10-01"


def test_sem_base(cfg_vazio: Config) -> None:
    with pytest.raises(ExportacaoNovasError, match="Rode a carga"):
        exportar_novas(cfg_vazio)
