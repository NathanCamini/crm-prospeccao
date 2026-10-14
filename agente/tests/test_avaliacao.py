"""Agente: medição (rótulos, precisão, revisão mensal), formato da API de lote e linha de comando."""

from __future__ import annotations

import csv
import json

import pytest

from agente import cli
from agente.armazenamento import ClassificacaoStore, Registro
from agente.avaliacao import amostra_para_rotular, amostra_revisao, avaliar
from agente.contrato import validar_mes
from agente.llm import custo_usd, interpretar_linha_lote, linha_pedido_lote
from agente.perfil import carregar_perfil
from tests.apoio import EMPRESAS_PADRAO, MES, NO_FUNIL, PERFIL_TESTE, ClienteFalso, escrever_mes, montar_cfg


@pytest.fixture()
def cfg(tmp_path):
    configuracao = montar_cfg(tmp_path, PERFIL_TESTE)
    escrever_mes(configuracao.novas_dir, EMPRESAS_PADRAO)
    return configuracao


def _ler_csv(caminho) -> list[dict]:
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        return list(csv.DictReader(arquivo, delimiter=";"))


def _registro(cnpj: str, alvo: bool, score: int, franquia: bool | None = None) -> Registro:
    final = {"e_alvo": alvo, "score": score, "justificativa": f"j{cnpj[-4:]}", "e_franquia": franquia, "marca": None,
             "padrao": "medio", "produto_sugerido": None, "confianca": "alta"}
    return Registro(cnpj, "teste_hotel", 1, MES, "m", "p", "e", final, {}, False, 10, 0.0)


# ---------------------------------------------------------------- rotulagem e avaliação
def test_amostra_para_rotular_e_aleatoria_e_reprodutivel(cfg, tmp_path) -> None:
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    perfil = carregar_perfil(cfg.perfis_dir, "teste_hotel")
    assert amostra_para_rotular(arquivo, perfil, 3, tmp_path / "r1.csv", semente=7) == 3
    amostra_para_rotular(arquivo, perfil, 3, tmp_path / "r2.csv", semente=7)
    linhas = _ler_csv(tmp_path / "r1.csv")
    assert linhas == _ler_csv(tmp_path / "r2.csv") and {l["cnpj"] for l in linhas} <= NO_FUNIL
    assert all(l["e_alvo_humano"] == "" and l["e_franquia_humano"] == "" for l in linhas)
    assert amostra_para_rotular(arquivo, perfil, 50, tmp_path / "r3.csv") == len(NO_FUNIL)  # não passa do funil


def test_avaliar_calcula_precisao_cobertura_e_meta(cfg, tmp_path) -> None:
    store = ClassificacaoStore(cfg.banco)
    cnpjs = [f"{i:014d}" for i in range(40)]
    # agente: 0-24 alvo; humano: 0-19 alvo e 30-34 alvo → VP 20, FP 5, FN 5, VN 10
    store.gravar([_registro(c, i < 25, 80 if i < 25 else 20, franquia=(i % 2 == 0) if i < 4 else None)
                  for i, c in enumerate(cnpjs)])
    rotulos = tmp_path / "rotulos.csv"
    with rotulos.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["cnpj", "e_alvo_humano", "e_franquia_humano"])
        for i, c in enumerate(cnpjs):
            escritor.writerow([c, "sim" if i < 20 or 30 <= i < 35 else "nao", "sim" if i in (0, 1) else ""])
        escritor.writerow(["99999999999999", "sim", ""])  # rotulada, mas não classificada
    relatorio = avaliar(store, "teste_hotel", rotulos, meta=0.85)
    assert (relatorio.verdadeiros_positivos, relatorio.falsos_positivos, relatorio.falsos_negativos,
            relatorio.verdadeiros_negativos) == (20, 5, 5, 10)
    assert relatorio.precisao == pytest.approx(0.8) and relatorio.cobertura == pytest.approx(0.8)
    assert not relatorio.aprovado and relatorio.sem_classificacao == 1
    assert (relatorio.franquia_avaliadas, relatorio.franquia_acertos) == (2, 1)  # 0: sim=sim; 1: não≠sim
    assert len(relatorio.exemplos_falsos_positivos) == 5 and "RESULTADO: abaixo da meta" in "\n".join(relatorio.linhas())
    assert avaliar(store, "teste_hotel", rotulos, meta=0.75).aprovado


def test_avaliar_avisa_quando_ha_poucos_alvos(cfg, tmp_path) -> None:
    store = ClassificacaoStore(cfg.banco)
    store.gravar([_registro("00000000000001", True, 90)])
    rotulos = tmp_path / "r.csv"
    rotulos.write_text("cnpj,e_alvo_humano\n00000000000001,sim\n", encoding="utf-8")
    relatorio = avaliar(store, "teste_hotel", rotulos)
    assert relatorio.precisao == 1.0 and not relatorio.aprovado and "instável" in relatorio.avisos[0]


