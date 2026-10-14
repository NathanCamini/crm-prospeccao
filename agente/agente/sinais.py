"""Sinais de franquia e de rede, calculados antes do LLM (o cadastro da Receita não diz se é franquia).

O modelo recebe os sinais prontos e decide; não deve adivinhar franquia pelo nome. Fontes:
- nome fantasia ou razão social com o nome de uma marca da rede (sem acento, palavra inteira, ou
  aproximado para erros de digitação) — indica a MARCA;
- domínio do e-mail da Receita igual ao domínio da rede — indica a REDE;
- sócio pessoa jurídica cujo nome é de uma holding da rede — indica a REDE;
- marcações da gold (filial de empresa existente), quando o arquivo do mês as tiver.

A lista (`perfis/marcas_rede.csv`) é montada uma vez a partir do catálogo da ABF e dos sites das
redes, e revisada quando aparecer marca nova.
"""

from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from agente.texto import dominio_do_email, normalizar_texto

SIMILARIDADE_MINIMA = 0.85   # comparação aproximada (só para termos de uma palavra com 6+ letras)
TAMANHO_MINIMO_APROXIMADO = 6


def _normalizar(texto: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalizar_texto(texto or "")).split())


@dataclass(frozen=True)
class Rede:
    nome: str
    marcas: tuple[tuple[str, tuple[str, ...]], ...]  # (marca, termos normalizados)
    dominios: tuple[str, ...]
    holdings: tuple[str, ...]                        # normalizados


def carregar_redes(caminho: Path) -> list[Rede]:
    """CSV ';' com colunas rede, marca, aliases, dominios, holdings (listas com '|').

    `aliases` são os termos procurados no nome; vazio = o próprio nome da marca. Assim uma bandeira
    de nome genérico ("Comfort") é procurada só nas formas completas ("comfort suites").
    Domínios e holdings valem para a rede inteira e podem estar em qualquer linha dela.
    """
    if not caminho.exists():
        return []
    acumulado: dict[str, dict[str, Any]] = {}
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        for linha in csv.DictReader((l for l in arquivo if not l.lstrip().startswith("#")), delimiter=";"):
            rede, marca = (linha.get("rede") or "").strip(), (linha.get("marca") or "").strip()
            if not rede or not marca:
                continue
            lista = lambda campo: [v.strip() for v in (linha.get(campo) or "").split("|") if v.strip()]  # noqa: E731
            item = acumulado.setdefault(rede, {"marcas": [], "dominios": [], "holdings": []})
            termos = tuple(sorted(dict.fromkeys(_normalizar(t) for t in (lista("aliases") or [marca]) if _normalizar(t)),
                                  key=len, reverse=True))  # o termo mais específico vira a evidência
            item["marcas"].append((marca, termos))
            item["dominios"] += [d.lower() for d in lista("dominios")]
            item["holdings"] += [_normalizar(h) for h in lista("holdings")]
    return [Rede(nome, tuple(v["marcas"]), tuple(dict.fromkeys(v["dominios"])), tuple(dict.fromkeys(v["holdings"])))
            for nome, v in acumulado.items()]


def socios_pj(gold_db: Path, cnpjs_basicos: list[str]) -> dict[str, list[str]]:
    """Nomes dos sócios pessoa jurídica por CNPJ básico, lidos da gold (vazio se a gold não existir)."""
    if not cnpjs_basicos or not gold_db.exists():
        return {}
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{gold_db.as_posix().replace(chr(39), chr(39) * 2)}' AS gold (READ_ONLY)")
        linhas = con.execute(
            "SELECT cnpj_basico, nome_socio FROM gold.socio "
            "WHERE tipo_socio = 'PESSOA JURÍDICA' AND list_contains(?::VARCHAR[], cnpj_basico)",
            [sorted(set(cnpjs_basicos))]).fetchall()
    finally:
        con.close()
    resultado: dict[str, list[str]] = {}
    for basico, nome in linhas:
        resultado.setdefault(basico, []).append(nome)
    return resultado


def _no_nome(termos: tuple[str, ...], nomes: dict[str, str]) -> str | None:
    for campo, texto in nomes.items():
        if not texto:
            continue
        for termo in termos:
            if f" {termo} " in f" {texto} ":
                return f"{campo} contém '{termo}'"
        tokens = texto.split()
        for termo in termos:
            if " " in termo or len(termo) < TAMANHO_MINIMO_APROXIMADO:
                continue
            parecido = next((t for t in tokens if len(t) >= TAMANHO_MINIMO_APROXIMADO and t != termo and
                             difflib.SequenceMatcher(None, t, termo).ratio() >= SIMILARIDADE_MINIMA), None)
            if parecido:
                return f"{campo} contém '{parecido}', parecido com '{termo}' (aproximado)"
    return None


class DetectorSinais:
    def __init__(self, redes: list[Rede], socios_pj_por_basico: dict[str, list[str]] | None = None) -> None:
        self.redes = redes
        self.socios = socios_pj_por_basico or {}

    def sinais(self, empresa: dict[str, Any]) -> dict[str, Any]:
        nomes = {"nome fantasia": _normalizar(empresa.get("nome_fantasia")),
                 "razão social": _normalizar(empresa.get("razao_social"))}
        dominio_email = dominio_do_email(empresa.get("email_receita"))
        socios = [_normalizar(s) for s in self.socios.get(empresa.get("cnpj_basico") or "", [])]

        achadas = []
        for rede in self.redes:
            evidencias, marca = [], None
            for nome_marca, termos in rede.marcas:
                if achado := _no_nome(termos, nomes):
                    evidencias.append(achado)
                    marca = marca or nome_marca
            pertence = lambda d: any(d == r or d.endswith("." + r) for r in rede.dominios)  # noqa: E731
            if dominio_email and pertence(dominio_email):
                evidencias.append(f"e-mail da Receita no domínio da rede ({dominio_email})")
            evidencias += [f"sócio pessoa jurídica '{s.upper()}' é holding da rede" for s in socios
                           if any(h and f" {h} " in f" {s} " for h in rede.holdings)]  # palavra inteira: "accor" ≠ "accorde"
            if evidencias:
                achadas.append({"rede": rede.nome, "marca": marca, "evidencias": evidencias})

        resultado: dict[str, Any] = {"redes": achadas, "dominio_email_proprio": dominio_email}
        for marcacao in ("filial_de_empresa_existente", "contato_compartilhado"):
            if marcacao in empresa:
                resultado[marcacao] = empresa[marcacao]
        return resultado
