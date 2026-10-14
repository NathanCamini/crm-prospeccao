from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auditoria import anotar, ator
from ..config import Config
from ..db import agora
from ..dependencias import RotaQueConfirma, Usuario, config, db, usuario_logado
from ..seguranca import (
    COOKIE,
    HASH_FALSO,
    conferir,
    criar_sessao,
    encerrar_sessao,
    encerrar_sessoes_do_usuario,
    gerar_hash,
    hash_token,
    validar_senha,
)

router = APIRouter(prefix="/api/auth", tags=["auth"], route_class=RotaQueConfirma)


class Entrada(BaseModel):
    usuario: str = Field(min_length=1, max_length=64)
    senha: str = Field(min_length=1, max_length=256)
    lembrar: bool = True


def descrever(conn: sqlite3.Connection, u: Usuario) -> dict:
    empresa_id = u.empresa_ativa if u.e_super else u.empresa_id
    empresa = None
    if empresa_id is not None:
        linha = conn.execute("SELECT id, nome FROM empresa WHERE id = ?", (empresa_id,)).fetchone()
        empresa = dict(linha) if linha else None
    dados = {
        "usuario": u.nome,
        "nome_exibicao": u.nome_exibicao,
        "papel": u.papel,
        "deve_trocar_senha": u.deve_trocar_senha,
        "empresa": empresa,
    }
    if u.e_super:
        dados["empresas"] = [dict(r) for r in conn.execute("SELECT id, nome, ativa FROM empresa ORDER BY nome")]
    return dados


@router.post("/entrar")
def entrar(
    dados: Entrada,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
) -> dict:
    nome = dados.usuario.strip().lower()
    linha = conn.execute(
        """SELECT u.*, e.ativa AS empresa_ativa FROM usuario u LEFT JOIN empresa e ON e.id = u.empresa_id
           WHERE u.nome = ?""",
        (nome,),
    ).fetchone()
    falha = conn.execute("SELECT falhas FROM login_falha WHERE usuario = ?", (nome,)).fetchone()
    falhas = falha["falhas"] if falha else 0

    senha_ok = conferir(dados.senha, linha["senha_hash"] if linha else HASH_FALSO)
    liberado = bool(linha) and linha["ativo"] == 1 and (linha["papel"] == "superadmin" or linha["empresa_ativa"] == 1)
    if not (senha_ok and liberado):
        falhas += 1
        conn.execute(
            "INSERT INTO login_falha (usuario, falhas, ultima) VALUES (?, ?, ?) "
            "ON CONFLICT(usuario) DO UPDATE SET falhas = excluded.falhas, ultima = excluded.ultima",
            (nome, falhas, agora()),
        )
        conn.commit()
        motivo = ("usuário inexistente" if not linha else "senha errada" if not senha_ok
                  else "pessoa desativada" if linha["ativo"] != 1 else "empresa desativada")
        anotar(request, "seguranca", "login.falhou", entidade="usuario", entidade_id=nome, usuario=nome,
               empresa_id=linha["empresa_id"] if linha else None,
               detalhe={"motivo": motivo, "tentativa": falhas}, sempre=True)
        time.sleep(min(cfg.atraso_login_base * 2 ** (falhas - 1), 8 * cfg.atraso_login_base))
        if senha_ok:
            raise HTTPException(403, "Acesso desativado. Fale com o administrador da sua empresa.")
        raise HTTPException(401, "Usuário ou senha inválidos.")

    conn.execute("DELETE FROM login_falha WHERE usuario = ?", (nome,))
    conn.execute("UPDATE usuario SET ultimo_acesso = ? WHERE nome = ?", (agora(), nome))
    empresa_ativa = None
    if linha["papel"] == "superadmin":
        primeira = conn.execute("SELECT id FROM empresa WHERE ativa = 1 ORDER BY nome LIMIT 1").fetchone()
        empresa_ativa = primeira["id"] if primeira else None
    token = criar_sessao(conn, nome, cfg.sessao_dias if dados.lembrar else 1, empresa_ativa)
    a = ator(request)
    a.usuario, a.papel = nome, linha["papel"]
    a.empresa_id = linha["empresa_id"] if linha["papel"] != "superadmin" else empresa_ativa
    anotar(request, "seguranca", "login.ok", entidade="usuario", entidade_id=nome,
           detalhe={"manter_conectado": dados.lembrar, "senha_provisoria": bool(linha["deve_trocar_senha"])})
    response.set_cookie(
        COOKIE,
        token,
        max_age=cfg.sessao_dias * 86400 if dados.lembrar else None,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="strict",
        path="/",
    )
    u = Usuario(
        nome=nome,
        nome_exibicao=linha["nome_exibicao"] or nome,
        papel=linha["papel"],
        empresa_id=linha["empresa_id"],
        empresa_ativa=empresa_ativa,
        deve_trocar_senha=bool(linha["deve_trocar_senha"]),
        token_hash=hash_token(token),
    )
    return descrever(conn, u)


