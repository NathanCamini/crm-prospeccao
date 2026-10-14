"""Leads locais e manuais, próximos passos, vendas e relatórios."""

from datetime import date, timedelta

from crm.db import conectar
from crm.sincronizacao import sincronizar
from tests.conftest import LEADS

HOTEL_UM, HOTEL_DOIS, HOSTEL = "11111111000191", "22222222000191", "33333333000191"
MANUAL = {"nome": "Pousada da Serra", "telefone": "(54) 3282-1000", "municipio": "Gramado", "uf": "rs",
          "contato_nome": "Marta (gerente)", "observacao": "Indicada pelo hotel vizinho"}


def chaves(c, **filtro):
    return [i["chave"] for i in c.post("/api/leads/busca", json=filtro).json()["itens"]]


# ------------------------------------------------------------------ cópia local e sincronização

def test_lead_que_sai_da_base_continua_para_quem_trabalhou(cfg, m3, entrar, empresas):
    ana = entrar("ana")
    ana.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "interessado"})
    removidos = [l for l in LEADS if l["cnpj"] in (HOTEL_UM, HOTEL_DOIS)]
    for l in removidos:
        LEADS.remove(l)
    try:
        r = sincronizar(cfg, m3)
        assert r["ausentes"] == 2
        assert HOTEL_UM in chaves(ana) and HOTEL_DOIS not in chaves(ana)
        ficha = ana.get(f"/api/leads/{HOTEL_UM}").json()
        assert ficha["lead"]["ausente_desde"] and ficha["crm"]["estagio"] == "interessado"
        assert ana.get(f"/api/leads/{HOTEL_DOIS}").status_code == 404
    finally:
        LEADS[0:0] = removidos
    assert sincronizar(cfg, m3)["ausentes"] == 0 and HOTEL_DOIS in chaves(ana)


def test_sincronizacao_atualiza_o_que_mudou(cfg, m3, cliente):
    original = LEADS[1]["classificacao"]["score"]
    LEADS[1]["classificacao"]["score"] = 99
    try:
        r = sincronizar(cfg, m3)
        assert r["atualizados"] == 1 and r["novos"] == 0
        assert chaves(cliente)[0] == HOTEL_DOIS
    finally:
        LEADS[1]["classificacao"]["score"] = original


def test_superadmin_ve_historico_e_sincroniza_na_hora(entrar):
    dono = entrar("dono")
    assert dono.post("/api/admin/sincronizacao").json()["lidos"] == 4
    h = dono.get("/api/admin/sincronizacao").json()
    assert len(h["execucoes"]) == 2 and h["leads"]["do_agente"] == 4
    assert entrar("ana").post("/api/admin/sincronizacao").status_code == 403


def test_filtros_ordenacao_e_busca_sem_acento(cliente):
    assert chaves(cliente, uf=["SC"]) == [HOTEL_DOIS]
    assert chaves(cliente, ordenar="nome") == [HOSTEL, HOTEL_DOIS, HOTEL_UM]
    assert chaves(cliente, texto="póusada") == [HOTEL_UM]
    assert chaves(cliente, texto="11.111.111") == [HOTEL_UM]
    assert chaves(cliente, score_min=85) == [HOTEL_UM]


# ------------------------------------------------------------------ cadastro manual

def test_cadastro_manual_aparece_so_para_a_empresa_e_ja_e_de_quem_cadastrou(cfg, entrar):
    bia, ana, beto = entrar("bia"), entrar("ana"), entrar("beto")
    r = bia.post("/api/leads/manual", json=MANUAL)
    assert r.status_code == 200, r.text
    chave = r.json()["chave"]
    assert chave.startswith("M")
    ficha = bia.get(f"/api/leads/{chave}").json()
    assert ficha["lead"]["origem"] == "manual" and ficha["crm"]["responsavel"] == "bia"
    assert ficha["lead"]["municipio"] == "GRAMADO" and ficha["lead"]["uf"] == "RS"
    assert ficha["cadastro"]["criado_por"] == "bia" and ficha["simulado"] is False
    assert [a["tipo"] for a in ficha["atividades"]] == ["cadastro"]
    assert chave in chaves(ana, origem="manual") and chave not in chaves(ana, origem="agente")
    assert chave not in chaves(entrar("caio"))
    assert beto.get(f"/api/leads/{chave}").status_code == 404
    assert chaves(ana, texto="marta") == [chave]


