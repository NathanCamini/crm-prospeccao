"""Equipe da empresa cliente: cadastro de funcionários e acompanhamento do trabalho.

Tudo aqui é do admin da empresa (ou do superadmin dentro dela), exceto
`/api/equipe/nomes`, que qualquer pessoa usa para ver nomes na lista de leads.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auditoria import anotar, diferencas
from ..db import agora, dias_atras
from ..dependencias import RotaQueConfirma, Contexto, contexto, db, exige_admin
from ..seguranca import encerrar_sessoes_do_usuario, gerar_hash, validar_nome, validar_senha

router = APIRouter(prefix="/api/equipe", tags=["equipe"], route_class=RotaQueConfirma)

Papel = Literal["admin", "vendedor"]


def _membro(conn: sqlite3.Connection, ctx: Contexto, nome: str) -> sqlite3.Row:
    linha = conn.execute(
        "SELECT * FROM usuario WHERE nome = ? AND empresa_id = ?", (nome.strip().lower(), ctx.empresa_id)
    ).fetchone()
    if not linha:
        raise HTTPException(404, "Pessoa não encontrada nesta empresa.")
    return linha


@router.get("/nomes")
def nomes(conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> list[dict]:
    linhas = conn.execute(
        "SELECT nome AS usuario, nome_exibicao, papel, ativo FROM usuario WHERE empresa_id = ? ORDER BY nome_exibicao",
        (ctx.empresa_id,),
    ).fetchall()
    resultado = [dict(r) for r in linhas]
    if ctx.usuario.e_super:
        resultado.append({"usuario": ctx.nome, "nome_exibicao": ctx.usuario.nome_exibicao, "papel": "superadmin", "ativo": 1})
    return resultado


@router.get("")
def listar(conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> list[dict]:
    membros = [
        dict(r) for r in conn.execute(
            "SELECT nome AS usuario, nome_exibicao, papel, ativo, deve_trocar_senha, criado_em, ultimo_acesso "
            "FROM usuario WHERE empresa_id = ? ORDER BY ativo DESC, nome_exibicao",
            (ctx.empresa_id,),
        )
    ]
    por_estagio: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT responsavel, estagio, COUNT(*) AS n FROM lead_estado WHERE empresa_id = ? AND responsavel IS NOT NULL "
        "GROUP BY responsavel, estagio",
        (ctx.empresa_id,),
    ):
        por_estagio.setdefault(r["responsavel"], {})[r["estagio"]] = r["n"]
    semana = dias_atras(7)
    atividade = {
        r["criado_por"]: dict(r)
        for r in conn.execute(
            """SELECT criado_por,
                      SUM(CASE WHEN tipo = 'contato' AND criado_em >= ? THEN 1 ELSE 0 END) AS contatos_7d,
                      SUM(CASE WHEN criado_em >= ? THEN 1 ELSE 0 END) AS atividades_7d,
                      MAX(criado_em) AS ultima_atividade
               FROM atividade WHERE empresa_id = ? GROUP BY criado_por""",
            (semana, semana, ctx.empresa_id),
        )
    }
    for m in membros:
        estagios = por_estagio.get(m["usuario"], {})
        a = atividade.get(m["usuario"], {})
        m["leads"] = sum(estagios.values())
        m["leads_por_estagio"] = estagios
        m["clientes"] = estagios.get("cliente", 0)
        m["contatos_7d"] = a.get("contatos_7d", 0) or 0
        m["atividades_7d"] = a.get("atividades_7d", 0) or 0
        m["ultima_atividade"] = a.get("ultima_atividade")
        m["ativo"] = bool(m["ativo"])
        m["deve_trocar_senha"] = bool(m["deve_trocar_senha"])
    return membros


class NovoMembro(BaseModel):
    usuario: str = Field(min_length=2, max_length=64)
    nome_exibicao: str = Field(min_length=1, max_length=120)
    papel: Papel = "vendedor"
    senha_inicial: str = Field(min_length=1, max_length=256)


@router.post("")
def criar(dados: NovoMembro, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    nome = dados.usuario.strip().lower()
    if erro := validar_nome(nome) or validar_senha(dados.senha_inicial):
        raise HTTPException(422, erro)
    if conn.execute("SELECT 1 FROM usuario WHERE nome = ?", (nome,)).fetchone():
        raise HTTPException(409, "Esse nome de usuário já existe. Escolha outro.")
    conn.execute(
        "INSERT INTO usuario (nome, nome_exibicao, senha_hash, papel, empresa_id, deve_trocar_senha, criado_em, criado_por) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (nome, dados.nome_exibicao.strip(), gerar_hash(dados.senha_inicial), dados.papel, ctx.empresa_id, agora(), ctx.nome),
    )
    anotar(request, "alteracao", "equipe.criar", entidade="usuario", entidade_id=nome,
           depois={"nome_exibicao": dados.nome_exibicao.strip(), "papel": dados.papel, "ativo": True, "senha": "provisória"})
    return {"usuario": nome}


class Alteracao(BaseModel):
    nome_exibicao: str | None = Field(default=None, min_length=1, max_length=120)
    papel: Papel | None = None
    ativo: bool | None = None


@router.put("/{nome}")
def alterar(nome: str, dados: Alteracao, request: Request, conn: sqlite3.Connection = Depends(db),
            ctx: Contexto = Depends(exige_admin)) -> dict:
    membro = _membro(conn, ctx, nome)
    antes = {"nome_exibicao": membro["nome_exibicao"], "papel": membro["papel"], "ativo": bool(membro["ativo"])}
    eu_mesmo = membro["nome"] == ctx.nome
    if eu_mesmo and (dados.papel == "vendedor" or dados.ativo is False):
        raise HTTPException(422, "Você não pode tirar o seu próprio acesso de administrador.")
    if dados.nome_exibicao is not None:
        conn.execute("UPDATE usuario SET nome_exibicao = ? WHERE nome = ?", (dados.nome_exibicao.strip(), membro["nome"]))
    if dados.papel is not None:
        conn.execute("UPDATE usuario SET papel = ? WHERE nome = ?", (dados.papel, membro["nome"]))
    if dados.ativo is not None:
        conn.execute("UPDATE usuario SET ativo = ? WHERE nome = ?", (int(dados.ativo), membro["nome"]))
        if not dados.ativo:
            encerrar_sessoes_do_usuario(conn, membro["nome"])
    depois = antes | {k: v for k, v in {"nome_exibicao": (dados.nome_exibicao or "").strip() or None,
                                         "papel": dados.papel, "ativo": dados.ativo}.items() if v is not None}
    a, d = diferencas(antes, depois)
    if a or d:
        anotar(request, "alteracao", "equipe.alterar", entidade="usuario", entidade_id=membro["nome"], antes=a, depois=d)
    return {"ok": True}


class NovaSenha(BaseModel):
    senha_provisoria: str = Field(min_length=1, max_length=256)


@router.post("/{nome}/senha")
def redefinir_senha(nome: str, dados: NovaSenha, request: Request, conn: sqlite3.Connection = Depends(db),
                    ctx: Contexto = Depends(exige_admin)) -> dict:
    membro = _membro(conn, ctx, nome)
    if erro := validar_senha(dados.senha_provisoria):
        raise HTTPException(422, erro)
    conn.execute(
        "UPDATE usuario SET senha_hash = ?, deve_trocar_senha = 1 WHERE nome = ?",
        (gerar_hash(dados.senha_provisoria), membro["nome"]),
    )
    encerrar_sessoes_do_usuario(conn, membro["nome"])
    anotar(request, "seguranca", "senha.provisoria", entidade="usuario", entidade_id=membro["nome"],
           detalhe="senha provisória criada pelo administrador; sessões da pessoa encerradas")
    return {"ok": True}


@router.delete("/{nome}")
def remover(nome: str, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(exige_admin)) -> dict:
    """Remove o acesso. O histórico continua com o nome da pessoa; os leads dela ficam livres."""
    membro = _membro(conn, ctx, nome)
    if membro["nome"] == ctx.nome:
        raise HTTPException(422, "Você não pode remover a si mesmo.")
    leads = [r["chave"] for r in conn.execute(
        "SELECT chave FROM lead_estado WHERE empresa_id = ? AND responsavel = ?", (ctx.empresa_id, membro["nome"]))]
    conn.execute(
        "UPDATE lead_estado SET responsavel = NULL, atualizado_em = ?, atualizado_por = ? WHERE empresa_id = ? AND responsavel = ?",
        (agora(), ctx.nome, ctx.empresa_id, membro["nome"]),
    )
    tarefas = conn.execute(
        "UPDATE tarefa SET responsavel = ? WHERE empresa_id = ? AND responsavel = ? AND concluida_em IS NULL",
        (ctx.nome, ctx.empresa_id, membro["nome"]),
    ).rowcount
    conn.execute("DELETE FROM usuario WHERE nome = ?", (membro["nome"],))
    anotar(request, "alteracao", "equipe.remover", entidade="usuario", entidade_id=membro["nome"],
           antes={"nome_exibicao": membro["nome_exibicao"], "papel": membro["papel"], "ativo": bool(membro["ativo"])},
           detalhe={"leads_liberados": leads, "tarefas_passadas_para": ctx.nome, "tarefas": tarefas})
    return {"ok": True, "leads_liberados": len(leads), "tarefas_transferidas": tarefas}


@router.get("/atividades")
def atividades(
    usuario: str | None = None,
    limite: int = Query(default=100, ge=1, le=300),
    conn: sqlite3.Connection = Depends(db),
    ctx: Contexto = Depends(exige_admin),
) -> list[dict]:
    """O que a equipe fez por último, com o nome de cada lead."""
    sql = """SELECT a.id, a.chave, a.tipo, a.canal, a.destino, a.assunto, a.texto, a.criado_em, a.criado_por, l.nome AS empresa_nome
             FROM atividade a LEFT JOIN lead l ON l.chave = a.chave WHERE a.empresa_id = ?"""
    params: list = [ctx.empresa_id]
    if usuario:
        sql += " AND a.criado_por = ?"
        params.append(usuario.strip().lower())
    linhas = [dict(r) for r in conn.execute(sql + " ORDER BY a.id DESC LIMIT ?", [*params, limite])]
    for a in linhas:
        if a["texto"] and len(a["texto"]) > 280:
            a["texto"] = a["texto"][:280] + "…"
    return linhas
