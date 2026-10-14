"""Isolamento entre empresas clientes e permissões de cada papel."""

HOTEL_UM, HOTEL_DOIS, HOSTEL_TRES, CANTINA = "11111111000191", "22222222000191", "33333333000191", "44444444000191"


def cnpjs(c, **filtro):
    return {i["cnpj"] for i in c.post("/api/leads/busca", json=filtro).json()["itens"]}


# ------------------------------------------------------------------ isolamento entre empresas

def test_cada_empresa_ve_so_os_perfis_dela(entrar):
    assert cnpjs(entrar("ana")) == {HOTEL_UM, HOTEL_DOIS, HOSTEL_TRES}
    assert cnpjs(entrar("beto")) == {CANTINA}
    assert entrar("beto").get(f"/api/leads/{HOTEL_UM}").status_code == 404
    assert [p["id"] for p in entrar("beto").get("/api/perfis").json()] == ["higiene_restaurante"]


def test_estagio_historico_e_modelos_nao_vazam(entrar, app, cfg, empresas):
    from crm.db import conectar

    ana, beto = entrar("ana"), entrar("beto")
    # a empresa B passa a ver hotéis também: o mesmo CNPJ, com estágio próprio
    conn = conectar(cfg.db_path)
    conn.execute("UPDATE empresa SET perfis = '[]' WHERE id = ?", (empresas["B"],))
    conn.commit()
    ana.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "cliente", "nota": "fechou"})
    ficha_b = beto.get(f"/api/leads/{HOTEL_UM}").json()
    assert ficha_b["crm"]["estagio"] == "novo" and ficha_b["atividades"] == []
    modelo = ana.post("/api/modelos", json={"nome": "Só da A", "canal": "email", "corpo": "x"}).json()
    assert "Só da A" not in [m["nome"] for m in beto.get("/api/modelos").json()["itens"]]
    assert beto.delete(f"/api/modelos/{modelo['id']}").status_code == 404


def test_supressao_da_empresa_nao_bloqueia_a_outra_mas_a_global_sim(entrar, app, cfg, empresas):
    from crm.db import conectar

    conn = conectar(cfg.db_path)
    conn.execute("UPDATE empresa SET perfis = '[]' WHERE id = ?", (empresas["B"],))
    conn.commit()
    ana, beto, dono = entrar("ana"), entrar("beto"), entrar("dono")
    ana.post("/api/supressao", json={"tipo": "email", "valor": "contato@um.test"})
    corpo = {"canal": "email", "destino": "contato@um.test"}
    assert ana.post(f"/api/leads/{HOTEL_UM}/contato/preparar", json=corpo).status_code == 409
    assert beto.post(f"/api/leads/{HOTEL_UM}/contato/preparar", json=corpo).status_code == 200
    assert beto.post("/api/supressao", json={"tipo": "email", "valor": "x@y.test", "global": True}).status_code == 403
    dono.post("/api/supressao", json={"tipo": "email", "valor": "contato@um.test", "global": True})
    assert beto.post(f"/api/leads/{HOTEL_UM}/contato/preparar", json=corpo).status_code == 409
    global_id = next(i["id"] for i in beto.get("/api/supressao").json()["itens"] if i["global"])
    assert beto.delete(f"/api/supressao/{global_id}").status_code == 403


# ------------------------------------------------------------------ vendedor

def test_vendedor_ve_livres_e_os_seus_nao_os_do_colega(entrar):
    bia, caio = entrar("bia"), entrar("caio")
    r = bia.post(f"/api/leads/{HOTEL_UM}/contato/registrar", json={"canal": "telefone", "destino": "5434510000", "texto": "liguei"})
    assert r.json()["responsavel"] == "bia"
    assert HOTEL_UM in cnpjs(bia)
    assert HOTEL_UM not in cnpjs(caio)
    assert caio.get(f"/api/leads/{HOTEL_UM}").status_code == 403
    assert caio.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "cliente"}).status_code == 403
    assert caio.post(f"/api/leads/{HOTEL_UM}/notas", json={"texto": "oi"}).status_code == 403
    assert cnpjs(bia, responsavel="eu") == {HOTEL_UM}
    assert HOTEL_UM not in cnpjs(bia, responsavel="ninguem")


def test_vendedor_assume_livre_libera_o_seu_mas_nao_transfere(entrar):
    bia = entrar("bia")
    assert bia.put(f"/api/leads/{HOTEL_DOIS}/responsavel", json={"responsavel": "bia"}).json() == {"responsavel": "bia"}
    assert bia.put(f"/api/leads/{HOTEL_DOIS}/responsavel", json={"responsavel": "caio"}).status_code == 403
    assert bia.put(f"/api/leads/{HOTEL_DOIS}/responsavel", json={"responsavel": None}).json() == {"responsavel": None}


def test_vendedor_mudando_estagio_de_lead_livre_vira_responsavel(entrar):
    bia = entrar("bia")
    bia.put(f"/api/leads/{HOSTEL_TRES}/estagio", json={"estagio": "respondeu"})
    assert bia.get(f"/api/leads/{HOSTEL_TRES}").json()["crm"]["responsavel"] == "bia"


