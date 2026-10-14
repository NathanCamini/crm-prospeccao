import io
import zipfile
from datetime import date

import pytest

from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.ingest.bronze import preparar_bronze
from prospeccao.ingest.layout import (
    ESTABELECIMENTOS,
    LayoutInvalidoError,
    tabela_do_zip,
    validar_linhas,
    verificar_conjunto_completo,
)

LINHA_OK = ";".join(f'"{v}"' for v in (
    ["00012345", "0001", "65", "1", "X", "02", "20261001", "00", "", "", "20261001", "4399103", "",
     "RUA", "A", "1", "", "CENTRO", "96200000", "RS", "9701", "53", "32345678", "", "", "", "", "a@b.com", "", ""]
))


def test_linha_correta_passa() -> None:
    assert validar_linhas(io.StringIO(LINHA_OK + "\n"), ESTABELECIMENTOS, "teste.csv") == 1


def test_coluna_a_menos_falha_com_mensagem_clara() -> None:
    linha = LINHA_OK.rsplit(";", 1)[0]  # 29 colunas
    with pytest.raises(LayoutInvalidoError, match=r"teste\.csv, linha 1.*esperava 30 colunas e encontrou 29"):
        validar_linhas(io.StringIO(linha + "\n"), ESTABELECIMENTOS, "teste.csv")


def test_formato_de_campo_chave_invalido() -> None:
    linha = LINHA_OK.replace('"20261001"', '"01/10/2026"', 1)
    with pytest.raises(LayoutInvalidoError, match="data_situacao_cadastral"):
        validar_linhas(io.StringIO(linha + "\n"), ESTABELECIMENTOS, "teste.csv")


def test_identifica_tabela_pelo_nome_do_zip() -> None:
    assert tabela_do_zip("Estabelecimentos3.zip").nome == "estabelecimentos"
    assert tabela_do_zip("Simples.zip").nome == "simples"
    assert tabela_do_zip("Leiame.pdf") is None


def test_conjunto_incompleto_lista_tabelas_ausentes() -> None:
    assert verificar_conjunto_completo(["Empresas0.zip", "Cnaes.zip"]) == [
        "estabelecimentos", "motivos", "municipios", "naturezas", "paises", "qualificacoes", "simples", "socios",
    ]


def test_bronze_rejeita_zip_com_layout_alterado(cfg_vazio) -> None:
    pasta = gerar_amostra(cfg_vazio.bronze_dir, "2026-09", date(2026, 10, 9))
    linha_ruim = LINHA_OK.rsplit(";", 1)[0].encode("latin-1")
    with zipfile.ZipFile(pasta / "Estabelecimentos0.zip", "w") as zf:
        zf.writestr("K3241.ESTABELE", linha_ruim + b"\n")
    with pytest.raises(LayoutInvalidoError, match="Estabelecimentos0.csv"):
        preparar_bronze(cfg_vazio, "2026-09")


def test_erro_de_layout_depois_da_linha_1000_tambem_interrompe(cfg_vazio) -> None:
    """A validação rápida olha 1.000 linhas; a leitura completa no DuckDB é estrita e pega o resto."""
    from prospeccao.pipeline import executar_carga

    pasta = gerar_amostra(cfg_vazio.bronze_dir, "2026-09", date(2026, 10, 9))
    linhas_ok = (LINHA_OK + "\n") * 1001
    linha_ruim = LINHA_OK + ';"COLUNA EXTRA"\n'
    with zipfile.ZipFile(pasta / "Estabelecimentos0.zip", "w") as zf:
        zf.writestr("K3241.ESTABELE", (linhas_ok + linha_ruim).encode("latin-1"))
    with pytest.raises(LayoutInvalidoError, match="estabelecimentos"):
        executar_carga(cfg_vazio, "2026-09", baixar=False, data_referencia=date(2026, 10, 9))


def test_conversao_windows1252_com_reserva_latin1() -> None:
    import io as _io

    from prospeccao.ingest.conversao import converter_para_utf8, decodificar

    bruto = b'"S\xc3O JO\xc3O";"\x93ASPAS\x94 \x96 TRA\x81"\n'  # Ã (latin-1), “ ” – (cp1252), 0x81 indefinido
    destino = _io.BytesIO()
    converter_para_utf8(_io.BytesIO(bruto), destino)
    assert destino.getvalue().decode("utf-8") == '"SÃO JOÃO";"“ASPAS” – TRA\x81"\n'
    assert decodificar(b"\xe7\xe3o") == "ção"


