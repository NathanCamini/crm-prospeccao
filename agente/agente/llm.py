"""Acesso ao LLM (Gemini): chamada individual e API de lote, mais a estimativa de custo.

A classificação mensal usa a API de lote: custa metade, e não há pressa (prazo-alvo de 24 h;
um lote não concluído em 48 h expira). Cada pedido vai num arquivo JSONL com uma chave (o CNPJ),
que volta na resposta: o resultado é ligado à empresa pela chave, nunca pela ordem.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("agente")

# US$ por 1 milhão de tokens (entrada, saída), preço interativo; o lote custa 50% disso.
# Fonte: https://ai.google.dev/gemini-api/docs/pricing (consultada em 13/10/2026). Os 3.6/3.8 Flash
# dobram de preço a partir de 01/01/2027, segundo a mesma página.
PRECOS_USD_POR_MILHAO: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
}
DESCONTO_LOTE = 0.5
CODIGOS_TRANSITORIOS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RespostaLLM:
    texto: str
    tokens_entrada: int
    tokens_saida: int  # inclui os tokens de raciocínio, cobrados como saída


@dataclass(frozen=True)
class EstadoLote:
    situacao: str          # pendente | concluido | falhou | expirado
    detalhe: str = ""


def custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int, lote: bool) -> float | None:
    """Custo estimado em US$ (None se o modelo não está na tabela de preços)."""
    precos = PRECOS_USD_POR_MILHAO.get(modelo)
    if precos is None:
        return None
    valor = (tokens_entrada * precos[0] + tokens_saida * precos[1]) / 1_000_000
    return round(valor * (DESCONTO_LOTE if lote else 1.0), 6)


def com_retentativas(funcao: Callable[[], Any], descricao: str, tentativas: int = 4,
                     espera_inicial: float = 4.0, dormir: Callable[[float], None] = time.sleep) -> Any:
    """Repete em erro transitório da API (cota, servidor ocupado); erro permanente sobe na hora."""
    from google.genai import errors

    espera = espera_inicial
    for tentativa in range(1, tentativas + 1):
        try:
            return funcao()
        except errors.APIError as erro:
            if erro.code not in CODIGOS_TRANSITORIOS or tentativa == tentativas:
                raise
            log.warning("%s: erro %s; nova tentativa em %.0fs (%d/%d)", descricao, erro.code, espera,
                        tentativa, tentativas - 1)
            dormir(espera)
            espera *= 2


def _texto_das_partes(partes: list[dict[str, Any]]) -> str:
    return "".join(p.get("text", "") for p in partes if not p.get("thought"))


def interpretar_linha_lote(linha: dict[str, Any]) -> tuple[str, RespostaLLM | str]:
    """Uma linha do arquivo de resultado do lote → (chave, resposta ou mensagem de erro)."""
    chave = str(linha.get("key", ""))
    if "response" not in linha:
        erro = linha.get("error") or linha.get("status") or "sem resposta"
        return chave, f"erro no lote: {json.dumps(erro, ensure_ascii=False)[:300]}"
    resposta = linha["response"]
    candidatos = resposta.get("candidates") or []
    if not candidatos:
        return chave, "resposta sem candidatos (bloqueio ou vazia)"
    texto = _texto_das_partes((candidatos[0].get("content") or {}).get("parts") or [])
    uso = resposta.get("usageMetadata") or resposta.get("usage_metadata") or {}
    pega = lambda *nomes: next((int(uso[n]) for n in nomes if uso.get(n) is not None), 0)  # noqa: E731
    entrada = pega("promptTokenCount", "prompt_token_count")
    saida = pega("candidatesTokenCount", "candidates_token_count") + pega("thoughtsTokenCount", "thoughts_token_count")
    return chave, RespostaLLM(texto, entrada, saida)


def linha_pedido_lote(chave: str, sistema: str, entrada: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Um pedido no formato JSONL da API de lote (REST: GenerateContentRequest)."""
    return {
        "key": chave,
        "request": {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": [{"text": entrada}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseJsonSchema": schema},
        },
    }


class ClienteGemini:
    """Implementação real sobre o SDK google-genai. Os testes usam um cliente falso com os mesmos métodos."""

    _ESTADOS = {
        "JOB_STATE_SUCCEEDED": "concluido", "JOB_STATE_PARTIALLY_SUCCEEDED": "concluido",
        "JOB_STATE_FAILED": "falhou", "JOB_STATE_CANCELLED": "falhou", "JOB_STATE_EXPIRED": "expirado",
    }

    def __init__(self, api_key: str, pasta_lotes: Path) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY não configurada no .env: o classificador precisa dela para o LLM.")
        from google import genai

        self._genai = genai
        self.cliente = genai.Client(api_key=api_key)
        self.pasta_lotes = pasta_lotes

    def gerar(self, modelo: str, sistema: str, entrada: str, schema: dict[str, Any]) -> RespostaLLM:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=sistema, temperature=0,
                                             response_mime_type="application/json", response_json_schema=schema)
        resposta = com_retentativas(
            lambda: self.cliente.models.generate_content(model=modelo, contents=entrada, config=config),
            descricao=f"classificação ({modelo})")
        uso = resposta.usage_metadata
        texto = "".join(p.text or "" for c in (resposta.candidates or [])[:1] for p in (c.content.parts or [])
                        if not getattr(p, "thought", False)) if resposta.candidates else ""
        return RespostaLLM(texto,
                           (uso.prompt_token_count or 0) if uso else 0,
                           ((uso.candidates_token_count or 0) + (uso.thoughts_token_count or 0)) if uso else 0)

    def enviar_lote(self, modelo: str, sistema: str, schema: dict[str, Any], itens: list[tuple[str, str]],
                    nome: str) -> str:
        from google.genai import types

        self.pasta_lotes.mkdir(parents=True, exist_ok=True)
        caminho = self.pasta_lotes / f"{nome}.jsonl"
        with caminho.open("w", encoding="utf-8") as arquivo:
            for chave, entrada in itens:
                arquivo.write(json.dumps(linha_pedido_lote(chave, sistema, entrada, schema), ensure_ascii=False) + "\n")
        enviado = com_retentativas(
            lambda: self.cliente.files.upload(file=str(caminho),
                                              config=types.UploadFileConfig(display_name=nome, mime_type="jsonl")),
            descricao="envio do arquivo do lote")
        job = com_retentativas(
            lambda: self.cliente.batches.create(model=modelo, src=enviado.name, config={"display_name": nome}),
            descricao="criação do lote")
        return job.name

    def consultar_lote(self, nome_job: str) -> EstadoLote:
        job = com_retentativas(lambda: self.cliente.batches.get(name=nome_job), descricao="consulta do lote")
        estado = job.state.name if job.state else "JOB_STATE_UNSPECIFIED"
        return EstadoLote(self._ESTADOS.get(estado, "pendente"), estado)

    def resultados_lote(self, nome_job: str) -> dict[str, RespostaLLM | str]:
        job = com_retentativas(lambda: self.cliente.batches.get(name=nome_job), descricao="consulta do lote")
        if not job.dest or not job.dest.file_name:
            raise RuntimeError(f"Lote {nome_job} sem arquivo de resultado")
        conteudo = com_retentativas(lambda: self.cliente.files.download(file=job.dest.file_name),
                                    descricao="download do resultado do lote")
        resultados: dict[str, RespostaLLM | str] = {}
        for linha in conteudo.decode("utf-8").splitlines():
            if linha.strip():
                chave, resposta = interpretar_linha_lote(json.loads(linha))
                resultados[chave] = resposta
        return resultados
