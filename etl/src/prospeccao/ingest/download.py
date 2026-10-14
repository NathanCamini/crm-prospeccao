"""Download dos Dados Abertos do CNPJ a partir do compartilhamento Nextcloud da Receita.

A Receita publica os arquivos em um compartilhamento público do Nextcloud, com uma
pasta por mês de referência (ex.: 2026-09). A listagem usa WebDAV (PROPFIND) do
compartilhamento público; o download usa GET com retomada (Range) e verificação
de tamanho. O SHA-256 de cada arquivo é calculado e gravado no manifest da bronze.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

import requests

from prospeccao.config import Config
from prospeccao.ingest.layout import verificar_conjunto_completo

log = logging.getLogger("prospeccao.download")

_PROPFIND_CORPO = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    "</d:prop></d:propfind>"
)
_MES = re.compile(r"\d{4}-\d{2}")
_BLOCO = 1024 * 1024
_TENTATIVAS = 5


class FonteIndisponivelError(RuntimeError):
    """A fonte da Receita não respondeu como esperado."""


@dataclass(frozen=True)
class ArquivoRemoto:
    nome: str
    tamanho: int | None
    eh_pasta: bool


def _endpoints_webdav(cfg: Config) -> list[str]:
    """Rotas WebDAV de compartilhamento público (Nextcloud novo e antigo)."""
    return [
        f"{cfg.receita_base_url}/public.php/dav/files/{cfg.receita_share_token}",
        f"{cfg.receita_base_url}/public.php/webdav",
    ]


def interpretar_propfind(xml_texto: str, caminho_pedido: str) -> list[ArquivoRemoto]:
    """Converte a resposta PROPFIND em lista de itens, ignorando a própria pasta."""
    ns = {"d": "DAV:"}
    raiz = ET.fromstring(xml_texto)
    itens: list[ArquivoRemoto] = []
    alvo = caminho_pedido.rstrip("/")
    for resp in raiz.findall("d:response", ns):
        href = unquote(resp.findtext("d:href", default="", namespaces=ns)).rstrip("/")
        if href.endswith(alvo) or not href:
            continue
        nome = href.rsplit("/", 1)[-1]
        eh_pasta = resp.find(".//d:resourcetype/d:collection", ns) is not None
        tamanho_txt = resp.findtext(".//d:getcontentlength", default="", namespaces=ns)
        tamanho = int(tamanho_txt) if tamanho_txt.isdigit() else None
        itens.append(ArquivoRemoto(nome=nome, tamanho=tamanho, eh_pasta=eh_pasta))
    return itens


class ClienteReceita:
    """Cliente mínimo do compartilhamento público da Receita."""

    def __init__(self, cfg: Config, sessao: requests.Session | None = None) -> None:
        self.cfg = cfg
        self.sessao = sessao or requests.Session()
        self.sessao.auth = (cfg.receita_share_token, "")
        self.sessao.headers["User-Agent"] = "prospeccao-cnpj/0.1 (+dados abertos)"
        self._base: str | None = None

    def _propfind(self, caminho: str) -> list[ArquivoRemoto]:
        erros: list[str] = []
        bases = [self._base] if self._base else _endpoints_webdav(self.cfg)
        for base in bases:
            url = base + quote(caminho)
            try:
                resp = self.sessao.request(
                    "PROPFIND", url, data=_PROPFIND_CORPO, headers={"Depth": "1"}, timeout=60
                )
            except requests.RequestException as exc:
                erros.append(f"{url}: {exc}")
                continue
            if resp.status_code == 207:
                self._base = base
                return interpretar_propfind(resp.text, caminho)
            erros.append(f"{url}: HTTP {resp.status_code}")
        raise FonteIndisponivelError(
            "Não foi possível listar o compartilhamento da Receita. Verifique RECEITA_BASE_URL, "
            "RECEITA_SHARE_TOKEN e RECEITA_SHARE_PATH no .env e se o endereço mudou. Detalhes: "
            + "; ".join(erros)
        )

    def listar_meses(self) -> list[str]:
        itens = self._propfind(self.cfg.receita_share_path + "/")
        meses = sorted(i.nome for i in itens if i.eh_pasta and _MES.fullmatch(i.nome))
        if not meses:
            raise FonteIndisponivelError(
                f"Nenhuma pasta no formato AAAA-MM em {self.cfg.receita_share_path}. "
                "A estrutura do compartilhamento pode ter mudado."
            )
        return meses

    def listar_arquivos(self, mes: str) -> list[ArquivoRemoto]:
        itens = self._propfind(f"{self.cfg.receita_share_path}/{mes}/")
        zips = [i for i in itens if not i.eh_pasta and i.nome.lower().endswith(".zip")]
        faltando = verificar_conjunto_completo([z.nome for z in zips])
        if faltando:
            raise FonteIndisponivelError(
                f"Mês {mes} incompleto no servidor da Receita. Tabelas ausentes: {', '.join(faltando)}."
            )
        return zips

    def url_arquivo(self, mes: str, nome: str) -> str:
        assert self._base, "listar_arquivos deve ser chamado antes"
        return self._base + quote(f"{self.cfg.receita_share_path}/{mes}/{nome}")

    def baixar(self, url: str, destino: Path, tamanho_esperado: int | None) -> None:
        """Baixa com retomada. Grava em .part e só renomeia se o tamanho conferir."""
        parcial = destino.with_suffix(destino.suffix + ".part")
        for tentativa in range(1, _TENTATIVAS + 1):
            ja_baixado = parcial.stat().st_size if parcial.exists() else 0
            cabecalhos = {"Range": f"bytes={ja_baixado}-"} if ja_baixado else {}
            try:
                with self.sessao.get(url, headers=cabecalhos, stream=True, timeout=120) as resp:
                    if resp.status_code == 416:
                        break  # já completo
                    resp.raise_for_status()
                    modo = "ab" if resp.status_code == 206 else "wb"
                    with parcial.open(modo) as arq:
                        for bloco in resp.iter_content(_BLOCO):
                            arq.write(bloco)
                break
            except requests.RequestException as exc:
                espera = 2**tentativa
                log.warning("Falha ao baixar %s (tentativa %d): %s. Nova tentativa em %ds",
                            destino.name, tentativa, exc, espera)
                if tentativa == _TENTATIVAS:
                    raise FonteIndisponivelError(f"Download falhou: {url}") from exc
                time.sleep(espera)
        tamanho = parcial.stat().st_size
        if tamanho_esperado is not None and tamanho != tamanho_esperado:
            raise FonteIndisponivelError(
                f"Tamanho divergente em {destino.name}: esperado {tamanho_esperado}, recebido {tamanho}."
            )
        parcial.replace(destino)


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as arq:
        while bloco := arq.read(_BLOCO):
            h.update(bloco)
    return h.hexdigest()


def baixar_mes(cfg: Config, mes: str | None = None, cliente: ClienteReceita | None = None) -> tuple[str, Path]:
    """Baixa todos os .zip do mês (o mais recente se `mes` for None). Devolve (mes, pasta_zip)."""
    cliente = cliente or ClienteReceita(cfg)
    meses = cliente.listar_meses()
    mes = mes or meses[-1]
    if mes not in meses:
        raise FonteIndisponivelError(f"Mês {mes} não encontrado. Disponíveis: {', '.join(meses[-6:])}")
    arquivos = cliente.listar_arquivos(mes)
    pasta = cfg.bronze_dir / mes / "zip"
    pasta.mkdir(parents=True, exist_ok=True)
    for arq in arquivos:
        destino = pasta / arq.nome
        if destino.exists() and (arq.tamanho is None or destino.stat().st_size == arq.tamanho):
            log.info("Já baixado: %s", arq.nome)
            continue
        log.info("Baixando %s (%s bytes)", arq.nome, arq.tamanho)
        cliente.baixar(cliente.url_arquivo(mes, arq.nome), destino, arq.tamanho)
    return mes, pasta
