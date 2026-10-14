"""Enriquecimento de contatos (serviço do módulo 1) e releitura do CSV do agente."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("prospeccao.enrich.servico", reason="instale o módulo 1: pip install --no-deps -e ../etl")

from prospeccao.app_store import AppStore  # noqa: E402
from prospeccao.enrich.api_busca import ResultadoBusca  # noqa: E402
from prospeccao.enrich.extracao import extrair_contatos  # noqa: E402
from prospeccao.enrich.servico import ServicoContatos  # noqa: E402
from prospeccao.enrich.site import ResultadoSite, SiteBloqueadoError  # noqa: E402

from modulo3.dados import Fonte  # noqa: E402
from modulo3.enriquecimento import ConfigEnriquecimento, Enriquecedor, contato_do_contrato  # noqa: E402
from modulo3.main import criar_app  # noqa: E402

CABECALHO = "cnpj;razao_social;nome_fantasia;uf;municipio;perfil_id;score;e_alvo;email;ddd_1;telefone_1\n"
HOTEL = "12345678000195;HOTEL SERRA AZUL LTDA;HOTEL SERRA AZUL;RS;GRAMADO;amenities_hotel;88;sim;reservas@serraazul.com.br;54;32860000\n"
POUSADA = "11222333000181;POUSADA DO VALE LTDA;POUSADA DO VALE;RS;CANELA;amenities_hotel;75;sim;pousadadovale@gmail.com;;\n"
BAR = "98765432000198;BAR X LTDA;;SC;BOMBINHAS;amenities_hotel;20;nao;contato@barx.com.br;;\n"

PAGINA = """<html><head><title>Hotel Serra Azul - Gramado</title></head><body>
<p>Hotel Serra Azul, no centro de Gramado.</p>
<a href="tel:+555432860000">ligar</a>
<a href="https://wa.me/5554991234567">WhatsApp</a>
<a href="mailto:comercial@serraazul.com.br">comercial@serraazul.com.br</a>
</body></html>"""


class LeitorFalso:
    def __init__(self, paginas: dict[str, str]) -> None:
        self.paginas = paginas
        self.lidos: list[str] = []

    def ler(self, url: str) -> ResultadoSite:
        self.lidos.append(url)
        if url not in self.paginas:
            raise SiteBloqueadoError(f"{url} fora do ar")
        return ResultadoSite(url, [extrair_contatos(self.paginas[url], url)])


class ProvedorFalso:
    nome = "Brave Search API"

    def __init__(self, resultados: dict[str, list[ResultadoBusca]]) -> None:
        self.resultados = resultados
        self.consultas: list[str] = []

    def buscar(self, consulta: str, quantidade: int = 5) -> list[ResultadoBusca]:
        self.consultas.append(consulta)
        return next((r for chave, r in self.resultados.items() if chave in consulta), [])


def _enriquecedor(tmp: Path, leitor: LeitorFalso, provedor: ProvedorFalso | None = None) -> Enriquecedor:
    cfg = ConfigEnriquecimento(dados_dir=tmp, minutos=0)
    store = AppStore(tmp / "modulo3.sqlite")
    return Enriquecedor(cfg, ServicoContatos(store, leitor, provedor))


def _csv(tmp: Path, *linhas: str) -> Path:
    arquivo = tmp / "classificacao.csv"
    arquivo.write_text(CABECALHO + "".join(linhas), encoding="utf-8")
    return arquivo


def test_whatsapp_lido_no_site_vira_tipo_whatsapp() -> None:
    c = contato_do_contrato({"tipo": "telefone", "valor": "(54) 99123-4567 (WhatsApp)", "fonte_nome": "site oficial",
                             "fonte_url": "https://x.com.br", "confianca": "alta", "observacao": None})
    assert (c.tipo, c.valor, c.fonte) == ("whatsapp", "(54) 99123-4567", "site oficial")


def test_busca_so_alvos_e_respeita_o_cache(tmp_path: Path) -> None:
    leitor = LeitorFalso({"https://serraazul.com.br": PAGINA})
    enr = _enriquecedor(tmp_path, leitor)
    leads = Fonte(_csv(tmp_path, HOTEL, POUSADA, BAR), enr.contatos).repo().leads

    r = enr.executar(leads)
    assert leitor.lidos == ["https://serraazul.com.br"]     # gmail não tem site próprio; o bar não é alvo
    assert r["trabalhadas"] == 1 and r["alvos"] == 2 and r["contatos_novos"] >= 3

    contatos = enr.contatos(["12345678000195"])["12345678000195"]
    por_tipo = {c.tipo: c for c in contatos}
    assert por_tipo["whatsapp"].valor == "(54) 99123-4567"
    assert por_tipo["email"].valor == "comercial@serraazul.com.br"
    assert all(c.confianca == "alta" for c in contatos)      # domínio do e-mail da Receita + nome no site
    assert "(54) 3286-0000" not in {c.valor for c in contatos}  # igual ao da Receita: não repete

    leitor.lidos.clear()
    r = enr.executar(leads)
    assert leitor.lidos == [] and r["trabalhadas"] == 0       # cache de 90 dias


def test_api_indica_o_site_e_o_modulo3_le(tmp_path: Path) -> None:
    leitor = LeitorFalso({"https://pousadadovale.com.br": PAGINA.replace("Hotel Serra Azul", "Pousada do Vale")})
    provedor = ProvedorFalso({"POUSADA DO VALE": [ResultadoBusca("https://pousadadovale.com.br/",
                                                                 "Pousada do Vale - Canela")]})
    enr = _enriquecedor(tmp_path, leitor, provedor)
    leads = Fonte(_csv(tmp_path, POUSADA), enr.contatos).repo().leads
    r = enr.executar(leads)
    assert provedor.consultas and leitor.lidos == ["https://pousadadovale.com.br"]
    assert r["trabalhadas"] == 1
    tipos = {c.tipo for c in enr.contatos(["11222333000181"])["11222333000181"]}
    assert {"site", "whatsapp", "email"} <= tipos


def test_limite_por_ciclo(tmp_path: Path) -> None:
    leitor = LeitorFalso({})
    enr = _enriquecedor(tmp_path, leitor)
    outro = HOTEL.replace("12345678000195", "22333444000106").replace("serraazul", "outrohotel")
    leads = Fonte(_csv(tmp_path, HOTEL, outro), enr.contatos).repo().leads
    assert enr.executar(leads, limite=1)["trabalhadas"] == 1
    assert len(leitor.lidos) == 2  # http depois de https, só na primeira empresa


def test_fonte_rele_o_csv_e_junta_os_contatos(tmp_path: Path) -> None:
    enr = _enriquecedor(tmp_path, LeitorFalso({"https://serraazul.com.br": PAGINA}))
    arquivo = _csv(tmp_path, HOTEL)
    fonte = Fonte(arquivo, enr.contatos)
    assert len(fonte.repo().leads) == 1
    enr.executar(fonte.repo().leads)

    arquivo.write_text(CABECALHO + HOTEL + POUSADA, encoding="utf-8")
    os.utime(arquivo, (arquivo.stat().st_atime, arquivo.stat().st_mtime + 5))
    c = TestClient(criar_app(fonte=fonte, enriquecedor=enr))
    pagina = c.post("/v1/leads/busca", json={"ordenar": "nome"}).json()
    assert pagina["fonte"] == "modulo2" and pagina["total"] == 2
    hotel = c.get("/v1/leads/12345678000195").json()
    assert {x["tipo"] for x in hotel["contatos"]} >= {"whatsapp", "email", "site"}
    assert hotel["canal_sugerido"] == "email"                # e-mail de confiança alta no site
    assert hotel["contato_receita"]["telefone_1"] == "32860000"
    saude = c.get("/saude").json()
    assert saude["enriquecimento"]["empresas_com_contato"] == 1


def test_csv_ainda_inexistente_serve_zero_leads(tmp_path: Path) -> None:
    c = TestClient(criar_app(fonte=Fonte(tmp_path / "nao_existe.csv")))
    assert c.get("/saude").json()["total"] == 0
    assert c.post("/v1/leads/busca", json={}).json()["itens"] == []
