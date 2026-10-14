"""Provedores de API de busca OFICIAL (nunca raspagem de páginas de resultado).

A API é usada só para indicar o site provável da empresa (URL e título). Os contatos
são lidos depois no próprio site, respeitando robots.txt. Assim não guardamos
trechos de resultados de busca, o que evita depender dos termos de armazenamento
de cada provedor.

- Google Custom Search JSON API: fechada para novos clientes e desligada pelo Google
  em 01/01/2027. Mantida só para quem já tem chave.
- Brave Search API: alternativa oficial, paga por consulta (há crédito mensal gratuito).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import requests

DOMINIOS_IGNORADOS = (
    "google.", "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "x.com", "twitter.com",
    "tiktok.com", "wikipedia.org", "gov.br", "jusbrasil.com.br", "cnpj.biz", "cnpja.com", "casadosdados.com.br",
    "econodata.com.br", "cnpj.info", "consultacnpj.com", "solutudo.com.br", "empresascnpj.com", "listamais.com.br",
    "guiamais.com.br", "telelistas.net", "apontador.com.br", "reclameaqui.com.br", "mercadolivre.com.br",
)


class ApiBuscaError(RuntimeError):
    """Falha na API de busca (chave inválida, cota esgotada, rede)."""


@dataclass(frozen=True)
class ResultadoBusca:
    url: str
    titulo: str


class ProvedorBusca(Protocol):
    nome: str

    def buscar(self, consulta: str, quantidade: int = 5) -> list[ResultadoBusca]: ...


class GoogleCSE:
    nome = "Google Custom Search JSON API"
    URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, chave: str, cx: str, sessao: requests.Session | None = None) -> None:
        self.chave, self.cx = chave, cx
        self.sessao = sessao or requests.Session()

    def buscar(self, consulta: str, quantidade: int = 5) -> list[ResultadoBusca]:
        try:
            resp = self.sessao.get(
                self.URL, params={"key": self.chave, "cx": self.cx, "q": consulta, "num": quantidade, "gl": "br"},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ApiBuscaError(f"{self.nome}: falha de rede") from exc
        if resp.status_code != 200:
            raise ApiBuscaError(f"{self.nome}: HTTP {resp.status_code}")
        return [ResultadoBusca(i["link"], i.get("title", "")) for i in resp.json().get("items", [])]


class BraveSearch:
    nome = "Brave Search API"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, chave: str, sessao: requests.Session | None = None) -> None:
        self.chave = chave
        self.sessao = sessao or requests.Session()

    def buscar(self, consulta: str, quantidade: int = 5) -> list[ResultadoBusca]:
        try:
            resp = self.sessao.get(
                self.URL,
                params={"q": consulta, "count": quantidade, "country": "BR", "search_lang": "pt-br"},
                headers={"Accept": "application/json", "X-Subscription-Token": self.chave},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ApiBuscaError(f"{self.nome}: falha de rede") from exc
        if resp.status_code != 200:
            raise ApiBuscaError(f"{self.nome}: HTTP {resp.status_code}")
        itens = resp.json().get("web", {}).get("results", [])
        return [ResultadoBusca(i["url"], i.get("title", "")) for i in itens]


def criar_provedor(nome: str, google_chave: str, google_cx: str, brave_chave: str) -> ProvedorBusca | None:
    """Monta o provedor configurado no .env (ou None se nenhum estiver configurado)."""
    nome = (nome or "nenhum").strip().lower()
    if nome == "google" and google_chave and google_cx:
        return GoogleCSE(google_chave, google_cx)
    if nome == "brave" and brave_chave:
        return BraveSearch(brave_chave)
    return None


def site_candidato(resultados: list[ResultadoBusca]) -> ResultadoBusca | None:
    """Primeiro resultado que não é rede social, governo, catálogo de CNPJ ou guia comercial."""
    for r in resultados:
        url = r.url.lower()
        if not any(d in url for d in DOMINIOS_IGNORADOS):
            return r
    return None
