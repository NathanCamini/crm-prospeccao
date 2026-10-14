"""Descrição legível dos filtros aplicados (para relatórios e para a tela)."""

from __future__ import annotations

from datetime import date

from prospeccao.domain.filtros import Filtros
from prospeccao.domain.status import STATUS


def _sim_nao(valor: bool | None, sim: str, nao: str) -> str | None:
    return None if valor is None else (sim if valor else nao)


def _moeda(valor: float) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def descrever_filtros(
    filtros: Filtros,
    referencia: date,
    municipios: dict[str, str] | None = None,
    naturezas: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Lista de (critério, valor). `municipios`: código -> 'NOME/UF'; `naturezas`: código -> descrição."""
    municipios, naturezas = municipios or {}, naturezas or {}
    itens: list[tuple[str, str]] = []

    if filtros.localidades:
        partes = []
        for loc in filtros.localidades:
            if loc.codigo_municipio:
                nome = municipios.get(loc.codigo_municipio.zfill(4), f"município {loc.codigo_municipio}")
                partes.append(nome + (f" (bairros: {', '.join(loc.bairros)})" if loc.bairros else ""))
            else:
                partes.append(f"{loc.uf} (UF inteira)")
        itens.append(("Localidade", "; ".join(partes)))
    else:
        itens.append(("Localidade", "Brasil inteiro"))

    if filtros.periodo is None:
        itens.append(("Abertura", "qualquer data"))
    else:
        inicio, fim = filtros.periodo.resolver(referencia)
        if filtros.periodo.janela_dias:
            rotulo = f"últimos {filtros.periodo.janela_dias} dias ({inicio:%d/%m/%Y} a {fim:%d/%m/%Y})"
        else:
            rotulo = f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}" if inicio and fim else "—"
        itens.append(("Abertura (início de atividade)", rotulo))

    cnae = []
    if filtros.cnae_codigos:
        cnae.append("códigos " + ", ".join(filtros.cnae_codigos))
    if filtros.cnae_prefixos:
        cnae.append("prefixos " + ", ".join(filtros.cnae_prefixos))
    if filtros.cnae_palavra:
        cnae.append(f'descrição contém "{filtros.cnae_palavra}"')
    if cnae:
        itens.append(("CNAE", "; ".join(cnae) + (" (inclui secundários)" if filtros.incluir_cnaes_secundarios else "")))

    if filtros.portes:
        itens.append(("Porte", ", ".join(filtros.portes)))
    if filtros.naturezas:
        itens.append(("Natureza jurídica", ", ".join(naturezas.get(n, n) for n in filtros.naturezas)))
    itens.append(("Situação cadastral", ", ".join(filtros.situacoes) if filtros.situacoes else "todas"))
    if filtros.capital_min is not None or filtros.capital_max is not None:
        mi = _moeda(filtros.capital_min) if filtros.capital_min is not None else "sem mínimo"
        ma = _moeda(filtros.capital_max) if filtros.capital_max is not None else "sem máximo"
        itens.append(("Capital social", f"{mi} a {ma}"))
    if filtros.matriz_filial:
        itens.append(("Matriz/filial", filtros.matriz_filial.capitalize()))
    for rotulo, valor in (
        ("Simples Nacional", _sim_nao(filtros.simples, "optante", "não optante")),
        ("Telefone da Receita", _sim_nao(filtros.tem_telefone_receita, "com telefone", "sem telefone")),
        ("E-mail da Receita", _sim_nao(filtros.tem_email_receita, "com e-mail", "sem e-mail")),
        ("Contato complementar", _sim_nao(filtros.tem_contato_complementar, "com contato", "sem contato")),
    ):
        if valor:
            itens.append((rotulo, valor))
    if filtros.status:
        itens.append(("Status de prospecção", ", ".join(STATUS[s] for s in filtros.status)))
    if filtros.palavra_chave:
        itens.append(("Palavra no nome", f'"{filtros.palavra_chave}"'))
    return itens
