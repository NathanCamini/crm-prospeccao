"""Arquivo mensal de empresas novas, entrada do agente classificador (módulo 2).

Grava `data/exportacoes/novas/AAAA-MM/empresas_novas.parquet` e `_manifest.json` no
formato de `agente/schema/empresas_novas.v1.json`:

- **Empresa nova** é a aberta depois do corte da carga anterior. Cada mês a Receita publica a
  base inteira de novo; nada do mês passado é guardado, só a data do corte (`_ultimo_corte.json`).
  O corte é a data de abertura mais recente da base (≈ o dia em que a Receita extraiu os dados).
  Há uma folga de `novas_folga_dias` para trás, para cadastros que entram atrasados; quem aparece
  nos dois meses não custa nada a mais no agente, que não reclassifica o que não mudou. Sem corte
  anterior (primeira carga), valem as aberturas dos últimos `novas_primeira_janela_dias`. O
  critério usado vai no manifesto.
- Só o arquivo do mês atual fica em `exportacoes/novas/`: os dos meses anteriores são apagados.
- As três marcações opcionais do contrato saem junto: `contato_compartilhado`,
  `filial_de_empresa_existente` e `pessoa_fisica_identificavel`.
- O Parquet é gravado num temporário e renomeado; o manifesto vem por último. O agente
  confere o SHA-256, então nunca lê um arquivo pela metade ou de outra versão.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from prospeccao.config import Config
from prospeccao.transform.gold import configurar_duckdb

log = logging.getLogger("prospeccao.novas")

VERSAO_SCHEMA = 1
NOME_PARQUET = "empresas_novas.parquet"
NOME_MANIFESTO = "_manifest.json"
NOME_ESTADO = "_ultimo_corte.json"
# Acima de tantos CNPJs com o mesmo e-mail ou telefone, o contato é de terceiro (em geral a contabilidade).
LIMITE_CONTATO_COMPARTILHADO = 5
# Naturezas jurídicas cuja razão social traz o nome do titular: empresário individual e pessoas físicas (4xxx).
_PESSOA_FISICA = "(e.natureza_juridica_codigo = '2135' OR e.natureza_juridica_codigo LIKE '4%' OR e.opcao_mei IS TRUE)"

_COLUNAS = """
    e.cnpj, e.cnpj_basico, e.razao_social, e.nome_fantasia, e.matriz_filial, e.situacao_cadastral,
    e.data_inicio_atividade, e.cnae_principal, e.cnae_principal_descricao, e.cnaes_secundarios,
    e.natureza_juridica, e.porte, e.capital_social, e.opcao_simples, e.opcao_mei,
    e.tipo_logradouro, e.logradouro, e.numero, e.complemento, e.bairro, e.cep, e.uf, e.municipio,
    e.ddd_receita_1, e.telefone_receita_1, e.ddd_receita_2, e.telefone_receita_2, e.email_receita,
    e.mes_referencia_base, e.natureza_juridica_codigo
