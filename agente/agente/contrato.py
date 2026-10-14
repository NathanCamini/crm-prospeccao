"""Contrato de dados entre o ETL (Módulo 1) e o classificador.

O agente só lê um Parquet depois de conferir, contra o contrato versionado:
- o manifesto (mês, total de linhas, SHA-256 e versão do schema);
- que conhece a versão do schema (versão desconhecida é recusada, em vez de classificar errado);
- que o arquivo é o mesmo descrito no manifesto (hash e total de linhas);
- que as colunas obrigatórias existem com o tipo certo (e as opcionais, se presentes, também).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

NOME_PARQUET = "empresas_novas.parquet"
NOME_MANIFESTO = "_manifest.json"
_FAMILIAS = {
    "VARCHAR": re.compile(r"^VARCHAR$"),
    "DATE": re.compile(r"^DATE$"),
    "BOOLEAN": re.compile(r"^BOOLEAN$"),
    "VARCHAR[]": re.compile(r"^VARCHAR\[\]$"),
    "NUMERIC": re.compile(r"^(DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|REAL|TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UBIGINT|UINTEGER)$"),
}


class ContratoInvalidoError(ValueError):
    """O arquivo do mês não atende ao contrato (ou o contrato não existe)."""


@dataclass(frozen=True)
class Coluna:
    nome: str
    tipo: str
    obrigatoria: bool


@dataclass(frozen=True)
class Contrato:
    nome: str
    versao: int
    colunas: tuple[Coluna, ...]


@dataclass(frozen=True)
class ArquivoDoMes:
    mes: str
    parquet: Path
    total_linhas: int
    contrato: Contrato
    colunas_presentes: frozenset[str]

    def tem(self, coluna: str) -> bool:
        return coluna in self.colunas_presentes


def carregar_contrato(pasta_schema: Path, versao: int) -> Contrato:
    caminho = pasta_schema / f"empresas_novas.v{versao}.json"
    if not caminho.exists():
        raise ContratoInvalidoError(
            f"Versão de schema desconhecida: v{versao} (não há {caminho.name} em {pasta_schema}). "
            "O agente não classifica um arquivo cujo contrato não conhece.")
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    if dados.get("versao") != versao:
        raise ContratoInvalidoError(f"{caminho.name} declara versao={dados.get('versao')}, esperado {versao}")
    colunas = tuple(Coluna(c["nome"], c["tipo"], bool(c["obrigatoria"])) for c in dados["colunas"])
    desconhecidos = {c.tipo for c in colunas} - set(_FAMILIAS)
    if desconhecidos:
        raise ContratoInvalidoError(f"Tipos não suportados no contrato: {sorted(desconhecidos)}")
    return Contrato(dados["contrato"], versao, colunas)


def localizar_mes(novas_dir: Path, mes: str) -> Path:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
        raise ContratoInvalidoError(f"Mês inválido: {mes!r} (use AAAA-MM)")
    return novas_dir / mes


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def validar_mes(novas_dir: Path, mes: str, pasta_schema: Path) -> ArquivoDoMes:
    """Confere manifesto, versão, hash, total de linhas e colunas; devolve o arquivo pronto para ler."""
    pasta = localizar_mes(novas_dir, mes)
    parquet, caminho_manifesto = pasta / NOME_PARQUET, pasta / NOME_MANIFESTO
    if not parquet.exists() or not caminho_manifesto.exists():
        raise ContratoInvalidoError(
            f"Arquivo do mês {mes} não encontrado em {pasta} (esperado {NOME_PARQUET} e {NOME_MANIFESTO}). "
            "Rode a exportação das novas do mês (Módulo 1) primeiro.")
    try:
        manifesto = json.loads(caminho_manifesto.read_text(encoding="utf-8"))
        versao, total, sha, mes_manifesto = (int(manifesto["versao_schema"]), int(manifesto["total_linhas"]),
                                             str(manifesto["sha256"]).lower(), str(manifesto["mes"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContratoInvalidoError(f"Manifesto inválido em {caminho_manifesto}: {exc}") from None
    if mes_manifesto != mes:
        raise ContratoInvalidoError(f"O manifesto é do mês {mes_manifesto}, não de {mes}")
    contrato = carregar_contrato(pasta_schema, versao)
    if _sha256(parquet) != sha:
        raise ContratoInvalidoError("O Parquet não corresponde ao SHA-256 do manifesto (arquivo trocado ou corrompido)")

    con = duckdb.connect()
    try:
        tipos = {nome: tipo for nome, tipo, *_ in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [parquet.as_posix()]).fetchall()}
        linhas = con.execute("SELECT count(*) FROM read_parquet(?)", [parquet.as_posix()]).fetchone()[0]
    finally:
        con.close()
    if linhas != total:
        raise ContratoInvalidoError(f"O Parquet tem {linhas} linhas; o manifesto diz {total}")
    problemas = []
    for coluna in contrato.colunas:
        if coluna.nome not in tipos:
            if coluna.obrigatoria:
                problemas.append(f"falta a coluna obrigatória {coluna.nome}")
        elif not _FAMILIAS[coluna.tipo].match(tipos[coluna.nome]):
            problemas.append(f"{coluna.nome}: tipo {tipos[coluna.nome]}, o contrato pede {coluna.tipo}")
    if problemas:
        raise ContratoInvalidoError(f"O arquivo de {mes} não atende ao contrato v{versao}: " + "; ".join(problemas))
    conhecidas = {c.nome for c in contrato.colunas}
    return ArquivoDoMes(mes, parquet, linhas, contrato, frozenset(tipos) & frozenset(conhecidas))
