"""Saída do LLM: estrutura validada por Pydantic + regras de negócio que o modelo não pode furar.

O JSON Schema garante a forma; as regras abaixo garantem o fundamento:
- franquia e marca só valem se vierem dos sinais de rede (o modelo não adivinha franquia pelo nome);
- padrão e produto sugerido só aceitam os valores do perfil;
- e_alvo final exige score no limiar do perfil.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agente.perfil import Perfil


class ResultadoLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    e_alvo: bool
    score: int = Field(ge=0, le=100)
    justificativa: str = Field(min_length=1, max_length=300)
    e_franquia: bool | None
    marca: str | None
    padrao: str
    produto_sugerido: str | None
    confianca: Literal["alta", "media", "baixa"]


def schema_saida(perfil: Perfil) -> dict[str, Any]:
    """JSON Schema enviado ao modelo (Structured Output), com os valores aceitos pelo perfil."""
    return {
        "type": "object",
        "properties": {
            "e_alvo": {"type": "boolean", "description": "a empresa é um bom alvo para o produto"},
            "score": {"type": "integer", "minimum": 0, "maximum": 100,
                      "description": "chance de ser bom cliente, de 0 a 100"},
            "justificativa": {"type": "string", "description": "uma frase, até 200 caracteres, citando o dado que pesou"},
            "e_franquia": {"type": ["boolean", "null"],
                           "description": "true só com sinal em sinais.redes; null se indeterminado"},
            "marca": {"type": ["string", "null"],
                      "description": "marca (ou, sem marca, a rede) exatamente como veio em sinais.redes, ou null"},
            "padrao": {"type": "string", "enum": list(perfil.padroes)},
            "produto_sugerido": {"type": ["string", "null"], "enum": [*perfil.produtos, None]},
            "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
        },
        "required": ["e_alvo", "score", "justificativa", "e_franquia", "marca", "padrao", "produto_sugerido", "confianca"],
    }


@dataclass(frozen=True)
class Avaliacao:
    final: dict[str, Any]          # resultado após as regras (o que vai para a tabela)
    avisos: tuple[str, ...]        # o que as regras corrigiram
    motivo_revisao: str | None     # por que o modelo maior deve confirmar (None = não precisa)


def aplicar_regras(bruto: ResultadoLLM, perfil: Perfil, sinais: dict[str, Any]) -> Avaliacao:
    final = bruto.model_dump()
    avisos: list[str] = []
    redes = sinais.get("redes", [])
    nomes_validos = {n.lower(): n for r in redes for n in (r["rede"], r["marca"]) if n}

    franquia_sem_sinal = bruto.e_franquia is True and not redes
    if franquia_sem_sinal:
        final["e_franquia"], final["marca"] = None, None
        avisos.append("franquia afirmada sem sinal de rede: tratada como indeterminada")
    if final["marca"] is not None:
        if final["marca"].lower() in nomes_validos:
            final["marca"] = nomes_validos[final["marca"].lower()]
        else:
            avisos.append(f"marca {final['marca']!r} não veio dos sinais: descartada")
            final["marca"] = None
    if final["padrao"] not in perfil.padroes:
        avisos.append(f"padrão {final['padrao']!r} fora do perfil: indefinido")
        final["padrao"] = "indefinido"
    if final["produto_sugerido"] is not None and final["produto_sugerido"] not in perfil.produtos:
        avisos.append(f"produto {final['produto_sugerido']!r} fora do perfil: descartado")
        final["produto_sugerido"] = None

    incoerente = bruto.e_alvo != (bruto.score >= perfil.limiar_alvo)
    final["e_alvo"] = bruto.e_alvo and bruto.score >= perfil.limiar_alvo
    if incoerente:
        avisos.append(f"e_alvo={bruto.e_alvo} incoerente com score {bruto.score} (limiar {perfil.limiar_alvo})")

    if bruto.score >= perfil.revisao.score_min:
        motivo = "score alto"
    elif bruto.confianca == "baixa" and perfil.revisao.confianca_baixa:
        motivo = "confiança baixa"
    elif incoerente:
        motivo = "e_alvo incoerente com o score"
    elif franquia_sem_sinal:
        motivo = "afirmou franquia sem sinal"
    else:
        motivo = None
    return Avaliacao(final, tuple(avisos), motivo)
