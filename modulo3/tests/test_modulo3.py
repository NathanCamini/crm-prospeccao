from pathlib import Path

from fastapi.testclient import TestClient

from modulo3 import seed
from modulo3.dados import Repositorio, ler_csv_modulo2
from modulo3.main import criar_app


def _cliente() -> TestClient:
    repo = Repositorio(leads=seed.gerar(), fonte="simulado", perfis_nome=seed.PERFIS)
    return TestClient(criar_app(repo))


def test_cnpjs_ficticios_tem_dv_valido_e_sao_unicos():
    leads = seed.gerar()
    cnpjs = [lead.cnpj for lead in leads]
    assert len(cnpjs) == len(set(cnpjs))
    for cnpj in cnpjs:
        assert seed._dv_cnpj(cnpj[:12]) == cnpj[12:]


def test_dv_cnpj_alfanumerico_exemplo_da_receita():
    # Exemplo publicado pela Receita para o CNPJ alfanumérico: 12.ABC.345/01DE-35
    assert seed._dv_cnpj("12ABC34501DE") == "35"


def test_emails_simulados_nao_sao_entregaveis():
    for lead in seed.gerar():
        emails = [c.valor for c in lead.contatos if c.tipo == "email"]
        if lead.contato_receita.email:
            emails.append(lead.contato_receita.email)
        assert all(e.endswith(".test") for e in emails)


def test_busca_filtra_ordena_e_pagina():
    c = _cliente()
    r = c.post("/v1/leads/busca", json={"perfil_id": "amenities_hotel", "uf": ["SC"], "por_pagina": 5}).json()
    assert r["fonte"] == "simulado"
    assert r["total"] > 0 and len(r["itens"]) <= 5
    assert all(i["uf"] == "SC" and i["classificacao"]["perfil_id"] == "amenities_hotel" for i in r["itens"])
    scores = [i["classificacao"]["score"] for i in r["itens"]]
    assert scores == sorted(scores, reverse=True)


def test_busca_texto_sem_acento_e_lista_de_cnpjs():
    c = _cliente()
    todos = c.post("/v1/leads/busca", json={"apenas_cnpjs": True, "por_pagina": 1}).json()
    assert len(todos["cnpjs"]) == todos["total"]
    alvo = todos["cnpjs"][:3]
    so = c.post("/v1/leads/busca", json={"cnpjs": alvo}).json()
    assert {i["cnpj"] for i in so["itens"]} == set(alvo)
    sem = c.post("/v1/leads/busca", json={"excluir_cnpjs": alvo}).json()
    assert sem["total"] == todos["total"] - 3
    gramado = c.post("/v1/leads/busca", json={"municipio": "gramado"}).json()
    assert all(i["municipio"] == "GRAMADO" for i in gramado["itens"])


def test_ficha_e_404():
    c = _cliente()
    cnpj = c.post("/v1/leads/busca", json={}).json()["itens"][0]["cnpj"]
    assert c.get(f"/v1/leads/{cnpj}").json()["cnpj"] == cnpj
    assert c.get("/v1/leads/00000000000000").status_code == 404


def test_perguntar_sem_agente_responde_simulado():
    c = _cliente()
    cnpj = c.post("/v1/leads/busca", json={}).json()["itens"][0]["cnpj"]
    r = c.post("/v1/perguntar", json={"pergunta": "Vale a pena?", "cnpj": cnpj}).json()
    assert r["fonte"] == "simulado" and "score" in r["resposta"]
    geral = c.post("/v1/perguntar", json={"pergunta": "Quantos hotéis?"}).json()
    assert "não está conectado" in geral["resposta"]


def test_csv_do_modulo2(tmp_path: Path):
    arquivo = tmp_path / "classificacao.csv"
    arquivo.write_text(
        "cnpj;razao_social;nome_fantasia;uf;municipio;perfil_id;score;e_alvo;justificativa;email\n"
        "12.345.678/0001-95;HOTEL TESTE LTDA;HOTEL TESTE;RS;GRAMADO;amenities_hotel;88;sim;hotel com 40 quartos;contato@hotelteste.test\n"
        "98765432000198;BAR X LTDA;;SC;BOMBINHAS;amenities_hotel;20;nao;não é hospedagem;\n",
        encoding="utf-8",
    )
    leads = ler_csv_modulo2(arquivo)
    assert [lead.cnpj for lead in leads] == ["12345678000195", "98765432000198"]
    assert leads[0].classificacao.e_alvo and not leads[1].classificacao.e_alvo
    assert leads[0].contato_receita.email == "contato@hotelteste.test"
    assert leads[0].canal_sugerido == "email"


def test_perguntar_repassa_o_motivo_do_agente(monkeypatch):
    import httpx

    monkeypatch.setenv("AGENTE_URL", "http://agente:9000/perguntar")
    resposta = httpx.Response(503, json={"detail": "GEMINI_API_KEY não configurada"},
                              request=httpx.Request("POST", "http://agente:9000/perguntar"))
    monkeypatch.setattr("modulo3.main.httpx.post", lambda *a, **k: resposta)
    r = _cliente().post("/v1/perguntar", json={"pergunta": "oi"})
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.json()["detail"]
