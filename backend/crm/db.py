"""Banco do CRM (SQLite).

Guarda só o que é do CRM: empresas clientes, usuários, sessões, estágio e
responsável de cada lead, atividades, modelos de mensagem e supressão.
Tudo que é de trabalho fica separado por empresa cliente (`empresa_id`).
Os dados das empresas prospectadas vêm sempre do módulo 3 e nunca são copiados.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Supressão com empresa_id = 0 vale para todas as empresas clientes (só o superadmin cria).
SUPRESSAO_GLOBAL = 0

MIGRACOES: list[str] = [
    # v1
    """
    CREATE TABLE empresa (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL UNIQUE,
        documento TEXT,
        perfis TEXT NOT NULL DEFAULT '[]',          -- JSON; vazio = todos os perfis (menos os produtos de outras empresas)
        ativa INTEGER NOT NULL DEFAULT 1,
        criada_em TEXT NOT NULL
    );
    CREATE TABLE usuario (
        nome TEXT PRIMARY KEY,
        nome_exibicao TEXT,
        senha_hash TEXT NOT NULL,
        papel TEXT NOT NULL CHECK (papel IN ('superadmin', 'admin', 'vendedor')),
        empresa_id INTEGER REFERENCES empresa(id),
        ativo INTEGER NOT NULL DEFAULT 1,
        deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
        criado_em TEXT NOT NULL,
        criado_por TEXT,
        ultimo_acesso TEXT,
        CHECK ((papel = 'superadmin') = (empresa_id IS NULL))
    );
    CREATE INDEX usuario_empresa ON usuario(empresa_id);
    CREATE TABLE sessao (
        token_hash TEXT PRIMARY KEY,
        usuario TEXT NOT NULL REFERENCES usuario(nome) ON DELETE CASCADE,
        empresa_ativa INTEGER REFERENCES empresa(id) ON DELETE SET NULL,
        criada_em TEXT NOT NULL,
        expira_em TEXT NOT NULL,
        ultimo_uso TEXT
    );
    CREATE TABLE login_falha (
        usuario TEXT PRIMARY KEY,
        falhas INTEGER NOT NULL,
        ultima TEXT NOT NULL
    );
    CREATE TABLE lead_estado (
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        cnpj TEXT NOT NULL,
        estagio TEXT NOT NULL,
        responsavel TEXT,
        atualizado_em TEXT NOT NULL,
        atualizado_por TEXT NOT NULL,
        PRIMARY KEY (empresa_id, cnpj)
    );
    CREATE INDEX lead_estado_responsavel ON lead_estado(empresa_id, responsavel);
    CREATE TABLE atividade (
        id INTEGER PRIMARY KEY,
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        cnpj TEXT NOT NULL,
        tipo TEXT NOT NULL,
        canal TEXT,
        destino TEXT,
        assunto TEXT,
        texto TEXT,
        criado_em TEXT NOT NULL,
        criado_por TEXT NOT NULL
    );
    CREATE INDEX atividade_cnpj ON atividade(empresa_id, cnpj, id DESC);
    CREATE INDEX atividade_autor ON atividade(empresa_id, criado_por, id DESC);
    CREATE TABLE supressao (
        id INTEGER PRIMARY KEY,
        empresa_id INTEGER NOT NULL,                 -- 0 = todas as empresas
        tipo TEXT NOT NULL CHECK (tipo IN ('email', 'telefone', 'dominio', 'cnpj')),
        valor TEXT NOT NULL,
        motivo TEXT,
        criado_em TEXT NOT NULL,
        criado_por TEXT NOT NULL,
        UNIQUE (empresa_id, tipo, valor)
    );
    CREATE TABLE modelo_mensagem (
        id INTEGER PRIMARY KEY,
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        nome TEXT NOT NULL,
        canal TEXT NOT NULL CHECK (canal IN ('email', 'whatsapp', 'formulario', 'telefone')),
        perfil_id TEXT,
        assunto TEXT,
        corpo TEXT NOT NULL,
        atualizado_em TEXT NOT NULL,
        UNIQUE (empresa_id, nome)
    );
    """,
    # v2 — leads guardados no CRM, próximos passos, vendas e auditoria.
    # A partir desta versão o banco só muda por migração; nada de recriar do zero.
    """
    ALTER TABLE lead_estado RENAME COLUMN cnpj TO chave;
    ALTER TABLE atividade RENAME COLUMN cnpj TO chave;

    -- Cópia local dos leads. Os do módulo 3 são compartilhados (empresa_id nulo) e
    -- sincronizados; os manuais pertencem a uma empresa cliente. `chave` é o CNPJ
    -- nos do módulo 3 e 'M' + código nos manuais.
    CREATE TABLE lead (
        chave TEXT PRIMARY KEY,
        origem TEXT NOT NULL CHECK (origem IN ('modulo3', 'manual')),
        empresa_id INTEGER REFERENCES empresa(id) ON DELETE CASCADE,
        cnpj TEXT,
        nome TEXT NOT NULL,
        municipio TEXT,
        uf TEXT,
        bairro TEXT,
        perfil_id TEXT,
        score INTEGER,
        e_alvo INTEGER NOT NULL DEFAULT 1,
        data_abertura TEXT,
        busca TEXT NOT NULL DEFAULT '',
        dados TEXT NOT NULL,
        fonte TEXT,
        mes_referencia TEXT,
        sincronizado_em TEXT,
        ausente_desde TEXT,
        criado_em TEXT NOT NULL,
        criado_por TEXT,
        CHECK ((origem = 'manual') = (empresa_id IS NOT NULL))
    );
    CREATE INDEX lead_lista ON lead(origem, e_alvo, perfil_id, score DESC);
    CREATE INDEX lead_empresa ON lead(empresa_id);
    CREATE INDEX lead_cnpj ON lead(cnpj);

    CREATE TABLE tarefa (
        id INTEGER PRIMARY KEY,
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        chave TEXT NOT NULL,
        titulo TEXT NOT NULL,
        vence_em TEXT NOT NULL,
        responsavel TEXT NOT NULL,
        concluida_em TEXT,
        concluida_por TEXT,
        criado_em TEXT NOT NULL,
        criado_por TEXT NOT NULL
    );
    CREATE INDEX tarefa_agenda ON tarefa(empresa_id, responsavel, concluida_em, vence_em);
    CREATE INDEX tarefa_lead ON tarefa(empresa_id, chave);

    CREATE TABLE venda (
        id INTEGER PRIMARY KEY,
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        chave TEXT NOT NULL,
        valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
        data TEXT NOT NULL,
        descricao TEXT,
        vendedor TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        criado_por TEXT NOT NULL,
        estornada_em TEXT,
        estornada_por TEXT,
        motivo_estorno TEXT
    );
    CREATE INDEX venda_empresa ON venda(empresa_id, data);
    CREATE INDEX venda_lead ON venda(empresa_id, chave);

    CREATE TABLE sincronizacao (
        id INTEGER PRIMARY KEY,
        inicio TEXT NOT NULL,
        fim TEXT,
        lidos INTEGER, novos INTEGER, atualizados INTEGER, ausentes INTEGER,
        fonte TEXT,
        erro TEXT
    );

    -- Auditoria: só inclusão. Cada linha guarda o hash da anterior, então qualquer
    -- alteração ou remoção feita direto no arquivo quebra a cadeia e é detectada.
    CREATE TABLE auditoria (
        id INTEGER PRIMARY KEY,
        quando TEXT NOT NULL,
        requisicao TEXT,
        usuario TEXT,
        papel TEXT,
        empresa_id INTEGER,
        tipo TEXT NOT NULL CHECK (tipo IN ('acesso', 'alteracao', 'seguranca', 'evento', 'sistema')),
        acao TEXT NOT NULL,
        entidade TEXT,
        entidade_id TEXT,
        antes TEXT,
        depois TEXT,
        detalhe TEXT,
        resultado TEXT NOT NULL CHECK (resultado IN ('ok', 'negado', 'recusado', 'erro')),
        status INTEGER,
        metodo TEXT,
        rota TEXT,
        ip TEXT,
        agente TEXT,
        hash_anterior TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX auditoria_empresa ON auditoria(empresa_id, id DESC);
    CREATE INDEX auditoria_usuario ON auditoria(usuario, id DESC);
    CREATE INDEX auditoria_entidade ON auditoria(entidade_id, id DESC);
    CREATE TRIGGER auditoria_sem_alteracao BEFORE UPDATE ON auditoria
        BEGIN SELECT RAISE(ABORT, 'a auditoria só aceita inclusão'); END;
    CREATE TRIGGER auditoria_sem_remocao BEFORE DELETE ON auditoria
        BEGIN SELECT RAISE(ABORT, 'a auditoria só aceita inclusão'); END;
    """,
    # v3 — produtos que cada empresa cliente descreve; o agente transforma cada um num perfil de leads.
    """
    CREATE TABLE produto (
        perfil_id TEXT PRIMARY KEY,                 -- id do perfil no agente (módulo 2)
        empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
        nome TEXT NOT NULL,
        descricao TEXT NOT NULL,
        versao INTEGER NOT NULL DEFAULT 1,
        criado_por TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL,
        removido_em TEXT,                           -- os leads já trabalhados continuam visíveis
        execucao_sincronizada TEXT                  -- fim da classificação do agente já copiada para cá
    );
    CREATE INDEX produto_empresa ON produto(empresa_id);

    -- Uma linha por lead × perfil: o mesmo CNPJ pode ser alvo do produto de duas empresas, e cada uma
    -- vê o lead pela classificação dos perfis dela (pontuação e justificativa do próprio produto).
    CREATE TABLE lead_perfil (
        chave TEXT NOT NULL REFERENCES lead(chave) ON DELETE CASCADE,
        perfil_id TEXT NOT NULL,
        score INTEGER,
        e_alvo INTEGER NOT NULL DEFAULT 1,
        classificacao TEXT NOT NULL,                -- JSON, como veio do módulo 3
        PRIMARY KEY (chave, perfil_id)
    );
    CREATE INDEX lead_perfil_perfil ON lead_perfil(perfil_id, chave);
    INSERT INTO lead_perfil (chave, perfil_id, score, e_alvo, classificacao)
        SELECT chave, perfil_id, score, e_alvo, json_extract(dados, '$.classificacao') FROM lead
        WHERE origem = 'modulo3' AND perfil_id IS NOT NULL AND json_extract(dados, '$.classificacao') IS NOT NULL;
    """,
]

MODELOS_PADRAO: list[tuple[str, str, str | None, str | None, str]] = [
    (
        "E-mail · primeiro contato (hotelaria)", "email", "amenities_hotel",
        "Amostra de amenities para o {nome_fantasia}",
        """Olá, equipe do {nome_fantasia},

Vi que vocês começaram as atividades em {municipio} há pouco tempo e imagino que estejam definindo os fornecedores dos quartos.

Trabalho com {produto}. Posso enviar um kit de amostra, sem custo, para vocês avaliarem?

Se fizer sentido, basta responder com o endereço e o nome de quem cuida das compras.

Obrigado,
{vendedor}

—
Encontrei o contato de vocês no cadastro público de CNPJ da Receita Federal e no site da empresa. Se preferir não receber mensagens, responda "remover" e não entraremos mais em contato.""",
    ),
    (
        "E-mail · primeiro contato (restaurantes)", "email", "higiene_restaurante",
        "Amostra para os lavatórios do {nome_fantasia}",
        """Olá, equipe do {nome_fantasia},

Parabéns pela abertura em {municipio}! Trabalho com {produto} e gostaria de enviar uma amostra sem custo para vocês testarem.

Posso mandar? É só responder com o endereço e o nome do responsável pelas compras.

Obrigado,
{vendedor}

—
Contato obtido no cadastro público de CNPJ da Receita Federal e no site da empresa. Para não receber mais mensagens, responda "remover".""",
    ),
    (
        "WhatsApp · primeiro contato", "whatsapp", None, None,
        "Olá! Aqui é {vendedor}. Vi que o {nome_fantasia} começou as atividades em {municipio}. Trabalho com {produto} e gostaria de enviar uma amostra sem custo. Posso mandar mais detalhes por aqui? Se preferir não receber mensagens, é só avisar.",
    ),
    (
        "Formulário do site · primeiro contato", "formulario", None, None,
        "Olá! Sou {vendedor} e trabalho com {produto}. Vi que o {nome_fantasia} abriu recentemente em {municipio} e gostaria de enviar uma amostra sem custo. Podem me indicar o responsável pelas compras? Se não tiverem interesse, me avisem e não entrarei mais em contato.",
    ),
    (
        "Ligação · roteiro", "telefone", None, None,
        """1. Apresentar-se: {vendedor}, fornecedor de {produto}.
2. Pedir para falar com quem cuida das compras (governança, gerência ou o dono).
3. Motivo: vi que o {nome_fantasia} abriu há pouco em {municipio}.
4. Oferta: kit de amostra sem custo. Confirmar endereço e nome de quem vai receber.
5. Se não houver interesse: agradecer e marcar "Sem interesse".""",
    ),
]


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dias_atras(dias: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")


def conectar(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # Uma conexão por requisição. O FastAPI pode abrir a dependência numa thread
    # e executar a rota em outra; a conexão nunca é usada por duas ao mesmo tempo.
    conn = sqlite3.connect(caminho, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrar(caminho: Path) -> int:
    conn = conectar(caminho)
    try:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        for numero, sql in enumerate(MIGRACOES[versao:], start=versao + 1):
            conn.executescript("BEGIN;" + sql + f"; PRAGMA user_version = {numero}; COMMIT;")
            versao = numero
        return versao
    finally:
        conn.close()


def semear_modelos(conn: sqlite3.Connection, empresa_id: int) -> None:
    for nome, canal, perfil, assunto, corpo in MODELOS_PADRAO:
        conn.execute(
            "INSERT OR IGNORE INTO modelo_mensagem (empresa_id, nome, canal, perfil_id, assunto, corpo, atualizado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (empresa_id, nome, canal, perfil, assunto, corpo, agora()),
        )


def criar_empresa(conn: sqlite3.Connection, nome: str, documento: str | None, perfis_json: str) -> int:
    cur = conn.execute(
        "INSERT INTO empresa (nome, documento, perfis, criada_em) VALUES (?, ?, ?, ?)",
        (nome, documento, perfis_json, agora()),
    )
    semear_modelos(conn, cur.lastrowid)
    return cur.lastrowid
