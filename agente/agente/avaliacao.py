"""Medir antes de confiar: rótulos humanos, precisão e cobertura, revisão mensal.

- Rotulagem: uma amostra aleatória do funil SQL do mês vira planilha; uma pessoa marca, para cada
  empresa, se ela é alvo (e se é franquia). 150 a 300 empresas por perfil.
- Avaliação: compara os rótulos com a classificação vigente do perfil. Para prospecção a precisão
  pesa mais que a cobertura: cada falso positivo é uma mensagem irrelevante e um risco de denúncia.
- Revisão mensal: 20 classificações aleatórias do mês para uma pessoa conferir.
"""

from __future__ import annotations

import csv
import json
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agente.armazenamento import ClassificacaoStore
from agente.contrato import ArquivoDoMes
from agente.funil import candidatos, dados_basicos
from agente.perfil import Perfil

META_PRECISAO = 0.85             # critério de saída da fase 1 do roteiro
MINIMO_ALVOS_PARA_MEDIR = 20     # abaixo disso a precisão medida ainda é instável
_SIM = {"sim", "s", "1", "true", "verdadeiro", "x", "yes", "y"}
_NAO = {"nao", "não", "n", "0", "false", "falso", "no"}
COLUNAS_ROTULO = ("e_alvo_humano", "e_franquia_humano", "observacao")


def _bool_humano(valor: str | None) -> bool | None:
    texto = (valor or "").strip().lower()
    return True if texto in _SIM else False if texto in _NAO else None


