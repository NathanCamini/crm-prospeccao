"""Contrato v1 entre o módulo 3 (enriquecimento) e o módulo 4 (CRM).

O CRM só conhece estes formatos. Quando o módulo 3 real existir, ele precisa
devolver exatamente isto (o arquivo contrato/lead.v1.json é gerado daqui).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

VERSAO_CONTRATO = "v1"

TipoContato = Literal["email", "telefone", "whatsapp", "site", "formulario", "rede_social"]
Confianca = Literal["alta", "media", "baixa"]
Canal = Literal["email", "whatsapp", "formulario", "telefone"]


class ContatoReceita(BaseModel):
    """Contato original da Receita, sem nenhuma transformação (decisão D8)."""

    ddd_1: str | None = None
    telefone_1: str | None = None
    ddd_2: str | None = None
    telefone_2: str | None = None
    email: str | None = None


class Contato(BaseModel):
    """Contato complementar encontrado pelo módulo 3."""

    tipo: TipoContato
    valor: str
    fonte: str
    url: str | None = None
    confianca: Confianca
    observacao: str | None = None


class Classificacao(BaseModel):
    """Resultado do agente (módulo 2), repassado como veio."""

    perfil_id: str
    perfil_versao: int = 1
    score: int = Field(ge=0, le=100)
    e_alvo: bool
    justificativa: str = ""
    e_franquia: bool | None = None
    marca: str | None = None
    padrao: str | None = None
    produto_sugerido: str | None = None
    classificado_em: str | None = None


class Lead(BaseModel):
    cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    razao_social: str
    nome_fantasia: str | None = None
    matriz_filial: str | None = None
    situacao_cadastral: str | None = None
    data_inicio_atividade: date | None = None
    cnae_principal: str | None = None
    cnae_descricao: str | None = None
    porte: str | None = None
    capital_social: float | None = None
    logradouro: str | None = None
    numero: str | None = None
    bairro: str | None = None
    municipio: str | None = None
    uf: str | None = None
    cep: str | None = None
    contato_receita: ContatoReceita = ContatoReceita()
    contatos: list[Contato] = []
    classificacao: Classificacao
    canal_sugerido: Canal | None = None
    mes_referencia: str | None = None


class Filtro(BaseModel):
    perfil_id: str | None = None
    perfis: list[str] = []          # restringe a estes perfis (empresa cliente)
    uf: list[str] = []
    municipio: str | None = None
    score_min: int = Field(default=0, ge=0, le=100)
    texto: str | None = None
    so_alvos: bool = True
    cnpjs: list[str] | None = None
    excluir_cnpjs: list[str] = []
    ordenar: Literal["score", "abertura", "nome"] = "score"
    pagina: int = Field(default=1, ge=1)
    por_pagina: int = Field(default=50, ge=1, le=200)
    apenas_cnpjs: bool = False


class Pagina(BaseModel):
    contrato: str = VERSAO_CONTRATO
    fonte: Literal["simulado", "modulo2"]
    mes_referencia: str | None
    total: int
    pagina: int
    por_pagina: int
    itens: list[Lead]
    cnpjs: list[str] | None = None


class Perfil(BaseModel):
    id: str
    nome: str
    produto: str | None = None
    total_alvos: int


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)
    cnpj: str | None = None
    # Dados de um lead que o módulo 3 não conhece (cadastrado à mão no CRM).
    contexto: dict | None = None
    # Perfis da empresa cliente que pergunta: a pergunta geral só vê os leads deles.
    perfis: list[str] | None = None


class Resposta(BaseModel):
    resposta: str
    fonte: Literal["simulado", "modulo2"]
