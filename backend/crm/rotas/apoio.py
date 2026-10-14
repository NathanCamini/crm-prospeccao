from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auditoria import anotar, diferencas
from ..config import Config
from ..db import SUPRESSAO_GLOBAL, agora
from ..dependencias import RotaQueConfirma, Contexto, config, contexto, db, exige_admin, modulo3
from ..mensagens import VARIAVEIS, normalizar
from ..modulo3 import Modulo3

router = APIRouter(prefix="/api", tags=["apoio"], route_class=RotaQueConfirma)


# ------------------------------------------------------------------ saúde e perfis

@router.get("/saude")
def saude(m3: Modulo3 = Depends(modulo3)) -> dict:
    try:
        return {"crm": "ok", "modulo3": m3.saude(forcar=True)}
    except HTTPException as erro:
        return {"crm": "ok", "modulo3": None, "erro": erro.detail}


@router.get("/perfis")
def perfis(m3: Modulo3 = Depends(modulo3), conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> list[dict]:
    try:
        todos = m3.perfis()
    except HTTPException:
        # módulo 3 fora do ar: os perfis que já existem na cópia local
        todos = [{"id": r[0], "nome": r[0], "produto": None, "total_alvos": r[1]} for r in conn.execute(
            "SELECT perfil_id, SUM(e_alvo) FROM lead WHERE origem = 'modulo3' AND perfil_id IS NOT NULL GROUP BY perfil_id")]
    return [p for p in todos if ctx.ve_perfil(p["id"])]


# ------------------------------------------------------------------ agente (via módulo 3)

class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)
    chave: str | None = None


@router.post("/agente/perguntar")
def perguntar(
    dados: Pergunta,
    request: Request,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    m3: Modulo3 = Depends(modulo3),
    ctx: Contexto = Depends(contexto),
) -> dict:
    from .leads import atividade, carregar_lead, dados_do_lead, exigir_acesso

    chave, cnpj, contexto_lead = None, None, None
    if dados.chave:
        linha = carregar_lead(conn, ctx, dados.chave, cfg, m3)
        exigir_acesso(conn, ctx, linha["chave"])
        chave = linha["chave"]
        if linha["origem"] == "modulo3":
            cnpj = chave
        else:
            contexto_lead = dados_do_lead(linha)
    resposta = m3.perguntar(dados.pergunta.strip(), cnpj, contexto_lead)
    if chave:
        atividade(conn, ctx, chave, "agente", resposta.get("resposta", "")[:8000], assunto=dados.pergunta.strip()[:300])
    anotar(request, "acesso", "agente.perguntar", entidade="lead" if chave else None, entidade_id=chave,
           detalhe={"pergunta": dados.pergunta.strip(), "fonte": resposta.get("fonte")})
    return resposta


# ------------------------------------------------------------------ modelos de mensagem (ler: todos; editar: admin)

