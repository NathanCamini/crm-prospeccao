"""Saída para o CRM: o CSV que o módulo 3 lê (`MODULO2_CSV`; colunas em "Ligar ao módulo 2" no README da raiz).

Uma linha por CNPJ × perfil, da versão vigente de cada perfil, com os dados cadastrais e o contato da
Receita guardados no banco do agente quando a empresa passou no funil (o módulo 1 só mantém o arquivo do
mês atual). Com `janela_dias`, ficam só as empresas abertas há no máximo tantos dias: lead velho sai da
lista do CRM, que continua mostrando os que a equipe já trabalhou.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
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
    puladas: dict[str, int] = field(default_factory=dict)   # mês → classificações sem cadastro guardado
    fora_da_janela: int = 0                                  # empresas abertas antes da janela de leads


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
                 nomes: dict[str, str] | None = None, janela_dias: int = 0,
                 hoje: date | None = None) -> ResumoExportacao:
    """Grava o CSV do CRM. Classificação sem cadastro guardado nem arquivo do mês fica de fora (e é contada)."""
    resumo = ResumoExportacao(perfis=perfis or store.perfis_classificados())
    classificadas = [c for perfil_id in resumo.perfis for c in store.classificacoes(perfil_id, mes=mes)]
    cadastro = store.cadastro(sorted({c["cnpj"] for c in classificadas}))
    _completar_com_arquivos(classificadas, cadastro, novas_dir, schema_dir)
    limite = ((hoje or date.today()) - timedelta(days=janela_dias)).isoformat() if janela_dias > 0 else None

    linhas, meses = [], set()
    for c in classificadas:
        e = cadastro.get(c["cnpj"])
        if e is None:
            resumo.puladas[c["mes_referencia"]] = resumo.puladas.get(c["mes_referencia"], 0) + 1
            continue
        if limite and str(e.get("data_inicio_atividade") or "") < limite:
            resumo.fora_da_janela += 1
            continue
        linhas.append(_linha(c, e, nomes or {}))
        meses.add(c["mes_referencia"])
    resumo.meses = sorted(meses)

    if not linhas:
        raise ValueError("Nenhuma classificação para exportar (confira --perfil e --mes); o CSV anterior foi mantido.")
    gravar_csv(saida, linhas, COLUNAS_CRM)
    resumo.linhas = len(linhas)
    resumo.alvos = sum(l["e_alvo"] == "sim" for l in linhas)
    return resumo


def _completar_com_arquivos(classificadas: list[dict[str, Any]], cadastro: dict[str, dict[str, Any]],
                            novas_dir: Path, schema_dir: Path) -> None:
    """Classificações de antes do cadastro guardado: busca no arquivo do mês, se ele ainda existir."""
    faltando: dict[str, list[str]] = {}
    for c in classificadas:
        if c["cnpj"] not in cadastro:
            faltando.setdefault(c["mes_referencia"], []).append(c["cnpj"])
    for mes_ref, cnpjs in faltando.items():
        try:
            arquivo = validar_mes(novas_dir, mes_ref, schema_dir)
        except ContratoInvalidoError:
            continue
        cadastro.update(dados_basicos(arquivo, cnpjs))
