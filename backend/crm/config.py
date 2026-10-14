from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(nome: str, padrao: bool) -> bool:
    valor = os.environ.get(nome)
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes"}


@dataclass(frozen=True)
class Config:
    dados_dir: Path = field(default_factory=lambda: Path(os.environ.get("CRM_DADOS_DIR", "./dados")))
    modulo3_url: str = field(default_factory=lambda: os.environ.get("MODULO3_URL", "http://localhost:8100"))
    modulo3_timeout: float = field(default_factory=lambda: float(os.environ.get("MODULO3_TIMEOUT_SEGUNDOS", "15")))
    agente_timeout: float = field(default_factory=lambda: float(os.environ.get("AGENTE_TIMEOUT_SEGUNDOS", "150")))
    sessao_dias: int = field(default_factory=lambda: int(os.environ.get("CRM_SESSAO_DIAS", "30")))
    cookie_secure: bool = field(default_factory=lambda: _bool("CRM_COOKIE_SECURE", False))
    atraso_login_base: float = field(default_factory=lambda: float(os.environ.get("CRM_ATRASO_LOGIN_SEGUNDOS", "1")))
    # De quantos em quantos minutos copiar os leads do módulo 3. 0 desliga (só manual e sob demanda).
    sincronizar_minutos: float = field(default_factory=lambda: float(os.environ.get("CRM_SINCRONIZAR_MINUTOS", "30")))
    # Se o CRM está atrás de um proxy (nginx), o IP real vem no X-Forwarded-For.
    confiar_proxy: bool = field(default_factory=lambda: _bool("CRM_CONFIAR_PROXY", True))
    # Fuso usado para "hoje", agenda e relatórios por dia.
    fuso: str = field(default_factory=lambda: os.environ.get("CRM_FUSO", "America/Sao_Paulo"))

    @property
    def db_path(self) -> Path:
        return self.dados_dir / "crm.sqlite"
