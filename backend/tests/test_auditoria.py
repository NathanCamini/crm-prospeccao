"""Tudo que acontece no sistema deixa rastro: quem, quando, de onde, o que mudou."""

import json
import sqlite3

import pytest

from crm.auditoria import verificar
from crm.db import conectar

HOTEL = "11111111000191"


def registros(cfg, **filtro) -> list[dict]:
    conn = conectar(cfg.db_path)
    onde = " AND ".join(f"{k} = ?" for k in filtro) or "1=1"
    linhas = [dict(r) for r in conn.execute(f"SELECT * FROM auditoria WHERE {onde} ORDER BY id", list(filtro.values()))]
    conn.close()
    for l in linhas:
        for k in ("antes", "depois", "detalhe"):
            if l[k] and l[k][:1] in "{[":
                l[k] = json.loads(l[k])
    return linhas


def test_login_com_sucesso_e_falha_ficam_registrados(cfg, anonimo):
    anonimo.post("/api/auth/entrar", json={"usuario": "ana", "senha": "errada-123"})
    anonimo.post("/api/auth/entrar", json={"usuario": "fantasma", "senha": "x"})
    anonimo.post("/api/auth/entrar", json={"usuario": "ana", "senha": "senha-teste-123"}, headers={"User-Agent": "Teste/1.0"})
    falhas = registros(cfg, acao="login.falhou")
    assert [(f["usuario"], f["detalhe"]["motivo"], f["resultado"]) for f in falhas] == [
        ("ana", "senha errada", "negado"), ("fantasma", "usuário inexistente", "negado")]
    ok = registros(cfg, acao="login.ok")[0]
    assert ok["usuario"] == "ana" and ok["resultado"] == "ok" and ok["agente"] == "Teste/1.0" and ok["ip"]


