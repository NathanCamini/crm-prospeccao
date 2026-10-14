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


class AgenteFalso:
    """Imita o servidor do agente: guarda as chamadas e devolve o que o teste pedir."""

    def __init__(self, status: int = 200, corpo=None):
        self.status, self.corpo, self.chamadas = status, corpo, []

    def __call__(self, metodo, url, **kwargs):
        import httpx

        self.chamadas.append((metodo, url, kwargs))
        corpo = self.corpo(metodo) if callable(self.corpo) else self.corpo
        return httpx.Response(self.status, json=corpo if corpo is not None else {"resposta": "real"},
                              request=httpx.Request(metodo, url))


def test_perguntar_repassa_o_motivo_do_agente(monkeypatch):
    monkeypatch.setenv("AGENTE_API", "http://agente:9000")
    monkeypatch.setattr("modulo3.main.httpx.request", AgenteFalso(503, {"detail": "GEMINI_API_KEY não configurada"}))
    r = _cliente().post("/v1/perguntar", json={"pergunta": "oi"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]


def test_perguntar_leva_os_perfis_da_empresa_e_aceita_agente_url_antigo(monkeypatch):
    monkeypatch.delenv("AGENTE_API", raising=False)
    monkeypatch.setenv("AGENTE_URL", "http://agente:9000/perguntar")
    agente = AgenteFalso()
    monkeypatch.setattr("modulo3.main.httpx.request", agente)
    r = _cliente().post("/v1/perguntar", json={"pergunta": "Quais os melhores?", "perfis": ["c7_x_ab12"]})
    assert r.json() == {"resposta": "real", "fonte": "modulo2"}
    metodo, url, kwargs = agente.chamadas[0]
    assert (metodo, url) == ("POST", "http://agente:9000/perguntar") and kwargs["json"]["perfis"] == ["c7_x_ab12"]


def test_produtos_repassados_ao_agente(monkeypatch):
    monkeypatch.setenv("AGENTE_API", "http://agente:9000")
    agente = AgenteFalso(corpo=lambda metodo: [] if metodo == "GET" else {"ok": True})
    monkeypatch.setattr("modulo3.main.httpx.request", agente)
    c = _cliente()
    assert c.post("/v1/produtos/sugerir", json={"dono": "7", "descricao": "Vendo X para Y"}).status_code == 200
    assert c.post("/v1/produtos", json={"dono": "7", "descricao": "d", "perfil": {}}).status_code == 202
    assert c.get("/v1/produtos", params={"dono": "7"}).status_code == 200
    assert c.delete("/v1/produtos/c7_x_ab12", params={"dono": "7"}).status_code == 200
    assert [(m, u.removeprefix("http://agente:9000")) for m, u, _ in agente.chamadas] == [
        ("POST", "/perfis/sugerir"), ("POST", "/perfis"), ("GET", "/perfis"), ("DELETE", "/perfis/c7_x_ab12")]
    assert agente.chamadas[2][2]["params"] == {"dono": "7"}


def test_produto_de_outro_cliente_e_descricao_vaga_passam_o_motivo(monkeypatch):
    monkeypatch.setenv("AGENTE_API", "http://agente:9000")
    monkeypatch.setattr("modulo3.main.httpx.request", AgenteFalso(422, {"detail": "Diga para quem você vende"}))
    r = _cliente().post("/v1/produtos/sugerir", json={"dono": "7", "descricao": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "Diga para quem você vende"


def test_produtos_sem_agente(monkeypatch):
    monkeypatch.delenv("AGENTE_API", raising=False)
    monkeypatch.delenv("AGENTE_URL", raising=False)
    assert _cliente().get("/v1/produtos", params={"dono": "7"}).status_code == 503


def test_nome_do_perfil_vem_do_csv(tmp_path: Path):
    from modulo3.dados import Fonte

    arquivo = tmp_path / "classificacao.csv"
    arquivo.write_text("cnpj;razao_social;perfil_id;score;e_alvo;perfil_nome\n"
                       "12345678000195;HOTEL TESTE LTDA;c7_amenities_ab12;88;sim;Amenities para hotéis\n", encoding="utf-8")
    perfis = Fonte(arquivo).repo().perfis()
    assert [(p.id, p.nome, p.total_alvos) for p in perfis] == [("c7_amenities_ab12", "Amenities para hotéis", 1)]


def test_mesmo_cnpj_em_dois_perfis_vira_um_lead_com_as_duas_classificacoes(tmp_path: Path):
    from modulo3.dados import Fonte

    arquivo = tmp_path / "classificacao.csv"
    arquivo.write_text(
        "cnpj;razao_social;perfil_id;score;e_alvo;justificativa;perfil_nome\n"
        "12345678000195;HOTEL TESTE LTDA;c7_amenities_ab12;70;sim;compra amenities;Amenities\n"
        "12345678000195;HOTEL TESTE LTDA;c8_software_cd34;90;sim;precisa de sistema;Software\n"
        "98765432000198;BAR X LTDA;c8_software_cd34;20;nao;bar pequeno;Software\n", encoding="utf-8")
    c = TestClient(criar_app(fonte=Fonte(arquivo)))
    pagina = c.post("/v1/leads/busca", json={"so_alvos": False}).json()
    assert pagina["total"] == 2
    hotel = c.get("/v1/leads/12345678000195").json()
    assert hotel["classificacao"]["perfil_id"] == "c8_software_cd34"            # a melhor
    assert {x["perfil_id"] for x in hotel["classificacoes"]} == {"c7_amenities_ab12", "c8_software_cd34"}
    so_amenities = c.post("/v1/leads/busca", json={"perfis": ["c7_amenities_ab12"]}).json()
    assert [i["cnpj"] for i in so_amenities["itens"]] == ["12345678000195"]
    assert {p["id"]: p["total_alvos"] for p in c.get("/v1/perfis").json()} == {"c7_amenities_ab12": 1, "c8_software_cd34": 1}


def test_perguntar_usa_o_contexto_enviado_pelo_crm(monkeypatch):
    monkeypatch.setenv("AGENTE_API", "http://agente:9000")
    agente = AgenteFalso()
    monkeypatch.setattr("modulo3.main.httpx.request", agente)
    c = _cliente()
    cnpj = c.post("/v1/leads/busca", json={}).json()["itens"][0]["cnpj"]
    contexto = {"cnpj": cnpj, "classificacao": {"perfil_id": "c7_meu", "justificativa": "do meu produto"}}
    c.post("/v1/perguntar", json={"pergunta": "?", "cnpj": cnpj, "contexto": contexto})
    assert agente.chamadas[0][2]["json"]["contexto"] == contexto
