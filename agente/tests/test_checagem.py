"""Pré-checagem da API com uma empresa fictícia (LLM falso)."""

from __future__ import annotations

from pathlib import Path

from agente.checagem import checar
from tests.apoio import ClienteFalso, montar_cfg, resposta_padrao


def test_online_e_lote_ok(tmp_path: Path) -> None:
    cliente = ClienteFalso()
    original = cliente.consultar_lote

    def consultar(job):
        cliente.concluir(job)
        return original(job)

    cliente.consultar_lote = consultar
    ok, linhas = checar(montar_cfg(tmp_path), cliente, "amenities_hotel", lote=True, dormir=lambda s: None)
    assert ok, linhas
    assert [c[0] for c in cliente.chamadas] == ["online", "online", "lote"]
    assert any("triagem em lote" in linha for linha in linhas)
    assert not (tmp_path / "dados" / "agente.sqlite").exists()   # nada gravado


def test_resposta_fora_do_schema_e_erro_da_api(tmp_path: Path) -> None:
    respostas = iter([{"score": 50}, RuntimeError("404 models/gemini-x not found")])
    ok, linhas = checar(montar_cfg(tmp_path), ClienteFalso(lambda m, e: next(respostas)), "amenities_hotel", lote=True)
    assert not ok
    assert "fora do schema" in linhas[1] and "not found" in linhas[2]
    assert "Corrija" in linhas[-1]


def test_sem_lote_so_online(tmp_path: Path) -> None:
    cliente = ClienteFalso(resposta_padrao)
    ok, _ = checar(montar_cfg(tmp_path), cliente, "amenities_hotel")
    assert ok and len(cliente.chamadas) == 2
