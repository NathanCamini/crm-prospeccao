"""Agente, execução com LLM falso: online, lote retomável, reclassificação seletiva e falhas."""

from __future__ import annotations

import json

import pytest

from agente.armazenamento import ClassificacaoStore
from agente.servico import Classificador
from agente.sinais import carregar_redes
from tests.apoio import (
    EMPRESAS_PADRAO, MES, NO_FUNIL, PERFIL_TESTE, RAIZ, A, E, G, I, ClienteFalso, escrever_mes, montar_cfg,
    resposta_padrao,
)

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


def _linhas(store: ClassificacaoStore) -> dict[str, dict]:
    with store.conexao() as con:
        return {r["cnpj"]: dict(r) for r in con.execute("SELECT * FROM classificacao")}


# ---------------------------------------------------------------- online
def test_online_triagem_e_revisao_so_no_que_precisa(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente
    resumo = classificador.executar(MES, PERFIL, modo="online")

    assert resumo.candidatos == len(NO_FUNIL) and resumo.gravados == len(NO_FUNIL)
    triagem = [c for c in cliente.chamadas if c[1] == cfg.modelo_triagem]
    revisao = [c for c in cliente.chamadas if c[1] == cfg.modelo_revisao]
    assert len(triagem) == 4 and {c[2] for c in revisao} == {"POUSADA MAR AZUL", "IBIS BUDGET FLORIPA", "CHALES DA SERRA"}

    linhas = _linhas(store)
    assert set(linhas) == NO_FUNIL
    assert linhas[E]["e_alvo"] == 1 and linhas[E]["e_franquia"] == 1 and linhas[E]["marca"] == "ibis"
    assert linhas[E]["modelo"] == cfg.modelo_revisao and linhas[E]["revisado"] == 1
    assert linhas[I]["e_alvo"] == 0 and linhas[I]["revisado"] == 0 and linhas[I]["modelo"] == cfg.modelo_triagem
    detalhes = json.loads(linhas[E]["resultado_json"])
    assert detalhes["triagem"]["modelo"] == cfg.modelo_triagem and detalhes["motivo_revisao"] == "score alto"
    assert linhas[E]["custo_tokens"] == 2 * 340 and linhas[E]["custo_usd"] > 0  # triagem + revisão
    assert resumo.alvos == 3 and resumo.custo_usd > 0
    assert {r["cnpj"] for r in store.classificacoes(PERFIL, somente_alvos=True)} == {A, E, G}


def test_amostra_classifica_so_as_primeiras_e_o_resto_fica_para_depois(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    resumo = classificador.executar(MES, PERFIL, modo="online", amostra=2)
    assert resumo.gravados == 2 and set(_linhas(store)) == {A, E}  # ordem de CNPJ
    resumo = classificador.executar(MES, PERFIL, modo="online")
    assert resumo.inalterados == 2 and set(_linhas(store)) == NO_FUNIL


def test_reexecucao_nao_chama_o_llm(ambiente) -> None:
    _, _, cliente, classificador = ambiente
    classificador.executar(MES, PERFIL, modo="online")
    chamadas = len(cliente.chamadas)
    resumo = classificador.executar(MES, PERFIL, modo="online")
    assert len(cliente.chamadas) == chamadas and resumo.inalterados == len(NO_FUNIL) and resumo.gravados == 0
    classificador.executar(MES, PERFIL, modo="online", forcar=True)
    assert len(cliente.chamadas) > chamadas


def test_so_a_empresa_que_mudou_e_reclassificada(ambiente) -> None:
    cfg, _, cliente, classificador = ambiente
    classificador.executar(MES, PERFIL, modo="online")
    cliente.chamadas.clear()
    alteradas = [e | {"capital_social": 900_000} if e["cnpj"] == I else e for e in EMPRESAS_PADRAO]
    escrever_mes(cfg.novas_dir, alteradas)
    resumo = classificador.executar(MES, PERFIL, modo="online")
    assert resumo.inalterados == 3 and [c[2] for c in cliente.chamadas] == ["ESPACO EVENTOS SOL"]


def test_versao_nova_do_perfil_reclassifica_e_guarda_historico(ambiente) -> None:
    cfg, store, _, classificador = ambiente
    classificador.executar(MES, PERFIL, modo="online")
    (cfg.perfis_dir / f"{PERFIL}.yaml").write_text(PERFIL_TESTE.replace("versao: 1", "versao: 2"), encoding="utf-8")
    resumo = classificador.executar(MES, PERFIL, modo="online")
    assert resumo.gravados == len(NO_FUNIL)
    with store.conexao() as con:
        assert con.execute("SELECT count(*) FROM classificacao").fetchone()[0] == 2 * len(NO_FUNIL)
        vigentes = con.execute("SELECT DISTINCT perfil_versao FROM classificacao_vigente").fetchall()
    assert [v[0] for v in vigentes] == [2]  # o CRM lê só a versão vigente


def test_resposta_invalida_vai_para_revisao_e_falha_dupla_e_reportada(ambiente) -> None:
    _, store, cliente, classificador = ambiente

    def responder(modelo, entrada):
        if entrada["nome_fantasia"] == "ESPACO EVENTOS SOL":
            return "isto não é JSON"
        return resposta_padrao(modelo, entrada)

    cliente.responder = responder
    resumo = classificador.executar(MES, PERFIL, modo="online")
    assert I not in _linhas(store) and len(resumo.falhas) == 1 and I in resumo.falhas[0]
    assert sum(1 for c in cliente.chamadas if c[2] == "ESPACO EVENTOS SOL") == 2  # triagem + revisão


def test_revisao_que_falha_mantem_a_triagem_marcada(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente

    def responder(modelo, entrada):
        if modelo == cfg.modelo_revisao and entrada["nome_fantasia"] == "POUSADA MAR AZUL":
            return ConnectionError("timeout")
        return resposta_padrao(modelo, entrada)

    cliente.responder = responder
    classificador.executar(MES, PERFIL, modo="online")
    linha = _linhas(store)[A]
    detalhes = json.loads(linha["resultado_json"])
    assert linha["revisado"] == 0 and linha["modelo"] == cfg.modelo_triagem
    assert "revisão falhou" in detalhes["avisos"][-1] and "timeout" in detalhes["revisao_falhou"]


def test_erro_permanente_do_llm_interrompe_na_primeira_empresa(ambiente) -> None:
    from google.genai import errors

    from agente.servico import ErroLLMPermanente

    _, store, cliente, classificador = ambiente
    erro = errors.ClientError(400, {"error": {"message": "API key not valid. Please pass a valid API key.",
                                              "status": "INVALID_ARGUMENT"}})
    cliente.responder = lambda modelo, entrada: erro
    with pytest.raises(ErroLLMPermanente, match="GEMINI_API_KEY"):
        classificador.executar(MES, PERFIL, modo="online")
    assert len(cliente.chamadas) == 1 and _linhas(store) == {}


def test_erro_permanente_guarda_o_que_ja_foi_classificado(ambiente) -> None:
    from google.genai import errors

    from agente.servico import ErroLLMPermanente

    _, store, cliente, classificador = ambiente

    def responder(modelo, entrada):
        if entrada["nome_fantasia"] == "IBIS BUDGET FLORIPA":
            return errors.ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}})
        return resposta_padrao(modelo, entrada)

    cliente.responder = responder
    with pytest.raises(ErroLLMPermanente):
        classificador.executar(MES, PERFIL, modo="online")
    assert set(_linhas(store)) == {A}  # a pousada (antes do erro) ficou gravada


