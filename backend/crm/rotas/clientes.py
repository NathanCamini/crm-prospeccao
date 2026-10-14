"""Empresas clientes do sistema. Só o superadmin acessa."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auditoria import anotar, diferencas
from ..config import Config
from ..db import agora, criar_empresa, dias_atras
from ..dependencias import RotaQueConfirma, Usuario, config, db, exige_super, modulo3
from ..modulo3 import Modulo3
from ..seguranca import encerrar_sessoes_do_usuario, gerar_hash, validar_nome, validar_senha

router = APIRouter(prefix="/api/admin", tags=["clientes"], route_class=RotaQueConfirma)


@router.get("/perfis")
def todos_os_perfis(m3: Modulo3 = Depends(modulo3), conn: sqlite3.Connection = Depends(db),
                    _: Usuario = Depends(exige_super)) -> list[dict]:
    try:
        return m3.perfis()
    except HTTPException:
        return [{"id": r[0], "nome": r[0], "produto": None, "total_alvos": r[1]} for r in conn.execute(
            "SELECT perfil_id, SUM(e_alvo) FROM lead WHERE origem = 'modulo3' AND perfil_id IS NOT NULL GROUP BY perfil_id")]


@router.get("/empresas")
def listar(conn: sqlite3.Connection = Depends(db), _: Usuario = Depends(exige_super)) -> list[dict]:
    semana = dias_atras(7)
    linhas = conn.execute(
        """SELECT e.id, e.nome, e.documento, e.perfis, e.ativa, e.criada_em,
                  (SELECT COUNT(*) FROM usuario u WHERE u.empresa_id = e.id AND u.ativo = 1) AS pessoas,
                  (SELECT group_concat(u.nome_exibicao, ', ') FROM usuario u WHERE u.empresa_id = e.id AND u.papel = 'admin') AS admins,
                  (SELECT COUNT(*) FROM lead_estado l WHERE l.empresa_id = e.id AND l.estagio != 'novo') AS leads_trabalhados,
                  (SELECT COUNT(*) FROM lead_estado l WHERE l.empresa_id = e.id AND l.estagio = 'cliente') AS clientes,
                  (SELECT COUNT(*) FROM atividade a WHERE a.empresa_id = e.id AND a.tipo = 'contato' AND a.criado_em >= ?) AS contatos_7d,
                  (SELECT MAX(criado_em) FROM atividade a WHERE a.empresa_id = e.id) AS ultima_atividade
           FROM empresa e ORDER BY e.ativa DESC, e.nome""",
        (semana,),
    ).fetchall()
    resultado = []
    for r in linhas:
        item = dict(r)
        item["perfis"] = json.loads(item["perfis"])
        item["ativa"] = bool(item["ativa"])
        resultado.append(item)
    return resultado


class PrimeiroAdmin(BaseModel):
    usuario: str = Field(min_length=2, max_length=64)
    nome_exibicao: str = Field(min_length=1, max_length=120)
    senha_inicial: str = Field(min_length=1, max_length=256)


class NovaEmpresa(BaseModel):
    nome: str = Field(min_length=2, max_length=160)
    documento: str | None = Field(default=None, max_length=32)
    perfis: list[str] = []
    admin: PrimeiroAdmin


@router.post("/empresas")
def criar(dados: NovaEmpresa, request: Request, conn: sqlite3.Connection = Depends(db), u: Usuario = Depends(exige_super)) -> dict:
    nome_admin = dados.admin.usuario.strip().lower()
    if erro := validar_nome(nome_admin) or validar_senha(dados.admin.senha_inicial):
        raise HTTPException(422, erro)
    if conn.execute("SELECT 1 FROM empresa WHERE nome = ?", (dados.nome.strip(),)).fetchone():
        raise HTTPException(409, "Já existe uma empresa com esse nome.")
    if conn.execute("SELECT 1 FROM usuario WHERE nome = ?", (nome_admin,)).fetchone():
        raise HTTPException(409, "Esse nome de usuário já existe. Escolha outro para o administrador.")
    empresa_id = criar_empresa(
        conn, dados.nome.strip(), (dados.documento or "").strip() or None, json.dumps(sorted(set(dados.perfis)))
    )
    conn.execute(
        "INSERT INTO usuario (nome, nome_exibicao, senha_hash, papel, empresa_id, deve_trocar_senha, criado_em, criado_por) "
        "VALUES (?, ?, ?, 'admin', ?, 1, ?, ?)",
        (nome_admin, dados.admin.nome_exibicao.strip(), gerar_hash(dados.admin.senha_inicial), empresa_id, agora(), u.nome),
    )
    anotar(request, "alteracao", "empresa.criar", entidade="empresa", entidade_id=empresa_id, empresa_id=empresa_id,
           depois={"nome": dados.nome.strip(), "documento": dados.documento, "perfis": sorted(set(dados.perfis)), "ativa": True})
    anotar(request, "alteracao", "equipe.criar", entidade="usuario", entidade_id=nome_admin, empresa_id=empresa_id,
           depois={"nome_exibicao": dados.admin.nome_exibicao.strip(), "papel": "admin", "ativo": True, "senha": "provisória"})
    return {"id": empresa_id, "admin": nome_admin}


class Alteracao(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=160)
    documento: str | None = Field(default=None, max_length=32)
    perfis: list[str] | None = None
    ativa: bool | None = None


@router.put("/empresas/{empresa_id}")
def alterar(empresa_id: int, dados: Alteracao, request: Request, conn: sqlite3.Connection = Depends(db),
            _: Usuario = Depends(exige_super)) -> dict:
    atual = conn.execute("SELECT nome, documento, perfis, ativa FROM empresa WHERE id = ?", (empresa_id,)).fetchone()
    if not atual:
        raise HTTPException(404, "Empresa não encontrada.")
    antes = {"nome": atual["nome"], "documento": atual["documento"], "perfis": json.loads(atual["perfis"]), "ativa": bool(atual["ativa"])}
    try:
        if dados.nome is not None:
            conn.execute("UPDATE empresa SET nome = ? WHERE id = ?", (dados.nome.strip(), empresa_id))
    except sqlite3.IntegrityError as erro:
        raise HTTPException(409, "Já existe uma empresa com esse nome.") from erro
    if dados.documento is not None:
        conn.execute("UPDATE empresa SET documento = ? WHERE id = ?", (dados.documento.strip() or None, empresa_id))
    if dados.perfis is not None:
        conn.execute("UPDATE empresa SET perfis = ? WHERE id = ?", (json.dumps(sorted(set(dados.perfis))), empresa_id))
    if dados.ativa is not None:
        conn.execute("UPDATE empresa SET ativa = ? WHERE id = ?", (int(dados.ativa), empresa_id))
        if not dados.ativa:
            for r in conn.execute("SELECT nome FROM usuario WHERE empresa_id = ?", (empresa_id,)).fetchall():
                encerrar_sessoes_do_usuario(conn, r["nome"])
    novo = conn.execute("SELECT nome, documento, perfis, ativa FROM empresa WHERE id = ?", (empresa_id,)).fetchone()
    depois = {"nome": novo["nome"], "documento": novo["documento"], "perfis": json.loads(novo["perfis"]), "ativa": bool(novo["ativa"])}
    a, d = diferencas(antes, depois)
    if a or d:
        anotar(request, "alteracao", "empresa.alterar", entidade="empresa", entidade_id=empresa_id, empresa_id=empresa_id, antes=a, depois=d)
    return {"ok": True}


@router.get("/sincronizacao")
def historico_sincronizacao(conn: sqlite3.Connection = Depends(db), _: Usuario = Depends(exige_super)) -> dict:
    execucoes = [dict(r) for r in conn.execute("SELECT * FROM sincronizacao ORDER BY id DESC LIMIT 10")]
    contagem = dict(conn.execute(
        """SELECT COUNT(*) AS total, SUM(origem = 'modulo3') AS do_agente, SUM(origem = 'manual') AS manuais,
                  SUM(origem = 'modulo3' AND ausente_desde IS NOT NULL) AS fora_da_base FROM lead""").fetchone())
    return {"execucoes": execucoes, "leads": contagem}


@router.post("/sincronizacao")
def sincronizar_agora(request: Request, cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3),
                      _: Usuario = Depends(exige_super)) -> dict:
    from ..sincronizacao import sincronizar

    resultado = sincronizar(cfg, m3)
    anotar(request, "alteracao", "sincronizacao.manual", detalhe=resultado)
    return resultado