def test_revisao_mensal(cfg, tmp_path) -> None:
    store = ClassificacaoStore(cfg.banco)
    store.gravar([_registro(c, True, 70) for c in NO_FUNIL])
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    assert amostra_revisao(store, arquivo, "teste_hotel", 2, tmp_path / "rev.csv", semente=1) == 2
    linhas = _ler_csv(tmp_path / "rev.csv")
    assert all(l["nome_fantasia"] and l["concorda"] == "" for l in linhas)


# ---------------------------------------------------------------- formato da API de lote e custo
def test_linha_do_resultado_do_lote() -> None:
    chave, resposta = interpretar_linha_lote({"key": "123", "response": {
        "candidates": [{"content": {"parts": [{"text": "pensando", "thought": True}, {"text": '{"a": 1}'}]}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}}})
    assert chave == "123" and resposta.texto == '{"a": 1}' and (resposta.tokens_entrada, resposta.tokens_saida) == (100, 25)
    assert "erro no lote" in interpretar_linha_lote({"key": "9", "error": {"code": 500}})[1]
    assert "sem candidatos" in interpretar_linha_lote({"key": "9", "response": {"candidates": []}})[1]


def test_pedido_do_lote_leva_schema_e_instrucao() -> None:
    linha = linha_pedido_lote("123", "sistema", "entrada", {"type": "object"})
    assert linha["key"] == "123" and linha["request"]["systemInstruction"]["parts"][0]["text"] == "sistema"
    assert linha["request"]["generationConfig"]["responseJsonSchema"] == {"type": "object"}
    json.dumps(linha)  # serializável em JSONL


def test_custo_estimado_e_desconto_do_lote() -> None:
    assert custo_usd("gemini-2.5-flash-lite", 1_000_000, 1_000_000, lote=False) == pytest.approx(0.5)
    assert custo_usd("gemini-2.5-flash-lite", 1_000_000, 1_000_000, lote=True) == pytest.approx(0.25)
    assert custo_usd("modelo-desconhecido", 10, 10, lote=True) is None


# ---------------------------------------------------------------- linha de comando
def test_cli_de_ponta_a_ponta(cfg, tmp_path, capsys) -> None:
    cliente = ClienteFalso()
    rodar = lambda *args: cli.main(list(args), fabrica_cliente=lambda _: cliente, cfg=cfg)  # noqa: E731

    assert rodar("perfis") == 0 and "teste_hotel v1" in capsys.readouterr().out
    assert rodar("validar", "--mes", MES) == 0 and "teste_hotel v1: 4 passam no filtro SQL" in capsys.readouterr().out
    assert not cfg.banco.exists()  # comandos de leitura não criam nem alteram o banco do usuário
    assert rodar("classificar", "--mes", MES, "--perfil", "teste_hotel", "--modo", "online") == 0
    assert "gravados nesta execução: 4 (alvos: 3)" in capsys.readouterr().out
    assert rodar("alvos", "--perfil", "teste_hotel", "--saida", str(tmp_path / "alvos.csv")) == 0
    assert len(_ler_csv(tmp_path / "alvos.csv")) == 3
    assert rodar("custos") == 0 and "teste_hotel" in capsys.readouterr().out
    assert rodar("amostrar-rotulos", "--mes", MES, "--perfil", "teste_hotel", "--n", "3",
                 "--saida", str(tmp_path / "rot.csv")) == 0
    assert rodar("exportar-crm") == 0 and len(_ler_csv(cfg.saida_dir / "classificacao.csv")) == 4
    assert rodar("exportar-crm", "--perfil", "nao_existe") == 2
    assert rodar("classificar", "--mes", "2026-01", "--perfil", "teste_hotel") == 2  # mês sem arquivo
    assert rodar("classificar", "--mes", MES, "--perfil", "nao_existe") == 2


def test_cli_avaliar_devolve_1_abaixo_da_meta(cfg, tmp_path) -> None:
    store = ClassificacaoStore(cfg.banco)
    store.gravar([_registro("00000000000001", True, 90)])
    rotulos = tmp_path / "r.csv"
    rotulos.write_text("cnpj;e_alvo_humano\n00000000000001;nao\n", encoding="utf-8")
    assert cli.main(["avaliar", "--perfil", "teste_hotel", "--rotulos", str(rotulos), "--json", str(tmp_path / "r.json")],
                    cfg=cfg) == 1
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["falsos_positivos"] == 1
