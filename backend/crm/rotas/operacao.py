"""Agenda (próximos passos), estorno de venda, relatórios, exportações e auditoria."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import auditoria as aud
from ..auditoria import anotar, diferencas
from ..config import Config
from ..db import agora
from ..dependencias import RotaQueConfirma, Contexto, Usuario, config, contexto, db, exige_admin, exige_super, usuario_liberado
from ..tempo import hoje, intervalo_utc
from .leads import ESTAGIOS, atividade, csv_resposta, pessoa_da_empresa

router = APIRouter(prefix="/api", tags=["operacao"], route_class=RotaQueConfirma)


# ================================================================== agenda

@router.get("/tarefas")
def listar_tarefas(
    quem: str = "eu",                       # "eu", "todos" (admin) ou um usuário (admin)
    situacao: Literal["abertas", "concluidas"] = "abertas",
    conn: sqlite3.Connection = Depends(db),
    ctx: Contexto = Depends(contexto),
) -> list[dict]:
    if quem != "eu" and not ctx.e_admin:
        raise HTTPException(403, "Só o administrador vê a agenda da equipe.")
    filtros, params = ["t.empresa_id = ?"], [ctx.empresa_id]
    if quem == "eu":
        filtros.append("t.responsavel = ?")
        params.append(ctx.nome)
    elif quem != "todos":
        filtros.append("t.responsavel = ?")
        params.append(quem.strip().lower())
    filtros.append("t.concluida_em IS NULL" if situacao == "abertas" else "t.concluida_em IS NOT NULL")
    ordem = "t.vence_em, t.id" if situacao == "abertas" else "t.concluida_em DESC"
    return [dict(r) for r in conn.execute(
        f"""SELECT t.*, l.nome AS lead_nome, l.municipio, l.uf, COALESCE(e.estagio, 'novo') AS estagio
            FROM tarefa t JOIN lead l ON l.chave = t.chave
            LEFT JOIN lead_estado e ON e.empresa_id = t.empresa_id AND e.chave = t.chave
            WHERE {' AND '.join(filtros)} ORDER BY {ordem} LIMIT 500""", params)]


@router.get("/tarefas/contagem")
def contagem_tarefas(conn: sqlite3.Connection = Depends(db), cfg: Config = Depends(config), ctx: Contexto = Depends(contexto)) -> dict:
    dia = hoje(cfg.fuso).isoformat()
    r = conn.execute(
        """SELECT SUM(vence_em < ?) AS atrasadas, SUM(vence_em = ?) AS hoje FROM tarefa
           WHERE empresa_id = ? AND responsavel = ? AND concluida_em IS NULL""",
        (dia, dia, ctx.empresa_id, ctx.nome)).fetchone()
    return {"atrasadas": r["atrasadas"] or 0, "hoje": r["hoje"] or 0, "data": dia}


class AlteracaoTarefa(BaseModel):
    titulo: str | None = Field(default=None, min_length=2, max_length=200)
    vence_em: date | None = None
    responsavel: str | None = None
    concluida: bool | None = None


def _tarefa(conn: sqlite3.Connection, ctx: Contexto, id_: int) -> sqlite3.Row:
    t = conn.execute("SELECT * FROM tarefa WHERE id = ? AND empresa_id = ?", (id_, ctx.empresa_id)).fetchone()
    if not t:
        raise HTTPException(404, "Próximo passo não encontrado.")
    if not ctx.e_admin and t["responsavel"] != ctx.nome:
        raise HTTPException(403, "Este próximo passo é de outra pessoa.")
    return t


@router.put("/tarefas/{id_}")
def alterar_tarefa(id_: int, dados: AlteracaoTarefa, request: Request, conn: sqlite3.Connection = Depends(db),
                   ctx: Contexto = Depends(contexto)) -> dict:
    t = _tarefa(conn, ctx, id_)
    antes = {"titulo": t["titulo"], "vence_em": t["vence_em"], "responsavel": t["responsavel"], "concluida": t["concluida_em"] is not None}
    depois = dict(antes)
    if dados.titulo is not None:
        depois["titulo"] = dados.titulo.strip()
    if dados.vence_em is not None:
        depois["vence_em"] = dados.vence_em.isoformat()
    if dados.responsavel is not None:
        novo = dados.responsavel.strip().lower()
        if novo != t["responsavel"] and not ctx.e_admin:
            raise HTTPException(403, "Só o administrador passa um próximo passo para outra pessoa.")
        if not pessoa_da_empresa(conn, ctx, novo):
            raise HTTPException(422, "Essa pessoa não faz parte da empresa ou está desativada.")
        depois["responsavel"] = novo
    if dados.concluida is not None:
        depois["concluida"] = dados.concluida
    conn.execute(
        "UPDATE tarefa SET titulo = ?, vence_em = ?, responsavel = ?, concluida_em = ?, concluida_por = ? WHERE id = ?",
        (depois["titulo"], depois["vence_em"], depois["responsavel"],
         (t["concluida_em"] or agora()) if depois["concluida"] else None,
         (t["concluida_por"] or ctx.nome) if depois["concluida"] else None, id_),
    )
    a, d = diferencas(antes, depois)
    if a or d:
        acao = "tarefa.concluir" if d and d.get("concluida") is True else "tarefa.reabrir" if d and d.get("concluida") is False else "tarefa.editar"
        if acao == "tarefa.concluir":
            atividade(conn, ctx, t["chave"], "tarefa_concluida", depois["titulo"])
        anotar(request, "alteracao", acao, entidade="tarefa", entidade_id=id_, antes=a, depois=d, detalhe={"lead": t["chave"]})
    return {"ok": True}


@router.delete("/tarefas/{id_}")
def apagar_tarefa(id_: int, request: Request, conn: sqlite3.Connection = Depends(db), ctx: Contexto = Depends(contexto)) -> dict:
    t = _tarefa(conn, ctx, id_)
    if not ctx.e_admin and t["criado_por"] != ctx.nome:
        raise HTTPException(403, "Só quem criou ou o administrador apaga um próximo passo.")
    conn.execute("DELETE FROM tarefa WHERE id = ?", (id_,))
    anotar(request, "alteracao", "tarefa.apagar", entidade="tarefa", entidade_id=id_,
           antes={k: t[k] for k in ("chave", "titulo", "vence_em", "responsavel", "concluida_em")})
    return {"ok": True}


# ================================================================== vendas

class Estorno(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


@router.post("/vendas/{id_}/estornar")
def estornar_venda(id_: int, dados: Estorno, request: Request, conn: sqlite3.Connection = Depends(db),
                   ctx: Contexto = Depends(exige_admin)) -> dict:
    """Venda não se apaga: o estorno fica registrado com motivo, e sai dos totais."""
    v = conn.execute("SELECT * FROM venda WHERE id = ? AND empresa_id = ?", (id_, ctx.empresa_id)).fetchone()
    if not v:
        raise HTTPException(404, "Venda não encontrada.")
    if v["estornada_em"]:
        raise HTTPException(409, "Esta venda já foi estornada.")
    conn.execute("UPDATE venda SET estornada_em = ?, estornada_por = ?, motivo_estorno = ? WHERE id = ?",
                 (agora(), ctx.nome, dados.motivo.strip(), id_))
    atividade(conn, ctx, v["chave"], "venda_estornada", dados.motivo.strip(), assunto=str(v["valor_centavos"]))
    anotar(request, "alteracao", "venda.estornar", entidade="venda", entidade_id=id_,
           antes={"estornada": False}, depois={"estornada": True, "motivo": dados.motivo.strip()},
           detalhe={"lead": v["chave"], "valor_centavos": v["valor_centavos"], "vendedor": v["vendedor"]})
    return {"ok": True}


# ================================================================== relatórios

def _periodo(de: date | None, ate: date | None, cfg: Config) -> tuple[date, date]:
    fim = ate or hoje(cfg.fuso)
    inicio = de or fim.replace(day=1)
    if inicio > fim:
        raise HTTPException(422, "A data inicial é depois da final.")
    if (fim - inicio).days > 731:
        raise HTTPException(422, "Escolha um período de até 2 anos.")
    return inicio, fim


AVANCO = ("respondeu", "interessado", "amostra_enviada", "cliente")


@router.get("/relatorios")
def relatorio(
    request: Request,
    de: date | None = None,
    ate: date | None = None,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    ctx: Contexto = Depends(exige_admin),
) -> dict:
    inicio, fim = _periodo(de, ate, cfg)
    ini_utc, fim_utc = intervalo_utc(inicio, fim, cfg.fuso)
    emp = ctx.empresa_id
    p = {"emp": emp, "ini": ini_utc, "fim": fim_utc, "d0": inicio.isoformat(), "d1": fim.isoformat(), "hoje": hoje(cfg.fuso).isoformat()}

    # Perfil de cada lead pelo olhar da empresa: o melhor entre os perfis que ela vê (nunca o produto de outra).
    perfil_visivel = (f"""(SELECT lp.perfil_id FROM lead_perfil lp WHERE lp.chave = {{t}}.chave
                           AND {ctx.condicao_perfis("lp.perfil_id", p)} ORDER BY lp.e_alvo DESC, lp.score DESC LIMIT 1)""")
    ativ = conn.execute(
        f"""SELECT a.chave, a.tipo, a.canal, a.assunto, a.criado_por, {perfil_visivel.format(t="a")} AS perfil_id
            FROM atividade a WHERE a.empresa_id = :emp AND a.criado_em >= :ini AND a.criado_em < :fim""", p).fetchall()
    vendas = conn.execute(
        f"""SELECT v.chave, v.valor_centavos, v.data, v.vendedor, {perfil_visivel.format(t="v")} AS perfil_id
            FROM venda v WHERE v.empresa_id = :emp AND v.estornada_em IS NULL AND v.data BETWEEN :d0 AND :d1""",
        p).fetchall()
    tarefas_feitas = conn.execute(
        "SELECT responsavel, COUNT(*) AS n FROM tarefa WHERE empresa_id = :emp AND concluida_em >= :ini AND concluida_em < :fim GROUP BY responsavel",
        p).fetchall()
    atrasadas = conn.execute(
        "SELECT responsavel, COUNT(*) AS n FROM tarefa WHERE empresa_id = :emp AND concluida_em IS NULL AND vence_em < :hoje GROUP BY responsavel",
        p).fetchall()

    def destino(a) -> str | None:
        return (a["assunto"] or "").split("→")[-1] if a["tipo"] == "estagio" else None

    pessoas: dict[str, dict] = defaultdict(lambda: {"contatos": 0, "leads": set(), "novos_clientes": set(), "respostas": set(),
                                                     "vendas_qtd": 0, "vendas_centavos": 0, "tarefas_concluidas": 0, "tarefas_atrasadas": 0})
    perfis: dict[str, dict] = defaultdict(lambda: {"leads": set(), "novos_clientes": set(), "vendas_centavos": 0})
    contatos_canal: dict[str, int] = defaultdict(int)
    leads_canal: dict[str, set] = defaultdict(set)
    for a in ativ:
        pp = pessoas[a["criado_por"]]
        pf = perfis[a["perfil_id"] or "manual"]
        pp["leads"].add(a["chave"])
        pf["leads"].add(a["chave"])
        if a["tipo"] == "contato":
            pp["contatos"] += 1
            contatos_canal[a["canal"] or "?"] += 1
            leads_canal[a["canal"] or "?"].add(a["chave"])
        d = destino(a)
        if d == "cliente":
            pp["novos_clientes"].add(a["chave"])
            pf["novos_clientes"].add(a["chave"])
        if d in AVANCO:
            pp["respostas"].add(a["chave"])
    for v in vendas:
        pp = pessoas[v["vendedor"]]
        pp["vendas_qtd"] += 1
        pp["vendas_centavos"] += v["valor_centavos"]
        perfis[v["perfil_id"] or "manual"]["vendas_centavos"] += v["valor_centavos"]
    for r in tarefas_feitas:
        pessoas[r["responsavel"]]["tarefas_concluidas"] = r["n"]
    for r in atrasadas:
        pessoas[r["responsavel"]]["tarefas_atrasadas"] = r["n"]

    # Canal do primeiro contato de cada lead (em qualquer época) e quem desses é cliente hoje.
    primeiro_canal = {r["chave"]: r["canal"] for r in conn.execute(
        """SELECT a.chave, a.canal FROM atividade a JOIN (SELECT chave, MIN(id) AS id FROM atividade
           WHERE empresa_id = :emp AND tipo = 'contato' GROUP BY chave) m ON m.id = a.id""", p)}
    clientes_hoje = {r["chave"] for r in conn.execute("SELECT chave FROM lead_estado WHERE empresa_id = :emp AND estagio = 'cliente'", p)}

    nomes = {r["nome"]: r["nome_exibicao"] for r in conn.execute("SELECT nome, nome_exibicao FROM usuario")}
    por_pessoa = sorted((
        {"usuario": u, "nome_exibicao": nomes.get(u, u), "contatos": d["contatos"], "leads_trabalhados": len(d["leads"]),
         "respostas": len(d["respostas"]), "novos_clientes": len(d["novos_clientes"]), "vendas_qtd": d["vendas_qtd"],
         "vendas_centavos": d["vendas_centavos"], "tarefas_concluidas": d["tarefas_concluidas"], "tarefas_atrasadas": d["tarefas_atrasadas"]}
        for u, d in pessoas.items()), key=lambda x: (-x["vendas_centavos"], -x["contatos"]))
    por_canal = sorted((
        {"canal": c, "contatos": contatos_canal[c], "leads": len(leads_canal[c]),
         "viraram_cliente": len({k for k in leads_canal[c] if primeiro_canal.get(k) == c and k in clientes_hoje})}
        for c in contatos_canal), key=lambda x: -x["contatos"])
    por_perfil = sorted((
        {"perfil_id": k, "leads_trabalhados": len(d["leads"]), "novos_clientes": len(d["novos_clientes"]), "vendas_centavos": d["vendas_centavos"]}
        for k, d in perfis.items()), key=lambda x: -x["leads_trabalhados"])

    semanas: dict[str, dict] = {}
    s = inicio - timedelta(days=inicio.weekday())
    while s <= fim:
        semanas[s.isoformat()] = {"semana": s.isoformat(), "qtd": 0, "centavos": 0}
        s += timedelta(days=7)
    for v in vendas:
        dia = date.fromisoformat(v["data"])
        chave_semana = (dia - timedelta(days=dia.weekday())).isoformat()
        semanas[chave_semana]["qtd"] += 1
        semanas[chave_semana]["centavos"] += v["valor_centavos"]

    total_vendas = sum(v["valor_centavos"] for v in vendas)
    funil = {e: 0 for e in ESTAGIOS}
    for r in conn.execute("SELECT estagio, COUNT(*) AS n FROM lead_estado WHERE empresa_id = :emp GROUP BY estagio", p):
        funil[r["estagio"]] = r["n"]
    totais = {
        "contatos": sum(contatos_canal.values()),
        "leads_trabalhados": len({a["chave"] for a in ativ}),
        "respostas": len({a["chave"] for a in ativ if destino(a) in AVANCO}),
        "novos_clientes": len({a["chave"] for a in ativ if destino(a) == "cliente"}),
        "vendas_qtd": len(vendas),
        "vendas_centavos": total_vendas,
        "ticket_medio_centavos": total_vendas // len(vendas) if vendas else 0,
        "tarefas_concluidas": sum(r["n"] for r in tarefas_feitas),
        "tarefas_atrasadas": sum(r["n"] for r in atrasadas),
    }
    anotar(request, "acesso", "relatorio.ver", detalhe={"de": inicio.isoformat(), "ate": fim.isoformat()})
    return {"periodo": {"de": inicio.isoformat(), "ate": fim.isoformat()}, "totais": totais, "por_pessoa": por_pessoa,
            "por_canal": por_canal, "por_perfil": por_perfil, "vendas_por_semana": list(semanas.values()), "funil": funil}


@router.get("/relatorios/exportar")
def exportar_relatorio(
    request: Request,
    tipo: Literal["vendas", "atividades", "tarefas"],
    de: date | None = None,
    ate: date | None = None,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    ctx: Contexto = Depends(exige_admin),
) -> Response:
    inicio, fim = _periodo(de, ate, cfg)
    ini_utc, fim_utc = intervalo_utc(inicio, fim, cfg.fuso)
    if tipo == "vendas":
        linhas = conn.execute(
            """SELECT v.id, v.data, l.nome, v.chave, v.valor_centavos, v.vendedor, v.descricao, v.criado_por,
                      v.estornada_em, v.estornada_por, v.motivo_estorno
               FROM venda v LEFT JOIN lead l ON l.chave = v.chave
               WHERE v.empresa_id = ? AND v.data BETWEEN ? AND ? ORDER BY v.data, v.id""",
            (ctx.empresa_id, inicio.isoformat(), fim.isoformat())).fetchall()
        cab = ["id", "data", "lead", "chave", "valor", "vendedor", "descricao", "registrada_por", "estornada_em", "estornada_por", "motivo_estorno"]
        corpo = [[r["id"], r["data"], r["nome"], r["chave"], f"{r['valor_centavos'] / 100:.2f}".replace(".", ","), r["vendedor"],
                  r["descricao"] or "", r["criado_por"], r["estornada_em"] or "", r["estornada_por"] or "", r["motivo_estorno"] or ""]
                 for r in linhas]
    elif tipo == "atividades":
        linhas = conn.execute(
            """SELECT a.criado_em, a.criado_por, a.tipo, a.canal, a.destino, a.assunto, a.texto, a.chave, l.nome
               FROM atividade a LEFT JOIN lead l ON l.chave = a.chave
               WHERE a.empresa_id = ? AND a.criado_em >= ? AND a.criado_em < ? ORDER BY a.id""",
            (ctx.empresa_id, ini_utc, fim_utc)).fetchall()
        cab = ["quando_utc", "pessoa", "tipo", "canal", "destino", "assunto", "texto", "chave", "lead"]
        corpo = [[r["criado_em"], r["criado_por"], r["tipo"], r["canal"] or "", r["destino"] or "", r["assunto"] or "",
                  (r["texto"] or "").replace("\n", " ")[:2000], r["chave"], r["nome"] or ""] for r in linhas]
    else:
        linhas = conn.execute(
            """SELECT t.id, t.vence_em, t.titulo, t.responsavel, t.concluida_em, t.concluida_por, t.criado_por, t.chave, l.nome
               FROM tarefa t LEFT JOIN lead l ON l.chave = t.chave
               WHERE t.empresa_id = ? AND t.vence_em BETWEEN ? AND ? ORDER BY t.vence_em, t.id""",
            (ctx.empresa_id, inicio.isoformat(), fim.isoformat())).fetchall()
        cab = ["id", "vence_em", "titulo", "responsavel", "concluida_em", "concluida_por", "criada_por", "chave", "lead"]
        corpo = [[r["id"], r["vence_em"], r["titulo"], r["responsavel"], r["concluida_em"] or "", r["concluida_por"] or "",
                  r["criado_por"], r["chave"], r["nome"] or ""] for r in linhas]
    anotar(request, "acesso", f"exportacao.{tipo}", detalhe={"de": inicio.isoformat(), "ate": fim.isoformat(), "linhas": len(corpo)})
    return csv_resposta(f"{tipo}-{inicio.isoformat()}-a-{fim.isoformat()}.csv", cab, corpo)


# ================================================================== auditoria

def _escopo_auditoria(u: Usuario, conn: sqlite3.Connection, empresa_id: int | None) -> int | None:
    """Superadmin vê tudo (ou uma empresa); admin só a própria empresa; vendedor não vê."""
    if u.e_super:
        return empresa_id
    if u.papel != "admin":
        raise HTTPException(403, "Só administradores consultam a auditoria.")
    return u.empresa_id


def _intervalo_opcional(de: date | None, ate: date | None, cfg: Config) -> tuple[str | None, str | None]:
    ini = intervalo_utc(de, de, cfg.fuso)[0] if de else None
    fim = intervalo_utc(ate, ate, cfg.fuso)[1] if ate else None
    return ini, fim


@router.get("/auditoria")
def consultar_auditoria(
    request: Request,
    de: date | None = None,
    ate: date | None = None,
    usuario: str | None = None,
    acao: str | None = None,
    resultado: Literal["ok", "negado", "recusado", "erro"] | None = None,
    tipo: Literal["acesso", "alteracao", "seguranca", "evento", "sistema"] | None = None,
    entidade_id: str | None = None,
    empresa_id: int | None = None,
    pagina: int = Query(default=1, ge=1),
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    u: Usuario = Depends(usuario_liberado),
) -> dict:
    escopo = _escopo_auditoria(u, conn, empresa_id)
    ini, fim = _intervalo_opcional(de, ate, cfg)
    total, linhas = aud.consultar(conn, empresa_id=escopo, de=ini, ate=fim, usuario=usuario, acao=acao, resultado=resultado,
                                  entidade_id=entidade_id, tipo=tipo, limite=50, deslocamento=(pagina - 1) * 50)
    nomes_lead = {}
    ids = {l["entidade_id"] for l in linhas if l["entidade"] == "lead" and l["entidade_id"]}
    if ids:
        marcas = ",".join("?" * len(ids))
        nomes_lead = {r["chave"]: r["nome"] for r in conn.execute(f"SELECT chave, nome FROM lead WHERE chave IN ({marcas})", list(ids))}
    for l in linhas:
        l["lead_nome"] = nomes_lead.get(l["entidade_id"]) if l["entidade"] == "lead" else None
    anotar(request, "acesso", "auditoria.consultar",
           detalhe={k: v for k, v in {"de": de, "ate": ate, "usuario": usuario, "acao": acao, "resultado": resultado,
                                      "tipo": tipo, "entidade_id": entidade_id, "empresa_id": empresa_id, "pagina": pagina}.items() if v})
    return {"total": total, "pagina": pagina, "por_pagina": 50, "itens": linhas}


@router.get("/auditoria/exportar")
def exportar_auditoria(
    request: Request,
    de: date | None = None,
    ate: date | None = None,
    usuario: str | None = None,
    acao: str | None = None,
    resultado: Literal["ok", "negado", "recusado", "erro"] | None = None,
    tipo: Literal["acesso", "alteracao", "seguranca", "evento", "sistema"] | None = None,
    entidade_id: str | None = None,
    empresa_id: int | None = None,
    conn: sqlite3.Connection = Depends(db),
    cfg: Config = Depends(config),
    u: Usuario = Depends(usuario_liberado),
) -> Response:
    import json

    escopo = _escopo_auditoria(u, conn, empresa_id)
    ini, fim = _intervalo_opcional(de, ate, cfg)
    total, linhas = aud.consultar(conn, empresa_id=escopo, de=ini, ate=fim, usuario=usuario, acao=acao, resultado=resultado,
                                  entidade_id=entidade_id, tipo=tipo, limite=100_000, deslocamento=0)
    cab = ["id", "quando_utc", "requisicao", "usuario", "papel", "empresa_id", "tipo", "acao", "entidade", "entidade_id",
           "antes", "depois", "detalhe", "resultado", "status", "metodo", "rota", "ip", "navegador"]
    j = lambda v: "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))  # noqa: E731
    corpo = [[l["id"], l["quando"], l["requisicao"], l["usuario"] or "", l["papel"] or "", l["empresa_id"] or "", l["tipo"],
              l["acao"], l["entidade"] or "", l["entidade_id"] or "", j(l["antes"]), j(l["depois"]), j(l["detalhe"]),
              l["resultado"], l["status"] or "", l["metodo"] or "", l["rota"] or "", l["ip"] or "", l["agente"] or ""] for l in linhas]
    anotar(request, "acesso", "exportacao.auditoria", detalhe={"linhas": total, "empresa_id": escopo})
    return csv_resposta(f"auditoria-{hoje(cfg.fuso).isoformat()}.csv", cab, corpo)


class Evento(BaseModel):
    acao: str = Field(max_length=60)
    entidade_id: str | None = Field(default=None, max_length=40)
    detalhe: dict | str | None = None


@router.post("/auditoria/evento")
def evento_de_tela(e: Evento, request: Request, u: Usuario = Depends(usuario_liberado)) -> dict:
    """Ações que só acontecem no navegador (copiar texto, abrir site, clicar para ligar)."""
    if e.acao not in aud.EVENTOS_DE_TELA:
        raise HTTPException(422, "Evento desconhecido.")
    detalhe = e.detalhe
    if isinstance(detalhe, dict):
        detalhe = {k: str(v)[:300] for k, v in list(detalhe.items())[:10]}
    elif isinstance(detalhe, str):
        detalhe = detalhe[:300]
    anotar(request, "evento", e.acao, entidade="lead" if e.entidade_id else None, entidade_id=e.entidade_id, detalhe=detalhe)
    return {"ok": True}


@router.get("/auditoria/verificar")
def verificar_auditoria(request: Request, cfg: Config = Depends(config), _: Usuario = Depends(exige_super)) -> dict:
    linhas, adulterada = aud.verificar(cfg.db_path)
    anotar(request, "acesso", "auditoria.verificar", detalhe={"linhas": linhas, "adulterada_em": adulterada},
           )
    return {"linhas": linhas, "integra": adulterada is None, "adulterada_em": adulterada}
