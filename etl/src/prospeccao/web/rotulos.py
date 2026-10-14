"""Rótulos e formatação de exibição. Nunca altera o dado; só a forma de mostrar.

Exceção deliberada: telefone e e-mail da Receita são mostrados exatamente como vieram.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from prospeccao.domain.cnpj import formatar_cnpj

ROTULOS: dict[str, str] = {
    "cnpj": "CNPJ",
    "razao_social": "Razão social",
    "nome_fantasia": "Nome fantasia",
    "situacao_cadastral": "Situação",
    "data_situacao": "Data da situação",
    "motivo_situacao": "Motivo da situação",
    "data_inicio_atividade": "Início da atividade",
    "cnae_principal": "CNAE",
    "cnae_principal_descricao": "Atividade principal",
    "cnaes_secundarios": "CNAEs secundários",
    "natureza_juridica": "Natureza jurídica",
    "porte": "Porte",
    "capital_social": "Capital social",
    "opcao_simples": "Simples",
    "opcao_mei": "MEI",
    "matriz_filial": "Matriz/filial",
    "tipo_logradouro": "Tipo logradouro",
    "logradouro": "Logradouro",
    "numero": "Número",
    "complemento": "Complemento",
    "bairro": "Bairro",
    "cep": "CEP",
    "municipio": "Município",
    "uf": "UF",
    "pais": "País",
    "ddd_receita_1": "DDD 1 (Receita)",
    "telefone_receita_1": "Telefone 1 (Receita)",
    "ddd_receita_2": "DDD 2 (Receita)",
    "telefone_receita_2": "Telefone 2 (Receita)",
    "ddd_fax_receita": "DDD fax (Receita)",
    "fax_receita": "Fax (Receita)",
    "email_receita": "E-mail (Receita)",
    "mes_referencia_base": "Mês da base",
}

CAMPOS_CONTATO_RECEITA = (
    "ddd_receita_1", "telefone_receita_1", "ddd_receita_2", "telefone_receita_2",
    "ddd_fax_receita", "fax_receita", "email_receita",
)


def rotulo(coluna: str) -> str:
    return ROTULOS.get(coluna, coluna)


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "—"


def formatar_moeda(valor: Decimal | float | None) -> str:
    if valor is None:
        return "—"
    texto = f"{float(valor):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_mes_referencia(mes: str | None) -> str:
    """'2026-09' -> '09/2026'."""
    if not mes or "-" not in mes:
        return mes or "—"
    ano, m = mes.split("-", 1)
    return f"{m}/{ano}"


def formatar_valor(coluna: str, valor: Any) -> str:
    """Texto para exibição na ficha. Contatos da Receita: sem nenhuma alteração."""
    if coluna in CAMPOS_CONTATO_RECEITA:
        return "" if valor is None else str(valor)
    if valor is None or valor == "":
        return "—"
    if coluna == "cnpj":
        return formatar_cnpj(valor)
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, date):
        return formatar_data(valor)
    if coluna == "capital_social":
        return formatar_moeda(valor)
    if coluna == "cnaes_secundarios":
        return ", ".join(valor) if valor else "—"
    if coluna == "cep" and isinstance(valor, str) and len(valor) == 8:
        return f"{valor[:5]}-{valor[5:]}"
    return str(valor)
