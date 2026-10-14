"""Ponto único de geração de arquivos (usado pela interface e pela linha de comando)."""

from __future__ import annotations

from datetime import date
from typing import BinaryIO

from prospeccao.app_store import AppStore
from prospeccao.consulta import COLUNAS_PADRAO, Repositorio
from prospeccao.domain.filtros import Filtros
from prospeccao.reports.descricao import descrever_filtros
from prospeccao.reports.exportacao import (
    Metadados,
    acrescentar_extras,
    colunas_exportacao,
    escrever_csv,
    escrever_xlsx,
    linhas_exportacao,
)
from prospeccao.reports.pdf import escrever_pdf

FORMATOS = ("csv", "xlsx", "pdf")
COLUNAS_PDF = (
    "cnpj", "razao_social", "nome_fantasia", "data_inicio_atividade", "cnae_principal", "cnae_principal_descricao",
    "municipio", "uf", "ddd_receita_1", "telefone_receita_1", "email_receita",
)


def metadados(repo: Repositorio, filtros: Filtros, referencia: date, total: int, usuario: str | None) -> Metadados:
    municipios = {m["codigo"]: f"{m['nome']}/{m['uf']}" for m in repo.todos_municipios()}
    naturezas = {n["codigo"]: n["descricao"] for n in repo.naturezas()}
    carga = repo.ultima_carga() or {}
    return Metadados(
        total=total, filtros=descrever_filtros(filtros, referencia, municipios, naturezas),
        mes_referencia=carga.get("mes_referencia"), data_carga=carga.get("data_ingestao"), usuario=usuario,
    )


def gerar_arquivo(
    formato: str,
    destino: BinaryIO,
    repo: Repositorio,
    store: AppStore | None,
    filtros: Filtros,
    colunas: tuple[str, ...] = COLUNAS_PADRAO,
    referencia: date | None = None,
    usuario: str | None = None,
    somente_cnpjs: list[str] | None = None,
    separador: str = ";",
    top_n: int = 50,
    limite: int | None = None,
) -> int:
    """Grava o arquivo no destino e devolve o número de empresas incluídas."""
    if formato not in FORMATOS:
        raise ValueError(f"Formato inválido: {formato}. Use: {', '.join(FORMATOS)}")
    referencia = referencia or date.today()
    total = repo.contar(filtros, referencia, somente_cnpjs)
    meta = metadados(repo, filtros, referencia, total, usuario)
    if formato == "pdf":
        indicadores = repo.indicadores(filtros, referencia, somente_cnpjs)
        lote = next(repo.iterar(filtros, COLUNAS_PDF, referencia, somente_cnpjs, limite=top_n), [])
        escrever_pdf(destino, meta, indicadores, acrescentar_extras(lote, store))
        return len(lote)
    colunas = colunas_exportacao(colunas)
    linhas = linhas_exportacao(repo, store, filtros, colunas, referencia, somente_cnpjs, limite)
    if formato == "csv":
        return escrever_csv(linhas, destino, colunas, separador)
    indicadores = repo.indicadores(filtros, referencia, somente_cnpjs)
    return escrever_xlsx(linhas, destino, colunas, meta, indicadores)