def test_cadastro_manual_valida_e_evita_duplicar(entrar):
    ana = entrar("ana")
    assert ana.post("/api/leads/manual", json={"nome": "Sem contato"}).status_code == 422
    assert ana.post("/api/leads/manual", json={**MANUAL, "cnpj": "11.222.333/0001-82"}).status_code == 422
    r = ana.post("/api/leads/manual", json={**MANUAL, "cnpj": HOTEL_UM})
    assert r.status_code == 409 and r.json()["detail"]["chave"] == HOTEL_UM
    primeiro = ana.post("/api/leads/manual", json={**MANUAL, "cnpj": "11.222.333/0001-81"}).json()["chave"]
    r = ana.post("/api/leads/manual", json={**MANUAL, "cnpj": "11222333000181"})
    assert r.status_code == 409 and r.json()["detail"]["chave"] == primeiro
    # outra empresa pode cadastrar o mesmo CNPJ: os cadastros não se misturam
    assert entrar("beto").post("/api/leads/manual", json={**MANUAL, "cnpj": "11222333000181"}).status_code == 200


def test_editar_lead_manual_registra_o_que_mudou(cfg, entrar):
    ana = entrar("ana")
    chave = ana.post("/api/leads/manual", json=MANUAL).json()["chave"]
    r = ana.put(f"/api/leads/{chave}/dados", json={**MANUAL, "email": "compras@serra.test", "telefone": None, "whatsapp": "54 99999 1234"})
    assert sorted(r.json()["alterados"]) == ["email", "telefone", "whatsapp"]
    assert ana.put(f"/api/leads/{HOTEL_UM}/dados", json=MANUAL).status_code == 422
    conn = conectar(cfg.db_path)
    import json
    linha = conn.execute("SELECT antes, depois FROM auditoria WHERE acao = 'lead.editar'").fetchone()
    assert json.loads(linha["antes"]) == {"telefone": "5432821000", "email": None, "whatsapp": None}
    assert json.loads(linha["depois"]) == {"email": "compras@serra.test", "whatsapp": "54999991234", "telefone": None}


def test_contato_e_pergunta_ao_agente_com_lead_manual(entrar):
    ana = entrar("ana")
    chave = ana.post("/api/leads/manual", json=MANUAL).json()["chave"]
    prep = ana.post(f"/api/leads/{chave}/contato/preparar", json={"canal": "telefone", "destino": "5432821000"}).json()
    assert "Pousada da Serra" in prep["texto"] and prep["simulado"] is False
    r = ana.post("/api/agente/perguntar", json={"pergunta": "vale a pena?", "chave": chave}).json()
    assert r["resposta"].startswith("eco: vale a pena?")


# ------------------------------------------------------------------ próximos passos

def test_tarefas_agenda_e_permissoes(entrar):
    bia, caio, ana = entrar("bia"), entrar("caio"), entrar("ana")
    ontem, amanha = (date.today() - timedelta(days=1)).isoformat(), (date.today() + timedelta(days=1)).isoformat()
    t1 = bia.post(f"/api/leads/{HOTEL_UM}/tarefas", json={"titulo": "Ligar para a governança", "vence_em": ontem}).json()["id"]
    bia.post(f"/api/leads/{HOTEL_UM}/tarefas", json={"titulo": "Enviar catálogo", "vence_em": amanha})
    assert bia.get("/api/tarefas/contagem").json()["atrasadas"] == 1
    assert [t["titulo"] for t in bia.get("/api/tarefas").json()] == ["Ligar para a governança", "Enviar catálogo"]
    assert bia.post(f"/api/leads/{HOTEL_UM}/tarefas", json={"titulo": "x1", "vence_em": amanha, "responsavel": "caio"}).status_code == 403
    assert caio.put(f"/api/tarefas/{t1}", json={"concluida": True}).status_code == 403
    assert caio.get("/api/tarefas?quem=todos").status_code == 403
    assert bia.put(f"/api/tarefas/{t1}", json={"concluida": True}).status_code == 200
    assert bia.get("/api/tarefas/contagem").json()["atrasadas"] == 0
    item = next(i for i in ana.post("/api/leads/busca", json={}).json()["itens"] if i["chave"] == HOTEL_UM)
    assert item["crm"]["proximo_passo_titulo"] == "Enviar catálogo"
    assert len(ana.get("/api/tarefas?quem=bia").json()) == 1
    assert ana.put(f"/api/tarefas/{t1}", json={"concluida": False, "responsavel": "caio"}).status_code == 200
    assert caio.get("/api/tarefas/contagem").json()["atrasadas"] == 1
    tipos = [a["tipo"] for a in ana.get(f"/api/leads/{HOTEL_UM}").json()["atividades"]]
    assert "tarefa" in tipos and "tarefa_concluida" in tipos


