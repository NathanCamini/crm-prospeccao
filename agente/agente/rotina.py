"""Rotina do mês, sem intervenção: arquivo novo do módulo 1 → classificação de cada perfil → CSV do módulo 3.

Cada execução avança o que der e sai (ou repete com `--repetir-horas`):

1. pega o mês mais recente publicado pelo módulo 1 em `exportacoes/novas/` (ou `--mes`);
2. para cada perfil, chama a classificação em lote, que é retomável: envia o lote, ou recolhe o
   que terminou e envia a revisão; o que não mudou não volta ao LLM;
3. quando nenhum perfil tem lote em andamento e há classificação nova desde a última
   exportação, grava o `classificacao.csv`. O módulo 3 relê o arquivo sozinho.

Erro de contrato ou de perfil (funil grande demais, perfil inválido) para só aquele perfil; os
outros seguem. A conferência humana (`revisao-mensal`) e os custos continuam com a equipe.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agente.armazenamento import ClassificacaoStore
from agente.config import AgenteConfig
from agente.contrato import NOME_MANIFESTO, ContratoInvalidoError, validar_mes
from agente.exportacao import ARQUIVO_CRM, exportar_crm
from agente.funil import FunilGrandeError
from agente.perfil import PerfilInvalidoError, listar_ids
from agente.servico import Classificador

log = logging.getLogger("agente.rotina")
_MES = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass
class ResumoRotina:
    mes: str | None
    perfis: dict[str, str] = field(default_factory=dict)   # perfil → situação
    pendentes: list[str] = field(default_factory=list)
    exportado: str | None = None
    erro: str | None = None

    def linhas(self) -> list[str]:
        if self.mes is None:
            return [self.erro or "Nenhum mês publicado pelo módulo 1 em exportacoes/novas/."]
        saida = [f"Rotina do mês {self.mes}"]
        if self.erro:
            saida.append(f"  erro: {self.erro}")
        saida += [f"  {perfil}: {situacao}" for perfil, situacao in self.perfis.items()]
        if self.pendentes:
            saida.append(f"  aguardando a API em {', '.join(self.pendentes)}: rode de novo mais tarde.")
        elif self.exportado:
            saida.append(f"  CSV do módulo 3 atualizado: {self.exportado}")
        else:
            saida.append("  nada novo para exportar.")
        return saida


def ultimo_mes(novas_dir: Path) -> str | None:
    if not novas_dir.exists():
        return None
    meses = sorted(p.name for p in novas_dir.iterdir() if _MES.fullmatch(p.name) and (p / NOME_MANIFESTO).exists())
    return meses[-1] if meses else None


def _assinatura(store: ClassificacaoStore) -> list[Any]:
    """Muda sempre que uma classificação é gravada ou refeita."""
    with store.conexao() as con:
        return list(con.execute("SELECT count(*), max(classificado_em) FROM classificacao").fetchone())


def _estado(cfg: AgenteConfig) -> Path:
    return cfg.dados_dir / "rotina.json"


def executar_rotina(cfg: AgenteConfig, store: ClassificacaoStore, classificador: Classificador,
                    mes: str | None = None, perfis: list[str] | None = None, aguardar: bool = False,
                    intervalo_s: float = 300) -> ResumoRotina:
    mes = mes or ultimo_mes(cfg.novas_dir)
    resumo = ResumoRotina(mes)
    if mes is None:
        return resumo
    try:
        validar_mes(cfg.novas_dir, mes, cfg.schema_dir)
    except ContratoInvalidoError as exc:
        resumo.erro = str(exc)
        return resumo

    for perfil_id in perfis or listar_ids(cfg.perfis_dir):
        try:
            r = classificador.executar(mes, perfil_id, modo="lote", aguardar=aguardar, intervalo_s=intervalo_s)
        except (PerfilInvalidoError, FunilGrandeError, ContratoInvalidoError) as exc:
            resumo.perfis[perfil_id] = f"não classificado: {exc}"
            log.error("%s: %s", perfil_id, exc)
            continue
        if r.lote_pendente:
            resumo.pendentes.append(perfil_id)
            resumo.perfis[perfil_id] = f"em andamento ({r.lote_pendente})"
        else:
            resumo.perfis[perfil_id] = (f"{r.candidatos} no funil, {r.gravados} gravados agora, {r.alvos} alvos"
                                        + (f", {len(r.falhas)} falhas" if r.falhas else ""))
        for falha in r.falhas[:5]:
            log.warning("%s: %s", perfil_id, falha)

    if not resumo.pendentes:
        resumo.exportado = _exportar_se_mudou(cfg, store)
    return resumo


def _exportar_se_mudou(cfg: AgenteConfig, store: ClassificacaoStore) -> str | None:
    assinatura = _assinatura(store)
    if not assinatura[0]:
        return None
    saida = cfg.saida_dir / ARQUIVO_CRM
    try:
        anterior = json.loads(_estado(cfg).read_text(encoding="utf-8")).get("exportado")
    except (OSError, ValueError):
        anterior = None
    if anterior == assinatura and saida.exists():
        return None
    r = exportar_crm(store, cfg.novas_dir, cfg.schema_dir, saida)
    _estado(cfg).write_text(json.dumps({"exportado": assinatura}), encoding="utf-8")
    for mes, n in r.puladas.items():
        log.warning("%d classificações de %s ficaram fora do CSV (arquivo do mês ausente ou alterado)", n, mes)
    return f"{saida} ({r.linhas} empresas, {r.alvos} alvos)"


def repetir(passo: Callable[[], ResumoRotina], horas: float, dormir: Callable[[float], None] = time.sleep,
            vezes: int | None = None) -> None:
    """Para o agendador: roda a rotina a cada `horas`. Um erro não derruba o laço."""
    n = 0
    while vezes is None or n < vezes:
        try:
            for linha in passo().linhas():
                log.info("%s", linha)
        except Exception:  # noqa: BLE001 — o próximo ciclo tenta de novo
            log.exception("Falha na rotina do mês")
        n += 1
        if vezes is None or n < vezes:
            dormir(horas * 3600)
