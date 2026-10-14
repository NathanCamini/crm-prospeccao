"""Banco do agente (`agente.sqlite`): classificações e lotes. Só o agente escreve nele.

As tabelas evoluem por migrações numeradas (versão em `classificador_meta`).

- `classificacao`: uma linha por CNPJ × perfil × versão do perfil (a chave do contrato).
- `classificacao_vigente`: visão com a versão mais nova de cada perfil — é a base do CSV do CRM.
- `classificacao_lote` / `classificacao_lote_item`: lotes enviados à API, para retomar depois de uma queda.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_MIGRACOES: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS classificacao (
        cnpj TEXT NOT NULL CHECK (length(cnpj) = 14),
        perfil_id TEXT NOT NULL,
        perfil_versao INTEGER NOT NULL,
        mes_referencia TEXT NOT NULL,
        modelo TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        entrada_hash TEXT NOT NULL,
        e_alvo INTEGER NOT NULL CHECK (e_alvo IN (0, 1)),
        score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
        confianca TEXT NOT NULL CHECK (confianca IN ('alta', 'media', 'baixa')),
        e_franquia INTEGER CHECK (e_franquia IN (0, 1)),
        marca TEXT,
        resultado_json TEXT NOT NULL,
        revisado INTEGER NOT NULL CHECK (revisado IN (0, 1)),
        custo_tokens INTEGER NOT NULL,
        custo_usd REAL,
        classificado_em TEXT NOT NULL,
        PRIMARY KEY (cnpj, perfil_id, perfil_versao)
    );
    CREATE INDEX IF NOT EXISTS ix_classificacao_alvo ON classificacao (perfil_id, perfil_versao, e_alvo, score);
    CREATE INDEX IF NOT EXISTS ix_classificacao_mes ON classificacao (mes_referencia, perfil_id);
    CREATE VIEW IF NOT EXISTS classificacao_vigente AS
        SELECT c.* FROM classificacao c
        WHERE c.perfil_versao = (SELECT max(m.perfil_versao) FROM classificacao m WHERE m.perfil_id = c.perfil_id);
    CREATE TABLE IF NOT EXISTS classificacao_lote (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        perfil_id TEXT NOT NULL,
        perfil_versao INTEGER NOT NULL,
        mes TEXT NOT NULL,
        etapa TEXT NOT NULL CHECK (etapa IN ('triagem', 'revisao')),
        modelo TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        job TEXT,
        estado TEXT NOT NULL CHECK (estado IN ('criando', 'enviado', 'processado', 'falhou', 'expirado')),
        total INTEGER NOT NULL,
        detalhe TEXT,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_lote_aberto ON classificacao_lote (perfil_id, perfil_versao, mes, estado);
    CREATE TABLE IF NOT EXISTS classificacao_lote_item (
        lote_id INTEGER NOT NULL REFERENCES classificacao_lote (id) ON DELETE CASCADE,
        cnpj TEXT NOT NULL,
        entrada_hash TEXT NOT NULL,
        entrada_json TEXT NOT NULL,
        triagem_json TEXT,
        PRIMARY KEY (lote_id, cnpj)
    );
    """,
    # 2: perfis descritos pelas empresas clientes no CRM (os do repositório continuam em perfis/*.yaml)
    """
    CREATE TABLE IF NOT EXISTS perfil_cliente (
        id TEXT PRIMARY KEY,
        dono TEXT NOT NULL,
        descricao TEXT NOT NULL,
        versao INTEGER NOT NULL,
        perfil_json TEXT NOT NULL,
        ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        execucao_json TEXT,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_perfil_cliente_dono ON perfil_cliente (dono, ativo);
    """,
    # 3: dados cadastrais e contato da Receita de quem passou no funil. O módulo 1 guarda só o arquivo do mês
    # atual; o CSV do CRM continua tendo os leads dos meses anteriores a partir daqui.
    """
    CREATE TABLE IF NOT EXISTS empresa_cadastro (
        cnpj TEXT PRIMARY KEY,
        mes_referencia TEXT NOT NULL,
        dados_json TEXT NOT NULL,
        atualizado_em TEXT NOT NULL
    );
    """,
]
ESTADOS_ABERTOS = ("criando", "enviado")


@dataclass(frozen=True)
class Registro:
    """Uma classificação pronta para gravar."""
    cnpj: str
    perfil_id: str
    perfil_versao: int
    mes_referencia: str
    modelo: str
    prompt_hash: str
    entrada_hash: str
    final: dict[str, Any]
    detalhes: dict[str, Any]       # triagem, avisos, motivo da revisão
    revisado: bool
    custo_tokens: int
    custo_usd: float | None


