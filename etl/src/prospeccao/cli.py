"""Linha de comando: carga, meses, amostra, benchmark, usuario, exportar, exportar-novas, exportar-parquet(-amostra),
agendador e web."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from prospeccao.config import Config, carregar_config
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.ingest.download import ClienteReceita, FonteIndisponivelError
from prospeccao.ingest.layout import LayoutInvalidoError
from prospeccao.monitor import configurar_log
from prospeccao.pipeline import (
    CargaEmAndamentoError,
    EspacoInsuficienteError,
    carregar_se_houver_novo,
    executar_carga,
    rodar_agendador,
)

log = logging.getLogger("prospeccao")


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="prospeccao", description="Prospecção de empresas novas (CNPJ)")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("carga", help="Baixa e processa um mês da base da Receita")
    c.add_argument("--mes", help="Mês de referência AAAA-MM (padrão: o mais recente publicado)")
    c.add_argument("--sem-download", action="store_true", help="Usa os zips já presentes em data/bronze/<mes>/zip")
    c.add_argument("--data-referencia", type=date.fromisoformat, help="Data base para a flag is_nova (padrão: hoje)")
    c.add_argument("--se-houver-novo", action="store_true",
                   help="Só carrega se a Receita publicou um mês mais novo que o da base atual")

    g = sub.add_parser("agendador", help="Fica rodando e carrega automaticamente cada mês novo publicado")
    g.add_argument("--intervalo-horas", type=float, default=float(os.getenv("AGENDADOR_INTERVALO_HORAS", "24")))
    g.add_argument("--uma-vez", action="store_true", help="Verifica uma vez e sai (para usar com cron)")

    sub.add_parser("meses", help="Lista os meses publicados pela Receita")

    a = sub.add_parser("amostra", help="Gera uma base fictícia no formato da Receita e carrega")
    a.add_argument("--mes", default="2026-09")
    a.add_argument("--volume", type=int, default=0, help="Estabelecimentos sintéticos extras (para medir desempenho)")
    a.add_argument("--data-referencia", type=date.fromisoformat, default=date.today())

    b = sub.add_parser("benchmark", help="Mede carga gold e consultas com base sintética de volume real")
    b.add_argument("--linhas", type=int, default=60_000_000)
    b.add_argument("--reaproveitar", action="store_true", help="Usa a base sintética já gerada")

    u = sub.add_parser("usuario", help="Gerencia usuários da interface")
    u.add_argument("acao", choices=["criar", "senha", "remover", "listar"])
    u.add_argument("nome", nargs="?")

    e = sub.add_parser("exportar", help="Exporta o resultado de uma consulta salva (CSV, XLSX ou PDF)")
    e.add_argument("--consulta", required=True, help="Nome da consulta salva na interface")
    e.add_argument("--formato", choices=["csv", "xlsx", "pdf"], default="csv")
    e.add_argument("--saida", type=Path, help="Arquivo de saída (padrão: data/exportacoes/<consulta>_<data>.<formato>)")
    e.add_argument("--separador", default=";", help="Separador do CSV (padrão ';')")
    e.add_argument("--top", type=int, default=50, help="Empresas na tabela do PDF")

    n = sub.add_parser("exportar-novas",
                       help="Grava o arquivo mensal de empresas novas para o agente classificador (módulo 2)")
    n.add_argument("--mes", help="Mês AAAA-MM (padrão: o da base carregada; precisa ser o mesmo)")
    n.add_argument("--desde", type=date.fromisoformat,
                   help="Usa as aberturas a partir desta data em vez de comparar com o mês anterior")

    x = sub.add_parser("exportar-parquet",
                       help="Grava os estabelecimentos ativos num único Parquet (ex.: para o agente no Colab)")
    x.add_argument("--saida", type=Path,
                   help="Arquivo de saída (padrão: data/exportacoes/empresas_ativas_<mes>.parquet)")

    xa = sub.add_parser("exportar-parquet-amostra",
                        help="Grava uma amostra de estabelecimentos ativos, variada em municípios e CNAEs, num Parquet")
    xa.add_argument("--linhas", type=int, default=10_000, help="Linhas da amostra (padrão: 10000)")
    xa.add_argument("--saida", type=Path,
                    help="Arquivo de saída (padrão: data/exportacoes/empresas_ativas_<mes>_amostra<linhas>.parquet)")

    w = sub.add_parser("web", help="Abre a interface no navegador (Streamlit)")
    w.add_argument("--porta", type=int, default=int(os.getenv("WEB_PORTA", "8501")))
    w.add_argument("--endereco", default=os.getenv("WEB_ENDERECO", "127.0.0.1"),
                   help="127.0.0.1 = só este computador; 0.0.0.0 = rede (use HTTPS na frente)")
    return p.parse_args(argv)


def _ler_senha() -> str:
    """Pede a senha duas vezes no terminal; sem terminal (ex.: script), lê uma linha da entrada padrão."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    senha = getpass.getpass("Senha (mín. 8 caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        raise ValueError("As senhas não conferem")
    return senha


def _usuario(cfg: Config, acao: str, nome: str | None) -> int:
    from prospeccao.app_store import AppStore

    store = AppStore(cfg.app_db)
    if acao == "listar":
        print("\n".join(store.listar_usuarios()) or "(nenhum usuário)")
        return 0
    if not nome:
        print("Informe o nome do usuário.", file=sys.stderr)
        return 2
    if acao == "criar":
        store.criar_usuario(nome, _ler_senha())
        print(f"Usuário '{nome.lower()}' criado.")
    elif acao == "senha":
        ok = store.alterar_senha(nome, _ler_senha())
        print("Senha alterada." if ok else "Usuário não encontrado.")
    elif acao == "remover":
        print("Usuário removido." if store.remover_usuario(nome) else "Usuário não encontrado.")
    return 0


def _exportar(cfg: Config, args: argparse.Namespace) -> int:
    import re
    from datetime import datetime

    from prospeccao.app_store import AppStore
    from prospeccao.consulta import Repositorio
    from prospeccao.domain.filtros import Filtros
    from prospeccao.reports.gerar import gerar_arquivo

    store = AppStore(cfg.app_db)
    consulta = store.obter_consulta(args.consulta)
    if consulta is None:
        nomes = ", ".join(c["nome"] for c in store.listar_consultas()) or "nenhuma"
        print(f"Consulta '{args.consulta}' não encontrada. Salvas: {nomes}", file=sys.stderr)
        return 2
    saida = args.saida or cfg.data_dir / "exportacoes" / (
        re.sub(r"[^\w-]+", "_", consulta["nome"]).strip("_") + f"_{datetime.now():%Y%m%d_%H%M}.{args.formato}")
    saida.parent.mkdir(parents=True, exist_ok=True)
    separador = "\t" if args.separador in ("tab", "\\t") else args.separador
    with Repositorio(cfg.gold_db, app=store) as repo, saida.open("wb") as destino:
        n = gerar_arquivo(args.formato, destino, repo, store, Filtros.de_dict(consulta["filtros"]),
                          separador=separador, top_n=args.top, usuario="linha de comando")
    print(f"{n} empresas exportadas em {saida}")
    return 0


def _exportar_parquet(cfg: Config, saida: Path | None, linhas: int | None = None) -> int:
    from prospeccao.consulta import Repositorio
    from prospeccao.domain.filtros import Filtros
    from prospeccao.transform.gold import configurar_duckdb

    with Repositorio(cfg.gold_db) as repo:
        # A ordenação de dezenas de milhões de linhas passa do limite de memória e usa o disco temporário.
        configurar_duckdb(repo.con, cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir, cfg.duckdb_max_temp)
        mes = (repo.ultima_carga() or {}).get("mes_referencia", "base")
        sufixo = f"_amostra{linhas}" if linhas is not None else ""
        saida = saida or cfg.data_dir / "exportacoes" / f"empresas_ativas_{mes}{sufixo}.parquet"
        n = repo.exportar_parquet(Filtros(periodo=None), saida, limite=linhas)  # situação padrão: só ATIVA
    print(f"{n} estabelecimentos ativos exportados em {saida} ({saida.stat().st_size / 1e6:.1f} MB)")
    return 0


def _web(porta: int, endereco: str) -> int:
    app = Path(__file__).parent / "web" / "app.py"
    comando = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.headless=true", "--browser.gatherUsageStats=false", "--client.toolbarMode=viewer",
        f"--server.port={porta}", f"--server.address={endereco}",
    ]
    print(f"Interface em http://{'localhost' if endereco == '127.0.0.1' else endereco}:{porta}")
    return subprocess.call(comando)


