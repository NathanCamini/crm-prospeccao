from crm.mensagens import montar_url, normalizar, renderizar, titulo


def test_busca_junta_estagio_padrao_novo(cliente):
    r = cliente.post("/api/leads/busca", json={}).json()
    assert r["total"] == 3
    assert all(i["crm"]["estagio"] == "novo" and not i["crm"]["suprimido"] for i in r["itens"])


def test_filtro_por_estagio_sai_do_banco_local_sem_chamar_o_modulo3(cliente, registro_m3):
    cliente.put("/api/leads/11111111000191/estagio", json={"estagio": "interessado"})
    antes = len(registro_m3)
    r = cliente.post("/api/leads/busca", json={"estagio": "interessado"}).json()
    assert [i["cnpj"] for i in r["itens"]] == ["11111111000191"]
    assert len(registro_m3) == antes
    novos = cliente.post("/api/leads/busca", json={"estagio": "novo"}).json()
    assert {i["cnpj"] for i in novos["itens"]} == {"22222222000191", "33333333000191"}


def test_resumo_conta_por_estagio(cliente):
    cliente.put("/api/leads/22222222000191/estagio", json={"estagio": "cliente"})
    r = cliente.post("/api/leads/resumo", json={}).json()
    assert r["total"] == 3
    assert r["por_estagio"]["cliente"] == 1 and r["por_estagio"]["novo"] == 2


def test_ficha_com_historico(cliente):
    cliente.put("/api/leads/11111111000191/estagio", json={"estagio": "em_contato", "nota": "ligar terça"})
    cliente.post("/api/leads/11111111000191/notas", json={"texto": "recepção pediu e-mail"})
    f = cliente.get("/api/leads/11.111.111.0001-91").json()
    assert f["lead"]["nome_fantasia"] == "POUSADA UM"
    assert f["crm"]["estagio"] == "em_contato"
    assert [a["tipo"] for a in f["atividades"]] == ["nota", "estagio"]
    assert f["atividades"][1]["assunto"] == "novo→em_contato"
    assert f["simulado"] is True


def test_contato_receita_vem_sem_alteracao(cliente):
    f = cliente.get("/api/leads/11111111000191").json()
    assert f["lead"]["contato_receita"]["telefone_1"] == " 34510000"


def test_empresa_inexistente_404(cliente):
    assert cliente.get("/api/leads/99999999000191").status_code == 404
    assert cliente.put("/api/leads/99999999000191/estagio", json={"estagio": "cliente"}).status_code == 404


def test_preparar_whatsapp_usa_modelo_e_variaveis(cliente):
    r = cliente.post("/api/leads/11111111000191/contato/preparar",
                     json={"canal": "whatsapp", "destino": "(54) 99999-0001"}).json()
    assert "Ana Souza" in r["texto"] and "Pousada Um" in r["texto"] and "Bento Goncalves" in r["texto"]
    assert "kit de amenities" in r["texto"]
    assert r["url"].startswith("https://wa.me/5554999990001?text=")
    assert r["simulado"] is True


def test_preparar_email_escolhe_modelo_do_perfil(cliente):
    r = cliente.post("/api/leads/22222222000191/contato/preparar",
                     json={"canal": "email", "destino": "reservas@dois.test"}).json()
    assert r["assunto"] == "Amostra de amenities para o Hotel Dois"
    assert r["url"].startswith("mailto:reservas@dois.test?subject=")


def test_registrar_contato_move_novo_para_em_contato(cliente):
    r = cliente.post("/api/leads/33333333000191/contato/registrar",
                     json={"canal": "telefone", "destino": "54 3451-0000", "texto": "liguei, pediram e-mail"}).json()
    assert r["estagio"] == "em_contato"
    tipos = [a["tipo"] for a in cliente.get("/api/leads/33333333000191").json()["atividades"]]
    assert tipos == ["estagio", "contato", "responsavel"]


def test_destino_invalido(cliente):
    r = cliente.post("/api/leads/11111111000191/contato/preparar", json={"canal": "whatsapp", "destino": "123"})
    assert r.status_code == 422