# ---------------------------------------------------------------- lote
def test_lote_retomavel_triagem_depois_revisao(ambiente) -> None:
    cfg, store, cliente, classificador = ambiente
    r1 = classificador.executar(MES, PERFIL)
    assert r1.enviados_triagem == 4 and r1.gravados == 0 and "triagem" in r1.lote_pendente
    assert classificador.executar(MES, PERFIL).lote_pendente  # ainda pendente: nada muda

    cliente.concluir()
    r3 = classificador.executar(MES, PERFIL)
    assert r3.gravados == 1 and r3.enviados_revisao == 3 and "revisao" in r3.lote_pendente
    assert set(_linhas(store)) == {I}  # o não alvo já está gravado; os alvos aguardam confirmação

    cliente.concluir()
    r4 = classificador.executar(MES, PERFIL)
    assert r4.gravados == 3 and r4.lote_pendente is None and set(_linhas(store)) == NO_FUNIL
    assert [l["estado"] for l in store.lotes(PERFIL)] == ["processado", "processado"]
    assert _linhas(store)[E]["custo_usd"] < 0.001  # preço de lote (50%)
    assert classificador.executar(MES, PERFIL).gravados == 0  # nada mudou


def test_lote_com_aguardar_vai_ate_o_fim(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    consultas = {"n": 0}
    original = cliente.consultar_lote

    def consultar(job):
        consultas["n"] += 1
        if consultas["n"] % 2 == 0:  # cada lote conclui na segunda consulta
            cliente.concluir(job)
        return original(job)

    cliente.consultar_lote = consultar
    resumo = classificador.executar(MES, PERFIL, aguardar=True)
    assert resumo.lote_pendente is None and resumo.gravados == len(NO_FUNIL) and set(_linhas(store)) == NO_FUNIL


def test_falha_no_envio_do_lote_e_retomada(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    cliente.falhar_envio = 1  # falha passageira: a mesma execução tenta de novo
    r1 = classificador.executar(MES, PERFIL)
    assert len(r1.falhas) == 1 and store.lotes(PERFIL)[0]["estado"] == "enviado"


def test_falha_persistente_no_envio_fica_para_a_proxima_execucao(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    cliente.falhar_envio = 2
    r1 = classificador.executar(MES, PERFIL)
    assert all("rede fora do ar" in f for f in r1.falhas) and store.lotes(PERFIL)[0]["estado"] == "criando"
    r2 = classificador.executar(MES, PERFIL)
    assert store.lotes(PERFIL)[0]["estado"] == "enviado" and r2.lote_pendente and not r2.falhas
    assert len(cliente.lotes) == 1  # o mesmo lote, sem duplicar empresas


def test_lote_expirado_e_reenviado_na_execucao_seguinte(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    classificador.executar(MES, PERFIL)
    cliente.concluir(situacao="expirado")
    r2 = classificador.executar(MES, PERFIL)
    assert "expirado" in r2.falhas[0]
    r3 = classificador.executar(MES, PERFIL)
    assert r3.enviados_triagem == 4 and len(cliente.lotes) == 2
    assert [l["estado"] for l in store.lotes(PERFIL)] == ["enviado", "expirado"]


def test_lote_de_versao_antiga_e_encerrado(ambiente) -> None:
    cfg, store, _, classificador = ambiente
    classificador.executar(MES, PERFIL)
    (cfg.perfis_dir / f"{PERFIL}.yaml").write_text(PERFIL_TESTE.replace("versao: 1", "versao: 2"), encoding="utf-8")
    classificador.executar(MES, PERFIL)
    estados = {(l["perfil_versao"], l["estado"]) for l in store.lotes(PERFIL)}
    assert estados == {(1, "falhou"), (2, "enviado")}


def test_lote_com_item_sem_resposta_vai_para_revisao(ambiente) -> None:
    _, store, cliente, classificador = ambiente
    classificador.executar(MES, PERFIL)
    job = list(cliente.lotes)[-1]
    cliente.lotes[job]["itens"] = [i for i in cliente.lotes[job]["itens"] if i[0] != I]  # a API "perdeu" um item
    cliente.concluir(job)
    r = classificador.executar(MES, PERFIL)
    assert r.enviados_revisao == 4  # 3 alvos + o item sem resposta
    cliente.concluir()
    classificador.executar(MES, PERFIL)
    assert set(_linhas(store)) == NO_FUNIL
