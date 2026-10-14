"""Quem está pedindo e em nome de qual empresa cliente.

Papéis:
- superadmin: o dono do sistema. Criado só pelo terminal. Gerencia empresas
  clientes e entra em qualquer uma com poderes de admin.
- admin: administra uma empresa cliente. Cadastra funcionários, vê tudo o que
  eles fazem, muda estágio e responsável de qualquer lead, edita modelos.
- vendedor: trabalha os leads livres e os seus. Não mexe nos dos colegas.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRoute

from .auditoria import ator
from .config import Config
from .db import conectar
from .modulo3 import Modulo3
from .seguranca import COOKIE, usuario_da_sessao


@dataclass(frozen=True)
class Usuario:
    nome: str
    nome_exibicao: str
    papel: str
    empresa_id: int | None
    empresa_ativa: int | None
    deve_trocar_senha: bool
    token_hash: str

    @property
    def e_super(self) -> bool:
        return self.papel == "superadmin"

    @property
    def e_admin(self) -> bool:
        return self.papel in ("admin", "superadmin")


@dataclass(frozen=True)
class Contexto:
    """Usuário + empresa cliente em que ele está trabalhando agora."""

    usuario: Usuario
    empresa_id: int
    empresa_nome: str
    perfis: list[str]                 # perfis que a empresa vê; vazio = todos, menos `perfis_ocultos`
    perfis_ocultos: list[str] = field(default_factory=list)  # produtos de outras empresas: nunca aparecem

    def ve_perfil(self, perfil_id: str | None) -> bool:
        if self.perfis:
            return perfil_id in self.perfis
        return perfil_id not in self.perfis_ocultos

    def condicao_perfis(self, coluna: str, params: dict) -> str:
        """Condição SQL que limita `coluna` (um perfil_id) aos perfis que a empresa vê."""
        lista, operador = (self.perfis, "IN") if self.perfis else (self.perfis_ocultos, "NOT IN")
        if not lista:
            return "1"
        nomes = [f":vperfil{i}" for i in range(len(lista))]
        params |= {f"vperfil{i}": v for i, v in enumerate(lista)}
        return f"{coluna} {operador} ({', '.join(nomes)})"

    @property
    def e_admin(self) -> bool:
        return self.usuario.e_admin

    @property
    def nome(self) -> str:
        return self.usuario.nome


def config(request: Request) -> Config:
    return request.app.state.config


def modulo3(request: Request) -> Modulo3:
    return request.app.state.modulo3


class RotaQueConfirma(APIRoute):
    """Confirma a transação da requisição assim que a rota devolve a resposta.

    O FastAPI só fecha as dependências com `yield` depois de enviar a resposta, e o
    middleware de auditoria grava antes disso. Sem este passo, a auditoria
    esperaria pelo lock de escrita que a própria requisição ainda segura.
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                resposta = await original(request)
            except Exception:
                conn = getattr(request.state, "conexao", None)
                if conn is not None:
                    conn.rollback()
                raise
            conn = getattr(request.state, "conexao", None)
            if conn is not None:
                if resposta.status_code < 400:
                    conn.commit()
                else:
                    conn.rollback()
            return resposta

        return handler


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = conectar(request.app.state.config.db_path)
    request.state.conexao = conn
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def usuario_logado(request: Request, conn: sqlite3.Connection = Depends(db)) -> Usuario:
    """Qualquer sessão válida, inclusive de quem ainda precisa trocar a senha."""
    linha = usuario_da_sessao(conn, request.cookies.get(COOKIE))
    # Confirma já o "último uso" da sessão: assim a requisição não segura o lock de
    # escrita enquanto lê, e outras conexões (sincronização, auditoria) não esperam.
    conn.commit()
    if not linha:
        raise HTTPException(401, "Sessão expirada. Entre de novo.")
    a = ator(request)
    a.usuario, a.papel = linha["nome"], linha["papel"]
    a.empresa_id = linha["empresa_id"] if linha["papel"] != "superadmin" else linha["empresa_ativa"]
    return Usuario(
        nome=linha["nome"],
        nome_exibicao=linha["nome_exibicao"] or linha["nome"],
        papel=linha["papel"],
        empresa_id=linha["empresa_id"],
        empresa_ativa=linha["empresa_ativa"],
        deve_trocar_senha=bool(linha["deve_trocar_senha"]),
        token_hash=linha["token_hash"],
    )


def usuario_liberado(u: Usuario = Depends(usuario_logado)) -> Usuario:
    if u.deve_trocar_senha:
        raise HTTPException(403, "Troque a senha provisória antes de continuar.")
    return u


def exige_super(u: Usuario = Depends(usuario_liberado)) -> Usuario:
    if not u.e_super:
        raise HTTPException(403, "Só o administrador do sistema pode fazer isso.")
    return u


def contexto(request: Request, u: Usuario = Depends(usuario_liberado), conn: sqlite3.Connection = Depends(db)) -> Contexto:
    empresa_id = u.empresa_ativa if u.e_super else u.empresa_id
    if empresa_id is None:
        raise HTTPException(409, "Escolha uma empresa cliente para trabalhar.")
    empresa = conn.execute("SELECT id, nome, perfis, ativa FROM empresa WHERE id = ?", (empresa_id,)).fetchone()
    if not empresa:
        raise HTTPException(409, "Escolha uma empresa cliente para trabalhar.")
    ator(request).empresa_id = empresa["id"]
    # Perfis escolhidos pelo superadmin + produtos que a própria empresa descreveu (inclusive os removidos,
    # para os leads já trabalhados continuarem visíveis). Produto de outra empresa nunca aparece.
    proprios = [r[0] for r in conn.execute("SELECT perfil_id FROM produto WHERE empresa_id = ?", (empresa["id"],))]
    alheios = [r[0] for r in conn.execute("SELECT perfil_id FROM produto WHERE empresa_id != ?", (empresa["id"],))]
    perfis = sorted(set(json.loads(empresa["perfis"])) | set(proprios))
    return Contexto(usuario=u, empresa_id=empresa["id"], empresa_nome=empresa["nome"], perfis=perfis,
                    perfis_ocultos=alheios)


def exige_admin(ctx: Contexto = Depends(contexto)) -> Contexto:
    if not ctx.e_admin:
        raise HTTPException(403, "Só o administrador da empresa pode fazer isso.")
    return ctx