@router.post("/sair")
def sair(request: Request, response: Response, conn: sqlite3.Connection = Depends(db)) -> dict:
    from ..seguranca import usuario_da_sessao

    sessao = usuario_da_sessao(conn, request.cookies.get(COOKIE))
    if sessao:
        a = ator(request)
        a.usuario, a.papel = sessao["nome"], sessao["papel"]
        a.empresa_id = sessao["empresa_id"] if sessao["papel"] != "superadmin" else sessao["empresa_ativa"]
        anotar(request, "seguranca", "sessao.sair", entidade="usuario", entidade_id=sessao["nome"])
    encerrar_sessao(conn, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/eu")
def eu(u: Usuario = Depends(usuario_logado), conn: sqlite3.Connection = Depends(db)) -> dict:
    return descrever(conn, u)


class TrocaSenha(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    senha_nova: str = Field(min_length=1, max_length=256)


@router.post("/senha")
def trocar_senha(dados: TrocaSenha, request: Request, u: Usuario = Depends(usuario_logado),
                 conn: sqlite3.Connection = Depends(db)) -> dict:
    atual = conn.execute("SELECT senha_hash FROM usuario WHERE nome = ?", (u.nome,)).fetchone()
    if not conferir(dados.senha_atual, atual["senha_hash"]):
        anotar(request, "seguranca", "senha.trocar_falhou", entidade="usuario", entidade_id=u.nome,
               detalhe={"motivo": "senha atual errada"}, sempre=True)
        raise HTTPException(422, "A senha atual não confere.")
    if erro := validar_senha(dados.senha_nova):
        raise HTTPException(422, erro)
    if dados.senha_nova == dados.senha_atual:
        raise HTTPException(422, "A senha nova precisa ser diferente da atual.")
    conn.execute(
        "UPDATE usuario SET senha_hash = ?, deve_trocar_senha = 0 WHERE nome = ?",
        (gerar_hash(dados.senha_nova), u.nome),
    )
    encerrar_sessoes_do_usuario(conn, u.nome, exceto_hash=u.token_hash)
    anotar(request, "seguranca", "senha.trocar", entidade="usuario", entidade_id=u.nome,
           detalhe={"era_provisoria": u.deve_trocar_senha, "outras_sessoes_encerradas": True})
    return {"ok": True}


class EscolhaEmpresa(BaseModel):
    empresa_id: int


@router.post("/empresa")
def escolher_empresa(dados: EscolhaEmpresa, request: Request, u: Usuario = Depends(usuario_logado),
                     conn: sqlite3.Connection = Depends(db)) -> dict:
    """Só o superadmin troca de empresa; os demais pertencem a uma só."""
    if not u.e_super:
        raise HTTPException(403, "Só o administrador do sistema troca de empresa.")
    if not conn.execute("SELECT 1 FROM empresa WHERE id = ?", (dados.empresa_id,)).fetchone():
        raise HTTPException(404, "Empresa não encontrada.")
    conn.execute("UPDATE sessao SET empresa_ativa = ? WHERE token_hash = ?", (dados.empresa_id, u.token_hash))
    anotar(request, "acesso", "empresa.entrar", entidade="empresa", entidade_id=dados.empresa_id,
           antes={"empresa_id": u.empresa_ativa}, depois={"empresa_id": dados.empresa_id}, empresa_id=dados.empresa_id)
    return descrever(conn, Usuario(**{**u.__dict__, "empresa_ativa": dados.empresa_id}))
