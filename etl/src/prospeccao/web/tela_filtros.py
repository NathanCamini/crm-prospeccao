"""Converte o que foi escolhido na tela em `Filtros` (função pura, testável sem Streamlit)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from prospeccao.domain.filtros import Filtros, FiltroInvalidoError, Localidade, Periodo, so_digitos

OPCOES_PERIODO: dict[str, int | None] = {
    "Últimos 30 dias": 30,
    "Últimos 60 dias": 60,
    "Últimos 90 dias": 90,
    "Últimos 180 dias": 180,
    "Últimos 365 dias": 365,
    "Datas personalizadas": None,
    "Qualquer data": -1,
}
PERIODO_PADRAO = "Últimos 90 dias"
TRES_ESTADOS = ("Indiferente", "Sim", "Não")


@dataclass
class EstadoTela:
    ufs: list[str] = field(default_factory=list)
    municipios: list[dict] = field(default_factory=list)  # {"uf", "codigo", "nome"}
    bairros: dict[str, list[str]] = field(default_factory=dict)  # codigo_municipio -> bairros
    periodo: str = PERIODO_PADRAO
    datas: tuple[date, date] | None = None
    cnae_selecionados: list[str] = field(default_factory=list)
    cnae_texto: str = ""
    cnae_palavra: str = ""
    incluir_secundarios: bool = False
    portes: list[str] = field(default_factory=list)
    naturezas: list[str] = field(default_factory=list)
    situacoes: list[str] = field(default_factory=lambda: ["ATIVA"])
    capital_min: float | None = None
    capital_max: float | None = None
    matriz_filial: str = "Indiferente"
    simples: str = "Indiferente"
    tem_telefone: str = "Indiferente"
    tem_email: str = "Indiferente"
    tem_contato_complementar: str = "Indiferente"
    palavra_chave: str = ""
    status: list[str] = field(default_factory=list)


def _tres_estados(valor: str) -> bool | None:
    return {"Sim": True, "Não": False}.get(valor)


def _localidades(estado: EstadoTela) -> tuple[Localidade, ...]:
    """Municípios escolhidos + UFs escolhidas que não têm município escolhido."""
    locs = [
        Localidade(m["uf"], m["codigo"], tuple(estado.bairros.get(m["codigo"], ())))
        for m in estado.municipios
    ]
    ufs_com_municipio = {m["uf"] for m in estado.municipios}
    locs += [Localidade(uf) for uf in estado.ufs if uf not in ufs_com_municipio]
    return tuple(locs)


def separar_cnaes(texto: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """'4399-1/03, 43, 10' -> (exatos=('4399103',), prefixos=('43', '10')). 7 dígitos = código exato."""
    exatos, prefixos = [], []
    for parte in re.split(r"[,;\s]+", texto.strip()):
        digitos = so_digitos(parte)
        if not digitos:
            continue
        (exatos if len(digitos) == 7 else prefixos).append(digitos)
    return tuple(exatos), tuple(prefixos)


def _periodo(estado: EstadoTela) -> Periodo | None:
    dias = OPCOES_PERIODO[estado.periodo]
    if dias == -1:
        return None
    if dias is None:
        if not estado.datas or len(estado.datas) != 2:
            raise FiltroInvalidoError("Escolha a data inicial e a final")
        return Periodo(None, estado.datas[0], estado.datas[1])
    return Periodo(dias)


def filtros_da_tela(estado: EstadoTela) -> Filtros:
    exatos, prefixos = separar_cnaes(estado.cnae_texto)
    filtros = Filtros(
        localidades=_localidades(estado),
        periodo=_periodo(estado),
        cnae_codigos=tuple(dict.fromkeys([*estado.cnae_selecionados, *exatos])),
        cnae_prefixos=prefixos,
        cnae_palavra=estado.cnae_palavra.strip() or None,
        incluir_cnaes_secundarios=estado.incluir_secundarios,
        portes=tuple(estado.portes),
        naturezas=tuple(estado.naturezas),
        situacoes=tuple(estado.situacoes),
        capital_min=estado.capital_min,
        capital_max=estado.capital_max,
        matriz_filial={"Matriz": "MATRIZ", "Filial": "FILIAL"}.get(estado.matriz_filial),
        simples=_tres_estados(estado.simples),
        tem_telefone_receita=_tres_estados(estado.tem_telefone),
        tem_email_receita=_tres_estados(estado.tem_email),
        tem_contato_complementar=_tres_estados(estado.tem_contato_complementar),
        palavra_chave=estado.palavra_chave.strip() or None,
        status=tuple(estado.status),
    )
    filtros.validar()
    return filtros