def gravar_csv(caminho: Path, linhas: list[dict[str, Any]], colunas: list[str]) -> None:
    """CSV ';' em UTF-8 com BOM (abre direto no Excel), trocado de uma vez: grava ao lado e renomeia."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    with temporario.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas, delimiter=";", extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(linhas)
    os.replace(temporario, caminho)


def amostra_para_rotular(arquivo: ArquivoDoMes, perfil: Perfil, n: int, saida: Path, semente: int = 42) -> int:
    """Amostra aleatória (sem viés do modelo) dos candidatos do funil, com colunas em branco para rotular."""
    empresas = candidatos(arquivo, perfil, limite=max(perfil.limite_candidatos, 10 ** 9))
    escolhidas = random.Random(semente).sample(empresas, min(n, len(empresas)))
    linhas = [{
        "cnpj": e["cnpj"], "nome_fantasia": e["nome_fantasia"], "razao_social": e["razao_social"],
        "atividade": f"{e['cnae_principal']} {e['cnae_principal_descricao'] or ''}", "municipio": f"{e['municipio']}/{e['uf']}",
        "bairro": e["bairro"], "porte": e["porte"], "capital_social": e["capital_social"], "matriz_filial": e["matriz_filial"],
        **{c: "" for c in COLUNAS_ROTULO},
    } for e in sorted(escolhidas, key=lambda e: e["cnpj"])]
    gravar_csv(saida, linhas, ["cnpj", "nome_fantasia", "razao_social", "atividade", "municipio", "bairro", "porte",
                                "capital_social", "matriz_filial", *COLUNAS_ROTULO])
    return len(linhas)


@dataclass
class Relatorio:
    perfil_id: str
    perfil_versao: int | None
    rotulados: int
    sem_classificacao: int
    verdadeiros_positivos: int
    falsos_positivos: int
    falsos_negativos: int
    verdadeiros_negativos: int
    precisao: float | None
    cobertura: float | None
    acuracia: float | None
    franquia_avaliadas: int
    franquia_acertos: int
    meta_precisao: float
    aprovado: bool
    avisos: list[str] = field(default_factory=list)
    exemplos_falsos_positivos: list[dict[str, Any]] = field(default_factory=list)
    exemplos_falsos_negativos: list[dict[str, Any]] = field(default_factory=list)

    def linhas(self) -> list[str]:
        pct = lambda v: "—" if v is None else f"{v:.1%}"  # noqa: E731
        saida = [
            f"Perfil {self.perfil_id} v{self.perfil_versao} · {self.rotulados} empresas rotuladas e classificadas"
            f" ({self.sem_classificacao} rotuladas ainda sem classificação)",
            f"  precisão {pct(self.precisao)} (meta {self.meta_precisao:.0%}) · cobertura {pct(self.cobertura)} · "
            f"acurácia {pct(self.acuracia)}",
            f"  VP {self.verdadeiros_positivos} · FP {self.falsos_positivos} · FN {self.falsos_negativos} · "
            f"VN {self.verdadeiros_negativos}",
        ]
        if self.franquia_avaliadas:
            saida.append(f"  franquia: {self.franquia_acertos}/{self.franquia_avaliadas} acertos onde o agente decidiu")
        saida.append("  RESULTADO: " + ("aprovado" if self.aprovado else "abaixo da meta"))
        saida += [f"  aviso: {a}" for a in self.avisos]
        for titulo, exemplos in (("falsos positivos", self.exemplos_falsos_positivos),
                                 ("falsos negativos", self.exemplos_falsos_negativos)):
            if exemplos:
                saida.append(f"  {titulo} (até 10):")
                saida += [f"    {e['cnpj']} score {e['score']}: {e['justificativa']}" for e in exemplos[:10]]
        return saida

    def como_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def avaliar(store: ClassificacaoStore, perfil_id: str, rotulos: Path, meta: float = META_PRECISAO) -> Relatorio:
    with rotulos.open(encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(2048)
        arquivo.seek(0)
        dialeto = csv.Sniffer().sniff(amostra, delimiters=";,")
        linhas = list(csv.DictReader(arquivo, dialect=dialeto))
    humanos = {}
    for linha in linhas:
        alvo = _bool_humano(linha.get("e_alvo_humano"))
        if alvo is not None and linha.get("cnpj"):
            humanos[linha["cnpj"].strip()] = (alvo, _bool_humano(linha.get("e_franquia_humano")))

    classificadas = {c["cnpj"]: c for c in store.classificacoes(perfil_id)}
    versao = next(iter(classificadas.values()))["perfil_versao"] if classificadas else None
    vp = fp = fn = vn = fr_avaliadas = fr_acertos = 0
    exemplos_fp, exemplos_fn = [], []
    sem_classificacao = 0
    for cnpj, (alvo_humano, franquia_humana) in sorted(humanos.items()):
        c = classificadas.get(cnpj)
        if c is None:
            sem_classificacao += 1
            continue
        alvo_agente = bool(c["e_alvo"])
        exemplo = {"cnpj": cnpj, "score": c["score"],
                   "justificativa": json.loads(c["resultado_json"])["final"]["justificativa"]}
        if alvo_agente and alvo_humano:
            vp += 1
        elif alvo_agente:
            fp += 1
            exemplos_fp.append(exemplo)
        elif alvo_humano:
            fn += 1
            exemplos_fn.append(exemplo)
        else:
            vn += 1
        if franquia_humana is not None and c["e_franquia"] is not None:
            fr_avaliadas += 1
            fr_acertos += int(bool(c["e_franquia"]) == franquia_humana)

    total = vp + fp + fn + vn
    precisao = vp / (vp + fp) if vp + fp else None
    avisos = []
    if not humanos:
        avisos.append("nenhuma linha com e_alvo_humano preenchido (use sim/nao)")
    if vp + fp < MINIMO_ALVOS_PARA_MEDIR:
        avisos.append(f"só {vp + fp} empresas previstas como alvo: a precisão ainda é instável "
                      f"(rotule mais até ter {MINIMO_ALVOS_PARA_MEDIR})")
    return Relatorio(
        perfil_id=perfil_id, perfil_versao=versao, rotulados=total, sem_classificacao=sem_classificacao,
        verdadeiros_positivos=vp, falsos_positivos=fp, falsos_negativos=fn, verdadeiros_negativos=vn,
        precisao=precisao, cobertura=vp / (vp + fn) if vp + fn else None, acuracia=(vp + vn) / total if total else None,
        franquia_avaliadas=fr_avaliadas, franquia_acertos=fr_acertos, meta_precisao=meta,
        aprovado=precisao is not None and precisao >= meta and vp + fp >= MINIMO_ALVOS_PARA_MEDIR, avisos=avisos,
        exemplos_falsos_positivos=sorted(exemplos_fp, key=lambda e: -e["score"]),
        exemplos_falsos_negativos=sorted(exemplos_fn, key=lambda e: -e["score"]),
    )


def amostra_revisao(store: ClassificacaoStore, arquivo: ArquivoDoMes, perfil_id: str, n: int, saida: Path,
                    semente: int | None = None) -> int:
    """Amostra aleatória das classificações do mês, com a decisão do agente e colunas para a pessoa conferir."""
    classificadas = store.classificacoes(perfil_id, mes=arquivo.mes)
    escolhidas = random.Random(semente).sample(classificadas, min(n, len(classificadas)))
    dados = dados_basicos(arquivo, [c["cnpj"] for c in escolhidas])
    linhas = []
    for c in sorted(escolhidas, key=lambda c: -c["score"]):
        final = json.loads(c["resultado_json"])["final"]
        empresa = dados.get(c["cnpj"], {})
        linhas.append({
            "cnpj": c["cnpj"], "nome_fantasia": empresa.get("nome_fantasia"), "razao_social": empresa.get("razao_social"),
            "atividade": f"{empresa.get('cnae_principal')} {empresa.get('cnae_principal_descricao') or ''}",
            "municipio": f"{empresa.get('municipio')}/{empresa.get('uf')}", "e_alvo": "sim" if c["e_alvo"] else "nao",
            "score": c["score"], "confianca": c["confianca"], "e_franquia": final.get("e_franquia"),
            "marca": final.get("marca"), "padrao": final.get("padrao"), "produto_sugerido": final.get("produto_sugerido"),
            "justificativa": final.get("justificativa"), "modelo": c["modelo"], "concorda": "", "comentario": "",
        })
    gravar_csv(saida, linhas, ["cnpj", "nome_fantasia", "razao_social", "atividade", "municipio", "e_alvo", "score",
                                "confianca", "e_franquia", "marca", "padrao", "produto_sugerido", "justificativa",
                                "modelo", "concorda", "comentario"])
    return len(linhas)
