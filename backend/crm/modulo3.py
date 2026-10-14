"""Cliente HTTP do módulo 3. É a única porta do CRM para os dados de empresas
e para o agente: o CRM nunca fala direto com o módulo 2 nem com o 1."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException


class Modulo3:
    def __init__(self, base_url: str, timeout: float, agente_timeout: float, cliente: httpx.Client | None = None):
        self._http = cliente or httpx.Client(base_url=base_url, timeout=timeout)
        self._timeout = timeout
        self._agente_timeout = agente_timeout
        self._saude_cache: tuple[float, dict] | None = None

    def _chamar(self, metodo: str, caminho: str, timeout: float | None = None, **kwargs: Any) -> Any:
        try:
            resp = self._http.request(metodo, caminho, timeout=timeout or self._timeout, **kwargs)
        except httpx.HTTPError as erro:
            raise HTTPException(503, "O módulo 3 está fora do ar. Confira se o serviço está rodando.") from erro
        if resp.status_code == 404:
            raise HTTPException(404, _detalhe(resp) or "Não encontrado no módulo 3.")
        if resp.status_code >= 400:
            raise HTTPException(502, _detalhe(resp) or f"O módulo 3 respondeu com erro {resp.status_code}.")
        return resp.json()

    def saude(self, forcar: bool = False) -> dict:
        if not forcar and self._saude_cache and time.monotonic() - self._saude_cache[0] < 60:
            return self._saude_cache[1]
        dados = self._chamar("GET", "/saude")
        self._saude_cache = (time.monotonic(), dados)
        return dados

    def simulado(self) -> bool:
        try:
            return self.saude().get("fonte") == "simulado"
        except HTTPException:
            return False

    def perfis(self) -> list[dict]:
        return self._chamar("GET", "/v1/perfis")

    def buscar(self, filtro: dict) -> dict:
        return self._chamar("POST", "/v1/leads/busca", json=filtro)

    def lead(self, cnpj: str) -> dict:
        return self._chamar("GET", f"/v1/leads/{cnpj}")

    def perguntar(self, pergunta: str, cnpj: str | None, contexto: dict | None = None) -> dict:
        """`contexto` leva os dados de um lead que o módulo 3 não conhece (cadastro manual)."""
        corpo: dict = {"pergunta": pergunta, "cnpj": cnpj}
        if contexto is not None:
            corpo["contexto"] = contexto
        return self._chamar("POST", "/v1/perguntar", timeout=self._agente_timeout, json=corpo)


def _detalhe(resp: httpx.Response) -> str | None:
    try:
        detalhe = resp.json().get("detail")
        return detalhe if isinstance(detalhe, str) else None
    except ValueError:
        return None
