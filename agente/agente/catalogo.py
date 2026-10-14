"""Todos os perfis que o agente classifica: os do repositório (`perfis/*.yaml`) e os que as empresas
clientes descreveram no CRM (tabela `perfil_cliente` do banco do agente).

Um perfil de cliente desativado sai da rotina e do CSV, mas continua carregável: as classificações
antigas dele seguem no histórico.
"""

from __future__ import annotations

from pydantic import ValidationError

from agente.armazenamento import ClassificacaoStore
from agente.config import AgenteConfig
from agente.perfil import Perfil, PerfilInvalidoError, carregar_perfil, listar_ids


def carregar(cfg: AgenteConfig, store: ClassificacaoStore | None, perfil_id: str) -> Perfil:
    registro = store.perfil_cliente(perfil_id) if store is not None else None
    if registro is None:
        return carregar_perfil(cfg.perfis_dir, perfil_id)
    try:
        return Perfil.model_validate(registro["perfil"])
    except ValidationError as exc:
        raise PerfilInvalidoError(f"Perfil {perfil_id} do banco inválido:\n{exc}") from None


def ids_ativos(cfg: AgenteConfig, store: ClassificacaoStore | None) -> list[str]:
    clientes = [p["id"] for p in store.perfis_cliente()] if store is not None else []
    return [*listar_ids(cfg.perfis_dir), *clientes]


def rotulos(cfg: AgenteConfig, store: ClassificacaoStore | None) -> dict[str, str]:
    """perfil_id → nome para mostrar no CRM, dos perfis ativos que carregam sem erro."""
    nomes: dict[str, str] = {}
    for perfil_id in ids_ativos(cfg, store):
        try:
            nomes[perfil_id] = carregar(cfg, store, perfil_id).rotulo
        except PerfilInvalidoError:
            continue
    return nomes