def test_supressao_bloqueia_contato_e_marca_lista(cliente):
    cliente.post("/api/supressao", json={"tipo": "email", "valor": " RESERVAS@dois.test "})
    r = cliente.post("/api/leads/22222222000191/contato/preparar", json={"canal": "email", "destino": "reservas@dois.test"})
    assert r.status_code == 409 and "não contatar" in r.json()["detail"]
    lista = cliente.post("/api/leads/busca", json={}).json()["itens"]
    assert {i["cnpj"]: i["crm"]["suprimido"] for i in lista}["22222222000191"] is True


def test_suprimir_empresa_bloqueia_todos_os_canais(cliente):
    r = cliente.post("/api/leads/11111111000191/suprimir", json={"motivo": "pediu remoção"}).json()
    assert r["suprimidos"] >= 3  # cnpj, e-mail, telefones
    f = cliente.get("/api/leads/11111111000191").json()
    assert f["crm"]["estagio"] == "descartado" and f["supressoes"]
    for canal, destino in (("whatsapp", "54999990001"), ("email", "outro@qualquer.test")):
        r = cliente.post("/api/leads/11111111000191/contato/registrar",
                         json={"canal": canal, "destino": destino, "texto": "oi"})
        assert r.status_code == 409


def test_agente_passa_pelo_modulo3_e_vira_atividade(cliente):
    r = cliente.post("/api/agente/perguntar", json={"pergunta": "É franquia?", "chave": "11111111000191"}).json()
    assert r["resposta"] == "eco: É franquia? (11111111000191)"
    atividades = cliente.get("/api/leads/11111111000191").json()["atividades"]
    assert atividades[0]["tipo"] == "agente"


def test_modelos_crud(cliente):
    base = cliente.get("/api/modelos").json()
    assert len(base["itens"]) == 5 and "vendedor" in base["variaveis"] and base["pode_editar"] is True
    novo = cliente.post("/api/modelos", json={"nome": "Lembrete", "canal": "email", "assunto": "Oi", "corpo": "Olá {nome_fantasia}"}).json()
    assert cliente.post("/api/modelos", json={"nome": "Lembrete", "canal": "email", "corpo": "x"}).status_code == 409
    assert cliente.put(f"/api/modelos/{novo['id']}", json={"nome": "Lembrete 2", "canal": "email", "corpo": "y"}).status_code == 200
    assert cliente.delete(f"/api/modelos/{novo['id']}").status_code == 200


def test_mensagens_utilitarios():
    assert titulo("SAO JOAO DO TESTE") == "Sao Joao do Teste"
    assert renderizar("{nome_fantasia} {desconhecida} {}", {"nome_fantasia": "X"}) == "X {desconhecida} {}"
    assert normalizar("telefone", "+55 (54) 99999-0001") == "54999990001"
    assert normalizar("dominio", "https://www.Hotel.test/contato") == "hotel.test"
    assert normalizar("cnpj", "12.ABC.345/01DE-35") == "12ABC34501DE35"
    assert montar_url("formulario", "javascript:alert(1)", "x", None) is None


def test_modulo3_fora_do_ar_a_lista_continua_pelo_banco_local(cfg, app, entrar):
    from fastapi.testclient import TestClient

    from crm.main import criar_app
    from crm.modulo3 import Modulo3

    fora = criar_app(cfg, Modulo3("http://127.0.0.1:9", 0.5, 0.5))
    c = TestClient(fora, headers={"X-CRM": "1"})
    c.post("/api/auth/entrar", json={"usuario": "ana", "senha": "senha-teste-123"})
    r = c.post("/api/leads/busca", json={})
    assert r.status_code == 200 and r.json()["total"] == 3
    assert c.get("/api/leads/11111111000191").status_code == 200
    r = c.post("/api/agente/perguntar", json={"pergunta": "oi"})
    assert r.status_code == 503 and "módulo 3" in r.json()["detail"]
    assert c.get("/api/saude").json()["modulo3"] is None
