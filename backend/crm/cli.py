"""Linha de comando do CRM.

    python -m crm superadmin criar NOME [--nome-exibicao "Seu Nome"]
    python -m crm superadmin senha NOME
    python -m crm empresa listar
    python -m crm usuario listar
    python -m crm usuario senha NOME          # redefine; a pessoa troca no próximo acesso
    python -m crm auditoria verificar         # confere se a auditoria foi adulterada
    python -m crm sincronizar                 # copia agora os leads do módulo 3
    python -m crm servir [--host 0.0.0.0] [--porta 8000]

Empresas clientes, administradores e funcionários são cadastrados pela tela.
O superadmin só existe por aqui, e só um.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .auditoria import registrar_sistema, verificar
from .config import Config
from .db import agora, conectar, migrar
from .seguranca import encerrar_sessoes_do_usuario, gerar_hash, validar_nome, validar_senha


def _ler_senha() -> str:
    if not sys.stdin.isatty():
        senha = sys.stdin.readline().rstrip("\n")
    else:
        senha = getpass.getpass("Senha: ")
        if getpass.getpass("Repita a senha: ") != senha:
            raise SystemExit("As senhas não conferem.")
    if erro := validar_senha(senha):
        raise SystemExit(erro)
    return senha


def _nome(valor: str) -> str:
    nome = valor.strip().lower()
    if erro := validar_nome(nome):
        raise SystemExit(erro)
    return nome


def superadmin(args: argparse.Namespace, conn) -> None:
    nome = _nome(args.nome)
    if args.acao == "criar":
        existente = conn.execute("SELECT nome FROM usuario WHERE papel = 'superadmin'").fetchone()
        if existente and not args.forcar:
            raise SystemExit(
                f"Já existe um superadmin ('{existente['nome']}'). O sistema tem um só. "
                "Use --forcar apenas se souber o que está fazendo."
            )
        if conn.execute("SELECT 1 FROM usuario WHERE nome = ?", (nome,)).fetchone():
            raise SystemExit(f"O usuário '{nome}' já existe.")
        conn.execute(
            "INSERT INTO usuario (nome, nome_exibicao, senha_hash, papel, empresa_id, criado_em, criado_por) "
            "VALUES (?, ?, ?, 'superadmin', NULL, ?, 'terminal')",
            (nome, (args.nome_exibicao or nome).strip(), gerar_hash(_ler_senha()), agora()),
        )
        conn.commit()
        registrar_sistema(Config().db_path, "terminal.superadmin_criar", usuario="terminal", detalhe={"superadmin": nome})
        print(f"Superadmin '{nome}' criado. Entre na tela e cadastre a primeira empresa cliente em 'Clientes'.")
    elif args.acao == "senha":
        if not conn.execute("SELECT 1 FROM usuario WHERE nome = ? AND papel = 'superadmin'", (nome,)).fetchone():
            raise SystemExit(f"'{nome}' não é o superadmin.")
        conn.execute("UPDATE usuario SET senha_hash = ?, deve_trocar_senha = 0 WHERE nome = ?", (gerar_hash(_ler_senha()), nome))
        encerrar_sessoes_do_usuario(conn, nome)
        conn.commit()
        registrar_sistema(Config().db_path, "terminal.senha", usuario="terminal", detalhe={"usuario": nome})
        print("Senha trocada. As sessões abertas foram encerradas.")


def usuario(args: argparse.Namespace, conn) -> None:
    if args.acao == "listar":
        linhas = conn.execute(
            """SELECT u.nome, u.nome_exibicao, u.papel, u.ativo, e.nome AS empresa, u.ultimo_acesso
               FROM usuario u LEFT JOIN empresa e ON e.id = u.empresa_id ORDER BY e.nome, u.papel, u.nome"""
        ).fetchall()
        if not linhas:
            print("Nenhum usuário. Comece com: python -m crm superadmin criar SEU-NOME")
        for r in linhas:
            situacao = "" if r["ativo"] else " (desativado)"
            print(f"{r['nome']:<20} {r['papel']:<11} {(r['empresa'] or '—'):<28} {r['nome_exibicao'] or ''}{situacao}")
        return
    nome = _nome(args.nome)
    if not conn.execute("SELECT 1 FROM usuario WHERE nome = ?", (nome,)).fetchone():
        raise SystemExit(f"O usuário '{nome}' não existe.")
    conn.execute("UPDATE usuario SET senha_hash = ?, deve_trocar_senha = 1 WHERE nome = ?", (gerar_hash(_ler_senha()), nome))
    encerrar_sessoes_do_usuario(conn, nome)
    conn.commit()
    registrar_sistema(Config().db_path, "terminal.senha_provisoria", usuario="terminal", detalhe={"usuario": nome})
    print(f"Senha provisória definida para '{nome}'. Ela será trocada no próximo acesso.")


def empresa(args: argparse.Namespace, conn) -> None:
    linhas = conn.execute(
        "SELECT e.id, e.nome, e.ativa, (SELECT COUNT(*) FROM usuario u WHERE u.empresa_id = e.id) AS pessoas FROM empresa e ORDER BY e.nome"
    ).fetchall()
    if not linhas:
        print("Nenhuma empresa cliente. Cadastre pela tela, em 'Clientes'.")
    for r in linhas:
        print(f"{r['id']:>4}  {r['nome']:<40} {r['pessoas']} pessoa(s){'' if r['ativa'] else '  (desativada)'}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="crm", description="CRM de prospecção")
    sub = p.add_subparsers(dest="comando", required=True)

    ps = sub.add_parser("superadmin", help="o administrador do sistema (um só)")
    ss = ps.add_subparsers(dest="acao", required=True)
    pc = ss.add_parser("criar")
    pc.add_argument("nome")
    pc.add_argument("--nome-exibicao")
    pc.add_argument("--forcar", action="store_true", help=argparse.SUPPRESS)
    ss.add_parser("senha").add_argument("nome")

    pu = sub.add_parser("usuario", help="consultar usuários e redefinir senhas")
    su = pu.add_subparsers(dest="acao", required=True)
    su.add_parser("listar")
    su.add_parser("senha").add_argument("nome")

    pa = sub.add_parser("auditoria", help="conferir a integridade da auditoria")
    sa = pa.add_subparsers(dest="acao", required=True)
    sa.add_parser("verificar")

    sub.add_parser("sincronizar", help="copiar agora os leads do módulo 3")

    pe = sub.add_parser("empresa", help="consultar empresas clientes")
    se = pe.add_subparsers(dest="acao", required=True)
    se.add_parser("listar")

    pv = sub.add_parser("servir", help="iniciar a API")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--porta", type=int, default=8000)

    args = p.parse_args(argv)
    cfg = Config()
    if args.comando == "servir":
        import uvicorn

        from .main import criar_app

        uvicorn.run(criar_app(cfg), host=args.host, port=args.porta, proxy_headers=True)
        return

    migrar(cfg.db_path)
    if args.comando == "auditoria":
        linhas, adulterada = verificar(cfg.db_path)
        if adulterada is None:
            print(f"Auditoria íntegra: {linhas} registros conferidos.")
            return
        raise SystemExit(f"ATENÇÃO: a auditoria foi alterada fora do sistema a partir do registro {adulterada}.")
    if args.comando == "sincronizar":
        from .modulo3 import Modulo3
        from .sincronizacao import sincronizar

        r = sincronizar(cfg, Modulo3(cfg.modulo3_url, cfg.modulo3_timeout, cfg.agente_timeout))
        print(f"Sincronizado: {r['lidos']} lidos, {r['novos']} novos, {r['atualizados']} atualizados, {r['ausentes']} saíram da base.")
        return
    conn = conectar(cfg.db_path)
    try:
        {"superadmin": superadmin, "usuario": usuario, "empresa": empresa}[args.comando](args, conn)
        conn.commit()
    finally:
        conn.close()
