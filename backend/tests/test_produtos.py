"""Produtos descritos pela empresa cliente e o isolamento dos leads de cada produto."""

from __future__ import annotations

import json

from crm.db import conectar
from crm.sincronizacao import sincronizar

HOTEL_UM, ACADEMIA = "11111111000191", "55555555000191"
DESCRICAO = "Vendo sistema de gestão para academias e estúdios no RS."


def _criar_produto(beto) -> dict:
    proposta = beto.post("/api/produtos/sugerir", json={"descricao": DESCRICAO}).json()
    r = beto.post("/api/produtos", json={"descricao": DESCRICAO, "perfil": proposta["perfil"]})
    assert r.status_code == 200, r.text
    return r.json()


def _cnpjs(c) -> set[str]:
    return {i["cnpj"] for i in c.post("/api/leads/busca", json={}).json()["itens"]}


def test_quem_descreve_e_o_admin_da_empresa(entrar):
    beto, bia, dono = entrar("beto"), entrar("bia"), entrar("dono")
    proposta = beto.post("/api/produtos/sugerir", json={"descricao": DESCRICAO})
    assert proposta.status_code == 200 and proposta.json()["previa"]["total"] == 2
    assert bia.post("/api/produtos/sugerir", json={"descricao": DESCRICAO}).status_code == 403
    assert bia.get("/api/produtos").status_code == 403
    assert dono.post("/api/produtos/sugerir", json={"descricao": DESCRICAO}).status_code == 403
    assert dono.get("/api/produtos").json()["pode_editar"] is False
    r = beto.post("/api/produtos/sugerir", json={"descricao": "isso aqui é vago demais"})
    assert r.status_code == 422 and "para quem" in r.json()["detail"]


def test_produto_salvo_traz_os_leads_so_para_a_empresa(entrar, cfg, m3):
    ana, beto = entrar("ana"), entrar("beto")
    salvo = _criar_produto(beto)
    lista = beto.get("/api/produtos").json()
    assert [p["id"] for p in lista["itens"]] == [salvo["id"]] and lista["itens"][0]["alvos"] == 2
    assert lista["sincronizando"] is True                       # terminou a classificação: o CRM copia na hora
    sincronizar(cfg, m3)

    assert {ACADEMIA, HOTEL_UM} <= _cnpjs(beto)                 # os do produto, além dos restaurantes
    ficha = beto.get(f"/api/leads/{HOTEL_UM}").json()
    assert ficha["lead"]["classificacao"]["perfil_id"] == salvo["id"]
    assert ficha["lead"]["classificacao"]["justificativa"] == "academia dentro do hotel"
    assert "classificacoes" not in ficha["lead"]

    # A empresa A vê o mesmo hotel pelo perfil dela, sem a justificativa do produto da B, e não vê a academia.
    ficha_a = ana.get(f"/api/leads/{HOTEL_UM}").json()
    assert ficha_a["lead"]["classificacao"]["perfil_id"] == "amenities_hotel"
    assert "academia" not in json.dumps(ficha_a, ensure_ascii=False)
    assert ACADEMIA not in _cnpjs(ana) and ana.get(f"/api/leads/{ACADEMIA}").status_code == 404
    assert all("classificacoes" not in i for i in ana.post("/api/leads/busca", json={}).json()["itens"])


def test_empresa_que_ve_todos_os_perfis_nao_ve_produto_de_outra(entrar, cfg, m3, empresas):
    ana, beto = entrar("ana"), entrar("beto")
    _criar_produto(beto)
    sincronizar(cfg, m3)
    conn = conectar(cfg.db_path)
    conn.execute("UPDATE empresa SET perfis = '[]' WHERE id = ?", (empresas["A"],))  # "vazio = todos"
    conn.commit()
    assert ACADEMIA not in _cnpjs(ana)
    assert entrar("ana").get(f"/api/leads/{ACADEMIA}").status_code == 404
    assert HOTEL_UM in _cnpjs(ana)


