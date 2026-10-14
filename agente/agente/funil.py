"""Etapa 1 do funil: o SQL elimina o grosso no DuckDB, sem custo de LLM.

Um mês tem centenas de milhares de CNPJs novos, a maioria MEI; o filtro do perfil (CNAE, situação,
porte, capital, UF) reduz para centenas ou poucos milhares, os únicos que seguem para o modelo.
"""

from __future__ import annotations

from typing import Any

import duckdb

from agente.contrato import ArquivoDoMes
from agente.perfil import FiltroSQL, Perfil
from agente.texto import normalizar_texto

# Colunas lidas para o prompt e os sinais (as opcionais entram só se o arquivo as tiver).
COLUNAS_OBRIGATORIAS = (
    "cnpj", "cnpj_basico", "razao_social", "nome_fantasia", "matriz_filial", "situacao_cadastral",
    "data_inicio_atividade", "cnae_principal", "cnae_principal_descricao", "cnaes_secundarios",
    "natureza_juridica", "porte", "capital_social", "opcao_simples", "opcao_mei", "bairro", "uf", "municipio",
    "email_receita", "mes_referencia_base",
)
COLUNAS_OPCIONAIS = ("contato_compartilhado", "filial_de_empresa_existente", "pessoa_fisica_identificavel")
# Colunas de `dados_basicos`: o que uma pessoa precisa ver na planilha e o que o CRM recebe da empresa.
COLUNAS_CADASTRO = (
    "cnpj", "nome_fantasia", "razao_social", "matriz_filial", "situacao_cadastral", "data_inicio_atividade",
    "cnae_principal", "cnae_principal_descricao", "porte", "capital_social", "tipo_logradouro", "logradouro", "numero",
    "bairro", "cep", "municipio", "uf", "ddd_receita_1", "telefone_receita_1", "ddd_receita_2", "telefone_receita_2",
    "email_receita",
)


class FunilGrandeError(RuntimeError):
    """O filtro do perfil deixou passar mais empresas do que o limite de custo permite."""


def montar_filtro(f: FiltroSQL) -> tuple[str, list[Any]]:
    """WHERE parametrizado (sem a palavra WHERE) e seus parâmetros."""
    condicoes: list[str] = []
    params: list[Any] = []

    atividade: list[str] = []
    for prefixo in f.cnae_prefixos:
        atividade.append("starts_with(cnae_principal, ?)")
        params.append(prefixo)
        if f.incluir_cnaes_secundarios:
            atividade.append("list_bool_or(list_transform(coalesce(cnaes_secundarios, []), lambda c: starts_with(c, ?)))")
            params.append(prefixo)
    if f.cnaes:
        atividade.append(f"cnae_principal IN ({', '.join('?' * len(f.cnaes))})")
        params += f.cnaes
        if f.incluir_cnaes_secundarios:
            atividade.append("list_has_any(coalesce(cnaes_secundarios, []), ?::VARCHAR[])")
            params.append(list(f.cnaes))
    condicoes.append("(" + " OR ".join(atividade) + ")")

    if f.situacao:
        condicoes.append(f"situacao_cadastral IN ({', '.join('?' * len(f.situacao))})")
        params += f.situacao
    if f.capital_social_min is not None:
        condicoes.append("capital_social >= ?")
        params.append(f.capital_social_min)
    if f.capital_social_max is not None:
        condicoes.append("capital_social <= ?")
        params.append(f.capital_social_max)
    if f.excluir_mei:
        condicoes.append("opcao_mei IS NOT TRUE AND coalesce(porte, '') <> 'MEI'")
    if f.portes:
        condicoes.append(f"porte IN ({', '.join('?' * len(f.portes))})")
        params += f.portes
    if f.ufs:
        condicoes.append(f"uf IN ({', '.join('?' * len(f.ufs))})")
        params += f.ufs
    if f.municipios:
        condicoes.append(f"strip_accents(upper(municipio)) IN ({', '.join('?' * len(f.municipios))})")
        params += [normalizar_texto(m).upper() for m in f.municipios]
    if f.matriz_filial:
        condicoes.append("matriz_filial = ?")
        params.append(f.matriz_filial)
    return " AND ".join(condicoes), params


