"""Fonte de leads do módulo 3 simulado.

Duas origens possíveis:
- `MODULO2_CSV` definido: lê o CSV que o agente (módulo 2) grava;
- caso contrário: dados fictícios de `seed.py`.

O módulo 3 só enxerga o módulo 2. O CRM só enxerga o módulo 3.
"""

from __future__ import annotations

import csv
import os
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .modelos import Classificacao, Contato, ContatoReceita, Filtro, Lead, Pagina, Perfil
from . import seed


def sem_acento(texto: str | None) -> str:
    if not texto:
        return ""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


@dataclass
class Repositorio:
    leads: list[Lead]
    fonte: str
    perfis_nome: dict[str, dict]

    def __post_init__(self) -> None:
        self._por_cnpj = {lead.cnpj: lead for lead in self.leads}
        self.mes_referencia = max((lead.mes_referencia or "" for lead in self.leads), default=None) or None

    def obter(self, cnpj: str) -> Lead | None:
        return self._por_cnpj.get(cnpj)

    def perfis(self) -> list[Perfil]:
        contagem: dict[str, int] = {}
        for lead in self.leads:
            if lead.classificacao.e_alvo:
                contagem[lead.classificacao.perfil_id] = contagem.get(lead.classificacao.perfil_id, 0) + 1
        ids = sorted({lead.classificacao.perfil_id for lead in self.leads})
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
            c = lead.classificacao
            if f.so_alvos and not c.e_alvo:
                return False
            if f.perfil_id and c.perfil_id != f.perfil_id:
                return False
            if f.perfis and c.perfil_id not in f.perfis:
                return False
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
            itens=encontrados[inicio : inicio + f.por_pagina],
            cnpjs=[lead.cnpj for lead in encontrados] if f.apenas_cnpjs else None,
        )


# ---------------------------------------------------------------- CSV do módulo 2

def _verdadeiro(valor: str | None) -> bool | None:
    if valor is None or valor.strip() == "":
        return None
    return valor.strip().lower() in {"1", "true", "sim", "s", "yes", "verdadeiro"}


def _vazio(valor: str | None) -> str | None:
    return valor if valor not in (None, "") else None


def ler_csv_modulo2(caminho: Path) -> list[Lead]:
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
    return leads


def carregar() -> Repositorio:
    caminho = os.environ.get("MODULO2_CSV", "").strip()
    if caminho:
        leads = ler_csv_modulo2(Path(caminho))
        return Repositorio(leads=leads, fonte="modulo2", perfis_nome=seed.PERFIS)
    return Repositorio(leads=seed.gerar(), fonte="simulado", perfis_nome=seed.PERFIS)


__all__ = ["Repositorio", "carregar", "ler_csv_modulo2", "Contato"]
