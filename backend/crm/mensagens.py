"""Modelos de mensagem, normalização de contatos e links de ação por clique."""

from __future__ import annotations

import re
from urllib.parse import quote

VARIAVEIS = ("nome_fantasia", "razao_social", "municipio", "uf", "bairro", "produto", "marca", "vendedor")
_MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def titulo(texto: str | None) -> str:
    if not texto:
        return ""
    palavras = texto.lower().split()
    return " ".join(p if i and p in _MINUSCULAS else p.capitalize() for i, p in enumerate(palavras))


def _meio_de_frase(texto: str) -> str:
    """'Kit de amenities' vira 'kit de amenities'; siglas ('LED…') ficam como estão."""
    if len(texto) > 1 and texto[0].isupper() and not texto[1].isupper():
        return texto[0].lower() + texto[1:]
    return texto


def contexto(lead: dict, vendedor: str) -> dict[str, str]:
    c = lead.get("classificacao") or {}
    return {
        "nome_fantasia": titulo(lead.get("nome_fantasia") or lead.get("razao_social")),
        "razao_social": lead.get("razao_social") or "",
        "municipio": titulo(lead.get("municipio")),
        "uf": lead.get("uf") or "",
        "bairro": titulo(lead.get("bairro")),
        "produto": _meio_de_frase(((c or {}).get("produto_sugerido") or "nossos produtos").rstrip(".")),
        "marca": c.get("marca") or "",
        "vendedor": vendedor,
    }


def renderizar(texto: str | None, ctx: dict[str, str]) -> str:
    if not texto:
        return ""
    return re.sub(r"\{(\w+)\}", lambda m: ctx.get(m.group(1), m.group(0)) if m.group(1) in VARIAVEIS else m.group(0), texto)


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def normalizar(tipo: str, valor: str) -> str:
    valor = (valor or "").strip()
    if tipo == "email":
        return valor.lower()
    if tipo == "telefone":
        d = so_digitos(valor)
        return d[2:] if d.startswith("55") and len(d) >= 12 else d
    if tipo == "cnpj":
        return re.sub(r"[^0-9A-Za-z]", "", valor).upper()
    if tipo == "dominio":
        v = valor.lower()
        v = re.sub(r"^https?://", "", v).split("/")[0]
        v = v.split("@")[-1]
        return v[4:] if v.startswith("www.") else v
    raise ValueError(f"tipo inválido: {tipo}")


def tipo_do_canal(canal: str) -> str | None:
    return {"email": "email", "whatsapp": "telefone", "telefone": "telefone"}.get(canal)


def telefone_internacional(destino: str) -> str:
    d = so_digitos(destino)
    if not d.startswith("55") or len(d) <= 11:
        d = "55" + d
    return d


def montar_url(canal: str, destino: str, texto: str, assunto: str | None) -> str | None:
    if canal == "whatsapp":
        return f"https://wa.me/{telefone_internacional(destino)}?text={quote(texto)}"
    if canal == "email":
        return f"mailto:{destino}?subject={quote(assunto or '')}&body={quote(texto)}"
    if canal == "telefone":
        return f"tel:+{telefone_internacional(destino)}"
    if canal == "formulario":
        return destino if re.match(r"^https?://", destino) else None
    return None


def dv_cnpj(base12: str) -> str:
    """Dígitos verificadores do CNPJ, inclusive alfanumérico (valor = código ASCII − 48)."""
    valores = [ord(c) - 48 for c in base12]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    r = sum(v * p for v, p in zip(valores, pesos1)) % 11
    d1 = 0 if r < 2 else 11 - r
    r = sum(v * p for v, p in zip(valores + [d1], [6, *pesos1])) % 11
    d2 = 0 if r < 2 else 11 - r
    return f"{d1}{d2}"


def cnpj_valido(cnpj: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj)) and dv_cnpj(cnpj[:12]) == cnpj[12:]


def chaves_supressao(lead: dict) -> set[tuple[str, str]]:
    """Todas as chaves que, se suprimidas, bloqueiam contato com a empresa."""
    chaves: set[tuple[str, str]] = {("cnpj", lead["cnpj"])} if lead.get("cnpj") else set()
    receita = lead.get("contato_receita") or {}
    emails = [receita.get("email")]
    telefones = [
        (receita.get("ddd_1") or "") + (receita.get("telefone_1") or ""),
        (receita.get("ddd_2") or "") + (receita.get("telefone_2") or ""),
    ]
    for c in lead.get("contatos") or []:
        if c["tipo"] == "email":
            emails.append(c["valor"])
        elif c["tipo"] in ("telefone", "whatsapp"):
            telefones.append(c["valor"])
    for e in filter(None, emails):
        chaves.add(("email", normalizar("email", e)))
        chaves.add(("dominio", normalizar("dominio", e)))
    for t in telefones:
        if so_digitos(t):
            chaves.add(("telefone", normalizar("telefone", t)))
    return chaves


def chaves_destino(canal: str, destino: str) -> set[tuple[str, str]]:
    tipo = tipo_do_canal(canal)
    if tipo == "email":
        return {("email", normalizar("email", destino)), ("dominio", normalizar("dominio", destino))}
    if tipo == "telefone":
        return {("telefone", normalizar("telefone", destino))}
    if canal == "formulario":
        return {("dominio", normalizar("dominio", destino))}
    return set()
