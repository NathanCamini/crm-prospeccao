"""Busca complementar de contatos: regras puras, leitura de site (servidor local) e serviço."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from prospeccao.app_store import AppStore
from prospeccao.enrich.api_busca import ResultadoBusca, site_candidato
from prospeccao.enrich.extracao import (
    dominio_do_email,
    extrair_contatos,
    links_busca_manual,
    normalizar_email,
    normalizar_telefone,
    normalizar_url,
    tokens_nome,
)
from prospeccao.enrich.servico import ServicoContatos, normalizar_manual
from prospeccao.enrich.site import LeitorSite, LimitadorFrequencia, SiteBloqueadoError


# ---------------------------------------------------------------- regras puras
@pytest.mark.parametrize(("entrada", "esperado"), [
    ("(53) 3234-5678", "5332345678"),
    ("+55 53 99123-4567", "53991234567"),
    ("053 3234 5678", "5332345678"),
    ("3234-5678", None),            # sem DDD
    ("(53) 0000-0000", None),
    ("(53) 81234-5678", None),      # 11 dígitos sem o 9
])
def test_normalizar_telefone(entrada: str, esperado: str | None) -> None:
    assert normalizar_telefone(entrada) == esperado


def test_normalizar_email_e_url() -> None:
    assert normalizar_email("mailto:Contato@Empresa.com.br?subject=oi") == "contato@empresa.com.br"
    assert normalizar_email("logo@2x.png") is None
    assert normalizar_url("https://www.Empresa.com.br/") == "empresa.com.br"
    assert normalizar_url("instagram.com/empresa/") == "instagram.com/empresa"


def test_dominio_do_email_ignora_provedores_genericos() -> None:
    assert dominio_do_email("contato@alfa-obras.com.br") == "alfa-obras.com.br"
    assert dominio_do_email("fulano@gmail.com") is None
    assert dominio_do_email(None) is None


def test_tokens_do_nome_ignoram_termos_genericos() -> None:
    assert tokens_nome("ALFA OBRAS E REFORMAS LTDA", "ALFA OBRAS") == {"alfa", "obras", "reformas"}


def test_links_de_busca_manual_sao_so_links_do_google() -> None:
    links = links_busca_manual("ALFA OBRAS LTDA", "ALFA", "CIDADE ALFA", "RS")
    assert links and all(url.startswith("https://www.google.com/search?q=") for _, url in links)
    assert "ALFA" in parse_qs(urlparse(links[0][1]).query)["q"][0]


HTML = """<html><head><title>Alfa Obras</title><script>var x='falso@script.com';</script></head>
<body><h1>Alfa Obras e Reformas</h1>
<a href="mailto:vendas@alfa-obras.com.br">e-mail</a>
<a href="tel:+555332345678">ligue</a>
<a href="https://wa.me/5553991234567">WhatsApp</a>
<a href="https://www.instagram.com/alfaobras/">Instagram</a>
<a href="/contato">Contato</a> <a href="https://outro-site.com/x">parceiro</a>
<p>Atendimento: (53) 3030-4040 ou financeiro@alfa-obras.com.br</p></body></html>"""


def test_extrair_contatos_de_html() -> None:
    pagina = extrair_contatos(HTML, "https://alfa-obras.com.br/")
    achados = {(c.tipo, c.valor_normalizado) for c in pagina.contatos}
    assert ("email", "vendas@alfa-obras.com.br") in achados
    assert ("email", "financeiro@alfa-obras.com.br") in achados
    assert ("telefone", "5332345678") in achados
    assert ("telefone", "53991234567") in achados       # WhatsApp
    assert ("telefone", "5330304040") in achados
    assert ("rede_social", "instagram.com/alfaobras") in achados
    assert not any("falso@script.com" in c.valor for c in pagina.contatos)  # ignora <script>
    assert pagina.links_internos == ["https://alfa-obras.com.br/contato"]  # não segue domínio externo
    assert pagina.titulo == "Alfa Obras"


def test_site_candidato_pula_catalogos_e_redes() -> None:
    resultados = [ResultadoBusca("https://cnpj.biz/123", "x"), ResultadoBusca("https://www.instagram.com/a", "a"),
                  ResultadoBusca("https://alfa-obras.com.br/sobre", "Alfa Obras")]
    assert site_candidato(resultados).url == "https://alfa-obras.com.br/sobre"


def test_normalizar_manual() -> None:
    assert normalizar_manual("telefone", "53 3234-5678") == ("(53) 3234-5678", "5332345678")
    with pytest.raises(ValueError):
        normalizar_manual("email", "sem-arroba")
    with pytest.raises(ValueError):
        normalizar_manual("fax", "123")


# ---------------------------------------------------------------- servidor local
class _Site:
    """Páginas servidas pelo servidor de teste: caminho -> (status, corpo, cabeçalhos)."""
    paginas: dict[str, tuple[int, str, dict[str, str]]] = {}
    acessos: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        _Site.acessos.append(self.path)
        status, corpo, cab = _Site.paginas.get(self.path, (404, "nada", {}))
        self.send_response(status)
        self.send_header("Content-Type", cab.get("Content-Type", "text/html; charset=utf-8"))
        for k, v in cab.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo.encode("utf-8"))

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture()
def servidor():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Site.paginas = {
        "/robots.txt": (200, "User-agent: *\nDisallow: /privado\n", {"Content-Type": "text/plain"}),
        "/": (200, HTML.replace("alfa-obras.com.br", "127.0.0.1"), {}),
        "/contato": (200, "<p>Comercial: (53) 98888-7777</p>", {}),
        "/privado": (200, "<p>(53) 3111-2222</p>", {}),
    }
    _Site.acessos = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _leitor() -> LeitorSite:
    return LeitorSite(LimitadorFrequencia(0.0), timeout=5)


def test_leitor_respeita_robots_e_segue_pagina_de_contato(servidor: str) -> None:
    _Site.paginas["/"] = (200, '<a href="/privado">x</a><a href="/contato">Contato</a>', {})
    resultado = _leitor().ler(servidor)
    assert "/privado" not in _Site.acessos
    assert any(c.valor_normalizado == "53988887777" for p in resultado.paginas for c in p.contatos)


def test_robots_bloqueando_tudo(servidor: str) -> None:
    _Site.paginas["/robots.txt"] = (200, "User-agent: *\nDisallow: /\n", {"Content-Type": "text/plain"})
    with pytest.raises(SiteBloqueadoError, match="robots"):
        _leitor().ler(servidor)
    assert _Site.acessos == ["/robots.txt"]


def test_robots_com_erro_de_servidor_bloqueia(servidor: str) -> None:
    _Site.paginas["/robots.txt"] = (503, "fora", {})
    with pytest.raises(SiteBloqueadoError, match="503"):
        _leitor().ler(servidor)


def test_robots_inexistente_libera(servidor: str) -> None:
    del _Site.paginas["/robots.txt"]
    assert _leitor().ler(servidor).paginas


def test_redirecionamento_para_outro_dominio_e_ignorado(servidor: str) -> None:
    _Site.paginas["/"] = (302, "", {"Location": "https://outro-dominio.example/"})
    _Site.paginas.pop("/contato")
    with pytest.raises(SiteBloqueadoError):
        _leitor().ler(servidor)


# ---------------------------------------------------------------- serviço
class _LeitorLocal(LeitorSite):
    """Troca https://127.0.0.1 pelo servidor de teste (o serviço monta a URL a partir do e-mail)."""

    def __init__(self, base: str) -> None:
        super().__init__(LimitadorFrequencia(0.0), timeout=5)
        self.base = base

    def ler(self, url_base: str):
        return super().ler(self.base if "127.0.0.1" in url_base and ":" not in url_base[8:] else url_base)


