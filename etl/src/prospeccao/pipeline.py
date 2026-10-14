"""Orquestra a carga mensal: download -> bronze -> silver -> gold. Também agendamento e limpeza."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import duckdb

from prospeccao.config import Config
from prospeccao.ingest.bronze import apagar_csvs_extraidos, csvs_por_tabela, preparar_bronze
from prospeccao.ingest.download import ClienteReceita, baixar_mes
from prospeccao.monitor import medir
from prospeccao.transform.gold import configurar_duckdb, gerar_gold, publicar_gold, registrar_metricas
from prospeccao.transform.silver import gerar_silver

log = logging.getLogger("prospeccao.pipeline")
_MES = re.compile(r"\d{4}-\d{2}")


class EspacoInsuficienteError(RuntimeError):
    """Pouco espaço livre em disco para a carga."""


class CargaEmAndamentoError(RuntimeError):
    """Já existe outra carga rodando."""


def verificar_espaco_em_disco(cfg: Config) -> None:
    """Interrompe antes de começar se o disco da pasta de dados tiver menos que ESPACO_MINIMO_GB livres."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.trabalho_dir.mkdir(parents=True, exist_ok=True)
    separado = os.stat(cfg.data_dir).st_dev != os.stat(cfg.trabalho_dir).st_dev
    exigido_dados = cfg.espaco_minimo_gb * (0.4 if separado else 1)  # sem os ~25 GB de CSV temporário
    exigido_trabalho = cfg.espaco_minimo_gb * 0.6 if separado else 0
    for pasta, exigido, conteudo in (
        (cfg.data_dir, exigido_dados, "zips, silver e gold" + ("" if separado else ", CSV temporário")),
        (cfg.trabalho_dir, exigido_trabalho, "CSV temporário e disco auxiliar do DuckDB"),
    ):
        livre_gb = shutil.disk_usage(pasta).free / 1024**3
        if exigido and livre_gb < exigido:
            raise EspacoInsuficienteError(
                f"Espaço livre em {pasta}: {livre_gb:.1f} GB. A carga precisa de pelo menos {exigido:.0f} GB "
                f"ali ({conteudo}). Libere espaço ou ajuste ESPACO_MINIMO_GB."
            )


