"""Configuração do agente: pastas e modelos, a partir de variáveis de ambiente.

O agente lê a saída do módulo 1 (somente leitura), guarda o próprio estado em `AGENTE_DADOS_DIR` e
grava o CSV que o módulo 3 lê em `AGENTE_SAIDA_DIR`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgenteConfig:
    novas_dir: Path                 # módulo 1: exportacoes/novas/AAAA-MM/
    dados_dir: Path                 # banco do agente e arquivos de lote
    saida_dir: Path                 # CSV para o módulo 3 (pasta montada como /modulo2 no compose)
    perfis_dir: Path
    schema_dir: Path
    gold_db: Path | None = None     # módulo 1: gold DuckDB, só para o sinal de sócio holding (se o arquivo existir)
    modelo_triagem: str = "gemini-2.5-flash-lite"
    modelo_revisao: str = "gemini-3.8-flash"
    gemini_api_key: str = field(default="", repr=False)

    @property
    def banco(self) -> Path:
        return self.dados_dir / "agente.sqlite"

    @property
    def lotes_dir(self) -> Path:
        """Arquivos JSONL enviados à API de lote (apagáveis depois de processados)."""
        return self.dados_dir / "lotes"


def carregar_config() -> AgenteConfig:
    caminho = lambda variavel, padrao: Path(os.getenv(variavel) or padrao).resolve()  # noqa: E731
    return AgenteConfig(
        novas_dir=caminho("AGENTE_ENTRADA_DIR", "../etl/data/exportacoes/novas"),
        dados_dir=caminho("AGENTE_DADOS_DIR", "dados"),
        saida_dir=caminho("AGENTE_SAIDA_DIR", "../modulo2_saida"),
        perfis_dir=caminho("AGENTE_PERFIS_DIR", "perfis"),
        schema_dir=caminho("AGENTE_SCHEMA_DIR", "schema"),
        gold_db=caminho("AGENTE_GOLD_DB", "../etl/data/gold/empresas.duckdb"),
        modelo_triagem=os.getenv("AGENTE_MODELO_TRIAGEM") or AgenteConfig.modelo_triagem,
        modelo_revisao=os.getenv("AGENTE_MODELO_REVISAO") or AgenteConfig.modelo_revisao,
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
    )
