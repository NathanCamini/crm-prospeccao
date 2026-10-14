"""Camada silver: CSV Latin-1 -> Parquet UTF-8 tipado e normalizado, em streaming.

Tabelas grandes (empresas, estabelecimentos, sócios, simples) viram Parquets
divididos em lotes ("baldes") por hash do CNPJ básico; domínios viram um arquivo. Regras:
- CNPJ e códigos como texto, com zeros à esquerda preservados;
- datas AAAAMMDD -> DATE (00000000 e vazio -> NULL);
- capital social '1000,50' -> DECIMAL(18,2);
- telefones e e-mail copiados SEM nenhuma alteração;
- sócios minimizados (LGPD): sem CPF/CNPJ do sócio, representante legal ou faixa etária;
- duplicidades removidas pela chave da tabela.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb

from prospeccao.ingest.bronze import sql_ler_csv
from prospeccao.ingest.layout import TABELAS, LayoutInvalidoError
from prospeccao.monitor import registrar_contagem

log = logging.getLogger("prospeccao.silver")

MACROS = """
CREATE OR REPLACE MACRO cod(x, n) AS lpad(NULLIF(trim(x), ''), n, '0');
CREATE OR REPLACE MACRO basico(x) AS lpad(upper(trim(x)), 8, '0');
CREATE OR REPLACE MACRO data_rf(x) AS CASE
    WHEN regexp_full_match(trim(x), '[0-9]{8}') AND trim(x) <> '00000000'
    THEN try_strptime(trim(x), '%Y%m%d')::DATE END;
