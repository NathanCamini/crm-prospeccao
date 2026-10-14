"""Perfis de cliente ideal (ICP): um YAML por produto, validado antes de qualquer gasto.

O perfil é configuração versionada, não prompt solto: o filtro SQL decide o que é barato
decidir, `criterios_llm` diz ao modelo o que só ele decide, e `versao` entra na chave da
classificação (subiu a versão, a empresa é reclassificada).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CANAIS = ("email", "ligacao", "whatsapp_assistido", "whatsapp_api", "formulario", "correio")
SITUACOES = ("ATIVA", "SUSPENSA", "INAPTA", "BAIXADA", "NULA")
PORTES = ("MEI", "ME", "EPP", "DEMAIS", "NÃO INFORMADO")
UFS = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE",
       "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO")


class PerfilInvalidoError(ValueError):
    """Perfil ausente ou com campo inválido."""


class _Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FiltroSQL(_Estrito):
    """Etapa barata, roda no DuckDB. Critérios diferentes combinam com E; valores de uma lista, com OU."""

    cnae_prefixos: list[str] = []
    cnaes: list[str] = []
    incluir_cnaes_secundarios: bool = False
    situacao: list[Literal[SITUACOES]] = ["ATIVA"]  # type: ignore[valid-type]
    capital_social_min: float | None = Field(default=None, ge=0)
    capital_social_max: float | None = Field(default=None, ge=0)
    excluir_mei: bool = False
    portes: list[Literal[PORTES]] = []  # type: ignore[valid-type]
    ufs: list[Literal[UFS]] = []  # type: ignore[valid-type]
    municipios: list[str] = []      # nome do município, comparado sem acento e sem diferenciar maiúsculas
    matriz_filial: Literal["MATRIZ", "FILIAL"] | None = None

    @field_validator("cnae_prefixos", "cnaes")
    @classmethod
    def _so_digitos(cls, valores: list[str]) -> list[str]:
        limpos = [re.sub(r"\D", "", str(v)) for v in valores]
        for original, limpo in zip(valores, limpos):
            if not 1 <= len(limpo) <= 7:
                raise ValueError(f"código CNAE inválido: {original!r}")
        return limpos

    @model_validator(mode="after")
    def _coerente(self) -> FiltroSQL:
        if not (self.cnae_prefixos or self.cnaes):
            raise ValueError("informe cnae_prefixos ou cnaes: sem filtro de atividade, o mês inteiro iria para o LLM")
        if any(len(c) != 7 for c in self.cnaes):
            raise ValueError("cnaes exige códigos completos de 7 dígitos (use cnae_prefixos para prefixos)")
        if self.capital_social_min is not None and self.capital_social_max is not None \
                and self.capital_social_min > self.capital_social_max:
            raise ValueError("capital_social_min maior que capital_social_max")
        return self


class Revisao(_Estrito):
    """Quando a triagem (modelo pequeno) é confirmada pelo modelo maior."""

    score_min: int = Field(default=60, ge=0, le=100)  # score alto: confirmar antes de virar alvo
    confianca_baixa: bool = True                      # dúvida declarada pelo próprio modelo


class Perfil(_Estrito):
    id: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    versao: int = Field(ge=1)
    produto: str = Field(min_length=3)
    nome: str | None = None          # como aparece no CRM (padrão: o produto)
    dono: str | None = None          # empresa cliente que descreveu o produto (perfil criado pelo CRM)
    filtro_sql: FiltroSQL
    criterios_llm: list[str] = Field(min_length=1)
    saida_score: Literal["0-100"] = "0-100"
    canal_preferido: list[Literal[CANAIS]] = []  # type: ignore[valid-type]  # usado pelo CRM, não pelo agente
    produtos_sugeriveis: list[str] = []
    padroes: list[str] = ["economico", "medio", "alto", "indefinido"]
    limiar_alvo: int = Field(default=60, ge=0, le=100)
    revisao: Revisao = Revisao()
    limite_candidatos: int = Field(default=5000, ge=1)  # trava de custo: funil maior que isso para a execução

    @model_validator(mode="after")
    def _coerente(self) -> Perfil:
        if "indefinido" not in self.padroes:
            raise ValueError("padroes precisa incluir 'indefinido' (resposta quando os dados não permitem concluir)")
        return self

    @property
    def rotulo(self) -> str:
        return self.nome or self.produto

    @property
    def produtos(self) -> list[str]:
        """Valores aceitos em produto_sugerido."""
        return self.produtos_sugeriveis or [self.produto]


def carregar_perfil(pasta: Path, perfil_id: str) -> Perfil:
    caminho = pasta / f"{perfil_id}.yaml"
    if not re.fullmatch(r"[a-z0-9_]{3,60}", perfil_id) or not caminho.exists():
        disponiveis = ", ".join(listar_ids(pasta)) or "nenhum"
        raise PerfilInvalidoError(f"Perfil {perfil_id!r} não encontrado em {pasta}. Disponíveis: {disponiveis}")
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
        perfil = Perfil.model_validate(dados)
    except (yaml.YAMLError, ValidationError) as exc:
        raise PerfilInvalidoError(f"Perfil {caminho.name} inválido:\n{exc}") from None
    if perfil.id != perfil_id:
        raise PerfilInvalidoError(f"{caminho.name} declara id {perfil.id!r}; o id deve ser o nome do arquivo")
    return perfil


def listar_ids(pasta: Path) -> list[str]:
    return sorted(p.stem for p in pasta.glob("*.yaml")) if pasta.exists() else []