def test_vendedor_resumo_conta_so_o_que_ele_ve(entrar):
    bia, caio = entrar("bia"), entrar("caio")
    caio.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "interessado"})
    resumo = bia.post("/api/leads/resumo", json={}).json()
    assert resumo["total"] == 2 and resumo["por_estagio"]["interessado"] == 0


def test_vendedor_nao_edita_modelos_nem_libera_supressao_mas_bloqueia(entrar):
    bia, ana = entrar("bia"), entrar("ana")
    assert bia.get("/api/modelos").json()["pode_editar"] is False
    assert bia.post("/api/modelos", json={"nome": "x", "canal": "email", "corpo": "x"}).status_code == 403
    assert bia.post("/api/supressao", json={"tipo": "telefone", "valor": "54 3451-0000"}).status_code == 200
    item = bia.get("/api/supressao").json()["itens"][0]
    assert bia.delete(f"/api/supressao/{item['id']}").status_code == 403
    assert ana.delete(f"/api/supressao/{item['id']}").status_code == 200


def test_vendedor_nao_acessa_equipe_nem_clientes(entrar):
    bia = entrar("bia")
    assert bia.get("/api/equipe").status_code == 403
    assert bia.post("/api/equipe", json={"usuario": "x1", "nome_exibicao": "X", "senha_inicial": "12345678"}).status_code == 403
    assert bia.get("/api/admin/empresas").status_code == 403
    assert {n["usuario"] for n in bia.get("/api/equipe/nomes").json()} == {"ana", "bia", "caio"}


# ------------------------------------------------------------------ admin da empresa

def test_admin_ve_tudo_e_transfere(entrar):
    bia, ana = entrar("bia"), entrar("ana")
    bia.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "em_contato"})
    assert HOTEL_UM in cnpjs(ana) and cnpjs(ana, responsavel="bia") == {HOTEL_UM}
    assert ana.put(f"/api/leads/{HOTEL_UM}/responsavel", json={"responsavel": "caio"}).json() == {"responsavel": "caio"}
    assert ana.put(f"/api/leads/{HOTEL_UM}/responsavel", json={"responsavel": "beto"}).status_code == 422
    tipos = [a["tipo"] for a in ana.get(f"/api/leads/{HOTEL_UM}").json()["atividades"]]
    assert tipos.count("responsavel") == 2


def test_admin_mudando_estagio_nao_toma_o_lead(entrar):
    ana = entrar("ana")
    ana.put(f"/api/leads/{HOTEL_DOIS}/estagio", json={"estagio": "respondeu"})
    assert ana.get(f"/api/leads/{HOTEL_DOIS}").json()["crm"]["responsavel"] is None


def test_admin_cadastra_altera_redefine_e_remove_funcionario(entrar):
    ana = entrar("ana")
    r = ana.post("/api/equipe", json={"usuario": "Dani", "nome_exibicao": "Dani Prado", "senha_inicial": "provisoria-1"})
    assert r.json() == {"usuario": "dani"}
    assert ana.post("/api/equipe", json={"usuario": "bia", "nome_exibicao": "B", "senha_inicial": "provisoria-1"}).status_code == 409
    assert ana.post("/api/equipe", json={"usuario": "x", "nome_exibicao": "X", "senha_inicial": "curta"}).status_code == 422
    dani = entrar("dani", "provisoria-1")
    dani.post("/api/auth/senha", json={"senha_atual": "provisoria-1", "senha_nova": "senha-da-dani"})
    dani.put(f"/api/leads/{HOTEL_DOIS}/estagio", json={"estagio": "interessado"})

    equipe = {m["usuario"]: m for m in ana.get("/api/equipe").json()}
    assert equipe["dani"]["leads"] == 1 and equipe["dani"]["leads_por_estagio"] == {"interessado": 1}
    assert equipe["dani"]["atividades_7d"] >= 1

    assert ana.put("/api/equipe/dani", json={"papel": "admin"}).status_code == 200
    assert ana.put("/api/equipe/dani", json={"ativo": False}).status_code == 200
    assert dani.get("/api/auth/eu").status_code == 401
    assert ana.post("/api/equipe/dani/senha", json={"senha_provisoria": "outra-prov-2"}).status_code == 200
    r = ana.delete("/api/equipe/dani").json()
    assert r["leads_liberados"] == 1
    assert ana.get(f"/api/leads/{HOTEL_DOIS}").json()["crm"]["responsavel"] is None


def test_funcionario_desativado_nao_entra(entrar, anonimo):
    entrar("ana").put("/api/equipe/caio", json={"ativo": False})
    r = anonimo.post("/api/auth/entrar", json={"usuario": "caio", "senha": "senha-teste-123"})
    assert r.status_code == 403 and "desativado" in r.json()["detail"]


def test_admin_nao_tira_o_proprio_acesso(entrar):
    ana = entrar("ana")
    assert ana.put("/api/equipe/ana", json={"papel": "vendedor"}).status_code == 422
    assert ana.put("/api/equipe/ana", json={"ativo": False}).status_code == 422
    assert ana.delete("/api/equipe/ana").status_code == 422


