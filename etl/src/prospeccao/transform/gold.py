"""Camada gold: banco DuckDB de consulta com uma linha por estabelecimento.

A junção Estabelecimentos + Empresas + Simples é feita balde a balde (lotes por
hash do CNPJ básico, ver silver.py). Cada lote cabe na memória, então a carga não
depende de dezenas de GB de disco temporário. Dentro de cada lote as linhas são
gravadas ordenadas por UF, município e data de abertura, o que permite ao DuckDB
pular blocos inteiros nas consultas por localidade.

O banco é gerado em um arquivo temporário e só substitui o anterior no fim
(troca atômica). O histórico de cargas é copiado do banco anterior. Dados do
usuário (status, contatos complementares, consultas salvas) ficam em outro
arquivo e nunca são tocados por esta etapa.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

import duckdb

from prospeccao.ingest.layout import VERSAO_LAYOUT
from prospeccao.monitor import registrar_contagem
from prospeccao.transform.silver import arquivos_balde

log = logging.getLogger("prospeccao.gold")

_SELECT_EMPRESA = """
SELECT
    e.cnpj, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv,
    emp.razao_social,
    e.nome_fantasia,
    e.situacao_cadastral AS situacao_cadastral_codigo,
    CASE e.situacao_cadastral
        WHEN '01' THEN 'NULA' WHEN '02' THEN 'ATIVA' WHEN '03' THEN 'SUSPENSA'
        WHEN '04' THEN 'INAPTA' WHEN '08' THEN 'BAIXADA' ELSE 'NÃO INFORMADA' END AS situacao_cadastral,
    e.data_situacao_cadastral AS data_situacao,
    mot.descricao AS motivo_situacao,
    e.data_inicio_atividade,
    e.cnae_principal,
    cn.descricao AS cnae_principal_descricao,
    e.cnaes_secundarios,
    emp.natureza_juridica AS natureza_juridica_codigo,
    nat.descricao AS natureza_juridica,
    emp.porte_empresa AS porte_codigo,
    CASE
        WHEN s.opcao_mei THEN 'MEI'
        WHEN emp.porte_empresa = '01' THEN 'ME'
        WHEN emp.porte_empresa = '03' THEN 'EPP'
        WHEN emp.porte_empresa = '05' THEN 'DEMAIS'
        ELSE 'NÃO INFORMADO' END AS porte,
    emp.capital_social,
    s.opcao_simples,
    s.opcao_mei,
    CASE e.identificador_matriz_filial WHEN '1' THEN 'MATRIZ' WHEN '2' THEN 'FILIAL' END AS matriz_filial,
    e.tipo_logradouro, e.logradouro, e.numero, e.complemento, e.bairro, e.cep,
    e.uf,
    e.codigo_municipio_receita,
    COALESCE(mun.descricao, e.nome_cidade_exterior) AS municipio,
    COALESCE(pa.descricao, CASE WHEN e.uf <> 'EX' THEN 'BRASIL' END) AS pais,
    -- Contato original da Receita: copiado sem nenhuma alteração.
    e.ddd_1 AS ddd_receita_1,
    e.telefone_1 AS telefone_receita_1,
    e.ddd_2 AS ddd_receita_2,
    e.telefone_2 AS telefone_receita_2,
    e.ddd_fax AS ddd_fax_receita,
    e.fax AS fax_receita,
    e.correio_eletronico AS email_receita,
    $mes::VARCHAR AS mes_referencia_base,
    $ingestao::TIMESTAMP AS data_ingestao,
    (e.data_inicio_atividade BETWEEN ($ref::DATE - to_days($dias::INTEGER))::DATE AND $ref::DATE) IS TRUE AS is_nova