def _empresa(**extra) -> dict:
    return {"cnpj": "00012345000165", "razao_social": "ALFA OBRAS E REFORMAS LTDA", "nome_fantasia": "ALFA OBRAS",
            "municipio": "CIDADE ALFA", "uf": "RS", "email_receita": "contato@127.0.0.1",
            "ddd_receita_1": "53", "telefone_receita_1": "32345678", "ddd_receita_2": None,
            "telefone_receita_2": None, **extra}


@pytest.fixture()
def store(tmp_path) -> AppStore:
    return AppStore(tmp_path / "app.sqlite")


def test_site_do_email_da_receita_com_nome_da_empresa_e_confianca_alta(servidor, store) -> None:
    servico = ServicoContatos(store, _LeitorLocal(servidor), None)
    r = servico.buscar_site(_empresa())
    assert r.situacao == "ok" and r.novos > 0
    contatos = store.listar_contatos("00012345000165")
    assert {c["confianca"] for c in contatos} == {"alta"}
    assert all(c["fonte_url"] and c["data_busca"] and c["fonte_nome"] for c in contatos)
    # telefone igual ao da Receita (53 3234-5678) não é duplicado como complementar
    assert "5332345678" not in {c["valor_normalizado"] for c in contatos}


def test_dominio_do_email_sem_nome_no_site_vira_media(servidor, store) -> None:
    _Site.paginas["/"] = (200, "<title>Escritório Contábil Exemplo</title><p>(53) 3555-6666</p>", {})
    servico = ServicoContatos(store, _LeitorLocal(servidor), None)
    servico.buscar_site(_empresa())
    contatos = store.listar_contatos("00012345000165")
    assert {c["confianca"] for c in contatos} == {"media"}
    assert "contador" in contatos[0]["observacao"]


