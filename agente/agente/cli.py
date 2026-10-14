"""Linha de comando do agente classificador (módulo 2).

    agente perfis
    agente validar --mes 2026-10
    agente classificar --mes 2026-10 --perfil amenities_hotel [--modo lote|online] [--aguardar]
    agente lotes
    agente alvos --perfil amenities_hotel [--mes 2026-10] [--saida alvos.csv]
    agente amostrar-rotulos --mes 2026-10 --perfil amenities_hotel --n 200 --saida rotulos.csv
    agente avaliar --perfil amenities_hotel --rotulos rotulos.csv
    agente revisao-mensal --mes 2026-10 --perfil amenities_hotel --saida revisao.csv
    agente custos [--mes 2026-10]
    agente exportar-crm [--perfil amenities_hotel] [--mes 2026-10]
    agente servir [--porta 9000]                 perguntas do CRM (POST /perguntar), via módulo 3
    agente perguntar "texto" [--contexto lead.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agente.armazenamento import ClassificacaoStore
from agente.avaliacao import META_PRECISAO, amostra_para_rotular, amostra_revisao, avaliar
from agente.config import AgenteConfig, carregar_config
from agente.contrato import ContratoInvalidoError, validar_mes
from agente.exportacao import ARQUIVO_CRM, exportar_crm
from agente.funil import FunilGrandeError, contar
from agente.llm import ClienteGemini
from agente.perfil import PerfilInvalidoError, carregar_perfil, listar_ids
from agente.servico import MODOS, Classificador
from agente.sinais import carregar_redes, socios_pj

log = logging.getLogger("agente")
FabricaCliente = Callable[[AgenteConfig], Any]


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agente",
                                description="Agente classificador: perfil (ICP) → funil SQL → LLM → classificacao → CSV do CRM")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("perfis", help="Lista e valida os perfis em perfis/")

    v = sub.add_parser("validar", help="Confere o arquivo do mês contra o contrato e mostra o funil de cada perfil")
    v.add_argument("--mes", required=True)

    c = sub.add_parser("classificar", help="Classifica as empresas novas do mês para um perfil")
    c.add_argument("--mes", required=True)
    c.add_argument("--perfil", required=True)
    c.add_argument("--modo", choices=MODOS, default="lote", help="lote (padrão, 50%% mais barato) ou online")
    c.add_argument("--aguardar", action="store_true", help="Espera o lote terminar (até 26 h) em vez de sair")
    c.add_argument("--intervalo", type=float, default=300, help="Segundos entre consultas ao lote com --aguardar")
    c.add_argument("--forcar", action="store_true", help="Reclassifica mesmo o que não mudou")
    c.add_argument("--limite", type=int, help="Trava de custo: teto de empresas no funil (padrão: o do perfil)")
    c.add_argument("--amostra", type=int, help="Classifica só as N primeiras pendentes (para testar o perfil e a API)")

    lt = sub.add_parser("lotes", help="Lotes enviados à API e o estado de cada um")
    lt.add_argument("--perfil")

    a = sub.add_parser("alvos", help="Empresas classificadas como alvo (versão vigente do perfil)")
    a.add_argument("--perfil", required=True)
    a.add_argument("--mes")
    a.add_argument("--saida", type=Path, help="Grava em CSV em vez de mostrar")

    r = sub.add_parser("amostrar-rotulos", help="Planilha com amostra aleatória do funil para rotular à mão")
    r.add_argument("--mes", required=True)
    r.add_argument("--perfil", required=True)
    r.add_argument("--n", type=int, default=200)
    r.add_argument("--saida", type=Path, required=True)
    r.add_argument("--semente", type=int, default=42)

    av = sub.add_parser("avaliar", help="Precisão e cobertura contra a planilha rotulada")
    av.add_argument("--perfil", required=True)
    av.add_argument("--rotulos", type=Path, required=True)
    av.add_argument("--meta", type=float, default=META_PRECISAO)
    av.add_argument("--json", type=Path, help="Grava também o relatório em JSON")

    rv = sub.add_parser("revisao-mensal", help="Amostra aleatória das classificações do mês para conferência humana")
    rv.add_argument("--mes", required=True)
    rv.add_argument("--perfil", required=True)
    rv.add_argument("--n", type=int, default=20)
    rv.add_argument("--saida", type=Path, required=True)

    cu = sub.add_parser("custos", help="Tokens e custo estimado por mês e perfil")
    cu.add_argument("--mes")

    ex = sub.add_parser("exportar-crm", help="Grava o CSV que o módulo 3 entrega ao CRM (versão vigente de cada perfil)")
    ex.add_argument("--perfil", action="append", help="Só este perfil (pode repetir). Padrão: todos os classificados")
    ex.add_argument("--mes", help="Só este mês. Padrão: todos os meses classificados")
    ex.add_argument("--saida", type=Path, help=f"Padrão: AGENTE_SAIDA_DIR/{ARQUIVO_CRM}")

    sv = sub.add_parser("servir", help="Responde às perguntas do CRM em POST /perguntar (o módulo 3 chama, AGENTE_URL)")
    sv.add_argument("--endereco", default="0.0.0.0")
    sv.add_argument("--porta", type=int, default=9000)

    pg = sub.add_parser("perguntar", help="Faz uma pergunta ao agente pelo terminal (para testar)")
    pg.add_argument("pergunta")
    pg.add_argument("--contexto", type=Path, help="JSON de um lead (contrato v1) para perguntar sobre ele")
    return p.parse_args(argv)


def _tabela(linhas: list[dict[str, Any]]) -> None:
    if not linhas:
        print("(nada)")
        return
    colunas = list(linhas[0])
    larguras = {c: max(len(c), *(len(str(l[c])) for l in linhas)) for c in colunas}
    print("  ".join(c.ljust(larguras[c]) for c in colunas))
    for linha in linhas:
        print("  ".join(str(linha[c]).ljust(larguras[c]) for c in colunas))


def _classificador(cfg: AgenteConfig, store: ClassificacaoStore, fabrica: FabricaCliente) -> Classificador:
    gold = cfg.gold_db
    return Classificador(cfg, store, fabrica(cfg), redes=carregar_redes(cfg.perfis_dir / "marcas_rede.csv"),
                         buscar_socios=(lambda basicos: socios_pj(gold, basicos)) if gold else None)


def _cliente_gemini(cfg: AgenteConfig) -> ClienteGemini:
    return ClienteGemini(cfg.gemini_api_key, cfg.lotes_dir)


def main(argv: list[str] | None = None, fabrica_cliente: FabricaCliente = _cliente_gemini,
         cfg: AgenteConfig | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    args = _argumentos(argv)
    cfg = cfg or carregar_config()
    banco = lambda: ClassificacaoStore(cfg.banco)  # noqa: E731 — só os comandos que usam o banco o abrem
    try:
        if args.comando == "perfis":
            for perfil_id in listar_ids(cfg.perfis_dir):
                try:
                    p = carregar_perfil(cfg.perfis_dir, perfil_id)
                    print(f"{p.id} v{p.versao}: {p.produto}")
                except PerfilInvalidoError as exc:
                    print(f"{perfil_id}: INVÁLIDO — {exc}")
            return 0
        if args.comando == "validar":
            arquivo = validar_mes(cfg.novas_dir, args.mes, cfg.schema_dir)
            print(f"{arquivo.parquet}: {arquivo.total_linhas} empresas, contrato v{arquivo.contrato.versao} ok")
            for perfil_id in listar_ids(cfg.perfis_dir):
                perfil = carregar_perfil(cfg.perfis_dir, perfil_id)
                print(f"  {perfil.id} v{perfil.versao}: {contar(arquivo, perfil)} passam no filtro SQL "
                      f"(limite {perfil.limite_candidatos})")
            return 0
        if args.comando == "classificar":
            resumo = _classificador(cfg, banco(), fabrica_cliente).executar(
                args.mes, args.perfil, modo=args.modo, forcar=args.forcar, limite=args.limite,
                aguardar=args.aguardar, intervalo_s=args.intervalo, amostra=args.amostra)
            print("\n".join(resumo.linhas()))
            return 0
        if args.comando == "lotes":
            _tabela(banco().lotes(args.perfil))
            return 0
        if args.comando == "alvos":
            alvos = [{"cnpj": c["cnpj"], "score": c["score"], "confianca": c["confianca"],
                      "e_franquia": c["e_franquia"], "marca": c["marca"], "mes": c["mes_referencia"],
                      "justificativa": json.loads(c["resultado_json"])["final"]["justificativa"]}
                     for c in banco().classificacoes(args.perfil, mes=args.mes, somente_alvos=True)]
            if args.saida:
                with args.saida.open("w", encoding="utf-8-sig", newline="") as arquivo:
                    escritor = csv.DictWriter(arquivo, fieldnames=list(alvos[0]) if alvos else ["cnpj"], delimiter=";")
                    escritor.writeheader()
                    escritor.writerows(alvos)
                print(f"{len(alvos)} alvos gravados em {args.saida}")
            else:
                _tabela(alvos)
            return 0
        if args.comando == "amostrar-rotulos":
            arquivo = validar_mes(cfg.novas_dir, args.mes, cfg.schema_dir)
            n = amostra_para_rotular(arquivo, carregar_perfil(cfg.perfis_dir, args.perfil), args.n, args.saida,
                                     args.semente)
            print(f"{n} empresas em {args.saida}. Preencha e_alvo_humano (sim/nao) e, se souber, e_franquia_humano.")
            return 0
        if args.comando == "avaliar":
            relatorio = avaliar(banco(), args.perfil, args.rotulos, args.meta)
            print("\n".join(relatorio.linhas()))
            if args.json:
                args.json.write_text(relatorio.como_json(), encoding="utf-8")
            return 0 if relatorio.aprovado else 1
        if args.comando == "revisao-mensal":
            arquivo = validar_mes(cfg.novas_dir, args.mes, cfg.schema_dir)
            n = amostra_revisao(banco(), arquivo, args.perfil, args.n, args.saida)
            print(f"{n} classificações em {args.saida}. Preencha 'concorda' (sim/nao) e, se não, o comentário.")
            return 0
        if args.comando == "custos":
            _tabela(banco().custos(args.mes))
            return 0
        if args.comando == "servir":
            from agente.perguntas import servir

            servir(cfg, fabrica_cliente, args.endereco, args.porta)
            return 0
        if args.comando == "perguntar":
            from agente.perguntas import Assistente

            contexto = json.loads(args.contexto.read_text(encoding="utf-8")) if args.contexto else None
            print(Assistente(cfg, fabrica_cliente(cfg)).responder(args.pergunta, contexto))
            return 0
        if args.comando == "exportar-crm":
            saida = args.saida or cfg.saida_dir / ARQUIVO_CRM
            r = exportar_crm(banco(), cfg.novas_dir, cfg.schema_dir, saida, args.perfil, args.mes)
            print(f"{r.linhas} empresas ({r.alvos} alvos) de {', '.join(r.perfis)} · meses {', '.join(r.meses)} "
                  f"→ {saida}")
            for mes, n in r.puladas.items():
                print(f"  atenção: {n} classificações de {mes} ficaram fora (arquivo do mês ausente ou alterado)")
            print("O módulo 3 relê o CSV sozinho; o CRM copia os leads na próxima sincronização.")
            return 0
    except (ContratoInvalidoError, PerfilInvalidoError, FunilGrandeError, RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
