"""Perfis descritos pelas empresas clientes: proposta, prévia, gravação, classificação na hora e API."""

from __future__ import annotations

import csv
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agente.api import Servicos, criar_servidor
from agente.armazenamento import ClassificacaoStore
from agente.llm import RespostaLLM
from agente.perfis_cliente import PerfilNaoEncontradoError, PerfisClientes, PropostaInvalidaError
from agente.perguntas import Assistente, resumo_base
from agente.servico import Classificador
from tests.apoio import A, E, EMPRESAS_PADRAO, G, MES, escrever_mes, ler_entrada, montar_cfg, resposta_padrao

DESCRICAO = "Vendo kits de amenities (sabonete e shampoo) para hotéis e pousadas de Santa Catarina."
PROPOSTA = {
    "nome": "Amenities para hotéis em SC", "produto": "Kit de amenities para hotelaria",
    "cnaes": ["5510801", "9999999"], "cnae_prefixos": ["5590", "77"], "incluir_cnaes_secundarios": False,
    "ufs": ["SC", "XX"], "municipios": [], "produtos_sugeriveis": [],
    "criterios_llm": ["É hospedagem com quartos?", "Porte indica compra recorrente?"],
    "explicacao": "Vou buscar hotéis e pousadas novos em SC.",
}


class LLM:
    """Proposta de perfil quando o schema é o da proposta; classificação (resposta padrão) nos outros casos."""

    def __init__(self, proposta: dict[str, Any] | None = None) -> None:
        self.proposta = proposta or PROPOSTA
        self.entradas: list[str] = []

    def gerar(self, modelo: str, sistema: str, entrada: str, schema: dict[str, Any]) -> RespostaLLM:
        self.entradas.append(entrada)
        if "cnae_prefixos" in schema["properties"]:
            return RespostaLLM(json.dumps(self.proposta), 2000, 200)
        if "resposta" in schema["properties"]:
            return RespostaLLM(json.dumps({"resposta": "ok"}), 100, 10)
        return RespostaLLM(json.dumps(resposta_padrao(modelo, ler_entrada(entrada))), 300, 40)


@pytest.fixture()
def ambiente(tmp_path: Path):
    cfg = montar_cfg(tmp_path)
    escrever_mes(cfg.novas_dir, EMPRESAS_PADRAO)
    store = ClassificacaoStore(cfg.banco)
    llm = LLM()
    perfis = PerfisClientes(cfg, store, llm, Classificador(cfg, store, llm, dormir=lambda s: None),
                            em_segundo_plano=False)
    return cfg, store, llm, perfis


def _csv(cfg) -> list[dict[str, str]]:
    with (cfg.saida_dir / "classificacao.csv").open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_proposta_so_com_codigos_que_existem_e_previa(ambiente) -> None:
    cfg, store, llm, perfis = ambiente
    p = perfis.propor("7", DESCRICAO)
    f = p["perfil"]["filtro_sql"]
    assert f["cnaes"] == ["5510801"] and f["cnae_prefixos"] == ["5590"] and f["ufs"] == ["SC"]
    assert p["perfil"]["dono"] == "7" and p["perfil"]["limite_candidatos"] == cfg.limite_perfil_cliente
    assert p["explicacao"].startswith("Vou buscar")
    # hotéis e alojamentos ativos de SC: pousada, hotel MEI, hotel pequeno, ibis, chalés e eventos (a baixada fica fora)
    assert p["previa"]["mes"] == MES and p["previa"]["total"] == 6 and p["previa"]["cabe"]
    nomes = {e["nome"] for e in p["previa"]["exemplos"]}
    assert "(MEI)" in nomes and "HOTELZINHO DO ZE" not in nomes      # nome de MEI é do titular
    assert {a["codigo"] for a in p["atividades"]} == {"5510801", "5590"}
    assert DESCRICAO in llm.entradas[0] and "5611201 Restaurantes" in llm.entradas[0]  # catálogo do mês
    assert store.perfis_cliente("7") == []                           # a prévia não grava nada


def test_descricao_sem_ramo_reconhecivel(ambiente) -> None:
    cfg, store, _, _ = ambiente
    perfis = PerfisClientes(cfg, store, LLM(dict(PROPOSTA, cnaes=["123"], cnae_prefixos=[])), None)
    with pytest.raises(PropostaInvalidaError, match="para quem você vende"):
        perfis.propor("7", DESCRICAO)
    with pytest.raises(PropostaInvalidaError, match="o que você vende"):
        perfis.propor("7", "curta")


def test_salvar_classifica_na_hora_e_entrega_no_csv(ambiente) -> None:
    cfg, store, _, perfis = ambiente
    proposta = perfis.propor("7", DESCRICAO)
    salvo = perfis.salvar("7", DESCRICAO, proposta["perfil"])
    assert salvo["id"].startswith("c7_amenities_para_hoteis_em_sc_") and salvo["versao"] == 1
    assert salvo["execucao"]["situacao"] == "pronto" and salvo["execucao"]["csv_atualizado"]
    assert salvo["alvos"] == 2                                       # pousada e chalés (resposta padrão do LLM falso)
    linhas = _csv(cfg)
    assert {r["cnpj"] for r in linhas if r["e_alvo"] == "sim"} == {A, G} and E in {r["cnpj"] for r in linhas}
    assert {r["perfil_nome"] for r in linhas} == {"Amenities para hotéis em SC"}
    assert [p["id"] for p in perfis.listar("7")] == [salvo["id"]]
    assert perfis.listar("8") == []                                  # outra empresa cliente não vê