def test_carga_com_bytes_windows1252_que_o_duckdb_recusa(cfg_vazio) -> None:
    """Arquivos reais da Receita trazem bytes 0x80–0x9F; o DuckDB recusa como latin-1 (erro visto na carga real)."""
    import duckdb

    from prospeccao.pipeline import executar_carga

    pasta = gerar_amostra(cfg_vazio.bronze_dir, "2026-09", date(2026, 10, 9))
    with zipfile.ZipFile(pasta / "Estabelecimentos0.zip") as zf:
        nome = zf.namelist()[0]
        original = zf.read(nome)
    linha = LINHA_OK.replace('"00012345"', '"99887766"').replace('"RUA";"A"', '"RUA";"DAS \x93FLORES\x94 \x96 S\xc3O"')
    linha = linha.encode("latin-1")
    linha = linha.replace(b'"X"', b'"PADARIA \x93BOM P\xc3O\x94"')
    with zipfile.ZipFile(pasta / "Estabelecimentos0.zip", "w") as zf:
        zf.writestr(nome, original + linha + b"\n")
    executar_carga(cfg_vazio, "2026-09", baixar=False, data_referencia=date(2026, 10, 9))
    with duckdb.connect(str(cfg_vazio.gold_db), read_only=True) as con:
        fantasia, logradouro = con.execute(
            "SELECT nome_fantasia, logradouro FROM empresa WHERE nome_fantasia LIKE 'PADARIA “%'"
        ).fetchone()
    assert fantasia == "PADARIA “BOM PÃO”"
    assert logradouro == "DAS “FLORES” – SÃO"


def test_sem_download_usa_ultimo_mes_baixado(cfg_vazio) -> None:
    from prospeccao.pipeline import executar_carga

    gerar_amostra(cfg_vazio.bronze_dir, "2026-08", date(2026, 9, 9))
    gerar_amostra(cfg_vazio.bronze_dir, "2026-09", date(2026, 10, 9))
    resumo = executar_carga(cfg_vazio, None, baixar=False, data_referencia=date(2026, 10, 9))
    assert resumo["mes_referencia"] == "2026-09"


def test_bronze_em_paralelo_igual_ao_sequencial(tmp_path) -> None:
    import json
    from dataclasses import replace

    from prospeccao.config import Config
    from prospeccao.ingest.bronze import numero_de_processos

    resultados = {}
    for processos in (1, 3):
        cfg = Config(data_dir=tmp_path / str(processos), bronze_processos=processos)
        gerar_amostra(cfg.bronze_dir, "2026-09", date(2026, 10, 9))
        manifest = preparar_bronze(cfg, "2026-09")
        resultados[processos] = [(a["zip"], a["sha256"], a["tabela"]) for a in manifest["arquivos"]]
        assert len(manifest["arquivos"]) == 37
        assert all(pathlib_exists(a["csv"]) for a in manifest["arquivos"])
        assert json.loads((cfg.bronze_dir / "2026-09" / "manifest.json").read_text())["arquivos"]
    assert resultados[1] == resultados[3]
    automatico = replace(Config(data_dir=tmp_path), bronze_processos=0)
    assert numero_de_processos(automatico, 37, 1000) == 1  # amostra pequena: sem paralelismo
    assert numero_de_processos(automatico, 37, 10**10) >= 1


def test_bronze_em_paralelo_propaga_erro_de_layout(tmp_path) -> None:
    from prospeccao.config import Config

    cfg = Config(data_dir=tmp_path, bronze_processos=3)
    pasta = gerar_amostra(cfg.bronze_dir, "2026-09", date(2026, 10, 9))
    with zipfile.ZipFile(pasta / "Estabelecimentos3.zip", "w") as zf:
        zf.writestr("K3241.ESTABELE", LINHA_OK.rsplit(";", 1)[0].encode("latin-1") + b"\n")
    with pytest.raises(LayoutInvalidoError, match=r"Estabelecimentos3\.csv.*esperava 30 colunas"):
        preparar_bronze(cfg, "2026-09")


def test_csv_extraido_vai_para_pasta_de_trabalho(tmp_path) -> None:
    from prospeccao.config import Config

    cfg = Config(data_dir=tmp_path / "dados", trabalho_dir_env=tmp_path / "trabalho")
    gerar_amostra(cfg.bronze_dir, "2026-09", date(2026, 10, 9))
    manifest = preparar_bronze(cfg, "2026-09")
    assert all(a["csv"].startswith(str(tmp_path / "trabalho" / "csv" / "2026-09")) for a in manifest["arquivos"])
    assert not (cfg.bronze_dir / "2026-09" / "csv").exists()


def pathlib_exists(caminho: str) -> bool:
    from pathlib import Path

    return Path(caminho).exists()
