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
        if resp.status_code in (422, 503):  # descrição vaga, teto de custo, agente sem chave: o motivo vai à tela
            raise HTTPException(resp.status_code, _detalhe(resp) or f"O módulo 3 respondeu com erro {resp.status_code}.")
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

    def perguntar(self, pergunta: str, cnpj: str | None, contexto: dict | None = None,
                  perfis: list[str] | None = None) -> dict:
        """`contexto`: o lead como a empresa o vê. `perfis`: os da empresa (a pergunta geral só vê os leads deles)."""
        corpo: dict = {"pergunta": pergunta, "cnpj": cnpj, "perfis": perfis}
        if contexto is not None:
            corpo["contexto"] = contexto
        return self._chamar("POST", "/v1/perguntar", timeout=self._agente_timeout, json=corpo)

    # Produtos das empresas clientes: o módulo 3 repassa ao agente, que monta e classifica os perfis.
    def produtos(self, dono: int) -> list[dict]:
        return self._chamar("GET", "/v1/produtos", params={"dono": str(dono)})

    def sugerir_produto(self, dono: int, descricao: str, perfil_id: str | None) -> dict:
        return self._chamar("POST", "/v1/produtos/sugerir", timeout=self._agente_timeout,
                            json={"dono": str(dono), "descricao": descricao, "perfil_id": perfil_id})

    def salvar_produto(self, dono: int, descricao: str, perfil: dict, perfil_id: str | None) -> dict:
        return self._chamar("POST", "/v1/produtos", timeout=self._agente_timeout,
                            json={"dono": str(dono), "descricao": descricao, "perfil": perfil, "perfil_id": perfil_id})

    def remover_produto(self, dono: int, perfil_id: str) -> dict:
        return self._chamar("DELETE", f"/v1/produtos/{perfil_id}", params={"dono": str(dono)})


def _detalhe(resp: httpx.Response) -> str | None:
    try:
        detalhe = resp.json().get("detail")
        return detalhe if isinstance(detalhe, str) else None
    except ValueError:
        return None