def test_admin_nao_mexe_em_gente_de_outra_empresa_nem_no_superadmin(entrar):
    ana = entrar("ana")
    for nome in ("beto", "dono"):
        assert ana.put(f"/api/equipe/{nome}", json={"ativo": False}).status_code == 404
        assert ana.post(f"/api/equipe/{nome}/senha", json={"senha_provisoria": "12345678x"}).status_code == 404
        assert ana.delete(f"/api/equipe/{nome}").status_code == 404
    assert ana.post("/api/equipe", json={"usuario": "z", "nome_exibicao": "Z", "papel": "superadmin", "senha_inicial": "12345678"}).status_code == 422
    assert ana.get("/api/admin/empresas").status_code == 403
    assert ana.post("/api/auth/empresa", json={"empresa_id": 2}).status_code == 403


def test_atividades_da_equipe_com_nome_da_empresa_e_filtro(entrar):
    bia, caio, ana = entrar("bia"), entrar("caio"), entrar("ana")
    bia.post(f"/api/leads/{HOTEL_UM}/notas", json={"texto": "nota da bia"})
    caio.post(f"/api/leads/{HOTEL_DOIS}/notas", json={"texto": "nota do caio"})
    todas = ana.get("/api/equipe/atividades").json()
    assert {a["criado_por"] for a in todas} == {"bia", "caio"}
    so_bia = ana.get("/api/equipe/atividades?usuario=bia").json()
    assert {a["criado_por"] for a in so_bia} == {"bia"}
    assert any(a["empresa_nome"] == "POUSADA UM" for a in so_bia)


# ------------------------------------------------------------------ superadmin

def test_superadmin_cria_empresa_com_admin_e_entra_nela(entrar):
    dono = entrar("dono")
    eu = dono.get("/api/auth/eu").json()
    assert eu["papel"] == "superadmin" and len(eu["empresas"]) == 2
    r = dono.post("/api/admin/empresas", json={
        "nome": "Hotelaria Norte", "perfis": ["amenities_hotel"],
        "admin": {"usuario": "nina", "nome_exibicao": "Nina Costa", "senha_inicial": "provisoria-1"},
    })
    assert r.status_code == 200
    nova = r.json()["id"]
    assert len(dono.get("/api/auth/eu").json()["empresas"]) == 3

    nina = entrar("nina", "provisoria-1")
    assert nina.get("/api/auth/eu").json()["deve_trocar_senha"] is True
    nina.post("/api/auth/senha", json={"senha_atual": "provisoria-1", "senha_nova": "senha-da-nina"})
    assert len(nina.get("/api/modelos").json()["itens"]) == 5  # modelos padrão já vêm prontos
    nina.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "cliente"})

    trocou = dono.post("/api/auth/empresa", json={"empresa_id": nova}).json()
    assert trocou["empresa"]["nome"] == "Hotelaria Norte"
    assert dono.get(f"/api/leads/{HOTEL_UM}").json()["crm"]["estagio"] == "cliente"
    assert {m["usuario"] for m in dono.get("/api/equipe").json()} == {"nina"}


def test_superadmin_desativa_empresa_e_todos_saem(entrar, empresas):
    ana, bia, dono = entrar("ana"), entrar("bia"), entrar("dono")
    dono.put(f"/api/admin/empresas/{empresas['A']}", json={"ativa": False})
    assert ana.get("/api/auth/eu").status_code == 401 and bia.get("/api/auth/eu").status_code == 401
    lista = {e["nome"]: e for e in dono.get("/api/admin/empresas").json()}
    assert lista["Amenities Sul"]["ativa"] is False and lista["Amenities Sul"]["pessoas"] == 3


def test_superadmin_sem_empresa_escolhida_recebe_orientacao(app, cfg):
    from fastapi.testclient import TestClient

    from crm.db import conectar

    conn = conectar(cfg.db_path)
    conn.execute("UPDATE empresa SET ativa = 0")
    conn.commit()
    c = TestClient(app, headers={"X-CRM": "1"})
    c.post("/api/auth/entrar", json={"usuario": "dono", "senha": "senha-teste-123"})
    r = c.post("/api/leads/busca", json={})
    assert r.status_code == 409 and "Escolha uma empresa" in r.json()["detail"]


def test_nomes_repetidos_e_invalidos(entrar):
    dono = entrar("dono")
    base = {"perfis": [], "admin": {"usuario": "novo-admin", "nome_exibicao": "N", "senha_inicial": "provisoria-1"}}
    assert dono.post("/api/admin/empresas", json={**base, "nome": "Amenities Sul"}).status_code == 409
    r = dono.post("/api/admin/empresas", json={**base, "nome": "Outra", "admin": {**base["admin"], "usuario": "ana"}})
    assert r.status_code == 409
    r = dono.post("/api/admin/empresas", json={**base, "nome": "Outra", "admin": {**base["admin"], "usuario": "com espaço"}})
    assert r.status_code == 422