def test_ip_forjado_pelo_cliente_nao_entra_na_auditoria(cfg, anonimo):
    # O nginx acrescenta o IP real no fim; o que o cliente mandou antes é ignorado.
    anonimo.post("/api/auth/entrar", json={"usuario": "ana", "senha": "errada-123"},
                 headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
    assert registros(cfg, acao="login.falhou")[-1]["ip"] == "203.0.113.9"


def test_acesso_negado_fica_registrado_mesmo_sem_alteracao(cfg, entrar):
    bia = entrar("bia")
    bia.get("/api/equipe")
    negado = registros(cfg, acao="acesso.negado", usuario="bia")
    assert negado and negado[-1]["resultado"] == "negado" and negado[-1]["status"] == 403
    assert negado[-1]["rota"] == "/api/equipe"


def test_requisicao_sem_login_e_sem_cabecalho_tambem_sao_registradas(cfg, app):
    from fastapi.testclient import TestClient

    TestClient(app).post("/api/leads/busca", json={})
    TestClient(app, headers={"X-CRM": "1"}).post("/api/leads/busca", json={})
    linhas = registros(cfg, rota="/api/leads/busca")
    assert [(l["status"], l["usuario"]) for l in linhas] == [(403, None), (401, None)]
    assert "X-CRM" in linhas[0]["detalhe"]


def test_mudanca_de_estagio_guarda_antes_e_depois(cfg, cliente):
    cliente.put(f"/api/leads/{HOTEL}/estagio", json={"estagio": "interessado", "nota": "pediu catálogo"})
    r = registros(cfg, acao="lead.estagio")[0]
    assert r["antes"] == {"estagio": "novo"} and r["depois"] == {"estagio": "interessado"}
    assert r["usuario"] == "ana" and r["entidade_id"] == HOTEL and r["empresa_id"] is not None
    assert r["detalhe"]["nota"] == "pediu catálogo" and r["tipo"] == "alteracao"


def test_ver_a_ficha_e_listar_sao_acessos_registrados(cfg, entrar):
    bia = entrar("bia")
    bia.post("/api/leads/busca", json={"texto": "pousada"})
    bia.get(f"/api/leads/{HOTEL}")
    assert registros(cfg, acao="leads.listar", usuario="bia")[0]["detalhe"]["filtro"] == {"texto": "pousada"}
    assert registros(cfg, acao="lead.ver", usuario="bia")[0]["entidade_id"] == HOTEL


def test_contato_bloqueado_por_supressao_registra_a_recusa(cfg, cliente):
    cliente.post("/api/supressao", json={"tipo": "email", "valor": "contato@um.test", "motivo": "pediu remoção"})
    r = cliente.post(f"/api/leads/{HOTEL}/contato/registrar", json={"canal": "email", "destino": "contato@um.test", "texto": "oi"})
    assert r.status_code == 409
    bloqueio = registros(cfg, acao="contato.bloqueado")[0]
    assert bloqueio["resultado"] == "recusado" and bloqueio["detalhe"]["bloqueio"] == "email contato@um.test"
    assert not registros(cfg, acao="lead.contato")  # nada foi gravado como contato


def test_alteracao_de_pessoa_guarda_so_o_que_mudou(cfg, cliente):
    cliente.put("/api/equipe/bia", json={"papel": "admin", "nome_exibicao": "Bia Lima"})
    r = registros(cfg, acao="equipe.alterar")[0]
    assert r["antes"] == {"papel": "vendedor"} and r["depois"] == {"papel": "admin"}


def test_modelo_editado_registra_o_texto_anterior(cfg, cliente):
    modelo = cliente.get("/api/modelos").json()["itens"][0]
    cliente.put(f"/api/modelos/{modelo['id']}", json={**{k: modelo[k] for k in ("nome", "canal", "perfil_id", "assunto")}, "corpo": "Novo texto"})
    r = registros(cfg, acao="modelo.editar")[0]
    assert r["antes"] == {"corpo": modelo["corpo"]} and r["depois"] == {"corpo": "Novo texto"}


def test_eventos_de_tela_e_lista_fechada(cfg, entrar):
    bia = entrar("bia")
    assert bia.post("/api/auditoria/evento", json={"acao": "ui.copiar_mensagem", "entidade_id": HOTEL, "detalhe": {"canal": "email"}}).status_code == 200
    assert bia.post("/api/auditoria/evento", json={"acao": "qualquer.coisa"}).status_code == 422
    e = registros(cfg, acao="ui.copiar_mensagem")[0]
    assert e["tipo"] == "evento" and e["usuario"] == "bia" and e["entidade_id"] == HOTEL


def test_tabela_nao_aceita_alteracao_nem_remocao(cfg, cliente):
    conn = conectar(cfg.db_path)
    with pytest.raises(sqlite3.IntegrityError, match="só aceita inclusão"):
        conn.execute("DELETE FROM auditoria")
    with pytest.raises(sqlite3.IntegrityError, match="só aceita inclusão"):
        conn.execute("UPDATE auditoria SET usuario = 'outro'")


def test_cadeia_detecta_adulteracao_feita_direto_no_arquivo(cfg, cliente):
    cliente.put(f"/api/leads/{HOTEL}/estagio", json={"estagio": "cliente"})
    linhas, adulterada = verificar(cfg.db_path)
    assert linhas >= 3 and adulterada is None
    conn = conectar(cfg.db_path)
    alvo = conn.execute("SELECT id FROM auditoria WHERE acao = 'lead.estagio'").fetchone()[0]
    conn.execute("DROP TRIGGER auditoria_sem_alteracao")
    conn.execute("UPDATE auditoria SET depois = '{\"estagio\": \"novo\"}' WHERE id = ?", (alvo,))
    conn.commit()
    assert verificar(cfg.db_path)[1] == alvo


def test_admin_consulta_so_a_propria_empresa_vendedor_nao_consulta(cfg, entrar, empresas):
    ana, beto, bia, dono = entrar("ana"), entrar("beto"), entrar("bia"), entrar("dono")
    ana.put(f"/api/leads/{HOTEL}/estagio", json={"estagio": "respondeu"})
    beto.get("/api/modelos")
    assert bia.get("/api/auditoria").status_code == 403
    da_ana = ana.get("/api/auditoria?acao=lead.estagio").json()
    assert da_ana["total"] == 1 and da_ana["itens"][0]["lead_nome"] == "POUSADA UM"
    assert {i["empresa_id"] for i in ana.get("/api/auditoria").json()["itens"]} == {empresas["A"]}
    assert ana.get("/api/auditoria?empresa_id=%d" % empresas["B"]).json()["itens"][0]["empresa_id"] == empresas["A"]
    assert {i["empresa_id"] for i in dono.get("/api/auditoria").json()["itens"]} >= {empresas["A"], empresas["B"]}
    assert dono.get("/api/auditoria/verificar").json()["integra"] is True
    assert ana.get("/api/auditoria/verificar").status_code == 403


def test_consulta_e_exportacao_da_auditoria_tambem_sao_auditadas(cfg, cliente):
    cliente.get("/api/auditoria?resultado=negado")
    csv = cliente.get("/api/auditoria/exportar")
    assert csv.status_code == 200 and csv.text.startswith("﻿id;quando_utc")
    assert registros(cfg, acao="auditoria.consultar")[0]["detalhe"] == {"resultado": "negado", "pagina": 1}
    assert registros(cfg, acao="exportacao.auditoria")


def test_superadmin_entrando_numa_empresa_aparece_para_ela(cfg, entrar, empresas):
    dono = entrar("dono")
    dono.post("/api/auth/empresa", json={"empresa_id": empresas["B"]})
    beto = entrar("beto")
    acoes = [i["acao"] for i in beto.get("/api/auditoria?usuario=dono").json()["itens"]]
    assert "empresa.entrar" in acoes


def test_sincronizacao_registrada_como_sistema(cfg, app):
    s = registros(cfg, acao="sincronizacao.concluida")[0]
    assert s["usuario"] == "sistema" and s["detalhe"]["novos"] == 4
