"""Regras puras: normalização de contatos, extração de contatos de HTML e links de busca manual."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

PROVEDORES_GENERICOS = frozenset({
    "gmail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br", "live.com", "msn.com",
    "yahoo.com", "yahoo.com.br", "ymail.com", "icloud.com", "me.com", "bol.com.br", "uol.com.br",
    "terra.com.br", "ig.com.br", "globo.com", "globomail.com", "r7.com", "zipmail.com.br", "oi.com.br",
    "protonmail.com", "proton.me", "aol.com", "gmx.com", "mail.com",
})
REDES_SOCIAIS = ("instagram.com", "facebook.com", "linkedin.com", "tiktok.com", "youtube.com", "x.com", "twitter.com")
PALAVRAS_IGNORADAS = frozenset({
    "ltda", "eireli", "epp", "me", "mei", "sa", "s/a", "cia", "comercio", "servicos", "industria",
    "empresa", "brasil", "do", "da", "de", "dos", "das", "e", "em", "com", "para", "limitada", "sociedade",
    "individual", "unipessoal", "holding", "participacoes", "grupo", "solucoes",
})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TELEFONE = re.compile(r"(?:\+?55[\s.-]?)?\(?\b([1-9]{2})\)?[\s.-]?(9?\d{4})[\s.-]?(\d{4})\b")
_EXTENSOES_FALSO_EMAIL = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


@dataclass(frozen=True)
class ContatoEncontrado:
    tipo: str
    valor: str
    valor_normalizado: str
    fonte_url: str


# ---------------------------------------------------------------- normalização
def normalizar_email(valor: str) -> str | None:
    texto = valor.strip().strip(".,;:").lower()
    if texto.startswith("mailto:"):
        texto = texto[7:].split("?")[0]
    if not _EMAIL.fullmatch(texto) or texto.endswith(_EXTENSOES_FALSO_EMAIL):
        return None
    return texto


def normalizar_telefone(valor: str) -> str | None:
    """Telefone brasileiro com DDD -> só dígitos (10 ou 11). Aceita +55 e máscaras."""
    digitos = re.sub(r"\D", "", valor)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if digitos.startswith("0") and len(digitos) in (11, 12):  # 0 + DDD
        digitos = digitos[1:]
    if len(digitos) not in (10, 11) or digitos[0] == "0" or digitos[1] == "0":
        return None
    if len(digitos) == 11 and digitos[2] != "9":
        return None
    if len(set(digitos[2:])) == 1:  # 0000-0000, 9999-9999
        return None
    return digitos


def formatar_telefone(digitos: str) -> str:
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"


def normalizar_url(valor: str) -> str | None:
    texto = valor.strip()
    if not texto:
        return None
    if not re.match(r"^https?://", texto, re.IGNORECASE):
        texto = "https://" + texto
    partes = urlparse(texto)
    if not partes.hostname or "." not in partes.hostname:
        return None
    host = partes.hostname.lower().removeprefix("www.")
    caminho = partes.path.rstrip("/")
    return f"{host}{caminho}"


def dominio(url_ou_host: str) -> str:
    texto = url_ou_host if "://" in url_ou_host else "https://" + url_ou_host
    return (urlparse(texto).hostname or "").lower().removeprefix("www.")


def dominio_do_email(email: str | None) -> str | None:
    """Domínio do e-mail da Receita, se não for de provedor genérico (gmail, hotmail...)."""
    if not email or "@" not in email:
        return None
    dom = email.strip().lower().rsplit("@", 1)[1]
    if dom in PROVEDORES_GENERICOS or "." not in dom:
        return None
    return dom


def mesmo_dominio(host: str, base: str) -> bool:
    host, base = dominio(host), dominio(base)
    return host == base or host.endswith("." + base)


# ---------------------------------------------------------------- nome da empresa
def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def tokens_nome(*nomes: str | None) -> set[str]:
    """Palavras distintivas do nome (>= 4 letras, sem termos genéricos como LTDA, COMERCIO)."""
    tokens: set[str] = set()
    for nome in nomes:
        for palavra in re.findall(r"[a-z0-9]+", _sem_acento(nome or "")):
            if len(palavra) >= 4 and palavra not in PALAVRAS_IGNORADAS and not palavra.isdigit():
                tokens.add(palavra)
    return tokens


def nome_aparece(texto: str, tokens: set[str]) -> bool:
    if not tokens:
        return False
    normalizado = _sem_acento(texto)
    return any(t in normalizado for t in tokens)


# ---------------------------------------------------------------- extração de HTML
class _Coletor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.textos: list[str] = []
        self.titulo = ""
        self._no_titulo = False
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._ignorar += 1
        if tag == "title":
            self._no_titulo = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._ignorar:
            self._ignorar -= 1
        if tag == "title":
            self._no_titulo = False

    def handle_data(self, data: str) -> None:
        if self._no_titulo:
            self.titulo += data
        if not self._ignorar:
            self.textos.append(data)


@dataclass(frozen=True)
class PaginaLida:
    url: str
    titulo: str
    texto: str
    contatos: list[ContatoEncontrado]
    links_internos: list[str]


def extrair_contatos(html: str, url: str, limite_por_tipo: int = 5) -> PaginaLida:
    coletor = _Coletor()
    coletor.feed(html)
    texto = " ".join(t.strip() for t in coletor.textos if t.strip())
    achados: dict[tuple[str, str], ContatoEncontrado] = {}

    def add(tipo: str, valor: str, normalizado: str | None) -> None:
        if normalizado and sum(1 for k in achados if k[0] == tipo) < limite_por_tipo:
            achados.setdefault((tipo, normalizado), ContatoEncontrado(tipo, valor, normalizado, url))

    internos: list[str] = []
    for href in coletor.links:
        baixo = href.lower()
        if baixo.startswith("mailto:"):
            email = normalizar_email(href)
            add("email", email or "", email)
        elif baixo.startswith("tel:"):
            tel = normalizar_telefone(href[4:])
            add("telefone", formatar_telefone(tel) if tel else "", tel)
        elif "wa.me/" in baixo or "api.whatsapp.com" in baixo:
            numero = re.search(r"(?:wa\.me/|phone=)(\d{10,13})", baixo)
            tel = normalizar_telefone(numero.group(1)) if numero else None
            add("telefone", f"{formatar_telefone(tel)} (WhatsApp)" if tel else "", tel)
        elif any(rede in dominio(href) for rede in REDES_SOCIAIS) and baixo.startswith("http"):
            norm = normalizar_url(href)
            add("rede_social", href, norm)
        elif baixo.startswith(("http", "/")) or not re.match(r"^[a-z]+:", baixo):
            completo = urljoin(url, href)
            if mesmo_dominio(completo, url):
                internos.append(completo.split("#")[0])

    for email in _EMAIL.findall(texto):
        normalizado = normalizar_email(email)
        add("email", normalizado or "", normalizado)
    for ddd, meio, fim in _TELEFONE.findall(texto):
        tel = normalizar_telefone(ddd + meio + fim)
        add("telefone", formatar_telefone(tel) if tel else "", tel)

    return PaginaLida(url, coletor.titulo.strip(), texto, list(achados.values()), list(dict.fromkeys(internos)))


# ---------------------------------------------------------------- busca manual assistida
def links_busca_manual(razao_social: str | None, nome_fantasia: str | None, municipio: str | None, uf: str | None) -> list[tuple[str, str]]:
    """Links prontos do Google para o usuário abrir no navegador (nenhuma requisição é feita pela ferramenta)."""
    local = " ".join(p for p in (municipio, uf) if p)
    nomes = [n for n in dict.fromkeys([nome_fantasia, razao_social]) if n]
    links: list[tuple[str, str]] = []
    for nome in nomes:
        links.append((f"{nome} – telefone", f'"{nome}" {local} telefone'))
        links.append((f"{nome} – site e e-mail", f'"{nome}" {local} contato e-mail site'))
    if razao_social:
        links.append(("Perfis em redes sociais", f'"{nome_fantasia or razao_social}" {local} instagram OR facebook OR linkedin'))
    return [(rotulo, "https://www.google.com/search?q=" + quote_plus(consulta)) for rotulo, consulta in links]
