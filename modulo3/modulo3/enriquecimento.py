"""Busca de contatos complementares dos leads que vieram do agente (módulo 2).

Reaproveita o serviço de contatos do módulo 1 (`prospeccao.enrich`), com as mesmas regras:
site do domínio do e-mail da Receita, API de busca opcional (só indica o site; os contatos
vêm do próprio site), robots.txt, intervalo mínimo por site e cache de 90 dias. Os contatos
ficam no banco próprio do módulo 3 (`modulo3.sqlite`); o contato original da Receita nunca é
alterado e não é repetido como complementar.

Só as empresas-alvo são buscadas. Um ciclo trabalha no máximo `lote` empresas; as que já
estão em cache não contam.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from prospeccao.app_store import AppStore
from prospeccao.enrich.api_busca import criar_provedor
from prospeccao.enrich.servico import METODO_API, METODO_SITE, ServicoContatos
from prospeccao.enrich.site import LeitorSite, LimitadorFrequencia

from .modelos import Contato, Lead

log = logging.getLogger("modulo3.enriquecimento")

_SEM_CONTATO_NO_SITE = ("sem_dominio", "bloqueado", "sem_resultado")


@dataclass(frozen=True)
class ConfigEnriquecimento:
    dados_dir: Path
    minutos: float = 60          # 0 = só sob demanda (python -m modulo3 enriquecer)
    lote: int = 100
    cache_dias: int = 90
    site_intervalo_segundos: float = 2.0
    provedor: str = "nenhum"     # nenhum | brave | google
    brave_chave: str = ""
    google_chave: str = ""
    google_cx: str = ""
    api_limite_diario: int = 100
    api_intervalo_segundos: float = 1.0

    @classmethod
    def do_ambiente(cls) -> ConfigEnriquecimento:
        e = os.environ.get
        return cls(
            dados_dir=Path(e("MODULO3_DADOS_DIR", "dados")),
            minutos=float(e("MODULO3_ENRIQUECER_MINUTOS", "60")),
            lote=int(e("MODULO3_LOTE", "100")),
            cache_dias=int(e("CACHE_BUSCA_DIAS", "90")),
            site_intervalo_segundos=float(e("SITE_INTERVALO_SEGUNDOS", "2")),
            provedor=e("BUSCA_API_PROVEDOR", "nenhum"),
            brave_chave=e("BRAVE_SEARCH_CHAVE", ""),
            google_chave=e("GOOGLE_CSE_CHAVE", ""),
            google_cx=e("GOOGLE_CSE_CX", ""),
            api_limite_diario=int(e("API_LIMITE_DIARIO", "100")),
            api_intervalo_segundos=float(e("API_INTERVALO_SEGUNDOS", "1")),
        )


def contato_do_contrato(bruto: dict[str, Any]) -> Contato:
    tipo, valor = bruto["tipo"], bruto["valor"]
    if tipo == "telefone" and valor.endswith("(WhatsApp)"):  # link wa.me lido no site
        tipo, valor = "whatsapp", valor.removesuffix("(WhatsApp)").strip()
    return Contato(tipo=tipo, valor=valor, fonte=bruto["fonte_nome"], url=bruto.get("fonte_url"),
                   confianca=bruto["confianca"], observacao=bruto.get("observacao"))


def empresa_do_lead(lead: Lead) -> dict[str, Any]:
    r = lead.contato_receita
    return {
        "cnpj": lead.cnpj, "razao_social": lead.razao_social, "nome_fantasia": lead.nome_fantasia,
        "municipio": lead.municipio, "uf": lead.uf, "email_receita": r.email,
        "ddd_receita_1": r.ddd_1, "telefone_receita_1": r.telefone_1,
        "ddd_receita_2": r.ddd_2, "telefone_receita_2": r.telefone_2,
    }


class Enriquecedor:
    def __init__(self, cfg: ConfigEnriquecimento, servico: ServicoContatos | None = None) -> None:
        cfg.dados_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.store = servico.store if servico else AppStore(cfg.dados_dir / "modulo3.sqlite")
        self.servico = servico or ServicoContatos(
            self.store,
            LeitorSite(LimitadorFrequencia(cfg.site_intervalo_segundos)),
            criar_provedor(cfg.provedor, cfg.google_chave, cfg.google_cx, cfg.brave_chave),
            cfg.cache_dias, cfg.api_limite_diario, LimitadorFrequencia(cfg.api_intervalo_segundos),
        )
        self.ultima: dict[str, Any] | None = None
        self._rodando = threading.Lock()

    def contatos(self, cnpjs: list[str]) -> dict[str, list[Contato]]:
        brutos = self.store.contatos_por_cnpjs(cnpjs)
        return {cnpj: [contato_do_contrato(c) for c in lista] for cnpj, lista in brutos.items()}

    def executar(self, leads: list[Lead], limite: int | None = None, forcar: bool = False) -> dict[str, Any]:
        """Um ciclo de busca. Se outro ciclo estiver rodando, não começa outro."""
        if not self._rodando.acquire(blocking=False):
            return {"situacao": "em_andamento"}
        try:
            return self._executar(leads, self.cfg.lote if limite is None else limite, forcar)
        finally:
            self._rodando.release()

    def _executar(self, leads: list[Lead], limite: int, forcar: bool) -> dict[str, Any]:
        inicio = datetime.now().isoformat(timespec="seconds")
        alvos = list({lead.cnpj: lead for lead in leads if lead.classificacao.e_alvo}.values())
        contagem: dict[str, int] = {}
        trabalhadas = novos = 0
        for lead in alvos:
            if trabalhadas >= limite:
                break
            empresa = empresa_do_lead(lead)
            site = self.servico.buscar_site(empresa, forcar=forcar)
            api = None
            if site.situacao in _SEM_CONTATO_NO_SITE and self.servico.provedor is not None:
                api = self.servico.buscar_api(empresa, forcar=forcar)
                if api.situacao == "ok":
                    if indicado := self._site_indicado(lead.cnpj):
                        site = self.servico.buscar_site(empresa, url_base=indicado, forcar=True)
            if site.situacao not in ("cache", "sem_dominio") or (api and api.situacao not in ("cache", "sem_api")):
                trabalhadas += 1
            contagem[site.situacao] = contagem.get(site.situacao, 0) + 1
            novos += site.novos + (api.novos if api else 0)
            if api and api.situacao == "limite":
                log.info("Limite diário da API de busca atingido; o resto fica para o próximo ciclo.")
        self.ultima = {"inicio": inicio, "fim": datetime.now().isoformat(timespec="seconds"), "alvos": len(alvos),
                       "trabalhadas": trabalhadas, "contatos_novos": novos, "situacoes": contagem}
        log.info("Enriquecimento: %s", self.ultima)
        return self.ultima

    def _site_indicado(self, cnpj: str) -> str | None:
        """Site que a API acabou de indicar, se a confiança não for baixa (aí só fica registrado)."""
        nome = self.servico.provedor.nome if self.servico.provedor else None
        for c in self.store.listar_contatos(cnpj):
            if c["tipo"] == "site" and c["fonte_nome"] == nome and c["confianca"] in ("alta", "media"):
                return c["valor"]
        return None

    def resumo(self) -> dict[str, Any]:
        return {
            "ativo": True,
            "api_busca": self.cfg.provedor if self.servico.provedor else "nenhum",
            "a_cada_minutos": self.cfg.minutos,
            "empresas_com_contato": len(self.store.cnpjs_com_contato_complementar()),
            "ultima_execucao": self.ultima,
            "metodos": [METODO_SITE] + ([METODO_API] if self.servico.provedor else []),
        }


def enriquecer_periodicamente(enriquecedor: Enriquecedor, leads, parar: threading.Event) -> None:
    """`leads` é uma função: a cada ciclo pega a lista atual (o CSV pode ter mudado)."""
    while not parar.wait(5 if enriquecedor.ultima is None else enriquecedor.cfg.minutos * 60):
        try:
            enriquecedor.executar(leads())
        except Exception:  # noqa: BLE001 — a thread não pode morrer
            log.exception("Erro no enriquecimento")
