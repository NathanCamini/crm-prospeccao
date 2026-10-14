"""Agente: CSV que o módulo 3 lê e entrega ao CRM."""

from __future__ import annotations

import csv
import shutil

import pytest

from agente.armazenamento import ClassificacaoStore
from agente.exportacao import COLUNAS_CRM, exportar_crm
from agente.servico import Classificador
from agente.sinais import carregar_redes
from tests.apoio import EMPRESAS_PADRAO, MES, NO_FUNIL, PERFIL_TESTE, RAIZ, A, E, I, ClienteFalso, empresa, escrever_mes, montar_cfg

J = "10111222000100"


@pytest.fixture()
def ambiente(tmp_path):
    cfg = montar_cfg(tmp_path, PERFIL_TESTE)
    escrever_mes(cfg.novas_dir, EMPRESAS_PADRAO)
    store = ClassificacaoStore(cfg.banco)
    classificador = Classificador(cfg, store, ClienteFalso(), redes=carregar_redes(RAIZ / "perfis" / "marcas_rede.csv"),
                                  dormir=lambda s: None)
    classificador.executar(MES, "teste_hotel", modo="online")
    return cfg, store, classificador


def _ler(caminho) -> list[dict]:
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        return list(csv.DictReader(arquivo, delimiter=";"))


def test_csv_tem_as_colunas_e_os_valores_do_contrato_do_crm(ambiente) -> None:
    cfg, store, _ = ambiente
    saida = cfg.saida_dir / "classificacao.csv"
    resumo = exportar_crm(store, cfg.novas_dir, cfg.schema_dir, saida)
    linhas = {l["cnpj"]: l for l in _ler(saida)}
    assert set(linhas) == NO_FUNIL and (resumo.linhas, resumo.alvos, resumo.meses) == (4, 3, [MES])
    assert list(linhas[A]) == COLUNAS_CRM
    ibis = linhas[E]
    assert (ibis["e_alvo"], ibis["e_franquia"], ibis["marca"], ibis["perfil_id"], ibis["perfil_versao"]) == \
        ("sim", "sim", "ibis", "teste_hotel", "1")
    assert (ibis["email"], ibis["ddd_1"], ibis["telefone_1"], ibis["ddd_2"]) == ("reservas.floripa@accor.com", "48", "33334444", "")
    assert (ibis["logradouro"], ibis["cnae_descricao"], ibis["data_inicio_atividade"], ibis["mes_referencia"]) == \
        ("RUA DAS FLORES", "Hotéis", "2026-09-01", MES)
    assert float(ibis["capital_social"]) == 2_000_000 and ibis["justificativa"] and ibis["classificado_em"]
    assert (linhas[I]["e_alvo"], linhas[I]["e_franquia"], linhas[I]["produto_sugerido"]) == ("nao", "", "")
    assert not saida.with_name("classificacao.csv.tmp").exists()


def test_mes_sem_arquivo_fica_fora_e_csv_anterior_e_mantido(ambiente) -> None:
    cfg, store, classificador = ambiente
    escrever_mes(cfg.novas_dir, [empresa(J, "POUSADA NOVA", "5510801", "Hotéis", 300_000, mes_referencia_base="2026-10")],
                 mes="2026-10")
    classificador.executar("2026-10", "teste_hotel", modo="online")
    saida = cfg.saida_dir / "classificacao.csv"
    shutil.rmtree(cfg.novas_dir / MES)

    resumo = exportar_crm(store, cfg.novas_dir, cfg.schema_dir, saida)
    assert [l["cnpj"] for l in _ler(saida)] == [J] and resumo.puladas == {MES: len(NO_FUNIL)}

    anterior = saida.read_bytes()
    with pytest.raises(ValueError, match="CSV anterior foi mantido"):
        exportar_crm(store, cfg.novas_dir, cfg.schema_dir, saida, mes=MES)
    assert saida.read_bytes() == anterior
