"""O CRM é testado contra um módulo 3 falso servido por HTTP (TestClient),
para exercitar o cliente real sem depender do serviço simulado.

Cenário fixo:
- `dono`: superadmin (sem empresa);
- empresa A "Amenities Sul" (perfil amenities_hotel): admin `ana`, vendedores `bia` e `caio`;
- empresa B "Limpa Mais" (perfil higiene_restaurante): admin `beto`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from crm.config import Config
from crm.db import agora, conectar, criar_empresa
from crm.main import criar_app
from crm.modulo3 import Modulo3
from crm.seguranca import gerar_hash

SENHA = "senha-teste-123"


def _lead(cnpj: str, nome: str, uf: str, score: int, email: str | None, whatsapp: str | None,
          perfil: str = "amenities_hotel") -> dict:
    contatos = []
    if whatsapp:
        contatos.append({"tipo": "whatsapp", "valor": whatsapp, "fonte": "site oficial", "url": None,
                         "confianca": "alta", "observacao": None})
    return {
        "cnpj": cnpj, "razao_social": f"{nome} LTDA", "nome_fantasia": nome, "uf": uf,
        "municipio": "BENTO GONCALVES", "bairro": "CENTRO",
        "contato_receita": {"ddd_1": "54", "telefone_1": " 34510000", "email": email},
        "contatos": contatos,
        "classificacao": {"perfil_id": perfil, "score": score, "e_alvo": True,
                          "justificativa": "hotel", "produto_sugerido": "Kit de amenities"},
        "canal_sugerido": "email", "mes_referencia": "2026-09",
    }


LEADS = [
    _lead("11111111000191", "POUSADA UM", "RS", 90, "contato@um.test", "54999990001"),
    _lead("22222222000191", "HOTEL DOIS", "SC", 80, "Reservas@Dois.test", None),
    _lead("33333333000191", "HOSTEL TRES", "RS", 70, None, None),
    _lead("44444444000191", "CANTINA QUATRO", "RS", 85, "oi@quatro.test", None, perfil="higiene_restaurante"),
]


def modulo3_falso(registro: list) -> FastAPI:
    app = FastAPI()
    # Produtos descritos pelas empresas: salvar um produto "classifica" HOTEL UM (que já é alvo de amenities_hotel)
    # e uma academia que só existe para esse produto, como o agente faria.
    produtos: dict[str, dict] = {}
    extras: dict[str, list[dict]] = {}
    so_do_produto: list[dict] = []

    def montar(lead: dict) -> dict:
        todas = [lead["classificacao"], *extras.get(lead["cnpj"], [])]
        melhor = max(todas, key=lambda c: (c["e_alvo"], c["score"]))
        return dict(lead, contatos=list(lead["contatos"]), classificacao=melhor, classificacoes=todas)

    def todos() -> list[dict]:
        return [montar(l) for l in [*LEADS, *so_do_produto]]

    @app.get("/saude")
    def saude():
        return {"ok": True, "fonte": "simulado", "mes_referencia": "2026-09", "total": len(LEADS)}

    @app.get("/v1/perfis")
    def perfis():
        return [{"id": "amenities_hotel", "nome": "Hotelaria", "produto": None, "total_alvos": 3},
                {"id": "higiene_restaurante", "nome": "Restaurantes", "produto": None, "total_alvos": 1}]

    @app.post("/v1/leads/busca")
    def busca(f: dict):
        registro.append(f)
        itens = todos()
        if f.get("perfil_id"):
            itens = [i for i in itens if i["classificacao"]["perfil_id"] == f["perfil_id"]]
        if f.get("perfis"):
            itens = [i for i in itens if i["classificacao"]["perfil_id"] in f["perfis"]]
        if f.get("uf"):
            itens = [i for i in itens if i["uf"] in f["uf"]]
        if f.get("cnpjs") is not None:
            itens = [i for i in itens if i["cnpj"] in f["cnpjs"]]
        itens = [i for i in itens if i["cnpj"] not in f.get("excluir_cnpjs", [])]
        total = len(itens)
        ini = (f.get("pagina", 1) - 1) * f.get("por_pagina", 25)
        return {"contrato": "v1", "fonte": "simulado", "mes_referencia": "2026-09", "total": total,
                "pagina": f.get("pagina", 1), "por_pagina": f.get("por_pagina", 25),
                "itens": itens[ini: ini + f.get("por_pagina", 25)],
                "cnpjs": [i["cnpj"] for i in itens] if f.get("apenas_cnpjs") else None}

    @app.get("/v1/leads/{cnpj}")
    def lead(cnpj: str):
        for l in todos():
            if l["cnpj"] == cnpj:
                return l
        raise HTTPException(404, "Empresa não encontrada no módulo 3.")

    @app.post("/v1/perguntar")
    def perguntar(p: dict):
        registro.append({"pergunta": p})
        return {"resposta": f"eco: {p['pergunta']} ({p.get('cnpj')})", "fonte": "simulado"}

    @app.post("/v1/produtos/sugerir")
    def sugerir(c: dict):
        if "vago" in c["descricao"]:
            raise HTTPException(422, "Diga para quem você vende.")
        return {"perfil": {"nome": "Sistema para academias", "produto": "Sistema de gestão",
                           "filtro_sql": {"cnaes": ["9313100"]}, "criterios_llm": ["É academia?"]},
                "explicacao": "Vou buscar academias.", "atividades": [{"codigo": "9313100", "descricao": "Academias"}],
                "previa": {"mes": "2026-09", "total": 2, "limite": 1500, "cabe": True, "exemplos": []}}

    @app.post("/v1/produtos", status_code=202)
    def salvar(c: dict):
        perfil_id = c.get("perfil_id") or f"c{c['dono']}_academias_{len(produtos) + 1}"
        versao = produtos[perfil_id]["versao"] + 1 if perfil_id in produtos else 1
        produtos[perfil_id] = {"id": perfil_id, "dono": c["dono"], "nome": c["perfil"]["nome"], "versao": versao,
                               "descricao": c["descricao"], "alvos": 2, "filtro": c["perfil"]["filtro_sql"],
                               "criterios": c["perfil"]["criterios_llm"],
                               "execucao": {"situacao": "pronto", "fim": f"2026-10-14T12:00:0{versao}"}}
        cls = {"perfil_id": perfil_id, "score": 95, "e_alvo": True, "justificativa": "academia dentro do hotel",
               "produto_sugerido": "Sistema de gestão"}
        extras["11111111000191"] = [cls]
        so_do_produto[:] = [_lead("55555555000191", "ACADEMIA CINCO", "RS", 0, "oi@cinco.test", None, perfil=perfil_id)]
        so_do_produto[0]["classificacao"] = dict(cls, justificativa="academia nova")
        return produtos[perfil_id]

    @app.get("/v1/produtos")
    def listar(dono: str):
        return [p for p in produtos.values() if p["dono"] == dono]

    @app.delete("/v1/produtos/{perfil_id}")
    def remover(perfil_id: str, dono: str):
        if produtos.get(perfil_id, {}).get("dono") != dono:
            raise HTTPException(404, "Produto não encontrado.")
        del produtos[perfil_id]
        extras.clear()
        so_do_produto.clear()
        return {"ok": True}

    return app


def inserir_usuario(conn, nome: str, papel: str, empresa_id: int | None, exibicao: str | None = None, trocar: bool = False):
    conn.execute(
        "INSERT INTO usuario (nome, nome_exibicao, senha_hash, papel, empresa_id, deve_trocar_senha, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nome, exibicao or nome.title(), gerar_hash(SENHA), papel, empresa_id, int(trocar), agora()),
    )


@pytest.fixture
def registro_m3() -> list:
    return []


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(dados_dir=tmp_path, modulo3_url="http://m3", sessao_dias=30, atraso_login_base=0.0, sincronizar_minutos=0)


@pytest.fixture
def empresas(cfg) -> dict:
    return {}


@pytest.fixture
def m3(registro_m3) -> Modulo3:
    return Modulo3("http://m3", 5, 5, cliente=TestClient(modulo3_falso(registro_m3), base_url="http://m3"))


@pytest.fixture
def app(cfg, m3, empresas):
    from crm.sincronizacao import sincronizar

    aplicacao = criar_app(cfg, m3)
    sincronizar(cfg, m3)
    conn = conectar(cfg.db_path)
    empresas["A"] = criar_empresa(conn, "Amenities Sul", None, '["amenities_hotel"]')
    empresas["B"] = criar_empresa(conn, "Limpa Mais", None, '["higiene_restaurante"]')
    inserir_usuario(conn, "dono", "superadmin", None, "Dono do Sistema")
    inserir_usuario(conn, "ana", "admin", empresas["A"], "Ana Souza")
    inserir_usuario(conn, "bia", "vendedor", empresas["A"], "Bia Lima")
    inserir_usuario(conn, "caio", "vendedor", empresas["A"], "Caio Reis")
    inserir_usuario(conn, "beto", "admin", empresas["B"], "Beto Alves")
    conn.commit()
    conn.close()
    return aplicacao


@pytest.fixture
def entrar(app):
    def _entrar(nome: str, senha: str = SENHA) -> TestClient:
        c = TestClient(app, headers={"X-CRM": "1"})
        r = c.post("/api/auth/entrar", json={"usuario": nome, "senha": senha})
        assert r.status_code == 200, r.text
        return c
    return _entrar


@pytest.fixture
def anonimo(app) -> TestClient:
    return TestClient(app, headers={"X-CRM": "1"})


@pytest.fixture
def cliente(entrar) -> TestClient:
    """Admin da empresa A."""
    return entrar("ana")
