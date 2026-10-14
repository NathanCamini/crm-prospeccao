"""Produtos da empresa cliente: o que ela vende e para quem.

O administrador da empresa descreve o produto em texto; o agente (módulo 2, via módulo 3) monta o perfil
de leads, mostra uma prévia com o último mês e, ao salvar, classifica na hora. Daí em diante a rotina
mensal traz os leads novos do produto. Cada empresa vê só os leads dos próprios produtos.

Quem descreve é sempre o administrador da empresa; o superadmin acompanha, mas não escreve por ela.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auditoria import anotar, diferencas
from ..config import Config
from ..db import agora
from ..dependencias import RotaQueConfirma, Contexto, config, db, exige_admin, modulo3
from ..modulo3 import Modulo3

router = APIRouter(prefix="/api/produtos", tags=["produtos"], route_class=RotaQueConfirma)


def exige_admin_da_empresa(ctx: Contexto = Depends(exige_admin)) -> Contexto:
    if ctx.usuario.e_super:
        raise HTTPException(403, "Quem descreve os produtos é o administrador da empresa cliente.")
    return ctx


def _locais(conn: sqlite3.Connection, empresa_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM produto WHERE empresa_id = ? AND removido_em IS NULL ORDER BY criado_em",
                        (empresa_id,)).fetchall()


@router.get("")
def listar(request: Request, conn: sqlite3.Connection = Depends(db), cfg: Config = Depends(config),
           m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(exige_admin)) -> dict:
    locais = _locais(conn, ctx.empresa_id)
    try:
        do_agente = {p["id"]: p for p in m3.produtos(ctx.empresa_id)}
        aviso = None
    except HTTPException as erro:
        do_agente, aviso = {}, f"Não foi possível consultar o agente agora: {erro.detail}"
    itens, sincronizar = [], False
    for r in locais:
        a = do_agente.get(r["perfil_id"], {})
        execucao = a.get("execucao") or {}
        # Terminou uma classificação que o CRM ainda não copiou: copia já, sem esperar os 30 minutos.
        if execucao.get("situacao") == "pronto" and execucao.get("fim") and execucao["fim"] != r["execucao_sincronizada"]:
            conn.execute("UPDATE produto SET execucao_sincronizada = ? WHERE perfil_id = ?", (execucao["fim"], r["perfil_id"]))
            sincronizar = True
        itens.append({"id": r["perfil_id"], "nome": r["nome"], "descricao": r["descricao"], "versao": r["versao"],
                      "criado_por": r["criado_por"], "criado_em": r["criado_em"], "atualizado_em": r["atualizado_em"],
                      "filtro": a.get("filtro"), "criterios": a.get("criterios"), "execucao": a.get("execucao"),
                      "alvos": a.get("alvos")})
    if sincronizar:
        from ..sincronizacao import sincronizar_em_segundo_plano

        conn.commit()
        sincronizar_em_segundo_plano(cfg, m3)
    anotar(request, "acesso", "produtos.listar")
    return {"itens": itens, "aviso": aviso, "pode_editar": not ctx.usuario.e_super, "sincronizando": sincronizar}


class Descricao(BaseModel):
    descricao: str = Field(min_length=15, max_length=2000)
    perfil_id: str | None = Field(default=None, max_length=80)


def _do_produto(conn: sqlite3.Connection, ctx: Contexto, perfil_id: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM produto WHERE perfil_id = ? AND empresa_id = ? AND removido_em IS NULL",
                     (perfil_id, ctx.empresa_id)).fetchone()
    if r is None:
        raise HTTPException(404, "Produto não encontrado.")
    return r


@router.post("/sugerir")
def sugerir(dados: Descricao, request: Request, conn: sqlite3.Connection = Depends(db), m3: Modulo3 = Depends(modulo3),
            ctx: Contexto = Depends(exige_admin_da_empresa)) -> dict:
    if dados.perfil_id:
        _do_produto(conn, ctx, dados.perfil_id)
    proposta = m3.sugerir_produto(ctx.empresa_id, dados.descricao.strip(), dados.perfil_id)
    anotar(request, "acesso", "produto.sugerir", entidade="produto", entidade_id=dados.perfil_id,
           detalhe={"descricao": dados.descricao.strip(), "total_ultimo_mes": (proposta.get("previa") or {}).get("total")})
    return proposta


class Salvar(Descricao):
    perfil: dict


@router.post("")
def salvar(dados: Salvar, request: Request, conn: sqlite3.Connection = Depends(db), m3: Modulo3 = Depends(modulo3),
           ctx: Contexto = Depends(exige_admin_da_empresa)) -> dict:
    atual = _do_produto(conn, ctx, dados.perfil_id) if dados.perfil_id else None
    salvo = m3.salvar_produto(ctx.empresa_id, dados.descricao.strip(), dados.perfil, dados.perfil_id)
    perfil_id, nome = salvo["id"], (salvo.get("nome") or "Produto")[:120]
    agora_ = agora()
    if atual is None:
        conn.execute(
            """INSERT INTO produto (perfil_id, empresa_id, nome, descricao, versao, criado_por, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (perfil_id, ctx.empresa_id, nome, salvo.get("descricao") or dados.descricao.strip(), salvo.get("versao", 1),
             ctx.nome, agora_, agora_))
        anotar(request, "alteracao", "produto.criar", entidade="produto", entidade_id=perfil_id,
               depois={"nome": nome, "descricao": dados.descricao.strip()})
    else:
        conn.execute("UPDATE produto SET nome = ?, descricao = ?, versao = ?, atualizado_em = ? WHERE perfil_id = ?",
                     (nome, salvo.get("descricao") or dados.descricao.strip(), salvo.get("versao", atual["versao"] + 1),
                      agora_, perfil_id))
        a, d = diferencas({"nome": atual["nome"], "descricao": atual["descricao"]},
                          {"nome": nome, "descricao": dados.descricao.strip()})
        anotar(request, "alteracao", "produto.alterar", entidade="produto", entidade_id=perfil_id, antes=a, depois=d,
               detalhe={"versao": salvo.get("versao")})
    return salvo


@router.delete("/{perfil_id}")
def remover(perfil_id: str, request: Request, conn: sqlite3.Connection = Depends(db), m3: Modulo3 = Depends(modulo3),
            ctx: Contexto = Depends(exige_admin_da_empresa)) -> dict:
    atual = _do_produto(conn, ctx, perfil_id)
    try:
        m3.remover_produto(ctx.empresa_id, perfil_id)
    except HTTPException as erro:
        if erro.status_code != 404:  # já não existia no agente: basta tirar daqui
            raise
    conn.execute("UPDATE produto SET removido_em = ? WHERE perfil_id = ?", (agora(), perfil_id))
    anotar(request, "alteracao", "produto.remover", entidade="produto", entidade_id=perfil_id,
           antes={"nome": atual["nome"], "descricao": atual["descricao"]})
    return {"ok": True}
