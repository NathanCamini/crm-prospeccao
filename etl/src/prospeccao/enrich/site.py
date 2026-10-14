"""Leitura educada de um site: respeita robots.txt, limita páginas, tamanho e frequência.

Só lê páginas do próprio domínio da empresa (página inicial e páginas de contato).
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

from prospeccao.enrich.extracao import PaginaLida, extrair_contatos, mesmo_dominio

log = logging.getLogger("prospeccao.enrich.site")

USER_AGENT = "prospeccao-cnpj/0.1 (busca de contato comercial; respeita robots.txt)"
CAMINHOS_CONTATO = ("/contato", "/fale-conosco", "/contact", "/contato.html")
_TAMANHO_MAXIMO = 2 * 1024 * 1024
_PALAVRAS_LINK_CONTATO = ("contato", "fale", "contact", "atendimento")


class SiteBloqueadoError(RuntimeError):
    """robots.txt não permite a leitura, ou o site não respondeu."""


@dataclass
class LimitadorFrequencia:
    """Intervalo mínimo entre requisições ao mesmo host (seguro entre threads)."""

    intervalo_segundos: float
    _ultimo: dict[str, float] = field(default_factory=dict)
    _trava: threading.Lock = field(default_factory=threading.Lock)

    def aguardar(self, host: str) -> None:
        with self._trava:
            agora = time.monotonic()
            espera = self._ultimo.get(host, 0) + self.intervalo_segundos - agora
            if espera > 0:
                time.sleep(espera)
            self._ultimo[host] = time.monotonic()


@dataclass
class ResultadoSite:
    url_base: str
    paginas: list[PaginaLida]

    @property
    def texto(self) -> str:
        return " ".join(p.titulo + " " + p.texto for p in self.paginas)


class LeitorSite:
    def __init__(self, limitador: LimitadorFrequencia, sessao: requests.Session | None = None,
                 max_paginas: int = 4, timeout: float = 15) -> None:
        self.limitador = limitador
        self.sessao = sessao or requests.Session()
        self.sessao.headers["User-Agent"] = USER_AGENT
        self.max_paginas = max_paginas
        self.timeout = timeout

    def _robots(self, base: str) -> urllib.robotparser.RobotFileParser:
        """Regras do robots.txt (RFC 9309): 4xx = tudo liberado; 5xx ou falha de rede = nada liberado."""
        parser = urllib.robotparser.RobotFileParser()
        url = urljoin(base, "/robots.txt")
        self.limitador.aguardar(urlparse(base).netloc)
        try:
            resp = self.sessao.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise SiteBloqueadoError(f"Site não respondeu ({exc.__class__.__name__})") from exc
        if resp.status_code >= 500:
            raise SiteBloqueadoError(f"robots.txt indisponível (HTTP {resp.status_code}); leitura não permitida")
        parser.parse(resp.text.splitlines() if resp.status_code < 400 else [])
        return parser

    def _baixar(self, url: str, base: str) -> str | None:
        """GET com redirecionamentos só dentro do domínio, limite de tamanho e somente HTML."""
        atual = url
        for _ in range(4):
            self.limitador.aguardar(urlparse(atual).netloc)
            try:
                resp = self.sessao.get(atual, timeout=self.timeout, allow_redirects=False, stream=True)
            except requests.RequestException:
                return None
            if resp.is_redirect:
                destino = urljoin(atual, resp.headers.get("Location", ""))
                resp.close()
                if not mesmo_dominio(destino, base):
                    log.info("Redirecionamento para fora do domínio ignorado: %s", destino)
                    return None
                atual = destino
                continue
            if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "text/html"):
                resp.close()
                return None
            conteudo = b""
            for bloco in resp.iter_content(64 * 1024):
                conteudo += bloco
                if len(conteudo) > _TAMANHO_MAXIMO:
                    break
            resp.close()
            return conteudo.decode(resp.encoding or "utf-8", errors="replace")
        return None

    def ler(self, url_base: str) -> ResultadoSite:
        base = url_base.rstrip("/") + "/"
        robots = self._robots(base)
        if not robots.can_fetch(USER_AGENT, base):
            raise SiteBloqueadoError("robots.txt do site não permite leitura automatizada")
        fila = [base] + [urljoin(base, c) for c in CAMINHOS_CONTATO]
        vistos: set[str] = set()
        paginas: list[PaginaLida] = []
        while fila and len(paginas) < self.max_paginas:
            url = fila.pop(0)
            if url in vistos or not robots.can_fetch(USER_AGENT, url):
                continue
            vistos.add(url)
            html = self._baixar(url, base)
            if html is None:
                continue
            pagina = extrair_contatos(html, url)
            paginas.append(pagina)
            # prioriza links de "contato" encontrados na própria página
            fila[:0] = [l for l in pagina.links_internos
                        if any(p in l.lower() for p in _PALAVRAS_LINK_CONTATO) and l not in vistos]
        if not paginas:
            raise SiteBloqueadoError("Nenhuma página do site pôde ser lida")
        return ResultadoSite(base, paginas)
