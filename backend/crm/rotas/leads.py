"""Leads da empresa cliente: lista, ficha, cadastro manual, estágio, responsável,
contato, próximos passos, vendas e supressão.

Os leads vêm da cópia local (tabela `lead`), alimentada pelo módulo 3 e pelo
cadastro manual. Regras de visibilidade:
- lead do módulo 3: aparece se for do perfil da empresa e estiver na base atual,
  ou, mesmo fora dela, se a empresa já trabalhou nele;
- lead manual: só para a empresa que o cadastrou;
- admin (e o superadmin dentro da empresa) vê todos; vendedor vê os livres e os seus.

Toda alteração anota na auditoria o que mudou (antes e depois).
"""

from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ..auditoria import anotar
from ..config import Config
from ..db import SUPRESSAO_GLOBAL, agora
from ..dependencias import RotaQueConfirma, Contexto, config, contexto, db, modulo3
from ..mensagens import (
    chaves_destino,
    chaves_supressao,
    cnpj_valido,
    montar_url,
    normalizar,
    renderizar,
    so_digitos,
)
from ..mensagens import contexto as contexto_mensagem
from ..modulo3 import Modulo3
from ..sincronizacao import buscar_e_gravar, normalizar_busca, sem_acento_maiusculo
from ..tempo import hoje

router = APIRouter(prefix="/api/leads", tags=["leads"], route_class=RotaQueConfirma)

ESTAGIOS = ("novo", "em_contato", "respondeu", "interessado", "amostra_enviada", "cliente", "sem_interesse", "descartado")
Estagio = Literal["novo", "em_contato", "respondeu", "interessado", "amostra_enviada", "cliente", "sem_interesse", "descartado"]
Canal = Literal["email", "whatsapp", "formulario", "telefone"]


class FiltroCRM(BaseModel):
    perfil_id: str | None = None
    uf: list[str] = []
    municipio: str | None = None
    score_min: int = Field(default=0, ge=0, le=100)
    texto: str | None = None
    ordenar: Literal["score", "abertura", "nome", "atividade", "proximo_passo", "cadastro"] = "score"
    estagio: Estagio | None = None
    # "eu", "ninguem" ou o nome de um usuário da empresa
    responsavel: str | None = None
    origem: Literal["agente", "manual"] | None = None
    pagina: int = Field(default=1, ge=1)
    por_pagina: int = Field(default=25, ge=1, le=100)


# ================================================================== carregar e checar acesso

def normalizar_chave(chave: str) -> str:
    return normalizar("cnpj", chave)