@dataclass(frozen=True)
class Lote:
    id: int
    perfil_id: str
    perfil_versao: int
    mes: str
    etapa: str
    modelo: str
    prompt_hash: str
    job: str | None
    estado: str
    total: int


def _perfil_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["perfil"] = json.loads(d.pop("perfil_json"))
    d["execucao"] = json.loads(d.pop("execucao_json")) if d.get("execucao_json") else None
    d["ativo"] = bool(d["ativo"])
    return d


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ClassificacaoStore:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._migrar()

    @contextmanager
    def conexao(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.caminho, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def _migrar(self) -> None:
        with self.conexao() as con:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("CREATE TABLE IF NOT EXISTS classificador_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            linha = con.execute("SELECT valor FROM classificador_meta WHERE chave = 'versao_esquema'").fetchone()
            versao = int(linha["valor"]) if linha else 0
            for numero, sql in enumerate(_MIGRACOES[versao:], start=versao + 1):
                con.executescript(sql)
                con.execute("INSERT INTO classificador_meta VALUES ('versao_esquema', ?) "
                            "ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor", (str(numero),))

    # ---------------------------------------------------------------- classificações
    def existentes(self, perfil_id: str, perfil_versao: int, cnpjs: list[str]) -> dict[str, tuple[str, str]]:
        """cnpj → (entrada_hash, prompt_hash) do que já está classificado nesta versão do perfil."""
        resultado: dict[str, tuple[str, str]] = {}
        with self.conexao() as con:
            for i in range(0, len(cnpjs), 900):
                lote = cnpjs[i:i + 900]
                for r in con.execute(
                        f"SELECT cnpj, entrada_hash, prompt_hash FROM classificacao WHERE perfil_id = ? "
                        f"AND perfil_versao = ? AND cnpj IN ({','.join('?' * len(lote))})",
                        [perfil_id, perfil_versao, *lote]):
                    resultado[r["cnpj"]] = (r["entrada_hash"], r["prompt_hash"])
        return resultado

    def gravar(self, registros: list[Registro], con: sqlite3.Connection | None = None) -> None:
        linhas = [(
            r.cnpj, r.perfil_id, r.perfil_versao, r.mes_referencia, r.modelo, r.prompt_hash, r.entrada_hash,
            int(r.final["e_alvo"]), r.final["score"], r.final["confianca"],
            None if r.final.get("e_franquia") is None else int(r.final["e_franquia"]), r.final.get("marca"),
            json.dumps({"final": r.final, **r.detalhes}, ensure_ascii=False), int(r.revisado),
            r.custo_tokens, r.custo_usd, _agora(),
        ) for r in registros]
        sql = """INSERT INTO classificacao VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT (cnpj, perfil_id, perfil_versao) DO UPDATE SET
                   mes_referencia = excluded.mes_referencia, modelo = excluded.modelo,
                   prompt_hash = excluded.prompt_hash, entrada_hash = excluded.entrada_hash,
                   e_alvo = excluded.e_alvo, score = excluded.score, confianca = excluded.confianca,
                   e_franquia = excluded.e_franquia, marca = excluded.marca, resultado_json = excluded.resultado_json,
                   revisado = excluded.revisado, custo_tokens = excluded.custo_tokens, custo_usd = excluded.custo_usd,
                   classificado_em = excluded.classificado_em"""
        if con is not None:
            con.executemany(sql, linhas)
        else:
            with self.conexao() as nova:
                nova.executemany(sql, linhas)

    def classificacoes(self, perfil_id: str, perfil_versao: int | None = None, mes: str | None = None,
                       somente_alvos: bool = False) -> list[dict[str, Any]]:
        """Classificações de um perfil (versão mais nova, se não informada), da maior para a menor nota."""
        condicoes, params = ["perfil_id = ?"], [perfil_id]
        if perfil_versao is None:
            condicoes.append("perfil_versao = (SELECT max(perfil_versao) FROM classificacao WHERE perfil_id = ?)")
            params.append(perfil_id)
        else:
            condicoes.append("perfil_versao = ?")
            params.append(perfil_versao)
        if mes:
            condicoes.append("mes_referencia = ?")
            params.append(mes)
        if somente_alvos:
            condicoes.append("e_alvo = 1")
        with self.conexao() as con:
            return [dict(r) for r in con.execute(
                f"SELECT * FROM classificacao WHERE {' AND '.join(condicoes)} ORDER BY score DESC, cnpj", params)]

    def custos(self, mes: str | None = None) -> list[dict[str, Any]]:
        with self.conexao() as con:
            return [dict(r) for r in con.execute(
                "SELECT mes_referencia AS mes, perfil_id, perfil_versao, count(*) AS classificadas, "
                "sum(e_alvo) AS alvos, sum(revisado) AS revisadas, sum(custo_tokens) AS tokens, "
                "round(sum(coalesce(custo_usd, 0)), 4) AS custo_usd FROM classificacao "
                "WHERE (? IS NULL OR mes_referencia = ?) GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3", (mes, mes))]

    def perfis_classificados(self) -> list[str]:
        with self.conexao() as con:
            return [r[0] for r in con.execute("SELECT DISTINCT perfil_id FROM classificacao ORDER BY 1")]

    # ---------------------------------------------------------------- cadastro (para o CSV do CRM)
    def gravar_cadastro(self, mes: str, dados: dict[str, dict[str, Any]]) -> None:
        agora = _agora()
        with self.conexao() as con:
            con.executemany(
                """INSERT INTO empresa_cadastro (cnpj, mes_referencia, dados_json, atualizado_em) VALUES (?, ?, ?, ?)
                   ON CONFLICT (cnpj) DO UPDATE SET mes_referencia = excluded.mes_referencia,
                     dados_json = excluded.dados_json, atualizado_em = excluded.atualizado_em
                   WHERE excluded.mes_referencia >= empresa_cadastro.mes_referencia""",
                [(cnpj, mes, json.dumps(d, ensure_ascii=False, default=str), agora) for cnpj, d in dados.items()])

    def cadastro(self, cnpjs: list[str]) -> dict[str, dict[str, Any]]:
        resultado: dict[str, dict[str, Any]] = {}
        with self.conexao() as con:
            for i in range(0, len(cnpjs), 900):
                lote = cnpjs[i:i + 900]
                for r in con.execute(f"SELECT cnpj, dados_json FROM empresa_cadastro WHERE cnpj IN ({','.join('?' * len(lote))})",
                                     lote):
                    resultado[r["cnpj"]] = json.loads(r["dados_json"])
        return resultado

    # ---------------------------------------------------------------- perfis das empresas clientes
    def salvar_perfil_cliente(self, perfil_id: str, dono: str, descricao: str, versao: int,
                              perfil_json: str) -> None:
        agora = _agora()
        with self.conexao() as con:
            con.execute(
                """INSERT INTO perfil_cliente (id, dono, descricao, versao, perfil_json, ativo, criado_em, atualizado_em)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET descricao = excluded.descricao, versao = excluded.versao,
                     perfil_json = excluded.perfil_json, ativo = 1, execucao_json = NULL,
                     atualizado_em = excluded.atualizado_em""",
                (perfil_id, dono, descricao, versao, perfil_json, agora, agora))

    def perfil_cliente(self, perfil_id: str) -> dict[str, Any] | None:
        with self.conexao() as con:
            r = con.execute("SELECT * FROM perfil_cliente WHERE id = ?", (perfil_id,)).fetchone()
        return _perfil_dict(r) if r else None

    def perfis_cliente(self, dono: str | None = None, ativos: bool = True) -> list[dict[str, Any]]:
        condicoes, params = [], []
        if dono is not None:
            condicoes.append("dono = ?")
            params.append(dono)
        if ativos:
            condicoes.append("ativo = 1")
        where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
        with self.conexao() as con:
            return [_perfil_dict(r) for r in con.execute(
                f"SELECT * FROM perfil_cliente {where} ORDER BY criado_em, id", params)]

    def desativar_perfil_cliente(self, perfil_id: str) -> bool:
        with self.conexao() as con:
            return con.execute("UPDATE perfil_cliente SET ativo = 0, atualizado_em = ? WHERE id = ? AND ativo = 1",
                               (_agora(), perfil_id)).rowcount > 0

    def registrar_execucao_perfil(self, perfil_id: str, execucao: dict[str, Any]) -> None:
        with self.conexao() as con:
            con.execute("UPDATE perfil_cliente SET execucao_json = ? WHERE id = ?",
                        (json.dumps(execucao, ensure_ascii=False), perfil_id))

    def alvos_por_perfil(self) -> dict[str, int]:
        """Alvos na versão vigente de cada perfil."""
        with self.conexao() as con:
            return {r[0]: r[1] for r in con.execute(
                "SELECT perfil_id, sum(e_alvo) FROM classificacao_vigente GROUP BY perfil_id")}

    # ---------------------------------------------------------------- lotes
    def criar_lote(self, perfil_id: str, perfil_versao: int, mes: str, etapa: str, modelo: str, prompt_hash: str,
                   itens: list[dict[str, Any]], con: sqlite3.Connection | None = None) -> int:
        """Registra o lote ANTES do envio (estado 'criando'): uma queda no meio não perde os itens."""
        def _criar(c: sqlite3.Connection) -> int:
            cur = c.execute(
                "INSERT INTO classificacao_lote (perfil_id, perfil_versao, mes, etapa, modelo, prompt_hash, estado, "
                "total, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, 'criando', ?, ?, ?)",
                (perfil_id, perfil_versao, mes, etapa, modelo, prompt_hash, len(itens), _agora(), _agora()))
            lote_id = cur.lastrowid
            c.executemany(
                "INSERT INTO classificacao_lote_item (lote_id, cnpj, entrada_hash, entrada_json, triagem_json) "
                "VALUES (?, ?, ?, ?, ?)",
                [(lote_id, i["cnpj"], i["entrada_hash"], json.dumps(i["entrada"], ensure_ascii=False, default=str),
                  json.dumps(i["triagem"], ensure_ascii=False) if i.get("triagem") is not None else None)
                 for i in itens])
            return lote_id
        if con is not None:
            return _criar(con)
        with self.conexao() as nova:
            return _criar(nova)

    def marcar_lote(self, lote_id: int, estado: str, job: str | None = None, detalhe: str | None = None,
                    con: sqlite3.Connection | None = None) -> None:
        sql = ("UPDATE classificacao_lote SET estado = ?, job = coalesce(?, job), detalhe = ?, atualizado_em = ? "
               "WHERE id = ?")
        params = (estado, job, detalhe, _agora(), lote_id)
        if con is not None:
            con.execute(sql, params)
        else:
            with self.conexao() as nova:
                nova.execute(sql, params)

    def lote_aberto(self, perfil_id: str, perfil_versao: int, mes: str) -> Lote | None:
        with self.conexao() as con:
            r = con.execute(
                f"SELECT * FROM classificacao_lote WHERE perfil_id = ? AND perfil_versao = ? AND mes = ? "
                f"AND estado IN ({','.join('?' * len(ESTADOS_ABERTOS))}) ORDER BY id LIMIT 1",
                (perfil_id, perfil_versao, mes, *ESTADOS_ABERTOS)).fetchone()
        return _lote(r) if r else None

    def encerrar_lotes_de_versoes_antigas(self, perfil_id: str, perfil_versao: int, mes: str) -> int:
        """Lotes abertos de outra versão do perfil não são mais aproveitados (o resultado seria de outro perfil)."""
        with self.conexao() as con:
            return con.execute(
                f"UPDATE classificacao_lote SET estado = 'falhou', atualizado_em = ?, "
                f"detalhe = 'perfil mudou para a versão ' || ? || ' antes de o lote terminar' "
                f"WHERE perfil_id = ? AND mes = ? AND perfil_versao <> ? "
                f"AND estado IN ({','.join('?' * len(ESTADOS_ABERTOS))})",
                (_agora(), perfil_versao, perfil_id, mes, perfil_versao, *ESTADOS_ABERTOS)).rowcount

    def itens_lote(self, lote_id: int) -> list[dict[str, Any]]:
        with self.conexao() as con:
            return [{"cnpj": r["cnpj"], "entrada_hash": r["entrada_hash"], "entrada": json.loads(r["entrada_json"]),
                     "triagem": json.loads(r["triagem_json"]) if r["triagem_json"] else None}
                    for r in con.execute("SELECT * FROM classificacao_lote_item WHERE lote_id = ? ORDER BY cnpj",
                                         (lote_id,))]

    def lotes(self, perfil_id: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        with self.conexao() as con:
            return [dict(r) for r in con.execute(
                "SELECT id, perfil_id, perfil_versao, mes, etapa, modelo, job, estado, total, detalhe, criado_em, "
                "atualizado_em FROM classificacao_lote WHERE (? IS NULL OR perfil_id = ?) ORDER BY id DESC LIMIT ?",
                (perfil_id, perfil_id, limite))]


def _lote(r: sqlite3.Row) -> Lote:
    return Lote(r["id"], r["perfil_id"], r["perfil_versao"], r["mes"], r["etapa"], r["modelo"], r["prompt_hash"],
                r["job"], r["estado"], r["total"])
