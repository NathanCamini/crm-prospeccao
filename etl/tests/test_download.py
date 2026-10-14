from pathlib import Path

import pytest

from prospeccao.config import Config
from prospeccao.ingest.download import ClienteReceita, FonteIndisponivelError, interpretar_propfind

RAIZ = "/Dados/Cadastros/CNPJ"


def _resp(href: str, pasta: bool, tamanho: int | None = None) -> str:
    tipo = "<d:collection/>" if pasta else ""
    tam = f"<d:getcontentlength>{tamanho}</d:getcontentlength>" if tamanho is not None else ""
    return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{tipo}</d:resourcetype>{tam}</d:prop></d:propstat></d:response>"


def _xml(*respostas: str) -> str:
    return '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(respostas) + "</d:multistatus>"


XML_MESES = _xml(
    _resp(f"/public.php/webdav{RAIZ}/", True),
    _resp(f"/public.php/webdav{RAIZ}/2026-08/", True),
    _resp(f"/public.php/webdav{RAIZ}/2026-09/", True),
    _resp(f"/public.php/webdav{RAIZ}/temp/", True),
    _resp(f"/public.php/webdav{RAIZ}/LEIAME.pdf", False, 100),
)


class _Resposta:
    def __init__(self, status: int, texto: str) -> None:
        self.status_code, self.text = status, texto


class _SessaoFalsa:
    def __init__(self, respostas: dict[str, str]) -> None:
        self.respostas, self.headers, self.auth = respostas, {}, None

    def request(self, metodo: str, url: str, **_: object) -> _Resposta:
        for sufixo, xml in self.respostas.items():
            if url.endswith(sufixo):
                return _Resposta(207, xml)
        return _Resposta(404, "")


def _cfg(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path)


def test_interpretar_propfind_ignora_a_propria_pasta() -> None:
    itens = interpretar_propfind(XML_MESES, RAIZ + "/")
    assert {i.nome for i in itens} == {"2026-08", "2026-09", "temp", "LEIAME.pdf"}
    assert next(i for i in itens if i.nome == "LEIAME.pdf").tamanho == 100


def test_listar_meses_filtra_formato_aaaa_mm(tmp_path: Path) -> None:
    cliente = ClienteReceita(_cfg(tmp_path), _SessaoFalsa({"CNPJ/": XML_MESES}))
    assert cliente.listar_meses() == ["2026-08", "2026-09"]


def test_mes_incompleto_falha_com_tabelas_ausentes(tmp_path: Path) -> None:
    xml_mes = _xml(_resp(f"/x{RAIZ}/2026-09/Empresas0.zip", False, 10))
    cliente = ClienteReceita(_cfg(tmp_path), _SessaoFalsa({"2026-09/": xml_mes}))
    with pytest.raises(FonteIndisponivelError, match="estabelecimentos"):
        cliente.listar_arquivos("2026-09")


def test_fonte_fora_do_ar_tem_mensagem_clara(tmp_path: Path) -> None:
    cliente = ClienteReceita(_cfg(tmp_path), _SessaoFalsa({}))
    with pytest.raises(FonteIndisponivelError, match="RECEITA_SHARE_TOKEN"):
        cliente.listar_meses()
