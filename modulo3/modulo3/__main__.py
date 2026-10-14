"""Terminal do módulo 3.

  python -m modulo3 enriquecer [--limite N] [--forcar]   um ciclo de busca de contatos, agora
  python -m modulo3 contatos CNPJ                        contatos complementares guardados de uma empresa
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .dados import Fonte, caminho_csv
from .enriquecimento import ConfigEnriquecimento, Enriquecedor


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m modulo3")
    sub = p.add_subparsers(dest="comando", required=True)
    e = sub.add_parser("enriquecer", help="Busca agora os contatos das empresas-alvo do CSV do agente")
    e.add_argument("--limite", type=int, help="Máximo de empresas trabalhadas (padrão: MODULO3_LOTE)")
    e.add_argument("--forcar", action="store_true", help="Busca de novo mesmo o que está no cache")
    c = sub.add_parser("contatos", help="Mostra os contatos complementares de uma empresa")
    c.add_argument("cnpj")
    args = p.parse_args(argv)

    enriquecedor = Enriquecedor(ConfigEnriquecimento.do_ambiente())
    if args.comando == "contatos":
        cnpj = "".join(ch for ch in args.cnpj.upper() if ch.isalnum())
        contatos = enriquecedor.contatos([cnpj]).get(cnpj, [])
        print(json.dumps([x.model_dump() for x in contatos], ensure_ascii=False, indent=2))
        return 0
    csv = caminho_csv()
    if csv is None:
        print("Defina MODULO2_CSV: sem o CSV do agente não há o que enriquecer.", file=sys.stderr)
        return 2
    resumo = enriquecedor.executar(Fonte(csv).repo().leads, args.limite, args.forcar)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
