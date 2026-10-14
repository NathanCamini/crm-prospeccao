"""Processo auxiliar da bronze: trata um único zip e devolve o resultado em JSON na saída padrão.

Executado como `python -m prospeccao.ingest.bronze_trabalho ZIP PASTA_CSV` por `preparar_bronze`.
Rodar como processo independente (e não via multiprocessing) evita depender de como o processo
principal foi iniciado (linha de comando, interface, testes).
"""

from __future__ import annotations

import json
import sys

from prospeccao.ingest.layout import LayoutInvalidoError


def main(argv: list[str]) -> int:
    from prospeccao.ingest.bronze import processar_zip

    try:
        resultado = processar_zip(argv[0], argv[1])
    except LayoutInvalidoError as exc:
        print(json.dumps({"erro": str(exc), "tipo": "layout"}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 — o processo principal transforma em erro claro
        print(json.dumps({"erro": f"{type(exc).__name__}: {exc}", "tipo": "outro"}, ensure_ascii=False))
        return 3
    print(json.dumps(resultado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
