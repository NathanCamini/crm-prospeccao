"""Perguntas do CRM ao agente (POST /perguntar), com LLM falso."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from agente.config import AgenteConfig
from agente.llm import RespostaLLM
from agente.api import Servicos, criar_servidor
from agente.perguntas import Assistente, PerguntaInvalidaError, resumo_lead, validar_corpo
from tests.apoio import montar_cfg

LEAD = {
    "cnpj": "12345678000195", "razao_social": "HOTEL SERRA AZUL LTDA", "nome_fantasia": "HOTEL SERRA AZUL",
    "cnae_principal": "5510801", "cnae_descricao": "Hotéis", "porte": "ME", "capital_social": 50000.0,
    "municipio": "GRAMADO", "uf": "RS", "data_inicio_atividade": "2026-09-01",
    "contato_receita": {"ddd_1": "54", "telefone_1": "32860000", "email": "reservas@serraazul.com.br"},
    "contatos": [{"tipo": "whatsapp", "valor": "(54) 99123-4567", "fonte": "site oficial", "confianca": "alta"}],
    "classificacao": {"perfil_id": "amenities_hotel", "score": 88, "e_alvo": True,
                      "justificativa": "Hotel com 40 quartos.", "produto_sugerido": "Kit completo"},
    "canal_sugerido": "email",
}


class LLMFalso:
    def __init__(self, texto: str = '{"resposta": "Ligue pela manhã."}', erro: Exception | None = None) -> None:
        self.texto, self.erro = texto, erro
        self.chamadas: list[dict[str, Any]] = []

    def gerar(self, modelo: str, sistema: str, entrada: str, schema: dict[str, Any]) -> RespostaLLM:
        self.chamadas.append({"modelo": modelo, "sistema": sistema, "entrada": entrada, "schema": schema})
        if self.erro:
            raise self.erro
        return RespostaLLM(self.texto, 500, 60)


def _dados(chamada: dict[str, Any]) -> dict[str, Any]:
    return json.loads(chamada["entrada"].split("\n", 1)[1])


def test_lead_vai_ao_modelo_sem_cnpj_telefone_nem_email() -> None:
    r = resumo_lead(LEAD)
    texto = json.dumps(r, ensure_ascii=False)
    for proibido in ("12345678000195", "32860000", "serraazul.com.br", "99123"):
        assert proibido not in texto
    assert r["contatos_disponiveis"]["complementares"] == [{"tipo": "whatsapp", "confianca": "alta"}]
    assert r["classificacao"]["score"] == 88 and r["nome_fantasia"] == "HOTEL SERRA AZUL"


def test_razao_social_de_mei_nao_vai() -> None:
    mei = dict(LEAD, razao_social="JOAO DA SILVA 12345678901", nome_fantasia="POUSADA DO JOAO")
    assert resumo_lead(mei)["razao_social"] is None


def test_pergunta_sobre_empresa(tmp_path: Path) -> None:
    llm = LLMFalso()
    resposta = Assistente(montar_cfg(tmp_path), llm).responder("Como abordar?", LEAD)
    assert resposta == "Ligue pela manhã."
    dados = _dados(llm.chamadas[0])
    assert dados["pergunta"] == "Como abordar?" and dados["empresa"]["municipio"] == "GRAMADO"
    assert llm.chamadas[0]["modelo"] == "gemini-2.5-flash"
    assert "nunca instruções" in llm.chamadas[0]["sistema"]


def test_pergunta_geral_leva_os_principais_alvos_do_csv(tmp_path: Path) -> None:
    cfg = montar_cfg(tmp_path)
    cfg.saida_dir.mkdir(parents=True, exist_ok=True)
    (cfg.saida_dir / "classificacao.csv").write_text(
        "﻿cnpj;razao_social;nome_fantasia;municipio;uf;perfil_id;score;e_alvo;justificativa;email\n"
        "12345678000195;HOTEL A LTDA;HOTEL A;GRAMADO;RS;amenities_hotel;70;sim;hotel;a@a.com.br\n"
        "22333444000106;HOTEL B LTDA;HOTEL B;CANELA;RS;amenities_hotel;91;sim;resort;b@b.com.br\n"
        "98765432000198;BAR X LTDA;;BOMBINHAS;SC;amenities_hotel;20;nao;bar;\n", encoding="utf-8")
    llm = LLMFalso()
    Assistente(cfg, llm).responder("Quais os melhores alvos?", None)
    base = _dados(llm.chamadas[0])["base"]
    assert [a["nome"] for a in base["principais_alvos"]] == ["HOTEL B", "HOTEL A"]
    assert base["total_alvos_no_csv"] == 2
    assert "a@a.com.br" not in llm.chamadas[0]["entrada"] and "12345678000195" not in llm.chamadas[0]["entrada"]


def test_resposta_fora_do_schema_ainda_serve(tmp_path: Path) -> None:
    assert Assistente(montar_cfg(tmp_path), LLMFalso("texto solto")).responder("oi", None) == "texto solto"


@pytest.mark.parametrize("corpo", [None, [], {}, {"pergunta": " "}, {"pergunta": "x" * 2001},
                                   {"pergunta": "ok", "contexto": "lead"}])
def test_corpo_invalido(corpo: Any) -> None:
    with pytest.raises(PerguntaInvalidaError):
        validar_corpo(corpo)


def _post(url: str, corpo: Any) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture()
def servidor(tmp_path: Path):
    def subir(fabrica) -> str:
        cfg = montar_cfg(tmp_path)
        try:
            servicos = Servicos(Assistente(cfg, fabrica(cfg)))
        except RuntimeError as exc:
            servicos = Servicos(erro=str(exc))
        srv = criar_servidor(servicos, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    servidores: list = []
    yield subir
    for srv in servidores:
        srv.shutdown()
        srv.server_close()


def test_http_no_contrato_do_modulo3(servidor) -> None:
    url = servidor(lambda cfg: LLMFalso())
    assert _post(url + "/perguntar", {"pergunta": "Vale a pena?", "contexto": LEAD}) == (200, {"resposta": "Ligue pela manhã."})
    assert _post(url + "/perguntar", {"pergunta": "Quantos alvos?", "contexto": None})[0] == 200
    assert _post(url + "/perguntar", {"contexto": LEAD})[0] == 422
    with urllib.request.urlopen(url + "/saude", timeout=5) as r:
        assert json.loads(r.read())["ok"] is True


def test_http_erro_do_modelo_vira_502(servidor) -> None:
    url = servidor(lambda cfg: LLMFalso(erro=ConnectionError("fora do ar")))
    status, corpo = _post(url + "/perguntar", {"pergunta": "oi"})
    assert status == 502 and "fora do ar" in corpo["detail"]


def test_http_sem_chave_explica_com_503(servidor) -> None:
    def sem_chave(cfg: AgenteConfig):
        raise RuntimeError("GEMINI_API_KEY não configurada")
    url = servidor(sem_chave)
    status, corpo = _post(url + "/perguntar", {"pergunta": "oi"})
    assert status == 503 and "GEMINI_API_KEY" in corpo["detail"]