def _travar_arquivo(arq) -> bool:
    """Trava exclusiva do sistema operacional, sem esperar. False se outro processo já tem a trava."""
    try:
        if os.name == "nt":
            import msvcrt

            arq.seek(0)
            msvcrt.locking(arq.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(arq.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _destravar_arquivo(arq) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            arq.seek(0)
            msvcrt.locking(arq.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(arq.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def trava_carga(cfg: Config) -> Iterator[None]:
    """Impede duas cargas ao mesmo tempo (ex.: agendador + comando manual).

    Usa a trava do sistema operacional sobre `data/.carga.lock`, e não o número do processo:
    o sistema solta a trava sozinho quando o processo termina (mesmo se morrer no meio), e
    funciona entre contêineres Docker, onde vários processos têm o mesmo número (PID 1).
    O PID gravado no arquivo serve só para a mensagem de erro.
    """
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    arquivo = cfg.data_dir / ".carga.lock"
    arq = open(arquivo, "a+", encoding="utf-8")  # noqa: SIM115 — fechado no finally
    try:
        if not _travar_arquivo(arq):
            arq.seek(0)
            dono = arq.read().strip() or "desconhecido"
            raise CargaEmAndamentoError(
                f"Já existe uma carga em andamento (processo {dono}). Aguarde ela terminar."
            )
        arq.seek(0)
        arq.truncate()
        arq.write(str(os.getpid()))
        arq.flush()
        try:
            yield
        finally:
            arq.seek(0)
            arq.truncate()
            arq.flush()
            _destravar_arquivo(arq)
    finally:
        arq.close()


def ultimo_mes_baixado(cfg: Config) -> str:
    """Mês mais recente com zips já baixados em data/bronze (usado por --sem-download sem --mes)."""
    meses = sorted(
        p.name for p in cfg.bronze_dir.glob("*")
        if p.is_dir() and _MES.fullmatch(p.name) and any((p / "zip").glob("*.zip"))
    ) if cfg.bronze_dir.exists() else []
    if not meses:
        raise ValueError("Nenhum mês baixado em data/bronze. Rode a carga sem --sem-download.")
    return meses[-1]


def mes_carregado(cfg: Config) -> str | None:
    """Mês de referência da base publicada (None se ainda não houve carga)."""
    if not cfg.gold_db.exists():
        return None
    with duckdb.connect(str(cfg.gold_db), read_only=True) as con:
        linha = con.execute("SELECT mes_referencia FROM carga ORDER BY data_ingestao DESC, rowid DESC LIMIT 1").fetchone()
    return linha[0] if linha else None


def limpar_meses_antigos(cfg: Config, manter: int) -> list[str]:
    """Apaga bronze (zips) e silver de meses além dos `manter` mais recentes. A gold não é afetada."""
    removidos: list[str] = []
    for camada in (cfg.bronze_dir, cfg.silver_dir):
        if not camada.exists():
            continue
        meses = sorted(p for p in camada.iterdir() if p.is_dir() and _MES.fullmatch(p.name))
        for pasta in meses[: max(0, len(meses) - manter)]:
            shutil.rmtree(pasta)
            removidos.append(f"{camada.name}/{pasta.name}")
    if removidos:
        log.info("Meses antigos removidos: %s", ", ".join(removidos))
    return removidos


def executar_carga(
    cfg: Config,
    mes: str | None = None,
    baixar: bool = True,
    data_referencia: date | None = None,
) -> dict:
    """Executa a carga completa de um mês de referência. Devolve um resumo."""
    with trava_carga(cfg):
        return _executar_carga(cfg, mes, baixar, data_referencia)


def _executar_carga(cfg: Config, mes: str | None, baixar: bool, data_referencia: date | None) -> dict:
    metricas: list[dict] = []
    contagens: list[dict] = []
    verificar_espaco_em_disco(cfg)

    if baixar:
        with medir("download") as m:
            mes, _ = baixar_mes(cfg, mes)
        metricas.append(m)
    if mes is None:
        mes = ultimo_mes_baixado(cfg)

    data_referencia = data_referencia or date.today()
    silver_dir = cfg.silver_dir / mes

    with duckdb.connect() as con:  # banco em memória, com spill em disco
        configurar_duckdb(con, cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir, cfg.duckdb_max_temp)
        with medir("bronze") as m:
            manifest = preparar_bronze(cfg, mes)
        metricas.append(m)
        with medir("silver") as m:
            contagens += gerar_silver(con, csvs_por_tabela(manifest), silver_dir)
        metricas.append(m)

    with medir("gold") as m:
        contagens_gold, tmp_db = gerar_gold(
            silver_dir, cfg.gold_db, mes, data_referencia, cfg.janela_nova_dias,
            cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir, cfg.duckdb_max_temp,
        )
    metricas.append(m)
    contagens += contagens_gold

    registrar_metricas(tmp_db, mes, contagens, metricas)
    publicar_gold(tmp_db, cfg.gold_db)
    if cfg.apagar_csv_extraido:
        apagar_csvs_extraidos(cfg, mes)
    if cfg.meses_mantidos > 0:
        limpar_meses_antigos(cfg, cfg.meses_mantidos)

    log.info("Carga %s concluída", mes)
    return {"mes_referencia": mes, "gold_db": str(cfg.gold_db), "contagens": contagens, "metricas": metricas}


def carregar_se_houver_novo(cfg: Config, cliente: ClienteReceita | None = None) -> dict | None:
    """Carrega o mês mais recente publicado pela Receita, se ainda não estiver carregado."""
    publicado = (cliente or ClienteReceita(cfg)).listar_meses()[-1]
    atual = mes_carregado(cfg)
    if atual is not None and atual >= publicado:
        log.info("Base já está no mês mais recente publicado (%s). Nada a fazer.", atual)
        return None
    log.info("Novo mês publicado: %s (carregado: %s). Iniciando carga.", publicado, atual or "nenhum")
    return executar_carga(cfg, publicado)


def rodar_agendador(cfg: Config, intervalo_horas: float, uma_vez: bool = False) -> None:
    """Verifica periodicamente se a Receita publicou um mês novo e carrega. Erros não derrubam o agendador."""
    log.info("Agendador iniciado: verificação a cada %.1f h", intervalo_horas)
    while True:
        try:
            carregar_se_houver_novo(cfg)
        except CargaEmAndamentoError as exc:
            log.warning("%s Tentando de novo no próximo ciclo.", exc)
        except Exception:  # noqa: BLE001 — registra e segue; o próximo ciclo tenta de novo
            log.exception("Falha na verificação/carga agendada")
        if uma_vez:
            return
        time.sleep(intervalo_horas * 3600)
