"""Fonte de leads do módulo 3.

Duas origens possíveis:
- `MODULO2_CSV` definido: lê o CSV que o agente (módulo 2) grava, de novo sempre que o
  arquivo muda, e junta os contatos complementares encontrados pelo enriquecimento;
- caso contrário: dados fictícios de `seed.py`.

O módulo 3 só enxerga o módulo 2. O CRM só enxerga o módulo 3.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .modelos import Classificacao, Contato, ContatoReceita, Filtro, Lead, Pagina, Perfil
from . import seed

log = logging.getLogger("modulo3.dados")
ContatosDe = Callable[[list[str]], dict[str, list[Contato]]]


def sem_acento(texto: str | None) -> str:
    if not texto:
        return ""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


@dataclass
class Repositorio:
    leads: list[Lead]
    fonte: str
    perfis_nome: dict[str, dict]
    contatos_de: ContatosDe | None = None  # contatos complementares atuais (enriquecimento)

    def __post_init__(self) -> None:
        self._por_cnpj = {lead.cnpj: lead for lead in self.leads}
        self.mes_referencia = max((lead.mes_referencia or "" for lead in self.leads), default=None) or None

    def _completar(self, leads: list[Lead]) -> list[Lead]:
        if self.contatos_de is None or not leads:
            return leads
        mapa = self.contatos_de([lead.cnpj for lead in leads])
        return [
            lead.model_copy(update={"contatos": c, "canal_sugerido": seed._canal(c, lead.contato_receita)})
            if (c := mapa.get(lead.cnpj)) else lead
            for lead in leads
        ]

    def obter(self, cnpj: str) -> Lead | None:
        lead = self._por_cnpj.get(cnpj)
        return self._completar([lead])[0] if lead else None

    def perfis(self) -> list[Perfil]:
        contagem: dict[str, int] = {}
        for lead in self.leads:
            for c in classificacoes(lead):
                if c.e_alvo:
                    contagem[c.perfil_id] = contagem.get(c.perfil_id, 0) + 1
        ids = sorted({c.perfil_id for lead in self.leads for c in classificacoes(lead)})
        return [
            Perfil(
                id=pid,
                nome=self.perfis_nome.get(pid, {}).get("nome", pid),
                produto=self.perfis_nome.get(pid, {}).get("produto"),
                total_alvos=contagem.get(pid, 0),
            )
            for pid in ids
        ]

    def buscar(self, f: Filtro) -> Pagina:
        ufs = {u.upper() for u in f.uf}
        municipio = sem_acento(f.municipio)
        texto = sem_acento(f.texto)
        somente = set(f.cnpjs) if f.cnpjs is not None else None
        excluir = set(f.excluir_cnpjs)

        def passa(lead: Lead) -> bool:
            cs = [c for c in classificacoes(lead)
                  if (not f.perfil_id or c.perfil_id == f.perfil_id) and (not f.perfis or c.perfil_id in f.perfis)]
            if not cs or (f.so_alvos and not any(c.e_alvo for c in cs)):
                return False
            c = max(cs, key=lambda x: (x.e_alvo, x.score))
            if ufs and (lead.uf or "") not in ufs:
                return False
            if municipio and municipio not in sem_acento(lead.municipio):
                return False
            if c.score < f.score_min:
                return False
            if somente is not None and lead.cnpj not in somente:
                return False
            if lead.cnpj in excluir:
                return False
            if texto:
                alvo = " ".join(sem_acento(x) for x in (lead.razao_social, lead.nome_fantasia, lead.cnpj, lead.municipio, lead.bairro))
                if texto not in alvo:
                    return False
            return True

        encontrados = [lead for lead in self.leads if passa(lead)]
        if f.ordenar == "score":
            encontrados.sort(key=lambda x: (-x.classificacao.score, x.razao_social))
        elif f.ordenar == "abertura":
            encontrados.sort(key=lambda x: x.data_inicio_atividade or date.min, reverse=True)
        else:
            encontrados.sort(key=lambda x: (x.nome_fantasia or x.razao_social))

        inicio = (f.pagina - 1) * f.por_pagina
        return Pagina(
            fonte=self.fonte,
            mes_referencia=self.mes_referencia,
            total=len(encontrados),
            pagina=f.pagina,
            por_pagina=f.por_pagina,
            itens=self._completar(encontrados[inicio : inicio + f.por_pagina]),
            cnpjs=[lead.cnpj for lead in encontrados] if f.apenas_cnpjs else None,
        )


def classificacoes(lead: Lead) -> list[Classificacao]:
    return lead.classificacoes or [lead.classificacao]


def agrupar(linhas: list[Lead]) -> list[Lead]:
    """Uma linha do CSV por CNPJ × perfil vira um lead por CNPJ, com todas as classificações."""
    por_cnpj: dict[str, list[Lead]] = {}
    for lead in linhas:
        por_cnpj.setdefault(lead.cnpj, []).append(lead)
    leads = []
    for grupo in por_cnpj.values():
        todas = sorted((g.classificacao for g in grupo), key=lambda c: (not c.e_alvo, -c.score, c.perfil_id))
        base = grupo[0]
        leads.append(base.model_copy(update={"classificacao": todas[0], "classificacoes": todas if len(todas) > 1 else []}))
    return leads


# ---------------------------------------------------------------- CSV do módulo 2

def _verdadeiro(valor: str | None) -> bool | None:
    if valor is None or valor.strip() == "":
        return None
    return valor.strip().lower() in {"1", "true", "sim", "s", "yes", "verdadeiro"}


def _vazio(valor: str | None) -> str | None:
    return valor if valor not in (None, "") else None


def ler_csv_modulo2(caminho: Path, nomes: dict[str, dict] | None = None) -> list[Lead]:
    """Lê o CSV de saída do agente. Colunas obrigatórias: cnpj, razao_social,
    perfil_id, score. As demais são opcionais (ver README)."""
    texto = caminho.read_text(encoding="utf-8-sig")
    separador = ";" if texto.splitlines()[0].count(";") > texto.splitlines()[0].count(",") else ","
    leads: list[Lead] = []
    for linha in csv.DictReader(texto.splitlines(), delimiter=separador):
        g = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in linha.items() if k}
        cnpj = "".join(ch for ch in (g.get("cnpj") or "").upper() if ch.isalnum()).zfill(14)
        score = int(float(g.get("score") or 0))
        e_alvo = _verdadeiro(g.get("e_alvo"))
        abertura = g.get("data_inicio_atividade")
        receita = ContatoReceita(
            ddd_1=_vazio(g.get("ddd_1")), telefone_1=_vazio(g.get("telefone_1")),
            ddd_2=_vazio(g.get("ddd_2")), telefone_2=_vazio(g.get("telefone_2")),
            email=_vazio(g.get("email")),
        )
        lead = Lead(
            cnpj=cnpj,
            razao_social=g.get("razao_social") or cnpj,
            nome_fantasia=_vazio(g.get("nome_fantasia")),
            matriz_filial=_vazio(g.get("matriz_filial")),
            situacao_cadastral=_vazio(g.get("situacao_cadastral")),
            data_inicio_atividade=date.fromisoformat(abertura) if abertura else None,
            cnae_principal=_vazio(g.get("cnae_principal")),
            cnae_descricao=_vazio(g.get("cnae_descricao")),
            porte=_vazio(g.get("porte")),
            capital_social=float(g["capital_social"].replace(",", ".")) if g.get("capital_social") else None,
            logradouro=_vazio(g.get("logradouro")),
            numero=_vazio(g.get("numero")),
            bairro=_vazio(g.get("bairro")),
            municipio=_vazio(g.get("municipio")),
            uf=_vazio(g.get("uf")),
            cep=_vazio(g.get("cep")),
            contato_receita=receita,
            contatos=[],
            classificacao=Classificacao(
                perfil_id=g.get("perfil_id") or "sem_perfil",
                perfil_versao=int(g.get("perfil_versao") or 1),
                score=max(0, min(100, score)),
                e_alvo=e_alvo if e_alvo is not None else score >= 60,
                justificativa=g.get("justificativa") or "",
                e_franquia=_verdadeiro(g.get("e_franquia")),
                marca=_vazio(g.get("marca")),
                padrao=_vazio(g.get("padrao")),
                produto_sugerido=_vazio(g.get("produto_sugerido")),
                classificado_em=_vazio(g.get("classificado_em")),
            ),
            mes_referencia=_vazio(g.get("mes_referencia")),
        )
        lead.canal_sugerido = seed._canal([], receita)
        leads.append(lead)
        if nomes is not None and g.get("perfil_nome"):
            nomes[lead.classificacao.perfil_id] = {"nome": g["perfil_nome"]}
    return leads


class Fonte:
    """Entrega o repositório atual. Com CSV, relê o arquivo quando ele muda: o agente grava um
    `classificacao.csv` novo e o módulo 3 passa a servi-lo sem reiniciar."""

    def __init__(self, arquivo: Path | None = None, contatos_de: ContatosDe | None = None) -> None:
        self.csv = arquivo
        self.contatos_de = contatos_de
        self._marca: tuple[float, int] | None = None
        self._trava = threading.Lock()
        self._repo = (Repositorio(leads=[], fonte="modulo2", perfis_nome=seed.PERFIS, contatos_de=contatos_de) if arquivo
                      else Repositorio(leads=seed.gerar(), fonte="simulado", perfis_nome=seed.PERFIS))

    def repo(self) -> Repositorio:
        if self.csv is None:
            return self._repo
        try:
            info = self.csv.stat()
        except FileNotFoundError:
            if self._marca is None:
                log.warning("%s ainda não existe: rode `agente exportar-crm`. Servindo 0 leads.", self.csv)
                self._marca = (0.0, -1)
            return self._repo
        marca = (info.st_mtime, info.st_size)
        if marca != self._marca:
            with self._trava:
                if marca != self._marca:
                    try:
                        nomes: dict[str, dict] = {}
                        leads = agrupar(ler_csv_modulo2(self.csv, nomes))
                    except (OSError, ValueError, KeyError) as erro:
                        log.error("Não foi possível ler %s (%s); segue o anterior.", self.csv, erro)
                    else:
                        self._repo = Repositorio(leads=leads, fonte="modulo2", perfis_nome={**seed.PERFIS, **nomes},
                                                 contatos_de=self.contatos_de)
                        log.info("%s carregado: %d leads", self.csv, len(leads))
                    self._marca = marca
        return self._repo


def caminho_csv() -> Path | None:
    caminho = os.environ.get("MODULO2_CSV", "").strip()
    return Path(caminho) if caminho else None


def carregar() -> Repositorio:
    """Repositório do momento (usado nos testes e no terminal)."""
    return Fonte(caminho_csv()).repo()


__all__ = ["Fonte", "Repositorio", "agrupar", "carregar", "caminho_csv", "classificacoes", "ler_csv_modulo2", "Contato"]