def test_dono_id_versao_e_teto_definidos_pelo_agente(ambiente) -> None:
    cfg, store, _, perfis = ambiente
    adulterado = dict(perfis.propor("7", DESCRICAO)["perfil"], id="amenities_hotel", dono="8",
                      versao=99, limite_candidatos=10**6)
    salvo = perfis.salvar("7", DESCRICAO, adulterado)
    registro = store.perfil_cliente(salvo["id"])
    assert salvo["id"] != "amenities_hotel" and registro["dono"] == "7" and registro["versao"] == 1
    assert registro["perfil"]["limite_candidatos"] == cfg.limite_perfil_cliente


def test_editar_cria_versao_nova_no_mesmo_id(ambiente) -> None:
    _, store, _, perfis = ambiente
    salvo = perfis.salvar("7", DESCRICAO, perfis.propor("7", DESCRICAO)["perfil"])
    nova = perfis.propor("7", DESCRICAO + " Só alto padrão.", perfil_id=salvo["id"])
    assert nova["perfil"]["id"] == salvo["id"] and nova["perfil"]["versao"] == 2
    editado = perfis.salvar("7", DESCRICAO + " Só alto padrão.", nova["perfil"], perfil_id=salvo["id"])
    assert editado["versao"] == 2 and editado["descricao"].endswith("Só alto padrão.")
    with pytest.raises(PerfilNaoEncontradoError):
        perfis.salvar("8", DESCRICAO, nova["perfil"], perfil_id=salvo["id"])


def test_teto_de_custo(ambiente) -> None:
    cfg, store, llm, _ = ambiente
    apertado = PerfisClientes(replace(cfg, limite_perfil_cliente=2), store, llm, None, em_segundo_plano=False)
    p = apertado.propor("7", DESCRICAO)
    assert not p["previa"]["cabe"] and "limite é 2" in p["previa"]["aviso"]
    with pytest.raises(PropostaInvalidaError, match="limite é 2"):
        apertado.salvar("7", DESCRICAO, p["perfil"])


def test_desativar_tira_do_csv(ambiente) -> None:
    cfg, store, _, perfis = ambiente
    salvo = perfis.salvar("7", DESCRICAO, perfis.propor("7", DESCRICAO)["perfil"])
    with pytest.raises(PerfilNaoEncontradoError):
        perfis.desativar("8", salvo["id"])
    perfis.desativar("7", salvo["id"])
    assert perfis.listar("7") == [] and _csv(cfg) == []
    assert store.classificacoes(salvo["id"])                         # histórico fica


def test_pergunta_geral_so_ve_os_perfis_da_empresa(ambiente) -> None:
    cfg, _, _, perfis = ambiente
    salvo = perfis.salvar("7", DESCRICAO, perfis.propor("7", DESCRICAO)["perfil"])
    assert len(resumo_base(cfg, [salvo["id"]])["principais_alvos"]) == 2
    assert resumo_base(cfg, ["outro"])["principais_alvos"] == []
    assert resumo_base(cfg)["principais_alvos"] == []               # sem lista: nunca perfis de clientes


# ---------------------------------------------------------------- HTTP
def _chamar(url: str, metodo: str = "GET", corpo: Any = None) -> tuple[int, Any]:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture()
def servidor():
    ativos: list = []

    def subir(servicos: Servicos) -> str:
        srv = criar_servidor(servicos, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ativos.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield subir
    for srv in ativos:
        srv.shutdown()
        srv.server_close()


def test_api_de_perfis(ambiente, servidor) -> None:
    cfg, _, llm, perfis = ambiente
    url = servidor(Servicos(Assistente(cfg, llm), perfis))
    status, proposta = _chamar(url + "/perfis/sugerir", "POST", {"dono": "7", "descricao": DESCRICAO})
    assert status == 200 and proposta["previa"]["total"] == 6
    status, salvo = _chamar(url + "/perfis", "POST", {"dono": "7", "descricao": DESCRICAO,
                                                      "perfil": proposta["perfil"]})
    assert status == 202 and salvo["execucao"]["situacao"] == "pronto"
    assert [p["id"] for p in _chamar(url + "/perfis?dono=7")[1]] == [salvo["id"]]
    assert _chamar(url + f"/perfis/{salvo['id']}?dono=8", "DELETE")[0] == 404
    assert _chamar(url + f"/perfis/{salvo['id']}?dono=7", "DELETE") == (200, {"ok": True})
    assert _chamar(url + "/perfis/sugerir", "POST", {"dono": "7", "descricao": "x"})[0] == 422
    assert _chamar(url + "/perfis/sugerir", "POST", {"descricao": DESCRICAO})[0] == 422
    assert _chamar(url + "/perguntar", "POST", {"pergunta": "oi", "perfis": [salvo["id"]]}) == (200, {"resposta": "ok"})


def test_api_sem_chave(servidor) -> None:
    url = servidor(Servicos(erro="GEMINI_API_KEY não configurada"))
    assert _chamar(url + "/saude")[1]["ok"] is False
    status, corpo = _chamar(url + "/perfis/sugerir", "POST", {"dono": "7", "descricao": DESCRICAO})
    assert status == 503 and "GEMINI_API_KEY" in corpo["detail"]
