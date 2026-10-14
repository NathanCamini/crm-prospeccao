"""Benchmark com volume próximo ao real, sem baixar a base da Receita.

1. Gera Parquets silver SINTÉTICOS (mesmo esquema da silver real) direto no DuckDB.
2. Roda a geração da gold real (`gerar_gold`) sobre eles, medindo tempo e memória.
3. Executa consultas típicas pelo `Repositorio` e mede o tempo de cada uma.

Distribuição aproximada da base real: municípios com concentração (poucas cidades
grandes), cerca de 30% das empresas abertas nos últimos 5 anos, ~55% ativas.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from prospeccao.config import Config
from prospeccao.consulta import Repositorio
from prospeccao.domain.filtros import Filtros, Localidade, Periodo
from prospeccao.monitor import medir
from prospeccao.transform.gold import configurar_duckdb, gerar_gold, publicar_gold, registrar_metricas
from prospeccao.transform.silver import N_BALDES, escrever_particionado

log = logging.getLogger("prospeccao.benchmark")

_UFS = "['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']"


def _sql_silver(destino: Path, n: int, ref: date) -> tuple[list[str], dict[str, str]]:
    d = destino.as_posix()
    ref_sql = f"DATE '{ref.isoformat()}'"
    dominios = [
        "SELECT setseed(0.42)",
        f"""COPY (SELECT lpad((1000 + m)::VARCHAR, 4, '0') AS codigo, 'MUNICIPIO ' || m AS descricao
                  FROM range(5570) t(m)) TO '{d}/municipios.parquet'""",
        f"""COPY (SELECT lpad((1000000 + k * 7000)::VARCHAR, 7, '0') AS codigo,
                         ['Comércio varejista de ', 'Fabricação de ', 'Serviços de ', 'Construção de '][k % 4 + 1]
                         || 'atividade ' || k AS descricao
                  FROM range(1300) t(k)) TO '{d}/cnaes.parquet'""",
        f"""COPY (SELECT * FROM (VALUES ('2062','Sociedade Empresária Limitada'),('2135','Empresário (Individual)'),
                  ('2054','Sociedade Anônima Fechada'),('2305','Empresa Individual de Responsabilidade Limitada'))
                  t(codigo, descricao)) TO '{d}/naturezas.parquet'""",
        f"""COPY (SELECT * FROM (VALUES ('00','SEM MOTIVO'),('01','EXTINCAO')) t(codigo, descricao))
                  TO '{d}/motivos.parquet'""",
        f"""COPY (SELECT * FROM (VALUES ('105','BRASIL')) t(codigo, descricao)) TO '{d}/paises.parquet'""",
        f"""COPY (SELECT * FROM (VALUES ('49','Sócio-Administrador'),('22','Sócio')) t(codigo, descricao))
                  TO '{d}/qualificacoes.parquet'""",
    ]
    grandes = {
        "estabelecimentos": f"""
            SELECT lpad(i::VARCHAR, 8, '0') || '000100' AS cnpj, lpad(i::VARCHAR, 8, '0') AS cnpj_basico,
                   '0001' AS cnpj_ordem, '00' AS cnpj_dv, '1' AS identificador_matriz_filial,
                   CASE WHEN random() < 0.6 THEN 'FANTASIA ' || i END AS nome_fantasia,
                   CASE WHEN r < 0.55 THEN '02' WHEN r < 0.95 THEN '08' ELSE '04' END AS situacao_cadastral,
                   inicio AS data_situacao_cadastral, '00' AS motivo_situacao_cadastral,
                   NULL::VARCHAR AS nome_cidade_exterior, NULL::VARCHAR AS pais,
                   inicio AS data_inicio_atividade,
                   lpad((1000000 + (floor(pow(random(), 2) * 1300))::INT * 7000)::VARCHAR, 7, '0') AS cnae_principal,
                   CASE WHEN random() < 0.4 THEN [lpad((1000000 + (floor(random() * 1300))::INT * 7000)::VARCHAR, 7, '0')]
                        ELSE []::VARCHAR[] END AS cnaes_secundarios,
                   'RUA' AS tipo_logradouro, 'LOGRADOURO ' || (i % 997) AS logradouro, (i % 3000)::VARCHAR AS numero,
                   NULL::VARCHAR AS complemento,
                   CASE WHEN random() < 0.8 THEN 'BAIRRO ' || (floor(random() * 60))::INT END AS bairro,
                   lpad((i % 99999999)::VARCHAR, 8, '0') AS cep,
                   {_UFS}[m % 27 + 1] AS uf, lpad((1000 + m)::VARCHAR, 4, '0') AS codigo_municipio_receita,
                   '11' AS ddd_1, '3' || lpad((i % 9999999)::VARCHAR, 7, '0') AS telefone_1,
                   NULL::VARCHAR AS ddd_2, NULL::VARCHAR AS telefone_2, NULL::VARCHAR AS ddd_fax, NULL::VARCHAR AS fax,
                   CASE WHEN random() < 0.6 THEN 'CONTATO' || i || '@EXEMPLO.COM.BR' END AS correio_eletronico,
                   NULL::VARCHAR AS situacao_especial, NULL::DATE AS data_situacao_especial
            FROM (
                SELECT i, random() AS r, (floor(pow(random(), 3) * 5570))::INT AS m,
                       CASE WHEN random() < 0.3 THEN {ref_sql} - (floor(random() * 1825))::INT
                            ELSE DATE '1970-01-01' + (floor(random() * 18000))::INT END AS inicio
                FROM range(1, {n + 1}) t(i)
            )""",
        "empresas": f"""SELECT lpad(i::VARCHAR, 8, '0') AS cnpj_basico, 'EMPRESA FICTICIA ' || i || ' LTDA' AS razao_social,
                         ['2062','2135','2054','2305'][i % 4 + 1] AS natureza_juridica, '49' AS qualificacao_responsavel,
                         ((i % 1000) * 1000)::DECIMAL(18,2) AS capital_social,
                         ['01','01','01','03','05','00'][i % 6 + 1] AS porte_empresa, NULL::VARCHAR AS ente_federativo_responsavel
                  FROM range(1, {n + 1}) t(i)""",
        "simples": f"""SELECT lpad(i::VARCHAR, 8, '0') AS cnpj_basico, i % 3 <> 0 AS opcao_simples,
                         NULL::DATE AS data_opcao_simples, NULL::DATE AS data_exclusao_simples,
                         i % 5 = 0 AS opcao_mei, NULL::DATE AS data_opcao_mei, NULL::DATE AS data_exclusao_mei
                  FROM range(1, {n + 1}, 2) t(i)""",
        "socios": f"""SELECT lpad(i::VARCHAR, 8, '0') AS cnpj_basico, '2' AS identificador_socio,
                         'SOCIO FICTICIO ' || i AS nome_socio, '49' AS qualificacao_socio, NULL::DATE AS data_entrada_sociedade
                  FROM range(1, {n + 1}, 3) t(i)""",
    }
    return dominios, grandes


def gerar_silver_sintetica(cfg: Config, destino: Path, n: int, ref: date) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        configurar_duckdb(con, cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir, cfg.duckdb_max_temp)
        dominios, grandes = _sql_silver(destino, n, ref)
        for sql in dominios:
            con.execute(sql)
        for tabela, select in grandes.items():
            escrever_particionado(con, select, destino / tabela, N_BALDES)


def _cronometrar(func: Callable[[], Any], repeticoes: int = 3) -> tuple[float, float]:
    """Devolve (primeira execução, mediana das seguintes) em segundos."""
    tempos = []
    for _ in range(repeticoes + 1):
        inicio = time.perf_counter()
        func()
        tempos.append(time.perf_counter() - inicio)
    return tempos[0], statistics.median(tempos[1:])


def consultas_tipicas(repo: Repositorio, ref: date) -> dict[str, Callable[[], Any]]:
    grande = Localidade(codigo_municipio="1000")  # município mais populoso da distribuição
    medio = Localidade(codigo_municipio="1500")
    return {
        "municipio grande, 90 dias, ativas (contagem + página 1)":
            lambda: repo.buscar(Filtros(localidades=(grande,)), referencia=ref),
        "municipio médio, 90 dias, ativas":
            lambda: repo.buscar(Filtros(localidades=(medio,)), referencia=ref),
        "duas cidades + CNAE prefixo + porte ME":
            lambda: repo.buscar(Filtros(localidades=(grande, medio), cnae_prefixos=("10",), portes=("ME",)), referencia=ref),
        "UF inteira (SP), 90 dias":
            lambda: repo.buscar(Filtros(localidades=(Localidade("SP"),)), referencia=ref),
        "Brasil inteiro, 30 dias":
            lambda: repo.buscar(Filtros(periodo=Periodo(30)), referencia=ref),
        "Brasil, 365 dias, palavra-chave no nome":
            lambda: repo.buscar(Filtros(periodo=Periodo(365), palavra_chave="fantasia 12345"), referencia=ref),
        "Brasil, 90 dias, CNAE por palavra + secundários":
            lambda: repo.buscar(Filtros(cnae_palavra="fabricação", incluir_cnaes_secundarios=True), referencia=ref),
        "município grande, página 50 ordenada por razão social":
            lambda: repo.buscar(Filtros(localidades=(grande,), periodo=Periodo(365)), pagina=50,
                                ordenar_por="razao_social", decrescente=False, referencia=ref),
        "município + bairro":
            lambda: repo.buscar(Filtros(localidades=(Localidade(codigo_municipio="1000", bairros=("bairro 7",)),)), referencia=ref),
        "autocomplete de município":
            lambda: repo.municipios("municipio 15"),
        "lista de bairros do município grande":
            lambda: repo.bairros("1000"),
    }


def executar_benchmark(cfg: Config, linhas: int, reaproveitar: bool = False) -> dict[str, Any]:
    ref = date.today()
    base = cfg.data_dir / "benchmark"
    silver = base / "silver"
    gold_db = base / "empresas.duckdb"
    metricas: list[dict] = []

    if not (reaproveitar and gold_db.exists()):
        with medir("silver_sintetica") as m:
            gerar_silver_sintetica(cfg, silver, linhas, ref)
        metricas.append(m)
        with medir("gold") as m:
            contagens, tmp = gerar_gold(silver, gold_db, "SINTETICO", ref, cfg.janela_nova_dias,
                                        cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir,
                                        cfg.duckdb_max_temp)
        metricas.append(m)
        registrar_metricas(tmp, "SINTETICO", contagens, metricas)
        publicar_gold(tmp, gold_db)

    resultados: list[dict[str, Any]] = []
    with Repositorio(gold_db) as repo:
        total = repo.contar(Filtros(periodo=None, situacoes=()))
        for nome, func in consultas_tipicas(repo, ref).items():
            primeira, mediana = _cronometrar(func)
            resultados.append({"consulta": nome, "primeira_s": round(primeira, 3), "mediana_s": round(mediana, 3)})
            log.info("%-60s primeira=%.3fs mediana=%.3fs", nome, primeira, mediana)
    return {"linhas": total, "metricas_carga": metricas, "consultas": resultados}
