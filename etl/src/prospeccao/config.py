"""Configuração lida de variáveis de ambiente (ou arquivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _bool(valor: str | None, padrao: bool) -> bool:
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes"}


@dataclass(frozen=True)
class Config:
    data_dir: Path
    receita_base_url: str = "https://arquivos.receitafederal.gov.br"
    receita_share_token: str = "gn672Ad4CF8N6TK"
    receita_share_path: str = "/Dados/Cadastros/CNPJ"
    duckdb_memory_limit: str = "4GB"
    duckdb_threads: int = 4
    janela_nova_dias: int = 90
    apagar_csv_extraido: bool = True
    duckdb_max_temp: str = "20GB"
    espaco_minimo_gb: float = 0.0
    meses_mantidos: int = 0  # 0 = não apaga meses antigos de bronze/silver
    trabalho_dir_env: Path | None = None  # pasta de arquivos temporários (padrão: data/tmp)
    bronze_processos: int = 0  # 0 = automático (núcleos disponíveis, até 8)
    sessao_horas: float = 12.0  # 0 = pede senha a cada recarga da página
    # Busca complementar de contatos
    cache_busca_dias: int = 90
    site_intervalo_segundos: float = 2.0
    busca_api_provedor: str = "nenhum"  # nenhum | google | brave
    api_limite_diario: int = 100
    api_intervalo_segundos: float = 1.0
    google_cse_chave: str = field(default="", repr=False)
    google_cse_cx: str = field(default="", repr=False)
    brave_chave: str = field(default="", repr=False)

    @property
    def bronze_dir(self) -> Path:
        return self.data_dir / "bronze"

    @property
    def silver_dir(self) -> Path:
        return self.data_dir / "silver"

    @property
    def gold_dir(self) -> Path:
        return self.data_dir / "gold"

    @property
    def trabalho_dir(self) -> Path:
        """Arquivos temporários grandes (CSV extraído, disco auxiliar do DuckDB).

        No Docker fica num volume interno, bem mais rápido que a pasta compartilhada com o computador.
        """
        return self.trabalho_dir_env or self.data_dir / "tmp"

    @property
    def tmp_dir(self) -> Path:
        return self.trabalho_dir / "duckdb"

    def csv_dir(self, mes: str) -> Path:
        return self.trabalho_dir / "csv" / mes

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app" / "app.sqlite"

    @property
    def gold_db(self) -> Path:
        return self.gold_dir / "empresas.duckdb"


def carregar_config(data_dir: Path | None = None) -> Config:
    """Lê o .env (se existir) e monta a configuração."""
    load_dotenv()
    base = data_dir or Path(os.getenv("PROSPECCAO_DATA_DIR", "./data"))
    return Config(
        data_dir=base.resolve(),
        receita_base_url=os.getenv("RECEITA_BASE_URL", Config.receita_base_url).rstrip("/"),
        receita_share_token=os.getenv("RECEITA_SHARE_TOKEN", Config.receita_share_token),
        receita_share_path=os.getenv("RECEITA_SHARE_PATH", Config.receita_share_path).rstrip("/"),
        duckdb_memory_limit=os.getenv("DUCKDB_MEMORY_LIMIT", Config.duckdb_memory_limit),
        duckdb_threads=int(os.getenv("DUCKDB_THREADS", str(Config.duckdb_threads))),
        janela_nova_dias=int(os.getenv("JANELA_NOVA_DIAS", str(Config.janela_nova_dias))),
        apagar_csv_extraido=_bool(os.getenv("APAGAR_CSV_EXTRAIDO"), True),
        duckdb_max_temp=os.getenv("DUCKDB_MAX_TEMP", Config.duckdb_max_temp),
        espaco_minimo_gb=float(os.getenv("ESPACO_MINIMO_GB", "50")),
        meses_mantidos=int(os.getenv("MESES_MANTIDOS", "2")),
        trabalho_dir_env=Path(os.environ["PROSPECCAO_TRABALHO_DIR"]).resolve() if os.getenv("PROSPECCAO_TRABALHO_DIR") else None,
        bronze_processos=int(os.getenv("BRONZE_PROCESSOS", "0")),
        sessao_horas=float(os.getenv("SESSAO_HORAS", "12")),
        cache_busca_dias=int(os.getenv("CACHE_BUSCA_DIAS", "90")),
        site_intervalo_segundos=float(os.getenv("SITE_INTERVALO_SEGUNDOS", "2")),
        busca_api_provedor=os.getenv("BUSCA_API_PROVEDOR", "nenhum"),
        api_limite_diario=int(os.getenv("API_LIMITE_DIARIO", "100")),
        api_intervalo_segundos=float(os.getenv("API_INTERVALO_SEGUNDOS", "1")),
        google_cse_chave=os.getenv("GOOGLE_CSE_CHAVE", ""),
        google_cse_cx=os.getenv("GOOGLE_CSE_CX", ""),
        brave_chave=os.getenv("BRAVE_SEARCH_CHAVE", ""),
    )
