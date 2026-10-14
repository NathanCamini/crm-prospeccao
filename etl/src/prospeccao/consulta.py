"""Consulta à base gold: listagem paginada, ordenação e listas auxiliares (autocomplete)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from prospeccao.app_store import AppStore
from collections.abc import Iterator

from prospeccao.domain.filtros import (
    ContextoConsulta,
    Filtros,
    montar_where,
    normalizar_texto,
    regra_cnae_sql,
    so_digitos,
    usa_cnae,
)

COLUNAS_DISPONIVEIS: tuple[str, ...] = (
    "cnpj", "razao_social", "nome_fantasia", "situacao_cadastral", "data_situacao", "data_inicio_atividade",
    "cnae_principal", "cnae_principal_descricao", "natureza_juridica", "porte", "capital_social",
    "opcao_simples", "opcao_mei", "matriz_filial", "tipo_logradouro", "logradouro", "numero", "complemento",
    "bairro", "cep", "municipio", "uf", "ddd_receita_1", "telefone_receita_1", "ddd_receita_2",
    "telefone_receita_2", "email_receita", "mes_referencia_base",
)
COLUNAS_PADRAO: tuple[str, ...] = (
    "cnpj", "razao_social", "nome_fantasia", "data_inicio_atividade", "cnae_principal",
    "cnae_principal_descricao", "porte", "municipio", "uf", "bairro",
    "ddd_receita_1", "telefone_receita_1", "email_receita",
)
# Arquivo Parquet para uso fora do projeto (ex.: agente no Colab, ver notebooks/)
COLUNAS_PARQUET: tuple[str, ...] = (*COLUNAS_DISPONIVEIS, "cnaes_secundarios")
TAMANHO_PAGINA_PADRAO = 50
TAMANHO_PAGINA_MAXIMO = 500


@dataclass(frozen=True)
class Pagina:
    total: int
    pagina: int
    tamanho: int
    colunas: list[str]
    linhas: list[dict[str, Any]]

    @property
    def total_paginas(self) -> int:
        return max(1, -(-self.total // self.tamanho))


class Repositorio:
    """Acesso somente leitura ao banco gold.

    Recebe o caminho do banco ou uma conexão já aberta (a interface reaproveita uma
    conexão por processo e cria um cursor por sessão, que é o modo seguro entre threads).
    O banco do usuário (`AppStore`) é opcional e só é usado no filtro de contato complementar.
    """

    def __init__(
        self,
        gold_db: Path | None = None,
        app: AppStore | None = None,
        conexao: duckdb.DuckDBPyConnection | None = None,
        banco: str | None = None,
    ) -> None:
        if conexao is not None:
            self.con = conexao.cursor()
            if banco:  # base anexada com ATTACH (ver web/app.py)
                self.con.execute(f"USE {banco}")
        else:
            if gold_db is None or not gold_db.exists():
                raise FileNotFoundError(f"Base não encontrada em {gold_db}. Rode `prospeccao carga` primeiro.")
            self.con = duckdb.connect(str(gold_db), read_only=True)
        self.app = app

    def fechar(self) -> None:
        self.con.close()

    def __enter__(self) -> Repositorio:
        return self

    def __exit__(self, *_: object) -> None:
        self.fechar()

    def _where(self, filtros: Filtros, referencia: date | None,
               somente_cnpjs: list[str] | None = None) -> tuple[str, list[Any]]:
        precisa_app = filtros.tem_contato_complementar is not None or bool(filtros.status)
        if precisa_app and self.app is None:
            raise ValueError("Filtros de contato complementar e de status exigem o banco do usuário (AppStore)")
        ctx = ContextoConsulta(
            cnaes_alvo=self.resolver_cnaes(filtros) if usa_cnae(filtros) else None,
            cnpjs_com_contato=self.app.cnpjs_com_contato_complementar() if filtros.tem_contato_complementar is not None else None,
            cnpjs_por_status=self.app.cnpjs_por_status() if filtros.status else None,
            somente_cnpjs=somente_cnpjs,
        )
        return montar_where(filtros, referencia or date.today(), ctx)

    def resolver_cnaes(self, filtros: Filtros) -> list[str]:
        """Códigos exatos + códigos da tabela de domínio que batem com prefixo ou palavra-chave."""
        filtros.validar()
        codigos = {so_digitos(c) for c in filtros.cnae_codigos}
        regra = regra_cnae_sql(filtros)
        if regra:
            sql, params = regra
            codigos |= {r[0] for r in self.con.execute(f"SELECT codigo FROM cnae WHERE {sql}", params).fetchall()}
        return sorted(codigos)

    def contar(self, filtros: Filtros, referencia: date | None = None, somente_cnpjs: list[str] | None = None) -> int:
        where, params = self._where(filtros, referencia, somente_cnpjs)
        return self.con.execute(f"SELECT count(*) FROM empresa WHERE {where}", params).fetchone()[0]

    def buscar(
        self,
        filtros: Filtros,
        pagina: int = 1,
        tamanho: int = TAMANHO_PAGINA_PADRAO,
        ordenar_por: str = "data_inicio_atividade",
        decrescente: bool = True,
        colunas: tuple[str, ...] = COLUNAS_PADRAO,
        referencia: date | None = None,
        somente_cnpjs: list[str] | None = None,
    ) -> Pagina:
        _validar_colunas((*colunas, ordenar_por))
        if not 1 <= tamanho <= TAMANHO_PAGINA_MAXIMO:
            raise ValueError(f"Tamanho de página deve estar entre 1 e {TAMANHO_PAGINA_MAXIMO}")
        pagina = max(1, pagina)
        where, params = self._where(filtros, referencia, somente_cnpjs)
        direcao = "DESC" if decrescente else "ASC"
        ordem = f"chave {direcao} NULLS LAST, cnpj"
        # Fase 1: uma única varredura, só com as colunas de filtro e ordenação, devolve o total
        # e os rowids da página. Fase 2: busca as colunas completas apenas dessas linhas.
        # Evita descompactar todas as colunas de milhões de linhas candidatas.
        total, rowids = self.con.execute(
            f"""WITH f AS MATERIALIZED (
                    SELECT rowid AS rid, {ordenar_por} AS chave, cnpj FROM empresa WHERE {where}
                )
                SELECT (SELECT count(*) FROM f),
                       (SELECT coalesce(list(rid), []) FROM (SELECT rid FROM f ORDER BY {ordem} LIMIT ? OFFSET ?))""",
            [*params, tamanho, (pagina - 1) * tamanho],
        ).fetchone()
        cursor = self.con.execute(
            f"SELECT {', '.join(colunas)}, {ordenar_por} AS chave, cnpj AS _cnpj_ordem FROM empresa "
            f"WHERE rowid = ANY(?::BIGINT[]) ORDER BY chave {direcao} NULLS LAST, _cnpj_ordem",
            [rowids],
        )
        nomes = [d[0] for d in cursor.description][: len(colunas)]
        linhas = [dict(zip(nomes, r[: len(colunas)], strict=True)) for r in cursor.fetchall()]
        return Pagina(total, pagina, tamanho, nomes, linhas)

    def iterar(
        self,
        filtros: Filtros,
        colunas: tuple[str, ...],
        referencia: date | None = None,
        somente_cnpjs: list[str] | None = None,
        limite: int | None = None,
        lote: int = 10_000,
    ) -> Iterator[list[dict[str, Any]]]:
        """Resultado completo em lotes (para exportação), do mais recente para o mais antigo."""
        _validar_colunas(colunas)
        where, params = self._where(filtros, referencia, somente_cnpjs)
        sql = f"SELECT {', '.join(colunas)} FROM empresa WHERE {where} ORDER BY data_inicio_atividade DESC NULLS LAST, cnpj"
        if limite is not None:
            sql += f" LIMIT {int(limite)}"
        cur = self.con.execute(sql, params)
        nomes = [d[0] for d in cur.description]
        while linhas := cur.fetchmany(lote):
            yield [dict(zip(nomes, r, strict=True)) for r in linhas]

    def exportar_parquet(self, filtros: Filtros, destino: Path, referencia: date | None = None,
                         limite: int | None = None) -> int:
        """Grava o resultado num único Parquet e devolve o número de linhas.

        Sem `limite`, grava o resultado completo. Com `limite`, grava uma amostra diversificada
        (ver `_sql_amostra_diversa`), para mandar a uma IA sem gastar muitos tokens.
        Ordenado por UF, município e abertura: quem lê o arquivo pula blocos inteiros nas
        consultas por localidade. Grava num temporário e só renomeia no fim, para que uma
        falha no meio (ex.: falta de memória) não deixe um arquivo truncado com o nome final.
        """
        where, params = self._where(filtros, referencia)
        colunas = ", ".join(COLUNAS_PARQUET)
        selecao = f"SELECT {colunas} FROM empresa WHERE {where}"
        if limite is not None:
            if limite < 1:
                raise ValueError("O limite de linhas deve ser positivo")
            selecao = _sql_amostra_diversa(selecao, colunas, limite)
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_name(destino.name + ".tmp")
        caminho = tmp.as_posix().replace("'", "''")
        try:
            total = self.con.execute(
                f"COPY (SELECT {colunas} FROM ({selecao}) ORDER BY uf, municipio, data_inicio_atividade) "
                f"TO '{caminho}' (FORMAT parquet, COMPRESSION zstd)",
                params,
            ).fetchone()[0]
            os.replace(tmp, destino)
        finally:
            tmp.unlink(missing_ok=True)
        return total

    def cnpjs(self, filtros: Filtros, referencia: date | None = None) -> list[str]:
        """Todos os CNPJs do resultado (usado para comparar execuções de consultas salvas)."""
        where, params = self._where(filtros, referencia)
        return [r[0] for r in self.con.execute(f"SELECT cnpj FROM empresa WHERE {where} ORDER BY cnpj", params).fetchall()]

    def indicadores(self, filtros: Filtros, referencia: date | None = None,
                    somente_cnpjs: list[str] | None = None) -> dict[str, Any]:
        """Painel da seção 4.6 numa única varredura: total, top 10 CNAE, porte, mês de abertura, municípios."""
        where, params = self._where(filtros, referencia, somente_cnpjs)
        linha = self.con.execute(
            f"""WITH f AS MATERIALIZED (
                    SELECT cnae_principal, cnae_principal_descricao, porte, uf, municipio,
                           strftime(data_inicio_atividade, '%Y-%m') AS mes
                    FROM empresa WHERE {where}
                ),
                c AS (SELECT cnae_principal AS codigo, any_value(cnae_principal_descricao) AS descricao, count(*) AS total
                      FROM f GROUP BY 1 ORDER BY total DESC, codigo LIMIT 10),
                p AS (SELECT coalesce(porte, 'NÃO INFORMADO') AS porte, count(*) AS total FROM f GROUP BY 1),
                m AS (SELECT mes, count(*) AS total FROM f WHERE mes IS NOT NULL GROUP BY 1),
                mu AS (SELECT uf, municipio, count(*) AS total FROM f GROUP BY ALL ORDER BY total DESC, municipio LIMIT 20)
            SELECT (SELECT count(*) FROM f),
                   (SELECT count(DISTINCT municipio) FROM f),
                   (SELECT coalesce(list(c ORDER BY total DESC, codigo), []) FROM c),
                   (SELECT coalesce(list(p ORDER BY total DESC), []) FROM p),
                   (SELECT coalesce(list(m ORDER BY mes), []) FROM m),
                   (SELECT coalesce(list(mu ORDER BY total DESC, municipio), []) FROM mu)""",
            params,
        ).fetchone()
        total, n_municipios, cnaes, portes, meses, municipios = linha
        return {
            "total": total,
            "municipios_distintos": n_municipios,
            "top_cnae": cnaes,
            "por_porte": portes,
            "por_mes": meses,
            "por_municipio": municipios if n_municipios > 1 else [],
        }

    def empresas_por_cnpjs(self, cnpjs: list[str]) -> list[dict[str, Any]]:
        """Campos necessários à busca complementar para vários CNPJs, numa única leitura."""
        cur = self.con.execute(
            "SELECT cnpj, razao_social, nome_fantasia, municipio, uf, email_receita, ddd_receita_1, "
            "telefone_receita_1, ddd_receita_2, telefone_receita_2 FROM empresa WHERE list_contains(?::VARCHAR[], cnpj)",
            [cnpjs],
        )
        nomes = [d[0] for d in cur.description]
        por_cnpj = {r[0]: dict(zip(nomes, r, strict=True)) for r in cur.fetchall()}
        return [por_cnpj[c] for c in cnpjs if c in por_cnpj]

    def ficha(self, cnpj: str) -> dict[str, Any] | None:
        """Todos os campos do estabelecimento, sócios e demais estabelecimentos da mesma empresa."""
        cur = self.con.execute("SELECT * FROM empresa WHERE cnpj = ?", [cnpj])
        linha = cur.fetchone()
        if linha is None:
            return None
        dados = dict(zip([d[0] for d in cur.description], linha, strict=True))
        socios = self.con.execute(
            "SELECT nome_socio, qualificacao, data_entrada, tipo_socio FROM socio WHERE cnpj_basico = ? ORDER BY nome_socio",
            [dados["cnpj_basico"]],
        ).fetchall()
        dados["socios"] = [
            dict(zip(["nome_socio", "qualificacao", "data_entrada", "tipo_socio"], s, strict=True)) for s in socios
        ]
        outros = self.con.execute(
            "SELECT cnpj, matriz_filial, municipio, uf, situacao_cadastral FROM empresa "
            "WHERE cnpj_basico = ? AND cnpj <> ? ORDER BY cnpj",
            [dados["cnpj_basico"], cnpj],
        ).fetchall()
        dados["outros_estabelecimentos"] = [
            dict(zip(["cnpj", "matriz_filial", "municipio", "uf", "situacao_cadastral"], o, strict=True)) for o in outros
        ]
        return dados

    # Listas auxiliares para a interface
    def ufs(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT DISTINCT uf FROM municipio ORDER BY uf").fetchall()]

    def municipios(self, termo: str = "", uf: str | None = None, limite: int = 20) -> list[dict[str, Any]]:
        """Autocomplete de municípios (sem acento, contém o termo)."""
        sql = (
            "SELECT uf, codigo_municipio_receita AS codigo, municipio AS nome, tem_bairro FROM municipio "
            "WHERE contains(strip_accents(lower(municipio)), ?) AND (? IS NULL OR uf = ?) "
            "ORDER BY starts_with(strip_accents(lower(municipio)), ?) DESC, estabelecimentos DESC LIMIT ?"
        )
        t = normalizar_texto(termo)
        cur = self.con.execute(sql, [t, uf, uf, t, limite])
        return [dict(zip(["uf", "codigo", "nome", "tem_bairro"], r, strict=True)) for r in cur.fetchall()]

    def todos_municipios(self) -> list[dict[str, Any]]:
        """Todos os municípios presentes na base (para o seletor com busca da interface)."""
        cur = self.con.execute(
            "SELECT uf, codigo_municipio_receita, municipio, tem_bairro FROM municipio ORDER BY municipio, uf"
        )
        return [dict(zip(["uf", "codigo", "nome", "tem_bairro"], r, strict=True)) for r in cur.fetchall()]

    def bairros(self, codigo_municipio: str) -> list[str]:
        """Bairros existentes no município. Lista vazia = não exibir o filtro de bairro."""
        return [
            r[0]
            for r in self.con.execute(
                "SELECT DISTINCT trim(bairro) FROM empresa WHERE codigo_municipio_receita = ? "
                "AND bairro IS NOT NULL ORDER BY 1",
                [codigo_municipio.zfill(4)],
            ).fetchall()
        ]

    def cnaes(self, termo: str = "", limite: int = 30) -> list[dict[str, str]]:
        t = normalizar_texto(termo)
        cur = self.con.execute(
            "SELECT codigo, descricao FROM cnae WHERE starts_with(codigo, ?) "
            "OR contains(strip_accents(lower(descricao)), ?) ORDER BY codigo LIMIT ?",
            [termo.strip(), t, limite],
        )
        return [{"codigo": c, "descricao": d} for c, d in cur.fetchall()]

    def naturezas(self) -> list[dict[str, str]]:
        cur = self.con.execute("SELECT codigo, descricao FROM natureza_juridica ORDER BY descricao")
        return [{"codigo": c, "descricao": d} for c, d in cur.fetchall()]

    def ultima_carga(self) -> dict[str, Any] | None:
        linha = self.con.execute(
            "SELECT mes_referencia, data_ingestao FROM carga ORDER BY data_ingestao DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return {"mes_referencia": linha[0], "data_ingestao": linha[1]} if linha else None


def _sql_amostra_diversa(selecao: str, colunas: str, limite: int) -> str:
    """`limite` linhas de `selecao` espalhadas pelo maior número possível de municípios e CNAEs.

    Sorteia antes uma amostra aleatória (evita ordenar dezenas de milhões de linhas) e escolhe
    em rodadas: cada rodada pega uma empresa de cada município; dentro do município, prefere a
    de CNAE principal menos repetido. O hash do CNPJ faz o papel de sorteio estável.
    """
    sorteio = max(limite * 100, 100_000)
    return f"""
        WITH a AS (
            SELECT *, hash(cnpj) AS _sorteio
            FROM ({selecao}) USING SAMPLE reservoir({sorteio} ROWS) REPEATABLE (42)
        ),
        c AS (SELECT *, row_number() OVER (PARTITION BY cnae_principal ORDER BY _sorteio) AS _vez_cnae FROM a),
        m AS (
            SELECT *, row_number() OVER (PARTITION BY uf, municipio ORDER BY _vez_cnae, _sorteio) AS _vez_municipio
            FROM c
        )
        SELECT {colunas} FROM m ORDER BY _vez_municipio, _vez_cnae, _sorteio LIMIT {int(limite)}"""


def _validar_colunas(colunas: tuple[str, ...]) -> None:
    invalidas = [c for c in colunas if c not in COLUNAS_DISPONIVEIS]
    if invalidas:
        raise ValueError(f"Colunas não permitidas: {', '.join(invalidas)}")