def main(argv: list[str] | None = None) -> int:
    configurar_log()
    args = _argumentos(argv)
    cfg = carregar_config()
    try:
        if args.comando == "meses":
            print("\n".join(ClienteReceita(cfg).listar_meses()))
            return 0
        if args.comando == "usuario":
            return _usuario(cfg, args.acao, args.nome)
        if args.comando == "web":
            return _web(args.porta, args.endereco)
        if args.comando == "exportar":
            return _exportar(cfg, args)
        if args.comando == "exportar-novas":
            from prospeccao.transform.novas import exportar_novas

            r = exportar_novas(cfg, args.mes, args.desde)
            print(f"{r['total_linhas']} empresas novas de {r['mes']} ({r['criterio']}) em {r['pasta']}")
            return 0
        if args.comando == "exportar-parquet":
            return _exportar_parquet(cfg, args.saida)
        if args.comando == "exportar-parquet-amostra":
            return _exportar_parquet(cfg, args.saida, args.linhas)
        if args.comando == "agendador":
            rodar_agendador(cfg, args.intervalo_horas, args.uma_vez)
            return 0
        if args.comando == "carga" and args.se_houver_novo:
            resumo = carregar_se_houver_novo(cfg)
            if resumo is None:
                print("Base já atualizada; nada a fazer.")
                return 0
            print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.comando == "benchmark":
            from prospeccao.benchmark import executar_benchmark

            resumo = executar_benchmark(cfg, args.linhas, args.reaproveitar)
            print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.comando == "amostra":
            cfg = replace(cfg, espaco_minimo_gb=min(cfg.espaco_minimo_gb, 1.0))
            gerar_amostra(cfg.bronze_dir, args.mes, args.data_referencia, args.volume)
            resumo = executar_carga(cfg, args.mes, baixar=False, data_referencia=args.data_referencia)
        else:
            resumo = executar_carga(cfg, args.mes, baixar=not args.sem_download, data_referencia=args.data_referencia)
    except (LayoutInvalidoError, FonteIndisponivelError, EspacoInsuficienteError, CargaEmAndamentoError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