def test_remover_pessoa_passa_tarefas_abertas_para_quem_removeu(entrar):
    bia, ana = entrar("bia"), entrar("ana")
    bia.post(f"/api/leads/{HOTEL_UM}/tarefas", json={"titulo": "Retornar", "vence_em": date.today().isoformat()})
    assert ana.delete("/api/equipe/bia").json()["tarefas_transferidas"] == 1
    assert [t["titulo"] for t in ana.get("/api/tarefas").json()] == ["Retornar"]


# ------------------------------------------------------------------ vendas e relatórios

def test_venda_vira_cliente_e_estorno_nao_apaga(cfg, entrar):
    bia, ana = entrar("bia"), entrar("ana")
    r = bia.post(f"/api/leads/{HOTEL_UM}/vendas", json={"valor": "1.250,50", "descricao": "500 kits"})
    assert r.json()["valor_centavos"] == 125050
    ficha = bia.get(f"/api/leads/{HOTEL_UM}").json()
    assert ficha["crm"]["estagio"] == "cliente" and ficha["total_vendas_centavos"] == 125050
    assert ficha["vendas"][0]["vendedor"] == "bia"
    assert bia.post(f"/api/leads/{HOTEL_UM}/vendas", json={"valor": "0"}).status_code == 422
    amanha = (date.today() + timedelta(days=2)).isoformat()
    assert bia.post(f"/api/leads/{HOTEL_UM}/vendas", json={"valor": "10", "data": amanha}).status_code == 422
    venda = ficha["vendas"][0]["id"]
    assert bia.post(f"/api/vendas/{venda}/estornar", json={"motivo": "devolvido"}).status_code == 403
    assert ana.post(f"/api/vendas/{venda}/estornar", json={"motivo": "devolvido"}).status_code == 200
    assert ana.post(f"/api/vendas/{venda}/estornar", json={"motivo": "de novo"}).status_code == 409
    ficha = ana.get(f"/api/leads/{HOTEL_UM}").json()
    assert ficha["total_vendas_centavos"] == 0 and ficha["vendas"][0]["motivo_estorno"] == "devolvido"


def test_relatorio_do_periodo(entrar):
    bia, caio, ana = entrar("bia"), entrar("caio"), entrar("ana")
    bia.post(f"/api/leads/{HOTEL_UM}/contato/registrar", json={"canal": "whatsapp", "destino": "54999990001", "texto": "oi"})
    bia.put(f"/api/leads/{HOTEL_UM}/estagio", json={"estagio": "respondeu"})
    bia.post(f"/api/leads/{HOTEL_UM}/vendas", json={"valor": "1000"})
    caio.post(f"/api/leads/{HOTEL_DOIS}/contato/registrar", json={"canal": "email", "destino": "reservas@dois.test", "texto": "oi"})
    caio.post(f"/api/leads/{HOTEL_DOIS}/vendas", json={"valor": "500,00"})
    assert bia.get("/api/relatorios").status_code == 403
    r = ana.get("/api/relatorios").json()
    t = r["totais"]
    assert t["contatos"] == 2 and t["novos_clientes"] == 2 and t["vendas_qtd"] == 2
    assert t["vendas_centavos"] == 150000 and t["ticket_medio_centavos"] == 75000 and t["respostas"] == 2
    pessoas = {p["usuario"]: p for p in r["por_pessoa"]}
    assert pessoas["bia"]["vendas_centavos"] == 100000 and pessoas["caio"]["contatos"] == 1
    canais = {c["canal"]: c for c in r["por_canal"]}
    assert canais["whatsapp"]["viraram_cliente"] == 1 and canais["email"]["viraram_cliente"] == 1
    assert sum(s["centavos"] for s in r["vendas_por_semana"]) == 150000
    assert r["funil"]["cliente"] == 2
    vendas = ana.get("/api/relatorios/exportar?tipo=vendas")
    assert vendas.status_code == 200 and "1000,00" in vendas.text and "500,00" in vendas.text
    assert ana.get("/api/relatorios?de=2026-12-01&ate=2026-01-01").status_code == 422


def test_exportar_leads_com_filtro(entrar):
    ana = entrar("ana")
    r = ana.post("/api/leads/exportar", json={"uf": ["RS"]})
    linhas = r.text.lstrip("﻿").strip().splitlines()
    assert linhas[0].startswith("chave;cnpj;nome") and len(linhas) == 3
    vendedor = entrar("caio")
    assert vendedor.post("/api/leads/exportar", json={}).status_code == 200
