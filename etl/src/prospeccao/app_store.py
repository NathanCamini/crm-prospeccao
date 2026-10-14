"""Banco do usuário (SQLite): login e, nas próximas etapas, status de prospecção,
contatos complementares e consultas salvas.

Fica separado do banco da base (DuckDB), que é regerado a cada carga mensal. Tudo
aqui é ligado ao CNPJ completo, então sobrevive a qualquer nova carga.

Por que SQLite e não DuckDB: o DuckDB permite um único processo com escrita por
arquivo. A interface, a linha de comando (criar usuário) e a carga agendada
precisam escrever aqui ao mesmo tempo; o SQLite resolve isso com travas
transacionais, sem servidor e sem dependência extra.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from prospeccao.auth import gerar_hash, verificar_senha
from prospeccao.domain.status import STATUS

# Cada item é uma migração; a posição + 1 é a versão do esquema (PRAGMA user_version).
_MIGRACOES: list[str] = [
    """
    CREATE TABLE usuario (
        nome TEXT PRIMARY KEY,
        senha_hash TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        ultimo_acesso TEXT
    );
    """,
    # v2 (Etapa 4): contatos complementares e registro de buscas (cache e limite de frequência)
    """
    CREATE TABLE contato_complementar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnpj TEXT NOT NULL CHECK (length(cnpj) = 14),
        tipo TEXT NOT NULL CHECK (tipo IN ('telefone', 'email', 'site', 'rede_social')),
        valor TEXT NOT NULL,
        valor_normalizado TEXT NOT NULL,
        fonte_url TEXT,
        fonte_nome TEXT NOT NULL,
        data_busca TEXT NOT NULL,
        confianca TEXT NOT NULL CHECK (confianca IN ('alta', 'media', 'baixa')),
        observacao TEXT,
        criado_por TEXT,
        UNIQUE (cnpj, tipo, valor_normalizado)
    );
    CREATE INDEX ix_contato_cnpj ON contato_complementar (cnpj);
    CREATE TABLE busca_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnpj TEXT NOT NULL,
        metodo TEXT NOT NULL,
        executado_em TEXT NOT NULL,
        resultado TEXT NOT NULL,
        detalhe TEXT
    );
    CREATE INDEX ix_busca_cnpj_metodo ON busca_log (cnpj, metodo, executado_em);
    """,
    # v3 (Etapa 5): acompanhamento de prospecção e consultas salvas
    """
    CREATE TABLE prospeccao_status (
        cnpj TEXT PRIMARY KEY CHECK (length(cnpj) = 14),
        status TEXT NOT NULL CHECK (status IN ('nao_prospectada', 'em_contato', 'contatada', 'sem_interesse', 'descartada', 'cliente')),
        observacao TEXT,
        atualizado_em TEXT NOT NULL,
        atualizado_por TEXT
    );
    CREATE INDEX ix_status_status ON prospeccao_status (status);
    CREATE TABLE status_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnpj TEXT NOT NULL,
        status TEXT NOT NULL,
        observacao TEXT,
        alterado_em TEXT NOT NULL,
        alterado_por TEXT
    );
    CREATE INDEX ix_historico_cnpj ON status_historico (cnpj, alterado_em);
    CREATE TABLE consulta_salva (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        filtros TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        criado_por TEXT,
        ultima_execucao TEXT
    );
    CREATE TABLE consulta_execucao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        consulta_id INTEGER NOT NULL REFERENCES consulta_salva (id) ON DELETE CASCADE,
        executado_em TEXT NOT NULL,
        mes_referencia TEXT,
        total INTEGER NOT NULL
    );
    CREATE TABLE consulta_execucao_cnpj (
        execucao_id INTEGER NOT NULL REFERENCES consulta_execucao (id) ON DELETE CASCADE,
        cnpj TEXT NOT NULL,
        PRIMARY KEY (execucao_id, cnpj)
    ) WITHOUT ROWID;
    """,
    # v4: sessões de login (o navegador guarda só o código; aqui fica apenas o hash dele)
    """
    CREATE TABLE sessao (
        token_hash TEXT PRIMARY KEY,
        usuario TEXT NOT NULL REFERENCES usuario (nome) ON DELETE CASCADE,
        criada_em TEXT NOT NULL,
        expira_em TEXT NOT NULL
    );
    CREATE INDEX ix_sessao_usuario ON sessao (usuario);
    """,
]


class UsuarioExistenteError(ValueError):
    """Já existe usuário com esse nome."""


class AppStore:
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
            with con:  # commit ou rollback automático
                yield con
        finally:
            con.close()

    def _migrar(self) -> None:
        with self.conexao() as con:
            con.execute("PRAGMA journal_mode = WAL")
            versao = con.execute("PRAGMA user_version").fetchone()[0]
            for numero, sql in enumerate(_MIGRACOES[versao:], start=versao + 1):
                con.executescript(sql)
                con.execute(f"PRAGMA user_version = {numero}")

    def versao_esquema(self) -> int:
        with self.conexao() as con:
            return con.execute("PRAGMA user_version").fetchone()[0]

    # ---------- usuários ----------
    def criar_usuario(self, nome: str, senha: str) -> None:
        nome = nome.strip().lower()
        if not nome:
            raise ValueError("Nome de usuário vazio")
        try:
            with self.conexao() as con:
                con.execute(
                    "INSERT INTO usuario (nome, senha_hash, criado_em) VALUES (?, ?, ?)",
                    (nome, gerar_hash(senha), _agora()),
                )
        except sqlite3.IntegrityError as exc:
            raise UsuarioExistenteError(f"Usuário já existe: {nome}") from exc

    def alterar_senha(self, nome: str, senha: str) -> bool:
        """Troca a senha e encerra todas as sessões abertas do usuário."""
        nome = nome.strip().lower()
        with self.conexao() as con:
            cur = con.execute("UPDATE usuario SET senha_hash = ? WHERE nome = ?", (gerar_hash(senha), nome))
            con.execute("DELETE FROM sessao WHERE usuario = ?", (nome,))
            return cur.rowcount == 1

    def remover_usuario(self, nome: str) -> bool:
        with self.conexao() as con:
            return con.execute("DELETE FROM usuario WHERE nome = ?", (nome.strip().lower(),)).rowcount == 1

    def listar_usuarios(self) -> list[str]:
        with self.conexao() as con:
            return [r["nome"] for r in con.execute("SELECT nome FROM usuario ORDER BY nome")]

    def autenticar(self, nome: str, senha: str) -> bool:
        nome = nome.strip().lower()
        with self.conexao() as con:
            linha = con.execute("SELECT senha_hash FROM usuario WHERE nome = ?", (nome,)).fetchone()
            ok = verificar_senha(senha, linha["senha_hash"]) if linha else verificar_senha(senha, _HASH_FALSO)
            if ok and linha:
                con.execute("UPDATE usuario SET ultimo_acesso = ? WHERE nome = ?", (_agora(), nome))
            return bool(ok and linha)

    # ---------- sessões de login ----------
    def criar_sessao(self, nome: str, horas: float) -> str:
        """Cria uma sessão e devolve o código secreto que vai para o navegador (só o hash fica guardado)."""
        token = secrets.token_urlsafe(32)
        agora = datetime.now()
        with self.conexao() as con:
            con.execute("DELETE FROM sessao WHERE expira_em < ?", (_agora(),))
            con.execute(
                "INSERT INTO sessao (token_hash, usuario, criada_em, expira_em) VALUES (?, ?, ?, ?)",
                (_hash_token(token), nome.strip().lower(), agora.isoformat(timespec="seconds"),
                 (agora + timedelta(hours=horas)).isoformat(timespec="seconds")),
            )
        return token

    def usuario_da_sessao(self, token: str | None) -> str | None:
        """Usuário dono de uma sessão válida (não expirada e de usuário que ainda existe)."""
        if not token or len(token) > 200:
            return None
        with self.conexao() as con:
            linha = con.execute(
                "SELECT s.usuario FROM sessao s JOIN usuario u ON u.nome = s.usuario "
                "WHERE s.token_hash = ? AND s.expira_em > ?",
                (_hash_token(token), _agora()),
            ).fetchone()
        return linha["usuario"] if linha else None

    def encerrar_sessao(self, token: str | None) -> None:
        if token:
            with self.conexao() as con:
                con.execute("DELETE FROM sessao WHERE token_hash = ?", (_hash_token(token),))

    # ---------- contatos complementares ----------
    def adicionar_contato(self, contato: ContatoNovo) -> bool:
        """Grava um contato complementar. Devolve False se o mesmo valor já existia para o CNPJ.

        Nunca toca no contato original da Receita, que mora em outro banco (somente leitura).
        """
        with self.conexao() as con:
            cur = con.execute(
                """INSERT OR IGNORE INTO contato_complementar
                   (cnpj, tipo, valor, valor_normalizado, fonte_url, fonte_nome, data_busca, confianca, observacao, criado_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (contato.cnpj, contato.tipo, contato.valor, contato.valor_normalizado, contato.fonte_url,
                 contato.fonte_nome, contato.data_busca or _agora(), contato.confianca, contato.observacao,
                 contato.criado_por),
            )
            return cur.rowcount == 1

    def listar_contatos(self, cnpj: str) -> list[dict]:
        with self.conexao() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM contato_complementar WHERE cnpj = ? "
                "ORDER BY CASE confianca WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, tipo, id",
                (cnpj,),
            )]

    def contatos_por_cnpjs(self, cnpjs: list[str]) -> dict[str, list[dict]]:
        """Contatos de vários CNPJs (para exportação), em lotes para não estourar o limite do SQLite."""
        resultado: dict[str, list[dict]] = {}
        with self.conexao() as con:
            for i in range(0, len(cnpjs), 900):
                lote = cnpjs[i : i + 900]
                marcadores = ",".join("?" * len(lote))
                for r in con.execute(
                    f"SELECT * FROM contato_complementar WHERE cnpj IN ({marcadores}) ORDER BY cnpj, tipo, id", lote
                ):
                    resultado.setdefault(r["cnpj"], []).append(dict(r))
        return resultado

    def remover_contato(self, contato_id: int) -> bool:
        with self.conexao() as con:
            return con.execute("DELETE FROM contato_complementar WHERE id = ?", (contato_id,)).rowcount == 1

    def cnpjs_com_contato_complementar(self) -> list[str]:
        with self.conexao() as con:
            return [r[0] for r in con.execute("SELECT DISTINCT cnpj FROM contato_complementar")]

    def versao_contatos(self) -> str:
        """Muda sempre que um contato é incluído ou removido (usado para renovar caches da interface)."""
        with self.conexao() as con:
            total, maior = con.execute("SELECT count(*), coalesce(max(id), 0) FROM contato_complementar").fetchone()
            return f"{total}-{maior}"

    # ---------- status de prospecção ----------
    def definir_status(self, cnpj: str, status: str, observacao: str | None = None, usuario: str | None = None) -> None:
        """Grava o status (ligado ao CNPJ completo, sobrevive a novas cargas) e registra no histórico."""
        if status not in STATUS:
            raise ValueError(f"Status inválido: {status}")
        agora = _agora()
        with self.conexao() as con:
            con.execute(
                """INSERT INTO prospeccao_status (cnpj, status, observacao, atualizado_em, atualizado_por)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (cnpj) DO UPDATE SET status = excluded.status, observacao = excluded.observacao,
                       atualizado_em = excluded.atualizado_em, atualizado_por = excluded.atualizado_por""",
                (cnpj, status, observacao or None, agora, usuario),
            )
            con.execute(
                "INSERT INTO status_historico (cnpj, status, observacao, alterado_em, alterado_por) VALUES (?, ?, ?, ?, ?)",
                (cnpj, status, observacao or None, agora, usuario),
            )

    def status_de(self, cnpjs: list[str]) -> dict[str, dict]:
        """Status atual de vários CNPJs. Quem não aparece está 'não prospectada'."""
        resultado: dict[str, dict] = {}
        with self.conexao() as con:
            for i in range(0, len(cnpjs), 900):
                lote = cnpjs[i : i + 900]
                for r in con.execute(
                    f"SELECT * FROM prospeccao_status WHERE cnpj IN ({','.join('?' * len(lote))})", lote
                ):
                    resultado[r["cnpj"]] = dict(r)
        return resultado

    def historico_status(self, cnpj: str) -> list[dict]:
        with self.conexao() as con:
            return [dict(r) for r in con.execute(
                "SELECT status, observacao, alterado_em, alterado_por FROM status_historico "
                "WHERE cnpj = ? ORDER BY alterado_em DESC, id DESC", (cnpj,))]

    def cnpjs_por_status(self) -> dict[str, list[str]]:
        with self.conexao() as con:
            mapa: dict[str, list[str]] = {s: [] for s in STATUS}
            for r in con.execute("SELECT cnpj, status FROM prospeccao_status"):
                mapa[r["status"]].append(r["cnpj"])
            return mapa

    def versao_status(self) -> str:
        with self.conexao() as con:
            total, maior = con.execute("SELECT count(*), coalesce(max(id), 0) FROM status_historico").fetchone()
            return f"{total}-{maior}"

    # ---------- consultas salvas ----------
    def salvar_consulta(self, nome: str, filtros: dict, usuario: str | None = None, sobrescrever: bool = False) -> int:
        nome = nome.strip()
        if not nome:
            raise ValueError("Dê um nome à consulta")
        texto = json.dumps(filtros, ensure_ascii=False, sort_keys=True)
        with self.conexao() as con:
            existente = con.execute("SELECT id FROM consulta_salva WHERE nome = ?", (nome,)).fetchone()
            if existente and not sobrescrever:
                raise ConsultaExistenteError(f"Já existe uma consulta chamada '{nome}'")
            if existente:
                con.execute("UPDATE consulta_salva SET filtros = ? WHERE id = ?", (texto, existente["id"]))
                return existente["id"]
            cur = con.execute(
                "INSERT INTO consulta_salva (nome, filtros, criado_em, criado_por) VALUES (?, ?, ?, ?)",
                (nome, texto, _agora(), usuario),
            )
            return cur.lastrowid

    def listar_consultas(self) -> list[dict]:
        with self.conexao() as con:
            return [dict(r) | {"filtros": json.loads(r["filtros"])}
                    for r in con.execute("SELECT * FROM consulta_salva ORDER BY nome")]

    def obter_consulta(self, nome_ou_id: str | int) -> dict | None:
        campo = "id" if isinstance(nome_ou_id, int) else "nome"
        with self.conexao() as con:
            r = con.execute(f"SELECT * FROM consulta_salva WHERE {campo} = ?", (nome_ou_id,)).fetchone()
            return dict(r) | {"filtros": json.loads(r["filtros"])} if r else None

    def excluir_consulta(self, consulta_id: int) -> bool:
        with self.conexao() as con:
            return con.execute("DELETE FROM consulta_salva WHERE id = ?", (consulta_id,)).rowcount == 1

    def registrar_execucao(self, consulta_id: int, cnpjs: list[str], mes_referencia: str | None) -> Execucao:
        """Guarda os CNPJs desta execução e devolve os que são novos em relação à anterior.

        Mantém só as duas execuções mais recentes por consulta (o necessário para comparar).
        """
        agora = _agora()
        with self.conexao() as con:
            anterior = con.execute(
                "SELECT id, executado_em FROM consulta_execucao WHERE consulta_id = ? ORDER BY id DESC LIMIT 1",
                (consulta_id,),
            ).fetchone()
            cur = con.execute(
                "INSERT INTO consulta_execucao (consulta_id, executado_em, mes_referencia, total) VALUES (?, ?, ?, ?)",
                (consulta_id, agora, mes_referencia, len(cnpjs)),
            )
            execucao_id = cur.lastrowid
            con.executemany(
                "INSERT OR IGNORE INTO consulta_execucao_cnpj (execucao_id, cnpj) VALUES (?, ?)",
                ((execucao_id, c) for c in cnpjs),
            )
            if anterior:
                novos = [r[0] for r in con.execute(
                    """SELECT cnpj FROM consulta_execucao_cnpj WHERE execucao_id = ?
                       EXCEPT SELECT cnpj FROM consulta_execucao_cnpj WHERE execucao_id = ? ORDER BY 1""",
                    (execucao_id, anterior["id"]),
                )]
            else:
                novos = sorted(set(cnpjs))
            con.execute(
                "DELETE FROM consulta_execucao WHERE consulta_id = ? AND id NOT IN "
                "(SELECT id FROM consulta_execucao WHERE consulta_id = ? ORDER BY id DESC LIMIT 2)",
                (consulta_id, consulta_id),
            )
            con.execute("UPDATE consulta_salva SET ultima_execucao = ? WHERE id = ?", (agora, consulta_id))
        return Execucao(consulta_id, execucao_id, agora, len(cnpjs), anterior["executado_em"] if anterior else None, novos)

    # ---------- registro de buscas (cache e limite) ----------
    def registrar_busca(self, cnpj: str, metodo: str, resultado: str, detalhe: str | None = None) -> None:
        with self.conexao() as con:
            con.execute(
                "INSERT INTO busca_log (cnpj, metodo, executado_em, resultado, detalhe) VALUES (?, ?, ?, ?, ?)",
                (cnpj, metodo, _agora(), resultado, detalhe),
            )

    def ultima_busca(self, cnpj: str, metodo: str) -> dict | None:
        with self.conexao() as con:
            r = con.execute(
                "SELECT * FROM busca_log WHERE cnpj = ? AND metodo = ? ORDER BY executado_em DESC, id DESC LIMIT 1",
                (cnpj, metodo),
            ).fetchone()
            return dict(r) if r else None

    def buscas_desde(self, metodo: str, desde: str) -> int:
        with self.conexao() as con:
            return con.execute(
                "SELECT count(*) FROM busca_log WHERE metodo = ? AND executado_em >= ? AND resultado <> 'cache'",
                (metodo, desde),
            ).fetchone()[0]


@dataclass(frozen=True)
class Execucao:
    consulta_id: int
    execucao_id: int
    executado_em: str
    total: int
    anterior_em: str | None  # None = primeira execução
    novos: list[str]  # CNPJs que não estavam na execução anterior


class ConsultaExistenteError(ValueError):
    """Já existe consulta salva com esse nome."""


@dataclass(frozen=True)
class ContatoNovo:
    cnpj: str
    tipo: str  # telefone | email | site | rede_social
    valor: str
    valor_normalizado: str
    fonte_nome: str  # ex.: "site oficial", "Brave Search API", "busca manual"
    confianca: str  # alta | media | baixa
    fonte_url: str | None = None
    observacao: str | None = None
    criado_por: str | None = None
    data_busca: str | None = None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# Usado quando o usuário não existe, para o tempo de resposta não revelar quais nomes existem.
_HASH_FALSO = gerar_hash("senha-inexistente-0000")