CREATE OR REPLACE MACRO sim_nao(x) AS CASE upper(trim(x)) WHEN 'S' THEN true WHEN 'N' THEN false END;
"""

_SELECTS: dict[str, tuple[str, str]] = {
    # tabela: (SELECT ... sobre a origem `src`, PARTITION BY [ORDER BY] da deduplicação)
    "empresas": (
        """SELECT basico(cnpj_basico) AS cnpj_basico,
                  razao_social,
                  cod(natureza_juridica, 4) AS natureza_juridica,
                  cod(qualificacao_responsavel, 2) AS qualificacao_responsavel,
                  TRY_CAST(replace(capital_social, ',', '.') AS DECIMAL(18,2)) AS capital_social,
                  cod(porte_empresa, 2) AS porte_empresa,
                  NULLIF(trim(ente_federativo_responsavel), '') AS ente_federativo_responsavel
           FROM src""",
        "cnpj_basico",
    ),
    "estabelecimentos": (
        """SELECT basico(cnpj_basico) || lpad(upper(trim(cnpj_ordem)), 4, '0') || lpad(trim(cnpj_dv), 2, '0') AS cnpj,
                  basico(cnpj_basico) AS cnpj_basico,
                  lpad(upper(trim(cnpj_ordem)), 4, '0') AS cnpj_ordem,
                  lpad(trim(cnpj_dv), 2, '0') AS cnpj_dv,
                  trim(identificador_matriz_filial) AS identificador_matriz_filial,
                  nome_fantasia,
                  cod(situacao_cadastral, 2) AS situacao_cadastral,
                  data_rf(data_situacao_cadastral) AS data_situacao_cadastral,
                  cod(motivo_situacao_cadastral, 2) AS motivo_situacao_cadastral,
                  NULLIF(trim(nome_cidade_exterior), '') AS nome_cidade_exterior,
                  cod(pais, 3) AS pais,
                  data_rf(data_inicio_atividade) AS data_inicio_atividade,
                  cod(cnae_fiscal_principal, 7) AS cnae_principal,
                  CASE WHEN NULLIF(trim(cnae_fiscal_secundaria), '') IS NULL THEN []::VARCHAR[]
                       ELSE list_transform(string_split(trim(cnae_fiscal_secundaria), ','), c -> lpad(trim(c), 7, '0'))
                  END AS cnaes_secundarios,
                  tipo_logradouro, logradouro, numero, complemento,
                  NULLIF(trim(bairro), '') AS bairro,
                  cep,
                  upper(trim(uf)) AS uf,
                  cod(municipio, 4) AS codigo_municipio_receita,
                  ddd_1, telefone_1, ddd_2, telefone_2, ddd_fax, fax,
                  correio_eletronico,
                  NULLIF(trim(situacao_especial), '') AS situacao_especial,
                  data_rf(data_situacao_especial) AS data_situacao_especial
           FROM src""",
        "cnpj ORDER BY data_situacao_cadastral DESC NULLS LAST",
    ),
    "socios": (
        """SELECT basico(cnpj_basico) AS cnpj_basico,
                  trim(identificador_socio) AS identificador_socio,
                  nome_socio,
                  cod(qualificacao_socio, 2) AS qualificacao_socio,
                  data_rf(data_entrada_sociedade) AS data_entrada_sociedade
           FROM src""",
        "cnpj_basico, nome_socio, qualificacao_socio",
    ),
    "simples": (
        """SELECT basico(cnpj_basico) AS cnpj_basico,
                  sim_nao(opcao_simples) AS opcao_simples,
                  data_rf(data_opcao_simples) AS data_opcao_simples,
                  data_rf(data_exclusao_simples) AS data_exclusao_simples,
                  sim_nao(opcao_mei) AS opcao_mei,
                  data_rf(data_opcao_mei) AS data_opcao_mei,
                  data_rf(data_exclusao_mei) AS data_exclusao_mei
           FROM src""",
        "cnpj_basico",
    ),
}

N_BALDES = 32
TABELAS_PARTICIONADAS = ("empresas", "estabelecimentos", "socios", "simples")

_TAMANHO_CODIGO = {"cnaes": 7, "motivos": 2, "municipios": 4, "naturezas": 4, "paises": 3, "qualificacoes": 2}


def _select_dominio(nome: str) -> tuple[str, str]:
    n = _TAMANHO_CODIGO[nome]
    return f"SELECT cod(codigo, {n}) AS codigo, trim(descricao) AS descricao FROM src", "codigo"


def preparar_conexao(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MACROS)


def _caminho(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def arquivos_balde(pasta_tabela: Path, balde: int) -> str:
    """Padrão glob dos Parquets de um balde (lote) de uma tabela particionada."""
    return _caminho(pasta_tabela / f"balde={balde}") + "/*.parquet"


def escrever_particionado(con: duckdb.DuckDBPyConnection, select_sql: str, destino: Path, baldes: int) -> int:
    """Grava o SELECT em Parquet dividido em `baldes` lotes por hash do CNPJ básico. Devolve linhas gravadas.

    Mesmo CNPJ básico sempre cai no mesmo balde, então deduplicação e junções da gold
    podem ser feitas lote a lote, com pouca memória e pouco disco temporário.
    """
    shutil.rmtree(destino, ignore_errors=True)
    sql = (
        f"COPY (SELECT *, hash(cnpj_basico) % {baldes} AS balde FROM ({select_sql})) "
        f"TO '{_caminho(destino)}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (balde))"
    )
    gravadas = con.execute(sql).fetchone()[0]
    _garantir_baldes(con, destino, baldes)
    return gravadas


def _garantir_baldes(con: duckdb.DuckDBPyConnection, destino: Path, baldes: int) -> None:
    """Cria um Parquet vazio (com o esquema) nos baldes sem dados, para a gold ler todos igual."""
    existentes = list(destino.glob("balde=*/*.parquet"))
    if not existentes:
        raise LayoutInvalidoError(f"Tabela sem registros: {destino.name}")
    modelo = _caminho(existentes[0])
    for b in range(baldes):
        pasta = destino / f"balde={b}"
        if not any(pasta.glob("*.parquet")):
            pasta.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"COPY (SELECT * FROM read_parquet('{modelo}', hive_partitioning=false) LIMIT 0) "
                f"TO '{_caminho(pasta / 'vazio.parquet')}' (FORMAT parquet)"
            )


def deduplicar_baldes(con: duckdb.DuckDBPyConnection, destino: Path, baldes: int, chave: str) -> int:
    """Remove duplicidades balde a balde (só reescreve os baldes que têm duplicatas). Devolve linhas finais."""
    total_final = 0
    for b in range(baldes):
        origem = arquivos_balde(destino, b)
        leitura = f"read_parquet('{origem}', hive_partitioning=false)"
        colunas_chave = chave.split(" ORDER BY ")[0]
        linhas, distintas = con.execute(
            f"SELECT (SELECT count(*) FROM {leitura}), (SELECT count(*) FROM (SELECT DISTINCT {colunas_chave} FROM {leitura}))"
        ).fetchone()
        if linhas != distintas:
            pasta = destino / f"balde={b}"
            temporario = destino / f"_dedup_{b}.parquet"
            con.execute(
                f"COPY (SELECT * FROM {leitura} QUALIFY row_number() OVER (PARTITION BY {chave}) = 1) "
                f"TO '{_caminho(temporario)}' (FORMAT parquet, COMPRESSION zstd)"
            )
            for arquivo in pasta.glob("*.parquet"):
                arquivo.unlink()
            temporario.replace(pasta / "dados.parquet")
            log.info("Balde %d de %s: %d duplicatas removidas", b, destino.name, linhas - distintas)
        total_final += distintas
    return total_final


def gerar_silver(
    con: duckdb.DuckDBPyConnection,
    csvs: dict[str, list[Path]],
    destino: Path,
    baldes: int = N_BALDES,
) -> list[dict]:
    """Gera a silver em `destino`. Tabelas grandes ficam particionadas em baldes; domínios em um arquivo.

    Devolve as contagens de bronze (linhas lidas dos CSVs) e de silver (linhas após deduplicação).
    """
    preparar_conexao(con)
    destino.mkdir(parents=True, exist_ok=True)
    contagens: list[dict] = []
    for nome, tabela in TABELAS.items():
        select, chave = _SELECTS.get(nome) or _select_dominio(nome)
        origem = f"WITH src AS (SELECT * FROM {sql_ler_csv(csvs[nome], tabela)}) {select}"
        try:
            if nome in TABELAS_PARTICIONADAS:
                lidas = escrever_particionado(con, origem, destino / nome, baldes)
                escritas = deduplicar_baldes(con, destino / nome, baldes, chave)
            else:
                saida = destino / f"{nome}.parquet"
                lidas = con.execute(f"SELECT count(*) FROM ({origem})").fetchone()[0]
                escritas = con.execute(
                    f"COPY (SELECT * FROM ({origem}) QUALIFY row_number() OVER (PARTITION BY {chave}) = 1) "
                    f"TO '{_caminho(saida)}' (FORMAT parquet, COMPRESSION zstd)"
                ).fetchone()[0]
        except duckdb.Error as exc:
            raise LayoutInvalidoError(
                f"Falha ao ler a tabela '{nome}' ({', '.join(a.name for a in csvs[nome])}): {exc}"
            ) from exc
        contagens.append(registrar_contagem("bronze", nome, len(csvs[nome]), lidas))
        contagens.append(registrar_contagem("silver", nome, lidas, escritas))
    return contagens
