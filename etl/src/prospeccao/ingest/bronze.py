"""Camada bronze: zips originais intocados, extração, validação de layout e manifest."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from prospeccao.config import Config
from prospeccao.ingest.conversao import converter_para_utf8
from prospeccao.ingest.download import sha256_arquivo
from prospeccao.ingest.layout import (
    ENCODING_EXTRAIDO,
    TABELAS,
    LayoutInvalidoError,
    Tabela,
    tabela_do_zip,
    validar_arquivo_csv,
    verificar_conjunto_completo,
)

log = logging.getLogger("prospeccao.bronze")


@dataclass
class ArquivoBronze:
    zip: str
    tabela: str
    tamanho_bytes: int
    sha256: str
    arquivo_interno: str
    csv: str


def _extrair(zip_path: Path, destino_dir: Path) -> tuple[str, Path]:
    """Extrai o único CSV do zip com nome controlado (evita zip-slip), já convertido para UTF-8."""
    with zipfile.ZipFile(zip_path) as zf:
        membros = [m for m in zf.infolist() if not m.is_dir()]
        if len(membros) != 1:
            raise LayoutInvalidoError(
                f"{zip_path.name}: esperado 1 arquivo dentro do zip, encontrados {len(membros)}."
            )
        membro = membros[0]
        destino = destino_dir / f"{zip_path.stem}.csv"
        with zf.open(membro) as origem, destino.open("wb") as saida:
            converter_para_utf8(origem, saida)
    return membro.filename, destino


def sql_ler_csv(arquivos: list[Path], tabela: Tabela) -> str:
    """Trecho SQL do DuckDB que lê os CSVs da Receita em streaming, tudo como texto."""
    lista = ", ".join("'" + str(a).replace("'", "''") + "'" for a in arquivos)
    colunas = ", ".join(f"'{c}': 'VARCHAR'" for c in tabela.colunas)
    return (
        f"read_csv([{lista}], delim=';', quote='\"', escape='\"', header=false, "
        f"encoding='{ENCODING_EXTRAIDO}', columns={{{colunas}}}, auto_detect=false, "
        "strict_mode=true, null_padding=false, parallel=true)"
    )


def processar_zip(zip_path: str, pasta_csv: str) -> dict:
    """Trabalho de um zip: extrai em UTF-8, valida e calcula o SHA-256."""
    zp = Path(zip_path)
    tabela = tabela_do_zip(zp.name)
    assert tabela is not None
    interno, csv_path = _extrair(zp, Path(pasta_csv))
    validar_arquivo_csv(csv_path, tabela)
    return asdict(ArquivoBronze(zp.name, tabela.nome, zp.stat().st_size, sha256_arquivo(zp), interno, str(csv_path)))


LIMITE_SUBPROCESSO_BYTES = 20 * 1024 * 1024


def _processar_em_subprocesso(zp: Path, pasta_csv: Path) -> dict:
    """Roda `processar_zip` num processo Python separado (usa outro núcleo; o GIL não limita).

    Arquivos pequenos (tabelas de domínio) ficam no próprio processo: abrir um processo custaria mais.
    """
    if zp.stat().st_size < LIMITE_SUBPROCESSO_BYTES:
        return processar_zip(str(zp), str(pasta_csv))
    proc = subprocess.run(
        [sys.executable, "-m", "prospeccao.ingest.bronze_trabalho", str(zp), str(pasta_csv)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    linhas = proc.stdout.strip().splitlines()
    try:
        dados = json.loads(linhas[-1]) if linhas else {}
    except json.JSONDecodeError:
        dados = {}
    if proc.returncode == 0 and "erro" not in dados and dados:
        return dados
    mensagem = dados.get("erro") or (proc.stderr.strip().splitlines() or ["falha sem mensagem"])[-1]
    if dados.get("tipo") == "layout":
        raise LayoutInvalidoError(mensagem)
    raise RuntimeError(f"Falha ao processar {zp.name}: {mensagem}")


LIMITE_SEQUENCIAL_BYTES = 200 * 1024 * 1024


def numero_de_processos(cfg: Config, tarefas: int, total_bytes: int) -> int:
    """BRONZE_PROCESSOS do .env; no automático, até 8 núcleos, e 1 só para volumes pequenos (ex.: amostra)."""
    if cfg.bronze_processos:
        return max(1, min(cfg.bronze_processos, tarefas))
    if total_bytes < LIMITE_SEQUENCIAL_BYTES:
        return 1
    return max(1, min(os.cpu_count() or 1, 8, tarefas))


def preparar_bronze(cfg: Config, mes: str) -> dict:
    """Extrai e valida todos os zips do mês, em paralelo, e grava manifest.json.

    Cada zip é tratado num processo separado (os maiores primeiro, para equilibrar a carga).
    Os CSVs extraídos vão para a pasta de trabalho (`PROSPECCAO_TRABALHO_DIR`), não para `data`.
    A contagem de registros lidos é feita na mesma passada que gera a silver.
    """
    pasta_mes = cfg.bronze_dir / mes
    pasta_zip = pasta_mes / "zip"
    pasta_csv = cfg.csv_dir(mes)
    pasta_csv.mkdir(parents=True, exist_ok=True)

    zips = sorted(pasta_zip.glob("*.zip"))
    faltando = verificar_conjunto_completo([z.name for z in zips])
    if faltando:
        raise LayoutInvalidoError(f"Bronze {mes} incompleta. Tabelas sem arquivo: {', '.join(faltando)}.")
    validos = []
    for zp in zips:
        if tabela_do_zip(zp.name) is None:
            log.warning("Arquivo ignorado (não pertence ao layout): %s", zp.name)
        else:
            validos.append(zp)
    validos.sort(key=lambda z: z.stat().st_size, reverse=True)

    processos = numero_de_processos(cfg, len(validos), sum(z.stat().st_size for z in validos))
    log.info("bronze: %d arquivos em %d processo(s) paralelos", len(validos), processos)
    resultados: dict[str, dict] = {}
    if processos == 1:
        for zp in validos:
            resultados[zp.name] = processar_zip(str(zp), str(pasta_csv))
    else:
        with ThreadPoolExecutor(max_workers=processos) as executor:
            futuros = {executor.submit(_processar_em_subprocesso, zp, pasta_csv): zp.name for zp in validos}
            try:
                for i, futuro in enumerate(as_completed(futuros), start=1):
                    resultados[futuros[futuro]] = futuro.result()
                    log.info("bronze: %d/%d (%s)", i, len(validos), futuros[futuro])
            except BaseException:
                for f in futuros:
                    f.cancel()
                raise

    manifest = {
        "mes_referencia": mes,
        "preparado_em": datetime.now().isoformat(timespec="seconds"),
        "arquivos": [resultados[n] for n in sorted(resultados)],
    }
    (pasta_mes / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest


def csvs_por_tabela(manifest: dict) -> dict[str, list[Path]]:
    resultado: dict[str, list[Path]] = {nome: [] for nome in TABELAS}
    for a in manifest["arquivos"]:
        resultado[a["tabela"]].append(Path(a["csv"]))
    return resultado


def apagar_csvs_extraidos(cfg: Config, mes: str) -> None:
    pasta = cfg.csv_dir(mes)
    if pasta.exists():
        shutil.rmtree(pasta)
        log.info("CSVs extraídos de %s apagados (zips originais mantidos)", mes)