class Modelo(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    canal: Literal["email", "whatsapp", "formulario", "telefone"]
    perfil_id: str | None = None
    assunto: str | None = Field(default=None, max_length=300)
    corpo: str = Field(min_length=1, max_length=8000)


CAMPOS_MODELO = ("nome", "canal", "perfil_id", "assunto", "corpo")


@router.get("/modelos")
def listar_modelos(conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> dict:
    itens = [dict(r) for r in conn.execute(
        "SELECT id, nome, canal, perfil_id, assunto, corpo, atualizado_em FROM modelo_mensagem WHERE empresa_id = ? ORDER BY canal, nome",
        (ctx.empresa_id,))]
    return {"itens": itens, "variaveis": list(VARIAVEIS), "pode_editar": ctx.e_admin}


def _modelo(conn: sqlite3.Connection, ctx: Contexto, id_: int) -> dict:
    r = conn.execute("SELECT * FROM modelo_mensagem WHERE id = ? AND empresa_id = ?", (id_, ctx.empresa_id)).fetchone()
    if not r:
        raise HTTPException(404, "Modelo não encontrado.")
    return {k: r[k] for k in CAMPOS_MODELO}


def _valores(m: Modelo) -> dict:
    return {"nome": m.nome.strip(), "canal": m.canal, "perfil_id": (m.perfil_id or "").strip() or None,
            "assunto": (m.assunto or "").strip() or None, "corpo": m.corpo}


@router.post("/modelos")
def criar_modelo(m: Modelo, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    v = _valores(m)
    try:
        id_ = conn.execute(
            "INSERT INTO modelo_mensagem (empresa_id, nome, canal, perfil_id, assunto, corpo, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ctx.empresa_id, v["nome"], v["canal"], v["perfil_id"], v["assunto"], v["corpo"], agora()),
        ).lastrowid
    except sqlite3.IntegrityError as erro:
        raise HTTPException(409, "Já existe um modelo com esse nome.") from erro
    anotar(request, "alteracao", "modelo.criar", entidade="modelo", entidade_id=id_, depois=v)
    return {"id": id_}


@router.put("/modelos/{id_}")
def editar_modelo(id_: int, m: Modelo, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    antes, depois = _modelo(conn, ctx, id_), _valores(m)
    try:
        conn.execute(
            "UPDATE modelo_mensagem SET nome = ?, canal = ?, perfil_id = ?, assunto = ?, corpo = ?, atualizado_em = ? WHERE id = ?",
            (depois["nome"], depois["canal"], depois["perfil_id"], depois["assunto"], depois["corpo"], agora(), id_),
        )
    except sqlite3.IntegrityError as erro:
        raise HTTPException(409, "Já existe um modelo com esse nome.") from erro
    a, d = diferencas(antes, depois)
    anotar(request, "alteracao", "modelo.editar", entidade="modelo", entidade_id=id_, antes=a, depois=d)
    return {"id": id_}


@router.delete("/modelos/{id_}")
def apagar_modelo(id_: int, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    antes = _modelo(conn, ctx, id_)
    conn.execute("DELETE FROM modelo_mensagem WHERE id = ?", (id_,))
    anotar(request, "alteracao", "modelo.apagar", entidade="modelo", entidade_id=id_, antes=antes)
    return {"ok": True}


# ------------------------------------------------------------------ não contatar
# Incluir: qualquer pessoa da empresa (um pedido de descadastro não pode esperar).
# Liberar: só o admin. Itens globais: só o superadmin.

class ItemSupressao(BaseModel):
    tipo: Literal["email", "telefone", "dominio", "cnpj"]
    valor: str = Field(min_length=2, max_length=320)
    motivo: str | None = Field(default=None, max_length=300)
    global_: bool = Field(default=False, alias="global")


@router.get("/supressao")
def listar_supressao(conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> dict:
    itens = []
    for r in conn.execute("SELECT * FROM supressao WHERE empresa_id IN (?, ?) ORDER BY id DESC", (ctx.empresa_id, SUPRESSAO_GLOBAL)):
        item = dict(r)
        item["global"] = item.pop("empresa_id") == SUPRESSAO_GLOBAL
        itens.append(item)
    return {"itens": itens, "pode_liberar": ctx.e_admin, "pode_global": ctx.usuario.e_super}


@router.post("/supressao")
def adicionar_supressao(item: ItemSupressao, request: Request, conn: sqlite3.Connection = Depends(db),
                        ctx: Contexto = Depends(contexto)) -> dict:
    if item.global_ and not ctx.usuario.e_super:
        raise HTTPException(403, "Só o administrador do sistema cria bloqueios para todas as empresas.")
    valor = normalizar(item.tipo, item.valor)
    if not valor:
        raise HTTPException(422, "Valor inválido.")
    alvo = SUPRESSAO_GLOBAL if item.global_ else ctx.empresa_id
    cur = conn.execute(
        "INSERT OR IGNORE INTO supressao (empresa_id, tipo, valor, motivo, criado_em, criado_por) VALUES (?, ?, ?, ?, ?, ?)",
        (alvo, item.tipo, valor, (item.motivo or "").strip() or None, agora(), ctx.nome),
    )
    anotar(request, "alteracao", "supressao.incluir", entidade="supressao", entidade_id=cur.lastrowid if cur.rowcount else None,
           depois={"tipo": item.tipo, "valor": valor, "motivo": item.motivo, "global": item.global_},
           detalhe=None if cur.rowcount else "já estava na lista")
    return {"tipo": item.tipo, "valor": valor}


@router.delete("/supressao/{id_}")
def remover_supressao(id_: int, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    alvo = conn.execute("SELECT * FROM supressao WHERE id = ?", (id_,)).fetchone()
    if not alvo or alvo["empresa_id"] not in (ctx.empresa_id, SUPRESSAO_GLOBAL):
        raise HTTPException(404, "Item não encontrado.")
    if alvo["empresa_id"] == SUPRESSAO_GLOBAL and not ctx.usuario.e_super:
        raise HTTPException(403, "Este bloqueio vale para todas as empresas; só o administrador do sistema libera.")
    conn.execute("DELETE FROM supressao WHERE id = ?", (id_,))
    anotar(request, "alteracao", "supressao.liberar", entidade="supressao", entidade_id=id_,
           antes={"tipo": alvo["tipo"], "valor": alvo["valor"], "motivo": alvo["motivo"],
                  "global": alvo["empresa_id"] == SUPRESSAO_GLOBAL, "incluido_por": alvo["criado_por"]})
    return {"ok": True}
