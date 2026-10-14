"""Saída para o CRM: o CSV que o módulo 3 lê (`MODULO2_CSV`; colunas em "Ligar ao módulo 2" no README da raiz).

Uma linha por CNPJ × perfil, da versão vigente de cada perfil, com os dados cadastrais e o contato da
Receita tirados do arquivo do mês em que a empresa foi classificada.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agente.armazenamento import ClassificacaoStore
from agente.avaliacao import gravar_csv
from agente.contrato import ContratoInvalidoError, validar_mes
from agente.funil import dados_basicos

log = logging.getLogger("agente")
ARQUIVO_CRM = "classificacao.csv"
COLUNAS_CRM = [
    "cnpj", "razao_social", "nome_fantasia", "matriz_filial", "situacao_cadastral", "data_inicio_atividade",
    "cnae_principal", "cnae_descricao", "porte", "capital_social", "logradouro", "numero", "bairro", "municipio", "uf",
    "cep", "ddd_1", "telefone_1", "ddd_2", "telefone_2", "email",
    "perfil_id", "perfil_versao", "score", "e_alvo", "justificativa", "e_franquia", "marca", "padrao",
    "produto_sugerido", "classificado_em", "mes_referencia", "perfil_nome",
]


@dataclass
class ResumoExportacao:
    linhas: int = 0
    alvos: int = 0
    perfis: list[str] = field(default_factory=list)
    meses: list[str] = field(default_factory=list)
    puladas: dict[str, int] = field(default_factory=dict)   # mês → classificações sem o arquivo do mês


def _sim_nao(valor: Any) -> str:
    return "" if valor is None else "sim" if valor else "nao"


def _linha(c: dict[str, Any], e: dict[str, Any], nomes: dict[str, str]) -> dict[str, Any]:
    final = json.loads(c["resultado_json"])["final"]
    return {
        "cnpj": c["cnpj"], "razao_social": e["razao_social"], "nome_fantasia": e["nome_fantasia"],
        "matriz_filial": e["matriz_filial"], "situacao_cadastral": e["situacao_cadastral"],
        "data_inicio_atividade": e["data_inicio_atividade"], "cnae_principal": e["cnae_principal"],
        "cnae_descricao": e["cnae_principal_descricao"], "porte": e["porte"], "capital_social": e["capital_social"],
        "logradouro": " ".join(p for p in (e["tipo_logradouro"], e["logradouro"]) if p) or None, "numero": e["numero"],
        "bairro": e["bairro"], "municipio": e["municipio"], "uf": e["uf"], "cep": e["cep"],
        "ddd_1": e["ddd_receita_1"], "telefone_1": e["telefone_receita_1"], "ddd_2": e["ddd_receita_2"],
        "telefone_2": e["telefone_receita_2"], "email": e["email_receita"],
        "perfil_id": c["perfil_id"], "perfil_versao": c["perfil_versao"], "score": c["score"],
        "e_alvo": _sim_nao(c["e_alvo"]), "justificativa": " ".join((final.get("justificativa") or "").split()),
        "e_franquia": _sim_nao(c["e_franquia"]), "marca": c["marca"], "padrao": final.get("padrao"),
        "produto_sugerido": final.get("produto_sugerido"), "classificado_em": c["classificado_em"],
        "mes_referencia": c["mes_referencia"], "perfil_nome": nomes.get(c["perfil_id"], c["perfil_id"]),
    }


def exportar_crm(store: ClassificacaoStore, novas_dir: Path, schema_dir: Path, saida: Path,
                 perfis: list[str] | None = None, mes: str | None = None,
                 nomes: dict[str, str] | None = None) -> ResumoExportacao:
    """Grava o CSV do CRM. Mês cujo arquivo sumiu ou não passa no contrato é pulado (e contado no resumo)."""
    resumo = ResumoExportacao(perfis=perfis or store.perfis_classificados())
    por_mes: dict[str, list[dict[str, Any]]] = {}
    for perfil_id in resumo.perfis:
        for c in store.classificacoes(perfil_id, mes=mes):
            por_mes.setdefault(c["mes_referencia"], []).append(c)

    linhas = []
    for mes_ref, classificadas in sorted(por_mes.items()):
        try:
            arquivo = validar_mes(novas_dir, mes_ref, schema_dir)
        except ContratoInvalidoError as exc:
            log.warning("mês %s fora do CSV: %s", mes_ref, exc)
            resumo.puladas[mes_ref] = len(classificadas)
            continue
        dados = dados_basicos(arquivo, [c["cnpj"] for c in classificadas])
        faltando = [c for c in classificadas if c["cnpj"] not in dados]
        if faltando:
            resumo.puladas[mes_ref] = len(faltando)
        linhas += [_linha(c, dados[c["cnpj"]], nomes or {}) for c in classificadas if c["cnpj"] in dados]
        resumo.meses.append(mes_ref)

    if not linhas:
        raise ValueError("Nenhuma classificação para exportar (confira --perfil e --mes); o CSV anterior foi mantido.")
    gravar_csv(saida, linhas, COLUNAS_CRM)
    resumo.linhas = len(linhas)
    resumo.alvos = sum(l["e_alvo"] == "sim" for l in linhas)
    return resumo
