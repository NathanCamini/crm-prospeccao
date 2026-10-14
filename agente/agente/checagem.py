"""Pré-checagem da API antes da primeira classificação real (`agente checar`).

Classifica UMA empresa fictícia com o perfil escolhido, nos dois modelos (triagem e revisão), e
confere que a resposta passa no schema do perfil. Com `--lote`, faz o mesmo pela API de lote,
no formato JSONL que a classificação mensal usa (`responseJsonSchema`). Pega chave inválida, modelo
inexistente e schema recusado antes de gastar com o mês inteiro. Custo: frações de centavo.
Nada é gravado no banco do agente.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import ValidationError

from agente.config import AgenteConfig
from agente.llm import custo_usd
from agente.perfil import carregar_perfil
from agente.prompt import entrada_empresa, instrucao_sistema, texto_entrada
from agente.resultado import ResultadoLLM, schema_saida

# Inventada: nenhum dado corresponde a empresa real.
EMPRESA_TESTE: dict[str, Any] = {
    "nome_fantasia": "POUSADA RECANTO DO TESTE", "razao_social": "RECANTO DO TESTE HOSPEDAGEM LTDA",
    "cnae_principal": "5510801", "cnae_principal_descricao": "Hotéis", "cnaes_secundarios": [],
    "natureza_juridica": "Sociedade Empresária Limitada", "porte": "EPP", "capital_social": 250000,
    "opcao_simples": True, "opcao_mei": False, "matriz_filial": "MATRIZ", "municipio": "GRAMADO", "uf": "RS",
    "bairro": "CENTRO", "data_inicio_atividade": date(2026, 9, 1),
}


def _conferir(rotulo: str, modelo: str, texto: str, tokens: tuple[int, int], lote: bool) -> tuple[bool, str]:
    try:
        r = ResultadoLLM.model_validate_json(texto)
    except ValidationError as exc:
        return False, f"FALHA {rotulo} ({modelo}): resposta fora do schema: {exc.errors()[0]['msg']} | {texto[:200]}"
    custo = custo_usd(modelo, *tokens, lote=lote)
    return True, (f"ok   {rotulo} ({modelo}): e_alvo={r.e_alvo} score={r.score} padrao={r.padrao} "
                  f"· {tokens[0]}+{tokens[1]} tokens · US$ {custo}")


def checar(cfg: AgenteConfig, cliente: Any, perfil_id: str, lote: bool = False, espera_maxima_s: float = 3600,
           intervalo_s: float = 30, dormir: Callable[[float], None] = time.sleep) -> tuple[bool, list[str]]:
    perfil = carregar_perfil(cfg.perfis_dir, perfil_id)
    sistema, schema = instrucao_sistema(perfil), schema_saida(perfil)
    texto = texto_entrada(entrada_empresa(EMPRESA_TESTE, {"redes": []}, {}))
    linhas, tudo_ok = [f"Perfil {perfil.id} v{perfil.versao} · empresa fictícia"], True

    for rotulo, modelo in (("triagem online", cfg.modelo_triagem), ("revisão online", cfg.modelo_revisao)):
        try:
            r = cliente.gerar(modelo, sistema, texto, schema)
        except Exception as exc:  # noqa: BLE001 — a mensagem da API é o diagnóstico
            tudo_ok = False
            linhas.append(f"FALHA {rotulo} ({modelo}): {exc}")
            continue
        ok, linha = _conferir(rotulo, modelo, r.texto, (r.tokens_entrada, r.tokens_saida), lote=False)
        tudo_ok &= ok
        linhas.append(linha)

    if lote and tudo_ok:
        nome = f"checagem-{perfil.id}-v{perfil.versao}-{int(time.time())}"
        try:
            job = cliente.enviar_lote(cfg.modelo_triagem, sistema, schema, [("checagem", texto)], nome)
            linhas.append(f"lote enviado: {job}; esperando (pode levar de minutos a horas)...")
            inicio = time.monotonic()
            while (estado := cliente.consultar_lote(job)).situacao == "pendente":
                if time.monotonic() - inicio > espera_maxima_s:
                    linhas.append(f"lote ainda pendente após {espera_maxima_s / 60:.0f} min. A parte online passou; "
                                  "rode `agente checar --lote` de novo mais tarde para conferir o lote.")
                    return tudo_ok, linhas
                dormir(intervalo_s)
            if estado.situacao != "concluido":
                return False, linhas + [f"FALHA lote: {estado.situacao} ({estado.detalhe})"]
            resposta = cliente.resultados_lote(job).get("checagem")
            if not hasattr(resposta, "texto"):
                return False, linhas + [f"FALHA lote: {resposta}"]
            ok, linha = _conferir("triagem em lote", cfg.modelo_triagem, resposta.texto,
                                  (resposta.tokens_entrada, resposta.tokens_saida), lote=True)
            tudo_ok &= ok
            linhas.append(linha)
        except Exception as exc:  # noqa: BLE001
            return False, linhas + [f"FALHA lote: {exc}"]

    linhas.append("Tudo certo: pode rodar `classificar --modo online --amostra 10` com o mês real." if tudo_ok
                  else "Corrija o que falhou antes de classificar o mês.")
    return tudo_ok, linhas