def _estado(conn: sqlite3.Connection, ctx: Contexto, chave: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM lead_estado WHERE empresa_id = ? AND chave = ?", (ctx.empresa_id, chave)).fetchone()


def _do_lead(conn: sqlite3.Connection, ctx: Contexto, chave: str) -> sqlite3.Row | None:
    p: dict = {"emp": ctx.empresa_id, "chave": chave}
    return conn.execute(f"SELECT l.*, {COLUNAS_VP} {_from(ctx, p)} WHERE l.chave = :chave", p).fetchone()


def carregar_lead(conn: sqlite3.Connection, ctx: Contexto, chave: str, cfg: Config, m3: Modulo3) -> sqlite3.Row:
    """Lead visível para a empresa; 404 para qualquer outro (inclusive manual de outra empresa)."""
    chave = normalizar_chave(chave)
    linha = _do_lead(conn, ctx, chave)
    if linha is None and len(chave) == 14 and buscar_e_gravar(conn, m3, chave):
        linha = _do_lead(conn, ctx, chave)
    if linha is None:
        raise HTTPException(404, "Lead não encontrado.")
    if linha["origem"] == "manual":
        if linha["empresa_id"] != ctx.empresa_id:
            raise HTTPException(404, "Lead não encontrado.")
        return linha
    if linha["vp_perfil"] is None:  # nenhum perfil que a empresa vê (ex.: produto de outra empresa cliente)
        raise HTTPException(404, "Lead não encontrado.")
    if (linha["ausente_desde"] or not linha["vp_alvo"]) and _estado(conn, ctx, chave) is None:
        raise HTTPException(404, "Lead não encontrado.")
    return linha


def dados_do_lead(linha: sqlite3.Row) -> dict:
    """O lead como a empresa o vê: com a classificação do perfil dela, nunca as dos produtos de outras empresas."""
    lead = json.loads(linha["dados"])
    lead.pop("classificacoes", None)
    if "vp_classificacao" in linha.keys() and linha["vp_classificacao"]:
        lead["classificacao"] = json.loads(linha["vp_classificacao"])
    lead["chave"] = linha["chave"]
    lead["origem"] = linha["origem"]
    lead["ausente_desde"] = linha["ausente_desde"]
    return lead


def exigir_acesso(conn: sqlite3.Connection, ctx: Contexto, chave: str) -> sqlite3.Row | None:
    """Vendedor não acessa lead de colega."""
    est = _estado(conn, ctx, chave)
    if not ctx.e_admin and est and est["responsavel"] not in (None, ctx.nome):
        raise HTTPException(403, "Este lead está com outro vendedor. Peça ao administrador para transferir.")
    return est


def _lead_com_acesso(request: Request, conn, ctx, chave, cfg, m3) -> tuple[sqlite3.Row, sqlite3.Row | None]:
    linha = carregar_lead(conn, ctx, chave, cfg, m3)
    return linha, exigir_acesso(conn, ctx, linha["chave"])


def atividade(conn: sqlite3.Connection, ctx: Contexto, chave: str, tipo: str, texto: str | None = None, *,
              canal: str | None = None, destino: str | None = None, assunto: str | None = None) -> None:
    conn.execute(
        "INSERT INTO atividade (empresa_id, chave, tipo, canal, destino, assunto, texto, criado_em, criado_por) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx.empresa_id, chave, tipo, canal, destino, assunto, texto, agora(), ctx.nome),
    )


def garantir_estado(request: Request, conn: sqlite3.Connection, ctx: Contexto, chave: str, assumir: bool) -> sqlite3.Row:
    """Cria o registro do lead se não existir; se `assumir` e o lead estiver livre, passa a ser do usuário."""
    est = _estado(conn, ctx, chave)
    if est is None:
        conn.execute(
            "INSERT INTO lead_estado (empresa_id, chave, estagio, responsavel, atualizado_em, atualizado_por) VALUES (?, ?, 'novo', ?, ?, ?)",
            (ctx.empresa_id, chave, ctx.nome if assumir else None, agora(), ctx.nome),
        )
    elif assumir and est["responsavel"] is None:
        conn.execute(
            "UPDATE lead_estado SET responsavel = ?, atualizado_em = ?, atualizado_por = ? WHERE empresa_id = ? AND chave = ?",
            (ctx.nome, agora(), ctx.nome, ctx.empresa_id, chave),
        )
    else:
        return est
    if assumir:
        atividade(conn, ctx, chave, "responsavel", assunto=f"—→{ctx.nome}")
        anotar(request, "alteracao", "lead.responsavel", entidade="lead", entidade_id=chave,
               antes={"responsavel": None}, depois={"responsavel": ctx.nome}, detalhe="assumiu automaticamente ao trabalhar o lead")
    return _estado(conn, ctx, chave)


def mudar_estagio(request: Request, conn: sqlite3.Connection, ctx: Contexto, chave: str, novo: str,
                  nota: str | None = None, motivo: str | None = None) -> str:
    est = garantir_estado(request, conn, ctx, chave, assumir=not ctx.e_admin)
    anterior = est["estagio"]
    if anterior == novo and not nota:
        return anterior
    conn.execute(
        "UPDATE lead_estado SET estagio = ?, atualizado_em = ?, atualizado_por = ? WHERE empresa_id = ? AND chave = ?",
        (novo, agora(), ctx.nome, ctx.empresa_id, chave),
    )
    atividade(conn, ctx, chave, "estagio", nota, assunto=f"{anterior}→{novo}")
    anotar(request, "alteracao", "lead.estagio", entidade="lead", entidade_id=chave,
           antes={"estagio": anterior}, depois={"estagio": novo}, detalhe={"nota": nota, "motivo": motivo} if nota or motivo else None)
    return anterior


# ================================================================== lista, resumo e exportação

def _from(ctx: Contexto, p: dict) -> str:
    """lead + estado da empresa + a classificação visível (`vp`): a melhor entre os perfis que a empresa vê."""
    return f"""FROM lead l LEFT JOIN lead_estado e ON e.empresa_id = :emp AND e.chave = l.chave
        LEFT JOIN (SELECT lp.chave, lp.perfil_id AS vp_perfil, lp.score AS vp_score, lp.e_alvo AS vp_alvo,
                          lp.classificacao AS vp_classificacao, MAX(lp.e_alvo * 1000 + COALESCE(lp.score, 0)) AS vp_ordem
                   FROM lead_perfil lp WHERE {ctx.condicao_perfis("lp.perfil_id", p)} GROUP BY lp.chave) vp
            ON vp.chave = l.chave"""


COLUNAS_VP = "vp.vp_perfil, vp.vp_score, vp.vp_alvo, vp.vp_classificacao"


def _onde(ctx: Contexto, f: FiltroCRM, com_estagio: bool) -> tuple[str, str, dict]:
    p: dict = {"emp": ctx.empresa_id, "eu": ctx.nome}
    origem = _from(ctx, p)
    conds = [
        """((l.origem = 'modulo3' AND vp.chave IS NOT NULL
             AND ((vp.vp_alvo = 1 AND l.ausente_desde IS NULL) OR e.chave IS NOT NULL))
            OR (l.origem = 'manual' AND l.empresa_id = :emp))"""
    ]
    if not ctx.e_admin:
        conds.append("(e.responsavel IS NULL OR e.responsavel = :eu)")
    if f.origem:
        conds.append("l.origem = :origem")
        p["origem"] = "modulo3" if f.origem == "agente" else "manual"
    if f.perfil_id:
        if not ctx.ve_perfil(f.perfil_id):
            conds.append("0")
        conds.append("EXISTS (SELECT 1 FROM lead_perfil x WHERE x.chave = l.chave AND x.perfil_id = :perfil)")
        p["perfil"] = f.perfil_id
    if f.uf:
        nomes = [f":uf{i}" for i in range(len(f.uf))]
        p |= {f"uf{i}": v.upper() for i, v in enumerate(f.uf)}
        conds.append(f"l.uf IN ({', '.join(nomes)})")
    if f.municipio and (m := sem_acento_maiusculo(f.municipio)):
        conds.append("l.municipio LIKE :municipio")
        p["municipio"] = f"%{m}%"
    if f.score_min:
        conds.append("COALESCE(vp.vp_score, l.score, 0) >= :score")
        p["score"] = f.score_min
    if f.texto and (t := normalizar_busca(f.texto).strip()):
        conds.append("(l.busca LIKE :texto OR l.chave LIKE :texto_chave)")
        p["texto"], p["texto_chave"] = f"%{t}%", f"%{so_digitos(f.texto) or t.upper()}%"
    if com_estagio and f.estagio:
        conds.append("COALESCE(e.estagio, 'novo') = :estagio")
        p["estagio"] = f.estagio
    if f.responsavel == "eu":
        conds.append("e.responsavel = :eu")
    elif f.responsavel == "ninguem":
        conds.append("e.responsavel IS NULL")
    elif f.responsavel:
        conds.append("e.responsavel = :resp")
        p["resp"] = f.responsavel.strip().lower()
    return origem, " AND ".join(conds), p


EXTRAS = """,
    (SELECT a.criado_em FROM atividade a WHERE a.empresa_id = :emp AND a.chave = l.chave ORDER BY a.id DESC LIMIT 1) AS ultima_em,
    (SELECT a.tipo FROM atividade a WHERE a.empresa_id = :emp AND a.chave = l.chave ORDER BY a.id DESC LIMIT 1) AS ultima_tipo,
    (SELECT t.vence_em FROM tarefa t WHERE t.empresa_id = :emp AND t.chave = l.chave AND t.concluida_em IS NULL
        ORDER BY t.vence_em, t.id LIMIT 1) AS proximo_em,
    (SELECT t.titulo FROM tarefa t WHERE t.empresa_id = :emp AND t.chave = l.chave AND t.concluida_em IS NULL
        ORDER BY t.vence_em, t.id LIMIT 1) AS proximo_titulo"""
ORDENS = {
    "score": "COALESCE(vp.vp_score, l.score, -1) DESC, l.nome COLLATE NOCASE",
    "abertura": "l.data_abertura IS NULL, l.data_abertura DESC, l.nome COLLATE NOCASE",
    "nome": "l.nome COLLATE NOCASE",
    "atividade": "ultima_em IS NULL, ultima_em DESC",
    "proximo_passo": "proximo_em IS NULL, proximo_em, l.nome COLLATE NOCASE",
    "cadastro": "l.criado_em DESC",
}


def _linhas(conn: sqlite3.Connection, ctx: Contexto, f: FiltroCRM, limite: int, deslocamento: int) -> tuple[int, list[sqlite3.Row]]:
    origem, onde, p = _onde(ctx, f, com_estagio=True)
    total = conn.execute(f"SELECT COUNT(*) {origem} WHERE {onde}", p).fetchone()[0]
    linhas = conn.execute(
        f"SELECT l.*, {COLUNAS_VP}, e.estagio, e.responsavel {EXTRAS} {origem} WHERE {onde} "
        f"ORDER BY {ORDENS[f.ordenar]} LIMIT :lim OFFSET :off",
        p | {"lim": limite, "off": deslocamento},
    ).fetchall()
    return total, linhas


def _ultima_sincronizacao(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sincronizacao WHERE fim IS NOT NULL AND erro IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def _bloqueios(conn: sqlite3.Connection, ctx: Contexto) -> set[tuple[str, str]]:
    return {
        (r["tipo"], r["valor"])
        for r in conn.execute("SELECT tipo, valor FROM supressao WHERE empresa_id IN (?, ?)", (ctx.empresa_id, SUPRESSAO_GLOBAL))
    }


@router.post("/busca")
def buscar(
    f: FiltroCRM,
    request: Request,
    conn: sqlite3.Connection = Depends(db),
    ctx: Contexto = Depends(contexto),
) -> dict:
    total, linhas = _linhas(conn, ctx, f, f.por_pagina, (f.pagina - 1) * f.por_pagina)
    bloqueio = _bloqueios(conn, ctx)
    itens = []
    for r in linhas:
        item = dados_do_lead(r)
        item["crm"] = {
            "estagio": r["estagio"] or "novo",
            "responsavel": r["responsavel"],
            "ultima_atividade_em": r["ultima_em"],
            "ultima_atividade_tipo": r["ultima_tipo"],
            "proximo_passo_em": r["proximo_em"],
            "proximo_passo_titulo": r["proximo_titulo"],
            "suprimido": bool(chaves_supressao(item) & bloqueio),
        }
        itens.append(item)
    sinc = _ultima_sincronizacao(conn)
    anotar(request, "acesso", "leads.listar", detalhe={"filtro": f.model_dump(exclude_defaults=True), "total": total})
    return {
        "fonte": sinc["fonte"] if sinc else None,
        "mes_referencia": conn.execute("SELECT MAX(mes_referencia) FROM lead WHERE origem = 'modulo3'").fetchone()[0],
        "sincronizado_em": sinc["fim"] if sinc else None,
        "total": total,
        "pagina": f.pagina,
        "por_pagina": f.por_pagina,
        "itens": itens,
    }


@router.post("/resumo")
def resumo(f: FiltroCRM, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> dict:
    origem, onde, p = _onde(ctx, f, com_estagio=False)
    contagem = {e: 0 for e in ESTAGIOS}
    for r in conn.execute(f"SELECT COALESCE(e.estagio, 'novo') AS st, COUNT(*) AS n {origem} WHERE {onde} GROUP BY st", p):
        contagem[r["st"]] = r["n"]
    sinc = _ultima_sincronizacao(conn)
    return {
        "total": sum(contagem.values()),
        "por_estagio": contagem,
        "fonte": sinc["fonte"] if sinc else None,
        "mes_referencia": conn.execute("SELECT MAX(mes_referencia) FROM lead WHERE origem = 'modulo3'").fetchone()[0],
    }


def _pontuacao(r: sqlite3.Row) -> int | str:
    score = r["vp_score"] if r["vp_score"] is not None else r["score"]
    return "" if score is None else score


def csv_resposta(nome: str, cabecalho: list[str], linhas: list[list]) -> Response:
    """CSV que o Excel abre direto: UTF-8 com BOM e ponto e vírgula."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    return Response(
        content="﻿" + saida.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/exportar")
def exportar(f: FiltroCRM, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> Response:
    total, linhas = _linhas(conn, ctx, f, 50_000, 0)
    corpo = []
    for r in linhas:
        lead = json.loads(r["dados"])
        receita = lead.get("contato_receita") or {}
        corpo.append([
            r["chave"], lead.get("cnpj") or "", r["nome"], lead.get("razao_social") or "", r["municipio"] or "", r["uf"] or "",
            r["bairro"] or "", r["vp_perfil"] or "", _pontuacao(r), r["estagio"] or "novo",
            r["responsavel"] or "", "manual" if r["origem"] == "manual" else "agente",
            f"{receita.get('ddd_1') or ''}{receita.get('telefone_1') or ''}".strip(), (receita.get("email") or "").strip(),
            r["proximo_em"] or "", r["proximo_titulo"] or "", (r["ultima_em"] or "")[:10],
        ])
    anotar(request, "acesso", "exportacao.leads", detalhe={"filtro": f.model_dump(exclude_defaults=True), "linhas": total})
    return csv_resposta(
        f"leads-{hoje('America/Sao_Paulo').isoformat()}.csv",
        ["chave", "cnpj", "nome", "razao_social", "municipio", "uf", "bairro", "perfil", "pontuacao", "estagio", "responsavel",
         "origem", "telefone_receita", "email_receita", "proximo_passo_em", "proximo_passo", "ultima_atividade"],
        corpo,
    )


# ================================================================== cadastro manual

class DadosManuais(BaseModel):
    nome: str = Field(min_length=2, max_length=160)
    cnpj: str | None = Field(default=None, max_length=20)
    razao_social: str | None = Field(default=None, max_length=200)
    contato_nome: str | None = Field(default=None, max_length=120)
    telefone: str | None = Field(default=None, max_length=30)
    whatsapp: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    site: str | None = Field(default=None, max_length=300)
    logradouro: str | None = Field(default=None, max_length=200)
    bairro: str | None = Field(default=None, max_length=120)
    municipio: str | None = Field(default=None, max_length=120)
    uf: str | None = Field(default=None, max_length=2)
    perfil_id: str | None = Field(default=None, max_length=80)
    observacao: str | None = Field(default=None, max_length=4000)

    @field_validator("*", mode="before")
    @classmethod
    def _vazio_vira_nulo(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


def _validar_manual(d: DadosManuais) -> str | None:
    cnpj = normalizar("cnpj", d.cnpj) if d.cnpj else None
    if cnpj and not cnpj_valido(cnpj.zfill(14)):
        raise HTTPException(422, "CNPJ inválido: confira os dígitos.")
    if d.email and ("@" not in d.email or " " in d.email):
        raise HTTPException(422, "E-mail inválido.")
    for campo in ("telefone", "whatsapp"):
        valor = getattr(d, campo)
        if valor and not 10 <= len(so_digitos(valor)) <= 13:
            raise HTTPException(422, f"{campo.title()} inválido: informe DDD + número.")
    if d.site and not d.site.startswith(("http://", "https://")):
        d.site = "https://" + d.site
    if not (d.telefone or d.whatsapp or d.email or d.site):
        raise HTTPException(422, "Informe pelo menos um contato: telefone, WhatsApp, e-mail ou site.")
    return cnpj.zfill(14) if cnpj else None


def _montar_manual(d: DadosManuais, cnpj: str | None) -> dict:
    contatos = []
    fonte = "cadastro manual"
    if d.email:
        contatos.append({"tipo": "email", "valor": d.email.lower(), "fonte": fonte, "url": None, "confianca": "alta", "observacao": None})
    if d.whatsapp:
        contatos.append({"tipo": "whatsapp", "valor": so_digitos(d.whatsapp), "fonte": fonte, "url": None, "confianca": "alta", "observacao": None})
    if d.telefone:
        contatos.append({"tipo": "telefone", "valor": so_digitos(d.telefone), "fonte": fonte, "url": None, "confianca": "alta", "observacao": None})
    if d.site:
        contatos.append({"tipo": "site", "valor": d.site, "fonte": fonte, "url": d.site, "confianca": "alta", "observacao": None})
    canal = "email" if d.email else "whatsapp" if d.whatsapp else "telefone" if d.telefone else None
    return {
        "cnpj": cnpj, "razao_social": d.razao_social or d.nome, "nome_fantasia": d.nome, "contato_nome": d.contato_nome,
        "logradouro": d.logradouro, "numero": None, "bairro": d.bairro, "municipio": sem_acento_maiusculo(d.municipio),
        "uf": (d.uf or "").upper() or None, "cep": None, "matriz_filial": None, "situacao_cadastral": None,
        "data_inicio_atividade": None, "cnae_principal": None, "cnae_descricao": None, "porte": None, "capital_social": None,
        "contato_receita": {"ddd_1": None, "telefone_1": None, "ddd_2": None, "telefone_2": None, "email": None},
        "contatos": contatos, "classificacao": None, "canal_sugerido": canal, "mes_referencia": None,
        "perfil_id": d.perfil_id,
    }


CAMPOS_EDITAVEIS = ("nome", "cnpj", "razao_social", "contato_nome", "telefone", "whatsapp", "email", "site",
                    "logradouro", "bairro", "municipio", "uf", "perfil_id")


def _visao_manual(d: DadosManuais, cnpj: str | None) -> dict:
    v = d.model_dump(include=set(CAMPOS_EDITAVEIS))
    v["cnpj"] = cnpj
    v["razao_social"] = d.razao_social or d.nome  # o que fica gravado quando a razão social não é informada
    return v


def _duplicado(conn: sqlite3.Connection, ctx: Contexto, cnpj: str, ignorar: str | None, cfg: Config, m3: Modulo3) -> None:
    """Mesmo CNPJ já existe para esta empresa: na base do agente (visível) ou em outro cadastro manual."""
    try:
        existente = carregar_lead(conn, ctx, cnpj, cfg, m3)
        raise HTTPException(409, {"mensagem": "Esse CNPJ já está na base do agente.", "chave": existente["chave"]})
    except HTTPException as erro:
        if erro.status_code == 409:
            raise
    outro = conn.execute(
        "SELECT chave FROM lead WHERE origem = 'manual' AND empresa_id = ? AND cnpj = ? AND chave IS NOT ?",
        (ctx.empresa_id, cnpj, ignorar),
    ).fetchone()
    if outro:
        raise HTTPException(409, {"mensagem": "Esse CNPJ já foi cadastrado pela equipe.", "chave": outro["chave"]})


@router.post("/manual")
def criar_manual(
    d: DadosManuais,
    request: Request,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    m3: Modulo3 = Depends(modulo3),
    ctx: Contexto = Depends(contexto),
) -> dict:
    cnpj = _validar_manual(d)
    if cnpj:
        _duplicado(conn, ctx, cnpj, None, cfg, m3)
    chave = "M" + secrets.token_hex(5).upper()
    dados = _montar_manual(d, cnpj)
    conn.execute(
        """INSERT INTO lead (chave, origem, empresa_id, cnpj, nome, municipio, uf, bairro, perfil_id, score, e_alvo,
                             busca, dados, criado_em, criado_por)
           VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?, ?)""",
        (chave, ctx.empresa_id, cnpj, d.nome, dados["municipio"], dados["uf"], d.bairro, d.perfil_id,
         normalizar_busca(d.nome, d.razao_social, cnpj, d.municipio, d.bairro, d.contato_nome),
         json.dumps(dados, ensure_ascii=False, sort_keys=True), agora(), ctx.nome),
    )
    conn.execute(
        "INSERT INTO lead_estado (empresa_id, chave, estagio, responsavel, atualizado_em, atualizado_por) VALUES (?, ?, 'novo', ?, ?, ?)",
        (ctx.empresa_id, chave, ctx.nome, agora(), ctx.nome),
    )
    atividade(conn, ctx, chave, "cadastro", d.observacao)
    anotar(request, "alteracao", "lead.criar_manual", entidade="lead", entidade_id=chave,
           depois=_visao_manual(d, cnpj) | {"responsavel": ctx.nome}, detalhe={"observacao": d.observacao} if d.observacao else None)
    return {"chave": chave}


@router.put("/{chave}/dados")
def editar_manual(
    chave: str,
    d: DadosManuais,
    request: Request,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    m3: Modulo3 = Depends(modulo3),
    ctx: Contexto = Depends(contexto),
) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    if linha["origem"] != "manual":
        raise HTTPException(422, "Só leads cadastrados pela equipe podem ser editados. Os dados do agente vêm da Receita.")
    cnpj = _validar_manual(d)
    if cnpj:
        _duplicado(conn, ctx, cnpj, linha["chave"], cfg, m3)
    antigo = json.loads(linha["dados"])
    antes = {k: antigo.get("nome_fantasia" if k == "nome" else k) for k in CAMPOS_EDITAVEIS}
    for c in antigo.get("contatos", []):
        if c["tipo"] in ("email", "whatsapp", "telefone", "site"):
            antes[c["tipo"]] = c["valor"]
    dados = _montar_manual(d, cnpj)
    depois = _visao_manual(d, cnpj) | {c["tipo"]: c["valor"] for c in dados["contatos"]}
    depois["municipio"], depois["uf"] = dados["municipio"], dados["uf"]
    conn.execute(
        "UPDATE lead SET cnpj = ?, nome = ?, municipio = ?, uf = ?, bairro = ?, perfil_id = ?, busca = ?, dados = ? WHERE chave = ?",
        (cnpj, d.nome, dados["municipio"], dados["uf"], d.bairro, d.perfil_id,
         normalizar_busca(d.nome, d.razao_social, cnpj, d.municipio, d.bairro, d.contato_nome),
         json.dumps(dados, ensure_ascii=False, sort_keys=True), linha["chave"]),
    )
    from ..auditoria import diferencas

    a, b = diferencas(antes, depois)
    if a or b:
        atividade(conn, ctx, linha["chave"], "dados", ", ".join(sorted({*(a or {}), *(b or {})})))
        anotar(request, "alteracao", "lead.editar", entidade="lead", entidade_id=linha["chave"], antes=a, depois=b)
    return {"ok": True, "alterados": sorted({*(a or {}), *(b or {})})}


# ================================================================== ficha

@router.get("/{chave}")
def ficha(
    chave: str,
    request: Request,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    m3: Modulo3 = Depends(modulo3),
    ctx: Contexto = Depends(contexto),
) -> dict:
    linha, est = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    lead = dados_do_lead(linha)
    k = linha["chave"]
    atividades = [dict(r) for r in conn.execute(
        "SELECT id, tipo, canal, destino, assunto, texto, criado_em, criado_por FROM atividade "
        "WHERE empresa_id = ? AND chave = ? ORDER BY id DESC LIMIT 200", (ctx.empresa_id, k))]
    tarefas = [dict(r) for r in conn.execute(
        "SELECT id, titulo, vence_em, responsavel, concluida_em, concluida_por, criado_em, criado_por FROM tarefa "
        "WHERE empresa_id = ? AND chave = ? ORDER BY concluida_em IS NOT NULL, vence_em, id", (ctx.empresa_id, k))]
    vendas = [dict(r) for r in conn.execute(
        "SELECT id, valor_centavos, data, descricao, vendedor, criado_em, criado_por, estornada_em, estornada_por, motivo_estorno "
        "FROM venda WHERE empresa_id = ? AND chave = ? ORDER BY data DESC, id DESC", (ctx.empresa_id, k))]
    bloqueios = []
    for tipo, valor in sorted(chaves_supressao(lead)):
        r = conn.execute(
            "SELECT tipo, valor, motivo, empresa_id FROM supressao WHERE empresa_id IN (?, ?) AND tipo = ? AND valor = ?",
            (ctx.empresa_id, SUPRESSAO_GLOBAL, tipo, valor),
        ).fetchone()
        if r:
            bloqueios.append({**dict(r), "global": r["empresa_id"] == SUPRESSAO_GLOBAL})
    anotar(request, "acesso", "lead.ver", entidade="lead", entidade_id=k)
    return {
        "lead": lead,
        "crm": {
            "estagio": est["estagio"] if est else "novo",
            "responsavel": est["responsavel"] if est else None,
            "atualizado_em": est["atualizado_em"] if est else None,
        },
        "atividades": atividades,
        "tarefas": tarefas,
        "vendas": vendas,
        "total_vendas_centavos": sum(v["valor_centavos"] for v in vendas if not v["estornada_em"]),
        "supressoes": bloqueios,
        "cadastro": {"criado_em": linha["criado_em"], "criado_por": linha["criado_por"]} if linha["origem"] == "manual" else None,
        "simulado": linha["fonte"] == "simulado",
    }


class MudancaEstagio(BaseModel):
    estagio: Estagio
    nota: str | None = Field(default=None, max_length=4000)


@router.put("/{chave}/estagio")
def alterar_estagio(chave: str, dados: MudancaEstagio, request: Request, conn: sqlite3.Connection = Depends(db),
                    cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    anterior = mudar_estagio(request, conn, ctx, linha["chave"], dados.estagio, (dados.nota or "").strip() or None)
    return {"anterior": anterior, "estagio": dados.estagio}


class MudancaResponsavel(BaseModel):
    responsavel: str | None


def pessoa_da_empresa(conn: sqlite3.Connection, ctx: Contexto, nome: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM usuario WHERE nome = ? AND ativo = 1 AND (empresa_id = ? OR papel = 'superadmin')",
        (nome, ctx.empresa_id),
    ).fetchone())


@router.put("/{chave}/responsavel")
def mudar_responsavel(chave: str, dados: MudancaResponsavel, request: Request, conn: sqlite3.Connection = Depends(db),
                      cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    """Admin passa o lead para qualquer pessoa da empresa (ou libera).
    Vendedor só assume um lead livre ou libera um que é dele."""
    linha, est = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    atual = est["responsavel"] if est else None
    novo = (dados.responsavel or "").strip().lower() or None
    if not ctx.e_admin and novo not in (ctx.nome, None):
        raise HTTPException(403, "Só o administrador transfere leads entre vendedores.")
    if novo and not pessoa_da_empresa(conn, ctx, novo):
        raise HTTPException(422, "Essa pessoa não faz parte da empresa ou está desativada.")
    if novo == atual:
        return {"responsavel": atual}
    garantir_estado(request, conn, ctx, linha["chave"], assumir=False)
    conn.execute(
        "UPDATE lead_estado SET responsavel = ?, atualizado_em = ?, atualizado_por = ? WHERE empresa_id = ? AND chave = ?",
        (novo, agora(), ctx.nome, ctx.empresa_id, linha["chave"]),
    )
    atividade(conn, ctx, linha["chave"], "responsavel", assunto=f"{atual or '—'}→{novo or '—'}")
    anotar(request, "alteracao", "lead.responsavel", entidade="lead", entidade_id=linha["chave"],
           antes={"responsavel": atual}, depois={"responsavel": novo})
    return {"responsavel": novo}


class Nota(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


@router.post("/{chave}/notas")
def nova_nota(chave: str, dados: Nota, request: Request, conn: sqlite3.Connection = Depends(db),
              cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    garantir_estado(request, conn, ctx, linha["chave"], assumir=not ctx.e_admin)
    atividade(conn, ctx, linha["chave"], "nota", dados.texto.strip())
    anotar(request, "alteracao", "lead.nota", entidade="lead", entidade_id=linha["chave"], depois={"nota": dados.texto.strip()})
    return {"ok": True}


# ================================================================== contato por clique

class Preparo(BaseModel):
    canal: Canal
    destino: str = Field(min_length=3, max_length=320)
    modelo_id: int | None = None


class Registro(BaseModel):
    canal: Canal
    destino: str = Field(min_length=3, max_length=320)
    assunto: str | None = Field(default=None, max_length=300)
    texto: str = Field(min_length=1, max_length=8000)


def _bloquear_se_suprimido(request: Request, conn: sqlite3.Connection, ctx: Contexto, lead: dict, canal: str, destino: str) -> None:
    chaves = chaves_destino(canal, destino) | ({("cnpj", lead["cnpj"])} if lead.get("cnpj") else set())
    for tipo, valor in sorted(chaves):
        r = conn.execute(
            "SELECT tipo, valor FROM supressao WHERE empresa_id IN (?, ?) AND tipo = ? AND valor = ?",
            (ctx.empresa_id, SUPRESSAO_GLOBAL, tipo, valor),
        ).fetchone()
        if r:
            anotar(request, "seguranca", "contato.bloqueado", entidade="lead", entidade_id=lead["chave"],
                   detalhe={"canal": canal, "destino": destino, "bloqueio": f"{r['tipo']} {r['valor']}"}, sempre=True)
            raise HTTPException(409, f"Contato bloqueado: {r['tipo']} {r['valor']} está na lista de não contatar.")


def _validar_destino(canal: str, destino: str) -> str:
    destino = destino.strip()
    if canal == "email" and ("@" not in destino or " " in destino):
        raise HTTPException(422, "E-mail inválido.")
    if canal in ("whatsapp", "telefone") and not 10 <= len(so_digitos(destino)) <= 13:
        raise HTTPException(422, "Telefone inválido: informe DDD + número.")
    if canal == "formulario" and not destino.startswith(("http://", "https://")):
        raise HTTPException(422, "Informe o endereço (URL) da página do formulário.")
    return destino


@router.post("/{chave}/contato/preparar")
def preparar_contato(chave: str, dados: Preparo, request: Request, conn: sqlite3.Connection = Depends(db),
                     cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    lead = dados_do_lead(linha)
    destino = _validar_destino(dados.canal, dados.destino)
    _bloquear_se_suprimido(request, conn, ctx, lead, dados.canal, destino)
    perfil = (lead.get("classificacao") or {}).get("perfil_id") or lead.get("perfil_id")
    if dados.modelo_id is not None:
        modelo = conn.execute("SELECT * FROM modelo_mensagem WHERE id = ? AND empresa_id = ?", (dados.modelo_id, ctx.empresa_id)).fetchone()
        if not modelo:
            raise HTTPException(404, "Modelo de mensagem não encontrado.")
    else:
        modelo = conn.execute(
            "SELECT * FROM modelo_mensagem WHERE empresa_id = ? AND canal = ? "
            "ORDER BY (perfil_id = ?) DESC, (perfil_id IS NULL) DESC, id LIMIT 1",
            (ctx.empresa_id, dados.canal, perfil),
        ).fetchone()
    variaveis = contexto_mensagem(lead, ctx.usuario.nome_exibicao)
    assunto = renderizar(modelo["assunto"], variaveis) if modelo else ""
    texto = renderizar(modelo["corpo"], variaveis) if modelo else ""
    anotar(request, "acesso", "contato.preparar", entidade="lead", entidade_id=linha["chave"],
           detalhe={"canal": dados.canal, "destino": destino, "modelo_id": modelo["id"] if modelo else None})
    return {
        "canal": dados.canal, "destino": destino, "modelo_id": modelo["id"] if modelo else None,
        "assunto": assunto, "texto": texto, "url": montar_url(dados.canal, destino, texto, assunto),
        "simulado": linha["fonte"] == "simulado",
    }


@router.post("/{chave}/contato/registrar")
def registrar_contato(chave: str, dados: Registro, request: Request, conn: sqlite3.Connection = Depends(db),
                      cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    lead = dados_do_lead(linha)
    destino = _validar_destino(dados.canal, dados.destino)
    _bloquear_se_suprimido(request, conn, ctx, lead, dados.canal, destino)
    # Quem faz o contato com um lead livre passa a ser o responsável, seja admin ou vendedor.
    est = garantir_estado(request, conn, ctx, linha["chave"], assumir=True)
    atividade(conn, ctx, linha["chave"], "contato", dados.texto, canal=dados.canal, destino=destino, assunto=dados.assunto)
    anotar(request, "alteracao", "lead.contato", entidade="lead", entidade_id=linha["chave"],
           depois={"canal": dados.canal, "destino": destino, "assunto": dados.assunto, "texto": dados.texto},
           detalhe={"simulado": linha["fonte"] == "simulado"})
    estagio = est["estagio"]
    if estagio == "novo":
        mudar_estagio(request, conn, ctx, linha["chave"], "em_contato", motivo="primeiro contato registrado")
        estagio = "em_contato"
    return {
        "estagio": estagio,
        "responsavel": _estado(conn, ctx, linha["chave"])["responsavel"],
        "url": montar_url(dados.canal, destino, dados.texto, dados.assunto),
        "simulado": linha["fonte"] == "simulado",
    }


class Supressao(BaseModel):
    motivo: str = Field(default="pediu para não receber contato", max_length=300)


@router.post("/{chave}/suprimir")
def suprimir_empresa(chave: str, dados: Supressao, request: Request, conn: sqlite3.Connection = Depends(db),
                     cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    """Qualquer pessoa da empresa pode bloquear: pedido de descadastro não espera aprovação."""
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    lead = dados_do_lead(linha)
    chaves = sorted((t, v) for t, v in chaves_supressao(lead) if t != "dominio")
    for tipo, valor in chaves:
        conn.execute(
            "INSERT OR IGNORE INTO supressao (empresa_id, tipo, valor, motivo, criado_em, criado_por) VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.empresa_id, tipo, valor, dados.motivo, agora(), ctx.nome),
        )
    atividade(conn, ctx, linha["chave"], "supressao", dados.motivo)
    anotar(request, "alteracao", "lead.suprimir", entidade="lead", entidade_id=linha["chave"],
           depois={"bloqueados": [f"{t} {v}" for t, v in chaves]}, detalhe={"motivo": dados.motivo})
    mudar_estagio(request, conn, ctx, linha["chave"], "descartado", motivo="marcado para não contatar")
    return {"suprimidos": len(chaves)}


# ================================================================== próximos passos (tarefas)

class NovaTarefa(BaseModel):
    titulo: str = Field(min_length=2, max_length=200)
    vence_em: date
    responsavel: str | None = None


@router.post("/{chave}/tarefas")
def criar_tarefa(chave: str, dados: NovaTarefa, request: Request, conn: sqlite3.Connection = Depends(db),
                 cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, _ = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    responsavel = (dados.responsavel or ctx.nome).strip().lower()
    if responsavel != ctx.nome and not ctx.e_admin:
        raise HTTPException(403, "Só o administrador cria próximos passos para outra pessoa.")
    if not pessoa_da_empresa(conn, ctx, responsavel):
        raise HTTPException(422, "Essa pessoa não faz parte da empresa ou está desativada.")
    garantir_estado(request, conn, ctx, linha["chave"], assumir=not ctx.e_admin)
    tid = conn.execute(
        "INSERT INTO tarefa (empresa_id, chave, titulo, vence_em, responsavel, criado_em, criado_por) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ctx.empresa_id, linha["chave"], dados.titulo.strip(), dados.vence_em.isoformat(), responsavel, agora(), ctx.nome),
    ).lastrowid
    atividade(conn, ctx, linha["chave"], "tarefa", dados.titulo.strip(), assunto=f"{dados.vence_em.isoformat()}|{responsavel}")
    anotar(request, "alteracao", "tarefa.criar", entidade="tarefa", entidade_id=tid,
           depois={"lead": linha["chave"], "titulo": dados.titulo.strip(), "vence_em": dados.vence_em.isoformat(), "responsavel": responsavel})
    return {"id": tid}


# ================================================================== vendas

class NovaVenda(BaseModel):
    valor: str = Field(min_length=1, max_length=20, description="Em reais: '1500', '1.500,00' ou '1500.50'")
    data: date | None = None
    descricao: str | None = Field(default=None, max_length=500)
    vendedor: str | None = None


def valor_em_centavos(texto: str) -> int:
    t = texto.strip().replace("R$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(t)
    except InvalidOperation as erro:
        raise HTTPException(422, "Valor inválido. Use, por exemplo, 1.500,00.") from erro
    if valor <= 0 or valor > Decimal("100000000"):
        raise HTTPException(422, "O valor precisa ser maior que zero.")
    return int((valor * 100).quantize(Decimal("1")))


@router.post("/{chave}/vendas")
def registrar_venda(chave: str, dados: NovaVenda, request: Request, conn: sqlite3.Connection = Depends(db),
                    cfg: Config = Depends(config), m3: Modulo3 = Depends(modulo3), ctx: Contexto = Depends(contexto)) -> dict:
    linha, est = _lead_com_acesso(request, conn, ctx, chave, cfg, m3)
    centavos = valor_em_centavos(dados.valor)
    quando = dados.data or hoje(cfg.fuso)
    if quando > hoje(cfg.fuso):
        raise HTTPException(422, "A data da venda não pode estar no futuro.")
    vendedor = (dados.vendedor or (est["responsavel"] if est and est["responsavel"] else ctx.nome)).strip().lower()
    if vendedor != ctx.nome and not ctx.e_admin:
        raise HTTPException(403, "Só o administrador registra venda em nome de outra pessoa.")
    if not pessoa_da_empresa(conn, ctx, vendedor):
        raise HTTPException(422, "Essa pessoa não faz parte da empresa ou está desativada.")
    garantir_estado(request, conn, ctx, linha["chave"], assumir=not ctx.e_admin)
    vid = conn.execute(
        "INSERT INTO venda (empresa_id, chave, valor_centavos, data, descricao, vendedor, criado_em, criado_por) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ctx.empresa_id, linha["chave"], centavos, quando.isoformat(), (dados.descricao or "").strip() or None, vendedor, agora(), ctx.nome),
    ).lastrowid
    atividade(conn, ctx, linha["chave"], "venda", (dados.descricao or "").strip() or None, assunto=str(centavos))
    anotar(request, "alteracao", "venda.registrar", entidade="venda", entidade_id=vid,
           depois={"lead": linha["chave"], "valor_centavos": centavos, "data": quando.isoformat(),
                   "descricao": dados.descricao, "vendedor": vendedor})
    if _estado(conn, ctx, linha["chave"])["estagio"] != "cliente":
        mudar_estagio(request, conn, ctx, linha["chave"], "cliente", motivo="venda registrada")
    return {"id": vid, "valor_centavos": centavos}