def test_cache_evita_repetir_e_forcar_repete(servidor, store) -> None:
    servico = ServicoContatos(store, _LeitorLocal(servidor), None)
    servico.buscar_site(_empresa())
    acessos = len(_Site.acessos)
    assert servico.buscar_site(_empresa()).situacao == "cache"
    assert len(_Site.acessos) == acessos
    assert servico.buscar_site(_empresa(), forcar=True).situacao in ("ok", "sem_resultado")
    assert len(_Site.acessos) > acessos


def test_email_generico_nao_tem_site_oficial(store) -> None:
    servico = ServicoContatos(store, _leitor(), None)
    assert servico.buscar_site(_empresa(email_receita="alfa@gmail.com")).situacao == "sem_dominio"


class _ProvedorFalso:
    nome = "API de teste"

    def __init__(self, resultados: list[ResultadoBusca]) -> None:
        self.resultados, self.chamadas = resultados, 0

    def buscar(self, consulta: str, quantidade: int = 5) -> list[ResultadoBusca]:
        self.chamadas += 1
        return self.resultados


def test_api_grava_so_o_site_com_confianca_pela_regra(store) -> None:
    prov = _ProvedorFalso([ResultadoBusca("https://cnpj.biz/x", "x"), ResultadoBusca("https://alfaobras.com.br/a", "Alfa Obras – Reformas")])
    servico = ServicoContatos(store, _leitor(), prov, limitador_api=LimitadorFrequencia(0.0))
    r = servico.buscar_api(_empresa(email_receita=None))
    assert r.situacao == "ok"
    (contato,) = store.listar_contatos("00012345000165")
    assert (contato["tipo"], contato["valor"], contato["confianca"]) == ("site", "https://alfaobras.com.br", "media")
    assert contato["fonte_nome"] == "API de teste" and contato["fonte_url"] == "https://alfaobras.com.br/a"


def test_api_sem_relacao_com_o_nome_e_baixa(store) -> None:
    prov = _ProvedorFalso([ResultadoBusca("https://qualquercoisa.com/", "Outra empresa")])
    ServicoContatos(store, _leitor(), prov, limitador_api=LimitadorFrequencia(0.0)).buscar_api(_empresa(email_receita=None))
    assert store.listar_contatos("00012345000165")[0]["confianca"] == "baixa"


def test_api_limite_diario(store) -> None:
    prov = _ProvedorFalso([])
    servico = ServicoContatos(store, _leitor(), prov, limite_diario_api=2, limitador_api=LimitadorFrequencia(0.0))
    situacoes = [servico.buscar_api(_empresa(cnpj=f"0001234500{i:04d}")).situacao for i in range(3)]
    assert situacoes == ["sem_resultado", "sem_resultado", "limite"]
    assert prov.chamadas == 2


def test_sem_api_configurada(store) -> None:
    assert ServicoContatos(store, _leitor(), None).buscar_api(_empresa()).situacao == "sem_api"


def test_registro_manual_e_duplicata(store) -> None:
    servico = ServicoContatos(store, _leitor(), None)
    assert servico.registrar_manual(_empresa(), "telefone", "(53) 3999-8888", "https://x.com", "alta", "visto no Maps")
    assert not servico.registrar_manual(_empresa(), "telefone", "53 3999 8888", None)
    (c,) = store.listar_contatos("00012345000165")
    assert (c["valor"], c["fonte_nome"], c["confianca"]) == ("(53) 3999-8888", "busca manual", "alta")