def test_pergunta_leva_o_lead_como_a_empresa_ve_e_os_perfis_dela(entrar, cfg, m3, registro_m3):
    beto = entrar("beto")
    salvo = _criar_produto(beto)
    sincronizar(cfg, m3)
    beto.post("/api/agente/perguntar", json={"pergunta": "Como abordar?", "chave": HOTEL_UM})
    corpo = next(r["pergunta"] for r in reversed(registro_m3) if "pergunta" in r)
    assert corpo["contexto"]["classificacao"]["perfil_id"] == salvo["id"]
    assert "classificacoes" not in corpo["contexto"]
    assert set(corpo["perfis"]) == {"higiene_restaurante", salvo["id"]}


def test_editar_e_remover_com_auditoria(entrar, cfg, m3):
    beto, ana = entrar("beto"), entrar("ana")
    salvo = _criar_produto(beto)
    sincronizar(cfg, m3)
    proposta = beto.post("/api/produtos/sugerir", json={"descricao": DESCRICAO + " Só estúdios.",
                                                        "perfil_id": salvo["id"]}).json()
    editado = beto.post("/api/produtos", json={"descricao": DESCRICAO + " Só estúdios.", "perfil": proposta["perfil"],
                                               "perfil_id": salvo["id"]}).json()
    assert editado["id"] == salvo["id"] and editado["versao"] == 2
    assert ana.delete(f"/api/produtos/{salvo['id']}").status_code == 404   # produto de outra empresa
    assert beto.delete(f"/api/produtos/{salvo['id']}").json() == {"ok": True}
    assert beto.get("/api/produtos").json()["itens"] == []
    conn = conectar(cfg.db_path)
    acoes = [r[0] for r in conn.execute("SELECT acao FROM auditoria WHERE entidade = 'produto' AND tipo = 'alteracao' ORDER BY id")]
    assert acoes == ["produto.criar", "produto.alterar", "produto.remover"]


def test_lead_trabalhado_continua_depois_de_remover_o_produto(entrar, cfg, m3):
    beto = entrar("beto")
    salvo = _criar_produto(beto)
    sincronizar(cfg, m3)
    beto.put(f"/api/leads/{ACADEMIA}/estagio", json={"estagio": "em_contato"})
    beto.delete(f"/api/produtos/{salvo['id']}")
    sincronizar(cfg, m3)                                          # a academia sai da carga
    assert ACADEMIA in _cnpjs(beto) and HOTEL_UM not in _cnpjs(beto)


def test_superadmin_nao_da_produto_de_uma_empresa_para_outra(entrar, empresas):
    salvo = _criar_produto(entrar("beto"))
    dono = entrar("dono")
    r = dono.put(f"/api/admin/empresas/{empresas['A']}", json={"perfis": [salvo["id"]]})
    assert r.status_code == 422
    clientes = {e["nome"]: e for e in dono.get("/api/admin/empresas").json()}
    assert clientes["Limpa Mais"]["produtos"] == "Sistema para academias"


def test_migracao_preenche_lead_perfil_dos_leads_existentes(tmp_path):
    from crm.db import MIGRACOES, migrar

    caminho = tmp_path / "antigo.sqlite"
    conn = conectar(caminho)
    for numero, sql in enumerate(MIGRACOES[:2], start=1):  # banco como estava antes dos produtos
        conn.executescript("BEGIN;" + sql + f"; PRAGMA user_version = {numero}; COMMIT;")
    dados = json.dumps({"cnpj": HOTEL_UM, "classificacao": {"perfil_id": "amenities_hotel", "score": 90, "e_alvo": True}})
    conn.execute("""INSERT INTO lead (chave, origem, cnpj, nome, perfil_id, score, e_alvo, dados, criado_em)
                    VALUES (?, 'modulo3', ?, 'POUSADA UM', 'amenities_hotel', 90, 1, ?, '2026-10-01')""",
                 (HOTEL_UM, HOTEL_UM, dados))
    conn.commit()
    conn.close()
    assert migrar(caminho) == len(MIGRACOES)
    conn = conectar(caminho)
    assert [tuple(r) for r in conn.execute("SELECT chave, perfil_id, score, e_alvo FROM lead_perfil")] == [
        (HOTEL_UM, "amenities_hotel", 90, 1)]