def contar(arquivo: ArquivoDoMes, perfil: Perfil) -> int:
    where, params = montar_filtro(perfil.filtro_sql)
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT count(*) FROM read_parquet(?) WHERE {where}",
                           [arquivo.parquet.as_posix(), *params]).fetchone()[0]
    finally:
        con.close()


def candidatos(arquivo: ArquivoDoMes, perfil: Perfil, limite: int | None = None) -> list[dict[str, Any]]:
    """Empresas que passaram no filtro do perfil, em ordem de CNPJ.

    `limite` (ou o `limite_candidatos` do perfil) é uma trava de custo: se o filtro deixar passar
    mais que isso, a execução para com erro em vez de mandar milhares de empresas ao LLM.
    """
    teto = limite or perfil.limite_candidatos
    total = contar(arquivo, perfil)
    if total > teto:
        raise FunilGrandeError(
            f"O filtro do perfil {perfil.id} deixou passar {total} empresas em {arquivo.mes}; o limite é {teto}. "
            "Aperte o filtro_sql ou aumente limite_candidatos (custo de LLM proporcional).")
    colunas = [*COLUNAS_OBRIGATORIAS, *(c for c in COLUNAS_OPCIONAIS if arquivo.tem(c))]
    where, params = montar_filtro(perfil.filtro_sql)
    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT {', '.join(colunas)} FROM read_parquet(?) WHERE {where} ORDER BY cnpj",
                          [arquivo.parquet.as_posix(), *params])
        nomes = [d[0] for d in cur.description]
        return [dict(zip(nomes, linha, strict=True)) for linha in cur.fetchall()]
    finally:
        con.close()


def dados_basicos(arquivo: ArquivoDoMes, cnpjs: list[str]) -> dict[str, dict[str, Any]]:
    """Dados cadastrais e contato da Receita de alguns CNPJs do mês (planilhas humanas e CSV do CRM)."""
    if not cnpjs:
        return {}
    con = duckdb.connect()
    try:
        cur = con.execute(
            f"SELECT {', '.join(COLUNAS_CADASTRO)} FROM read_parquet(?) WHERE list_contains(?::VARCHAR[], cnpj)",
            [arquivo.parquet.as_posix(), cnpjs])
        nomes = [d[0] for d in cur.description]
        return {linha[0]: dict(zip(nomes, linha, strict=True)) for linha in cur.fetchall()}
    finally:
        con.close()


def exemplos(arquivo: ArquivoDoMes, perfil: Perfil, n: int = 8) -> list[dict[str, Any]]:
    """Algumas empresas do funil, para a prévia de um perfil novo (só dados cadastrais públicos)."""
    where, params = montar_filtro(perfil.filtro_sql)
    con = duckdb.connect()
    try:
        cur = con.execute(
            f"SELECT coalesce(nome_fantasia, razao_social) AS nome, municipio, uf, cnae_principal_descricao AS atividade, "
            f"data_inicio_atividade, (opcao_mei IS TRUE OR porte = 'MEI') AS mei FROM read_parquet(?) WHERE {where} "
            f"ORDER BY hash(cnpj) LIMIT ?", [arquivo.parquet.as_posix(), *params, n])
        nomes = [d[0] for d in cur.description]
        linhas = [dict(zip(nomes, linha, strict=True)) for linha in cur.fetchall()]
    finally:
        con.close()
    for linha in linhas:  # nome de MEI é o nome do titular: não vai para a prévia
        if linha.pop("mei"):
            linha["nome"] = "(MEI)"
        if linha["data_inicio_atividade"] is not None:
            linha["data_inicio_atividade"] = linha["data_inicio_atividade"].isoformat()
    return linhas


def descricoes_cnae(arquivo: ArquivoDoMes) -> dict[str, str]:
    """Código → descrição, tirado do próprio arquivo (para descrever os CNAEs secundários ao modelo)."""
    con = duckdb.connect()
    try:
        return dict(con.execute(
            "SELECT cnae_principal, any_value(cnae_principal_descricao) FROM read_parquet(?) "
            "WHERE cnae_principal IS NOT NULL GROUP BY 1", [arquivo.parquet.as_posix()]).fetchall())
    finally:
        con.close()
