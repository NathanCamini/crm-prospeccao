"""Layout vigente dos Dados Abertos do CNPJ e validação de arquivos.

Os CSVs da Receita NÃO têm linha de cabeçalho: são separados por ';', com campos
entre aspas e codificação Latin-1. Por isso a validação confere o número de
colunas e o formato de campos-chave nas primeiras linhas de cada arquivo.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

VERSAO_LAYOUT = "receita-cnpj-2023+ (sem cabeçalho, ';', latin-1)"
ENCODING_RECEITA = "latin-1"  # declarado pela Receita; na prática vem Windows-1252 (ver conversao.py)
ENCODING_EXTRAIDO = "utf-8"  # os CSVs extraídos na bronze já são gravados em UTF-8
LINHAS_VALIDADAS = 1000


class LayoutInvalidoError(RuntimeError):
    """O arquivo não corresponde ao layout esperado."""


@dataclass(frozen=True)
class Tabela:
    nome: str
    prefixo_zip: str
    colunas: tuple[str, ...]
    multipla: bool = False
    formatos: dict[str, str] = field(default_factory=dict)


_DATA = r"[0-9]{8}|0*"
_BASICO = r"[0-9A-Z]{1,8}"

EMPRESAS = Tabela(
    "empresas",
    "Empresas",
    (
        "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
        "capital_social", "porte_empresa", "ente_federativo_responsavel",
    ),
    multipla=True,
    formatos={"cnpj_basico": _BASICO, "capital_social": r"[0-9]*(,[0-9]*)?"},
)

ESTABELECIMENTOS = Tabela(
    "estabelecimentos",
    "Estabelecimentos",
    (
        "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial", "nome_fantasia",
        "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
        "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal_principal",
        "cnae_fiscal_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
        "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
        "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial",
    ),
    multipla=True,
    formatos={
        "cnpj_basico": _BASICO,
        "cnpj_ordem": r"[0-9A-Z]{1,4}",
        "cnpj_dv": r"[0-9]{1,2}",
        "identificador_matriz_filial": r"[12]",
        "data_inicio_atividade": _DATA,
        "data_situacao_cadastral": _DATA,
        "uf": r"[A-Z]{2}|",
    },
)

SOCIOS = Tabela(
    "socios",
    "Socios",
    (
        "cnpj_basico", "identificador_socio", "nome_socio", "cnpj_cpf_socio", "qualificacao_socio",
        "data_entrada_sociedade", "pais", "representante_legal", "nome_representante",
        "qualificacao_representante_legal", "faixa_etaria",
    ),
    multipla=True,
    formatos={"cnpj_basico": _BASICO, "data_entrada_sociedade": _DATA},
)

SIMPLES = Tabela(
    "simples",
    "Simples",
    (
        "cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples",
        "opcao_mei", "data_opcao_mei", "data_exclusao_mei",
    ),
    formatos={"cnpj_basico": _BASICO, "opcao_simples": r"[SN]?", "opcao_mei": r"[SN]?"},
)

_DOMINIO = ("codigo", "descricao")
CNAES = Tabela("cnaes", "Cnaes", _DOMINIO, formatos={"codigo": r"[0-9]+"})
MOTIVOS = Tabela("motivos", "Motivos", _DOMINIO, formatos={"codigo": r"[0-9]+"})
MUNICIPIOS = Tabela("municipios", "Municipios", _DOMINIO, formatos={"codigo": r"[0-9]+"})
NATUREZAS = Tabela("naturezas", "Naturezas", _DOMINIO, formatos={"codigo": r"[0-9]+"})
PAISES = Tabela("paises", "Paises", _DOMINIO, formatos={"codigo": r"[0-9]+"})
QUALIFICACOES = Tabela("qualificacoes", "Qualificacoes", _DOMINIO, formatos={"codigo": r"[0-9]+"})

TABELAS: dict[str, Tabela] = {
    t.nome: t
    for t in (
        EMPRESAS, ESTABELECIMENTOS, SOCIOS, SIMPLES,
        CNAES, MOTIVOS, MUNICIPIOS, NATUREZAS, PAISES, QUALIFICACOES,
    )
}


def tabela_do_zip(nome_zip: str) -> Tabela | None:
    """Identifica a tabela pelo nome do zip (ex.: 'Estabelecimentos3.zip')."""
    base = Path(nome_zip).stem
    for tabela in TABELAS.values():
        padrao = rf"{tabela.prefixo_zip}[0-9]+" if tabela.multipla else tabela.prefixo_zip
        if re.fullmatch(padrao, base, flags=re.IGNORECASE):
            return tabela
    return None


def verificar_conjunto_completo(nomes_zip: list[str]) -> list[str]:
    """Devolve a lista de tabelas ausentes (vazia se o conjunto estiver completo)."""
    presentes = {t.nome for n in nomes_zip if (t := tabela_do_zip(n))}
    return sorted(set(TABELAS) - presentes)


def validar_linhas(texto: io.TextIOBase, tabela: Tabela, origem: str, limite: int = LINHAS_VALIDADAS) -> int:
    """Valida número de colunas e formatos das primeiras `limite` linhas. Devolve linhas lidas."""
    leitor = csv.reader(texto, delimiter=";", quotechar='"')
    esperado = len(tabela.colunas)
    lidas = 0
    for numero, linha in enumerate(leitor, start=1):
        if numero > limite:
            break
        lidas = numero
        if len(linha) != esperado:
            raise LayoutInvalidoError(
                f"Layout inválido em {origem}, linha {numero}: tabela '{tabela.nome}' "
                f"esperava {esperado} colunas e encontrou {len(linha)}. "
                "A Receita pode ter alterado o layout; confira o dicionário de dados vigente."
            )
        for coluna, regex in tabela.formatos.items():
            valor = linha[tabela.colunas.index(coluna)].strip()
            if not re.fullmatch(regex, valor):
                raise LayoutInvalidoError(
                    f"Layout inválido em {origem}, linha {numero}: coluna '{coluna}' "
                    f"com valor {valor!r} fora do formato esperado ({regex})."
                )
    if lidas == 0 and not tabela.multipla:
        raise LayoutInvalidoError(f"Arquivo vazio: {origem} (tabela '{tabela.nome}').")
    return lidas


def validar_arquivo_csv(caminho: Path, tabela: Tabela) -> int:
    with caminho.open("r", encoding=ENCODING_EXTRAIDO, newline="") as arq:
        return validar_linhas(arq, tabela, caminho.name)