FROM read_parquet($p_est, hive_partitioning = false) e
LEFT JOIN read_parquet($p_emp, hive_partitioning = false) emp USING (cnpj_basico)
LEFT JOIN read_parquet($p_sim, hive_partitioning = false) s USING (cnpj_basico)
LEFT JOIN dom_cnae cn ON cn.codigo = e.cnae_principal
LEFT JOIN dom_natureza nat ON nat.codigo = emp.natureza_juridica
LEFT JOIN dom_municipio mun ON mun.codigo = e.codigo_municipio_receita
LEFT JOIN dom_motivo mot ON mot.codigo = e.motivo_situacao_cadastral
LEFT JOIN dom_pais pa ON pa.codigo = e.pais
ORDER BY e.uf, e.codigo_municipio_receita, e.data_inicio_atividade
"""

_SELECT_SOCIO = """
SELECT so.cnpj_basico,
       so.nome_socio,
       q.descricao AS qualificacao,
       so.data_entrada_sociedade AS data_entrada,
       CASE so.identificador_socio WHEN '1' THEN 'PESSOA JURÍDICA' WHEN '2' THEN 'PESSOA FÍSICA'
            WHEN '3' THEN 'ESTRANGEIRO' END AS tipo_socio
FROM read_parquet($p_soc, hive_partitioning = false) so
LEFT JOIN dom_qualificacao q ON q.codigo = so.qualificacao_socio
ORDER BY so.cnpj_basico
"""

_DOMINIOS = {
    "dom_cnae": "cnaes", "dom_natureza": "naturezas", "dom_municipio": "municipios",
    "dom_motivo": "motivos", "dom_pais": "paises", "dom_qualificacao": "qualificacoes",
}

_SQL_FINAIS = [
    """CREATE TABLE municipio AS
       SELECT uf, codigo_municipio_receita, municipio, count(*) AS estabelecimentos,
              count(bairro) > 0 AS tem_bairro
       FROM empresa WHERE municipio IS NOT NULL
       GROUP BY ALL ORDER BY uf, municipio""",
    "CREATE TABLE cnae AS SELECT codigo, descricao FROM dom_cnae ORDER BY codigo",
    "CREATE TABLE natureza_juridica AS SELECT codigo, descricao FROM dom_natureza ORDER BY codigo",
    """CREATE TABLE carga (
           mes_referencia VARCHAR, data_ingestao TIMESTAMP, data_referencia_nova DATE,
           janela_nova_dias INTEGER, versao_layout VARCHAR, contagens JSON, metricas JSON
       )""",
]


def _carregar_dominios(con: duckdb.DuckDBPyConnection, silver: Path) -> None:
    """Tabelas de domínio (pequenas) vão para tabelas temporárias, lidas uma vez."""
    for tabela, arquivo in _DOMINIOS.items():
        con.execute(
            f"CREATE TEMP TABLE {tabela} AS SELECT * FROM read_parquet($p)",
            {"p": (silver / f"{arquivo}.parquet").as_posix()},
        )


def _numero_de_baldes(silver: Path) -> int:
    baldes = sorted(int(p.name.split("=")[1]) for p in (silver / "estabelecimentos").glob("balde=*"))
    if not baldes or baldes != list(range(len(baldes))):
        raise RuntimeError(f"Silver incompleta em {silver}: baldes de estabelecimentos {baldes}")
    return len(baldes)


def _contar(con: duckdb.DuckDBPyConnection, silver: Path, tabela: str) -> int:
    padrao = (silver / tabela).as_posix() + "/*/*.parquet"
    return con.execute(f"SELECT count(*) FROM read_parquet('{padrao}', hive_partitioning=false)").fetchone()[0]


def _copiar_historico(con: duckdb.DuckDBPyConnection, anterior: Path) -> None:
    if not anterior.exists():
        return
    con.execute(f"ATTACH '{anterior.as_posix()}' AS antigo (READ_ONLY)")
    try:
        existe = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE database_name='antigo' AND table_name='carga'"
        ).fetchone()[0]
        if existe:
            con.execute("INSERT INTO carga SELECT * FROM antigo.carga")
    finally:
        con.execute("DETACH antigo")


def gerar_gold(
    silver: Path,
    destino_db: Path,
    mes: str,
    data_referencia: date,
    janela_dias: int,
    memoria: str,
    threads: int,
    tmp_dir: Path,
    max_temp: str = "20GB",
) -> tuple[list[dict], Path]:
    """Gera o banco gold em arquivo temporário, lote a lote. Devolve (contagens, caminho_temporario)."""
    destino_db.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = destino_db.with_suffix(".novo.duckdb")
    tmp_db.unlink(missing_ok=True)
    ingestao = datetime.now()  # com microssegundos: duas cargas no mesmo segundo não empatam
    baldes = _numero_de_baldes(silver)
    fixos = {"mes": mes, "ingestao": ingestao, "ref": data_referencia, "dias": janela_dias}

    with duckdb.connect(str(tmp_db)) as con:
        configurar_duckdb(con, memoria, threads, tmp_dir, max_temp, preservar_ordem=True)
        _carregar_dominios(con, silver)
        for b in range(baldes):
            lote = {
                "p_est": arquivos_balde(silver / "estabelecimentos", b),
                "p_emp": arquivos_balde(silver / "empresas", b),
                "p_sim": arquivos_balde(silver / "simples", b),
            }
            prefixo = "CREATE TABLE empresa AS" if b == 0 else "INSERT INTO empresa"
            con.execute(f"{prefixo} {_SELECT_EMPRESA}", {**fixos, **lote})
            prefixo = "CREATE TABLE socio AS" if b == 0 else "INSERT INTO socio"
            con.execute(f"{prefixo} {_SELECT_SOCIO}", {"p_soc": arquivos_balde(silver / "socios", b)})
            if (b + 1) % 8 == 0 or b + 1 == baldes:
                log.info("gold: lote %d/%d concluído", b + 1, baldes)
        for sql in _SQL_FINAIS:
            con.execute(sql)

        entrada = _contar(con, silver, "estabelecimentos")
        saida = con.execute("SELECT count(*) FROM empresa").fetchone()[0]
        socios_in = _contar(con, silver, "socios")
        socios_out = con.execute("SELECT count(*) FROM socio").fetchone()[0]
        contagens = [
            registrar_contagem("gold", "empresa", entrada, saida),
            registrar_contagem("gold", "socio", socios_in, socios_out),
        ]
        if entrada != saida:
            raise RuntimeError(f"Gold perdeu ou duplicou estabelecimentos: entrada={entrada} saida={saida}")
        _copiar_historico(con, destino_db)
        con.execute(
            "INSERT INTO carga VALUES ($1, $2, $3, $4, $5, NULL, NULL)",
            [mes, ingestao, data_referencia, janela_dias, VERSAO_LAYOUT],
        )
    return contagens, tmp_db


def registrar_metricas(tmp_db: Path, mes: str, contagens: list[dict], metricas: list[dict]) -> None:
    with duckdb.connect(str(tmp_db)) as con:
        con.execute(
            """UPDATE carga SET contagens = $1, metricas = $2
               WHERE mes_referencia = $3 AND data_ingestao = (SELECT max(data_ingestao) FROM carga)""",
            [json.dumps(contagens, ensure_ascii=False), json.dumps(metricas, ensure_ascii=False), mes],
        )


def publicar_gold(tmp_db: Path, destino_db: Path, tentativas: int = 5) -> None:
    """Troca atômica do banco de consulta.

    Em Linux/macOS/Docker a interface pode continuar aberta: ela reabre a base nova sozinha.
    No Windows o arquivo aberto pela interface impede a troca; tentamos algumas vezes e,
    se não der, a base nova fica pronta em `*.novo.duckdb` com instrução clara.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            os.replace(tmp_db, destino_db)
            log.info("Banco gold publicado em %s", destino_db)
            return
        except PermissionError:
            if tentativa == tentativas:
                raise PermissionError(
                    f"Não foi possível substituir {destino_db}: o arquivo está em uso (provavelmente a interface "
                    f"aberta no Windows). Feche a interface e renomeie {tmp_db.name} para {destino_db.name}, "
                    "ou rode a carga com a interface fechada."
                ) from None
            time.sleep(2 * tentativa)


def configurar_duckdb(
    con: duckdb.DuckDBPyConnection,
    memoria: str,
    threads: int,
    tmp_dir: Path,
    max_temp: str = "20GB",
    preservar_ordem: bool = False,
) -> None:
    """Limites de memória, threads e disco temporário (falha em vez de lotar o disco)."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit = '{memoria}'")
    con.execute(f"SET threads = {int(threads)}")
    con.execute(f"SET temp_directory = '{tmp_dir.as_posix()}'")
    con.execute(f"SET max_temp_directory_size = '{max_temp}'")
    con.execute(f"SET preserve_insertion_order = {'true' if preservar_ordem else 'false'}")
