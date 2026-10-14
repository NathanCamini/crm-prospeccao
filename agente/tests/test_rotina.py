"""Rotina do mês: arquivo do módulo 1 → classificação de todos os perfis → CSV do módulo 3."""

from __future__ import annotations

import csv

import pytest

from agente.armazenamento import ClassificacaoStore
from agente.rotina import executar_rotina, repetir, ultimo_mes
from agente.servico import Classificador
from agente.sinais import carregar_redes
from tests.apoio import EMPRESAS_PADRAO, MES, NO_FUNIL, PERFIL_TESTE, RAIZ, ClienteFalso, escrever_mes, montar_cfg

PERFIL = "teste_hotel"


@pytest.fixture()
def ambiente(tmp_path):
    cfg = montar_cfg(tmp_path, PERFIL_TESTE)
    escrever_mes(cfg.novas_dir, EMPRESAS_PADRAO)
    store = ClassificacaoStore(cfg.banco)
    cliente = ClienteFalso()
    classificador = Classificador(cfg, store, cliente, redes=carregar_redes(RAIZ / "perfis" / "marcas_rede.csv"),
                                  dormir=lambda s: None)
    return cfg, store, cliente, classificador


def _csv(cfg) -> list[dict[str, str]]:
    with (cfg.saida_dir / "classificacao.csv").open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_sem_mes_publicado(tmp_path) -> None:
    cfg = montar_cfg(tmp_path)
    r = executar_rotina(cfg, ClassificacaoStore(cfg.banco), None)
    assert r.mes is None and "Nenhum mês" in r.linhas()[0]


def test_ultimo_mes_so_conta_pasta_com_manifesto(ambiente) -> None:
    cfg, *_ = ambiente
    (cfg.novas_dir / "2026-10").mkdir()           # pasta sem manifesto: carga ainda escrevendo
    assert ultimo_mes(cfg.novas_dir) == MES
    escrever_mes(cfg.novas_dir, EMPRESAS_PADRAO, mes="2026-10")
    assert ultimo_mes(cfg.novas_dir) == "2026-10"


def test_avanca_os_lotes_e_exporta_so_no_fim(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente
    saida = cfg.saida_dir / "classificacao.csv"

    r1 = executar_rotina(cfg, store, classificador, perfis=[PERFIL])
    assert r1.mes == MES and r1.pendentes == [PERFIL] and r1.exportado is None and not saida.exists()

    cliente.concluir()                             # triagem pronta → envia a revisão
    r2 = executar_rotina(cfg, store, classificador, perfis=[PERFIL])
    assert r2.pendentes == [PERFIL] and not saida.exists()

    cliente.concluir()                             # revisão pronta → exporta
    r3 = executar_rotina(cfg, store, classificador, perfis=[PERFIL])
    assert r3.pendentes == [] and r3.exportado and saida.exists()
    assert {linha["cnpj"] for linha in _csv(cfg)} == NO_FUNIL

    r4 = executar_rotina(cfg, store, classificador, perfis=[PERFIL])
    assert r4.exportado is None and "nada novo" in r4.linhas()[-1]
    assert len([c for c in cliente.chamadas if c[0] == "lote"]) == 4 + 3  # nada reenviado


def test_csv_apagado_e_regravado(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente
    for _ in range(2):
        executar_rotina(cfg, store, classificador, perfis=[PERFIL])
        cliente.concluir()
    assert executar_rotina(cfg, store, classificador, perfis=[PERFIL]).exportado
    (cfg.saida_dir / "classificacao.csv").unlink()
    assert executar_rotina(cfg, store, classificador, perfis=[PERFIL]).exportado


def test_perfil_com_funil_grande_nao_para_os_outros(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente
    (cfg.perfis_dir / "teste_apertado.yaml").write_text(
        PERFIL_TESTE.replace("id: teste_hotel", "id: teste_apertado") + "limite_candidatos: 1\n", encoding="utf-8")
    r = executar_rotina(cfg, store, classificador, perfis=["teste_apertado", PERFIL])
    assert r.perfis["teste_apertado"].startswith("não classificado")
    assert r.pendentes == [PERFIL]


def test_arquivo_fora_do_contrato(ambiente) -> None:
    cfg, store, _, classificador = ambiente
    (cfg.novas_dir / MES / "empresas_novas.parquet").write_bytes(b"corrompido")
    r = executar_rotina(cfg, store, classificador)
    assert r.erro and "SHA-256" in r.erro


def test_repetir_sobrevive_a_erro() -> None:
    chamadas: list[int] = []

    def passo():
        chamadas.append(1)
        raise RuntimeError("API fora do ar")

    dormidas: list[float] = []
    repetir(passo, 6, dormir=dormidas.append, vezes=3)
    assert len(chamadas) == 3 and dormidas == [6 * 3600] * 2