"""


class ExportacaoNovasError(ValueError):
    """Não há base para exportar ou ela é de outro mês."""


def pasta_novas(cfg: Config, mes: str) -> Path:
    return cfg.data_dir / "exportacoes" / "novas" / mes


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def _ler_corte_anterior(cfg: Config) -> date | None:
    try:
        return date.fromisoformat(json.loads((cfg.data_dir / "exportacoes" / "novas" / NOME_ESTADO)
                                             .read_text(encoding="utf-8"))["corte"])
    except (OSError, ValueError, KeyError):
        return None


def _gravar_corte(cfg: Config, mes: str, corte: date) -> None:
    caminho = cfg.data_dir / "exportacoes" / "novas" / NOME_ESTADO
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mes": mes, "corte": corte.isoformat()}), encoding="utf-8")
    os.replace(tmp, caminho)


def apagar_meses_anteriores(cfg: Config, mes: str) -> list[str]:
    """Deixa em exportacoes/novas/ só o arquivo do mês atual."""
    raiz = cfg.data_dir / "exportacoes" / "novas"
    removidos = []
    for pasta in sorted(raiz.glob("????-??")) if raiz.exists() else []:
        if pasta.is_dir() and pasta.name != mes:
            shutil.rmtree(pasta)
            removidos.append(pasta.name)
    return removidos


def _contato(ddd: str, tel: str) -> str:
    """Telefone normalizado só para comparar (o arquivo leva o contato original, sem alteração)."""
    return f"NULLIF(regexp_replace(coalesce({ddd}, '') || coalesce({tel}, ''), '[^0-9]', '', 'g'), '')"


def exportar_novas(cfg: Config, mes: str | None = None, desde: date | None = None) -> dict:
    """Gera o arquivo do mês a partir da gold publicada. Devolve um resumo (inclui o manifesto)."""
    if not cfg.gold_db.exists():
        raise ExportacaoNovasError(f"Base não encontrada em {cfg.gold_db}. Rode a carga primeiro.")
    con = duckdb.connect()
    try:
        configurar_duckdb(con, cfg.duckdb_memory_limit, cfg.duckdb_threads, cfg.tmp_dir, cfg.duckdb_max_temp)
        con.execute(f"ATTACH '{cfg.gold_db.as_posix()}' AS g (READ_ONLY)")
        carga = con.execute(
            "SELECT mes_referencia, data_referencia_nova FROM g.carga ORDER BY data_ingestao DESC LIMIT 1").fetchone()
        if carga is None:
            raise ExportacaoNovasError("A base não tem registro de carga.")
        mes_base, data_ref = carga
        if mes and mes != mes_base:
            raise ExportacaoNovasError(
                f"A base carregada é de {mes_base}, não de {mes}. Carregue {mes} antes de exportar as novas dele.")
        mes = mes_base

        # Corte desta base: a abertura mais recente (data futura é erro da fonte e fica fora, como na is_nova).
        corte = con.execute("SELECT max(data_inicio_atividade) FROM g.empresa WHERE data_inicio_atividade <= $ref",
                            {"ref": data_ref or date.today()}).fetchone()[0]
        if corte is None:
            raise ExportacaoNovasError("A base não tem datas de abertura.")
        if desde is None:
            anterior = _ler_corte_anterior(cfg)
            if anterior is not None and anterior < corte:
                desde = anterior - timedelta(days=cfg.novas_folga_dias)
                criterio = (f"abertura de {desde.isoformat()} a {corte.isoformat()} (corte anterior {anterior.isoformat()}, "
                            f"folga de {cfg.novas_folga_dias} dias)")
            else:
                desde = corte - timedelta(days=cfg.novas_primeira_janela_dias)
                criterio = (f"abertura de {desde.isoformat()} a {corte.isoformat()} "
                            f"(sem corte anterior: últimos {cfg.novas_primeira_janela_dias} dias)")
        else:
            criterio = f"abertura de {desde.isoformat()} a {corte.isoformat()} (informado)"
        # "Já existia": outro estabelecimento do mesmo CNPJ básico aberto antes da janela.
        con.execute(f"""CREATE TEMP TABLE novas AS
            SELECT {_COLUNAS},
                   CASE WHEN e.matriz_filial = 'FILIAL' THEN EXISTS (
                       SELECT 1 FROM g.empresa o
                       WHERE o.cnpj_basico = e.cnpj_basico AND o.cnpj <> e.cnpj
                         AND o.data_inicio_atividade < $desde) END AS filial_de_empresa_existente
            FROM g.empresa e
            WHERE e.data_inicio_atividade >= $desde AND e.data_inicio_atividade <= $corte""",
                    {"desde": desde, "corte": corte})

        _marcar_contato_compartilhado(con)
        destino = pasta_novas(cfg, mes)
        manifesto = _gravar(con, destino, mes, criterio)
        manifesto["corte"] = corte.isoformat()
        _gravar_corte(cfg, mes, corte)
        apagados = apagar_meses_anteriores(cfg, mes)
    finally:
        con.close()
    if apagados:
        log.info("Arquivos de empresas novas de meses anteriores apagados: %s", ", ".join(apagados))
    log.info("Empresas novas de %s: %d (%s) em %s", mes, manifesto["total_linhas"], criterio, destino)
    return {"mes": mes, "pasta": str(destino), **manifesto}


def _marcar_contato_compartilhado(con: duckdb.DuckDBPyConnection) -> None:
    """Conta, na base inteira, quantos CNPJs usam cada e-mail e telefone que aparece nas novas."""
    tel1 = _contato("ddd_receita_1", "telefone_receita_1")
    tel2 = _contato("ddd_receita_2", "telefone_receita_2")
    con.execute(f"""CREATE TEMP TABLE contatos_novas AS
        SELECT DISTINCT c FROM (
            SELECT lower(trim(email_receita)) AS c FROM novas
            UNION ALL SELECT {tel1} FROM novas
            UNION ALL SELECT {tel2} FROM novas) WHERE c IS NOT NULL AND c <> ''""")
    con.execute(f"""CREATE TEMP TABLE uso_contato AS
        SELECT c, count(DISTINCT cnpj) AS cnpjs FROM (
            SELECT cnpj, lower(trim(email_receita)) AS c FROM g.empresa
            UNION ALL SELECT cnpj, {tel1} FROM g.empresa
            UNION ALL SELECT cnpj, {tel2} FROM g.empresa) u
        SEMI JOIN contatos_novas USING (c)
        GROUP BY c""")
    con.execute(f"""CREATE TEMP TABLE compartilhados AS
        SELECT c FROM uso_contato WHERE cnpjs > {LIMITE_CONTATO_COMPARTILHADO}""")


def _gravar(con: duckdb.DuckDBPyConnection, destino: Path, mes: str, criterio: str) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    parquet, manifesto_path = destino / NOME_PARQUET, destino / NOME_MANIFESTO
    tmp = destino / (NOME_PARQUET + ".tmp")
    tel1 = _contato("e.ddd_receita_1", "e.telefone_receita_1")
    tel2 = _contato("e.ddd_receita_2", "e.telefone_receita_2")
    con.execute(f"""COPY (
        SELECT e.* EXCLUDE (filial_de_empresa_existente, natureza_juridica_codigo),
               (lower(trim(e.email_receita)) IN (SELECT c FROM compartilhados)
                OR {tel1} IN (SELECT c FROM compartilhados)
                OR {tel2} IN (SELECT c FROM compartilhados)) IS TRUE AS contato_compartilhado,
               e.filial_de_empresa_existente,
               {_PESSOA_FISICA} IS TRUE AS pessoa_fisica_identificavel
        FROM novas e
        ORDER BY e.uf, e.municipio, e.cnpj
    ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)""")
    total = con.execute(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()[0]
    manifesto = {
        "mes": mes,
        "total_linhas": total,
        "sha256": _sha256(tmp),
        "versao_schema": VERSAO_SCHEMA,
        "criterio": criterio,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }
    os.replace(tmp, parquet)
    tmp_manifesto = destino / (NOME_MANIFESTO + ".tmp")
    tmp_manifesto.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_manifesto, manifesto_path)
    return manifesto
