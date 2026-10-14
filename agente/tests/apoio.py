"""Apoio aos testes do agente: arquivo do mês no formato do contrato e um LLM falso."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from agente.config import AgenteConfig
from agente.llm import EstadoLote, RespostaLLM

RAIZ = Path(__file__).resolve().parents[1]
MES = "2026-09"

_TIPOS = {
    "cnpj": "VARCHAR", "cnpj_basico": "VARCHAR", "razao_social": "VARCHAR", "nome_fantasia": "VARCHAR",
    "matriz_filial": "VARCHAR", "situacao_cadastral": "VARCHAR", "data_inicio_atividade": "DATE",
    "cnae_principal": "VARCHAR", "cnae_principal_descricao": "VARCHAR", "cnaes_secundarios": "VARCHAR[]",
    "natureza_juridica": "VARCHAR", "porte": "VARCHAR", "capital_social": "DECIMAL(18,2)", "opcao_simples": "BOOLEAN",
    "opcao_mei": "BOOLEAN", "tipo_logradouro": "VARCHAR", "logradouro": "VARCHAR", "numero": "VARCHAR",
    "complemento": "VARCHAR", "bairro": "VARCHAR", "cep": "VARCHAR", "uf": "VARCHAR", "municipio": "VARCHAR",
    "ddd_receita_1": "VARCHAR", "telefone_receita_1": "VARCHAR", "ddd_receita_2": "VARCHAR",
    "telefone_receita_2": "VARCHAR", "email_receita": "VARCHAR", "mes_referencia_base": "VARCHAR",
}
LTDA = "Sociedade Empresária Limitada"


def empresa(cnpj: str, nome_fantasia: str | None, cnae: str, descricao: str, capital: float, **extra: Any) -> dict[str, Any]:
    base = {
        "cnpj": cnpj, "cnpj_basico": cnpj[:8], "razao_social": f"{(nome_fantasia or 'EMPRESA')} LTDA",
        "nome_fantasia": nome_fantasia, "matriz_filial": "MATRIZ", "situacao_cadastral": "ATIVA",
        "data_inicio_atividade": date(2026, 9, 1), "cnae_principal": cnae, "cnae_principal_descricao": descricao,
        "cnaes_secundarios": [], "natureza_juridica": LTDA, "porte": "DEMAIS", "capital_social": capital,
        "opcao_simples": False, "opcao_mei": False, "tipo_logradouro": "RUA", "logradouro": "DAS FLORES",
        "numero": "10", "complemento": None, "bairro": "CENTRO", "cep": "88000000", "uf": "SC",
        "municipio": "FLORIANOPOLIS", "ddd_receita_1": "48", "telefone_receita_1": "33334444", "ddd_receita_2": None,
        "telefone_receita_2": None, "email_receita": f"contato@empresa{cnpj[:2]}.com.br", "mes_referencia_base": MES,
    }
    return base | extra


# A: pousada alvo · B: hotel MEI · C: restaurante · D: hotel de capital baixo · E: ibis (rede Accor)
# F: pousada baixada · G: chalés de empresário individual (CPF na razão social) · H: restaurante com hotel secundário
# I: espaço de eventos com CNAE de alojamento (passa no funil, mas não é alvo)
A, B, C, D, E, F, G, H, I = (f"{i:02d}111222000100"[:14] for i in range(1, 10))
EMPRESAS_PADRAO = [
    empresa(A, "POUSADA MAR AZUL", "5510801", "Hotéis", 500_000),
    empresa(B, "HOTELZINHO DO ZE", "5510801", "Hotéis", 5_000, porte="MEI", opcao_mei=True),
    empresa(C, "CANTINA BELLA", "5611201", "Restaurantes e similares", 1_000_000),
    empresa(D, "HOTEL PEQUENO", "5510801", "Hotéis", 50_000),
    empresa(E, "IBIS BUDGET FLORIPA", "5510801", "Hotéis", 2_000_000, matriz_filial="FILIAL",
            email_receita="reservas.floripa@accor.com"),
    empresa(F, "POUSADA FECHADA", "5590699", "Outros alojamentos não especificados anteriormente", 200_000,
            situacao_cadastral="BAIXADA"),
    empresa(G, "CHALES DA SERRA", "5590699", "Outros alojamentos não especificados anteriormente", 300_000,
            razao_social="JOAO DA SILVA 123.456.789-01", natureza_juridica="Empresário (Individual)"),
    empresa(H, "RESTAURANTE E HOSPEDAGEM", "5611201", "Restaurantes e similares", 400_000,
            cnaes_secundarios=["5510801"]),
    empresa(I, "ESPACO EVENTOS SOL", "5590699", "Outros alojamentos não especificados anteriormente", 150_000),
]
NO_FUNIL = {A, E, G, I}


def escrever_mes(novas_dir: Path, linhas: list[dict[str, Any]], mes: str = MES, versao: int = 1,
                 extras: dict[str, str] | None = None, remover: tuple[str, ...] = (),
                 manifesto: dict[str, Any] | None = None) -> Path:
    """Grava empresas_novas.parquet + _manifest.json do mês (com os tipos do contrato)."""
    pasta = novas_dir / mes
    pasta.mkdir(parents=True, exist_ok=True)
    tipos = {k: v for k, v in (_TIPOS | (extras or {})).items() if k not in remover}
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({', '.join(f'{c} {t}' for c, t in tipos.items())})")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(tipos))})",
                    [[linha.get(c) for c in tipos] for linha in linhas])
    parquet = pasta / "empresas_novas.parquet"
    con.execute(f"COPY t TO '{parquet.as_posix()}' (FORMAT parquet)")
    con.close()
    dados = {"mes": mes, "total_linhas": len(linhas), "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
             "versao_schema": versao} | (manifesto or {})
    (pasta / "_manifest.json").write_text(json.dumps(dados), encoding="utf-8")
    return parquet


def montar_cfg(tmp: Path, perfil_yaml: str | None = None) -> AgenteConfig:
    """Config isolada: dados em tmp, contrato do repositório, perfis copiados (e um perfil extra opcional)."""
    perfis = tmp / "perfis"
    shutil.copytree(RAIZ / "perfis", perfis, dirs_exist_ok=True)
    if perfil_yaml:
        (perfis / "teste_hotel.yaml").write_text(perfil_yaml, encoding="utf-8")
    return AgenteConfig(novas_dir=tmp / "modulo1" / "exportacoes" / "novas", dados_dir=tmp / "dados",
                        saida_dir=tmp / "saida", perfis_dir=perfis, schema_dir=RAIZ / "schema",
                        modelo_triagem="gemini-2.5-flash-lite", modelo_revisao="gemini-3.8-flash")


def com_modelos(cfg: AgenteConfig, triagem: str, revisao: str) -> AgenteConfig:
    return replace(cfg, modelo_triagem=triagem, modelo_revisao=revisao)


PERFIL_TESTE = """
id: teste_hotel
versao: 1
produto: "Kit de amenities"
filtro_sql:
  cnae_prefixos: ["5510", "5590"]
  situacao: [ATIVA]
  capital_social_min: 100000
  excluir_mei: true
criterios_llm:
  - "É hospedagem com quartos?"
  - "É franquia ou bandeira de rede? Qual?"
produtos_sugeriveis: ["Kit econômico", "Kit completo"]
limiar_alvo: 60
revisao:
  score_min: 60
  confianca_baixa: true
"""


def ler_entrada(texto: str) -> dict[str, Any]:
    """O JSON da empresa dentro do texto enviado ao modelo."""
    return json.loads(texto.split("\n", 1)[1])


Resposta = dict[str, Any] | str | Exception
Responder = Callable[[str, dict[str, Any]], Resposta]


def resposta_padrao(modelo: str, entrada: dict[str, Any]) -> dict[str, Any]:
    """Pousada e chalés: alvo; rede: alvo com marca; o resto: não alvo. A revisão confirma com confiança alta."""
    nome = entrada.get("nome_fantasia") or ""
    redes = entrada["sinais"]["redes"]
    if redes:
        return {"e_alvo": True, "score": 85, "justificativa": "Unidade de rede com hospedagem.", "e_franquia": True,
                "marca": redes[0]["marca"] or redes[0]["rede"], "padrao": "medio", "produto_sugerido": "Kit completo",
                "confianca": "alta" if "flash-lite" not in modelo else "media"}
    if "POUSADA" in nome or "CHALES" in nome:
        return {"e_alvo": True, "score": 72, "justificativa": "Pousada com quartos.", "e_franquia": None, "marca": None,
                "padrao": "medio", "produto_sugerido": "Kit econômico", "confianca": "alta"}
    return {"e_alvo": False, "score": 20, "justificativa": "Não parece hospedagem.", "e_franquia": None, "marca": None,
            "padrao": "indefinido", "produto_sugerido": None, "confianca": "alta"}


class ClienteFalso:
    """Imita ClienteGemini: `gerar` (online) e lote (enviar → pendente → concluído pelo teste)."""

    def __init__(self, responder: Responder = resposta_padrao) -> None:
        self.responder = responder
        self.chamadas: list[tuple[str, str, str]] = []   # (modo, modelo, nome_fantasia)
        self.lotes: dict[str, dict[str, Any]] = {}
        self.falhar_envio = 0

    def _responder(self, modelo: str, texto: str) -> RespostaLLM | str:
        resposta = self.responder(modelo, ler_entrada(texto))
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, str):
            return RespostaLLM(resposta, 300, 40)
        return RespostaLLM(json.dumps(resposta, ensure_ascii=False), 300, 40)

    def gerar(self, modelo: str, sistema: str, entrada: str, schema: dict[str, Any]) -> RespostaLLM:
        self.chamadas.append(("online", modelo, ler_entrada(entrada).get("nome_fantasia") or ""))
        return self._responder(modelo, entrada)

    def enviar_lote(self, modelo: str, sistema: str, schema: dict[str, Any], itens: list[tuple[str, str]],
                    nome: str) -> str:
        if self.falhar_envio:
            self.falhar_envio -= 1
            raise ConnectionError("rede fora do ar")
        job = f"batches/{len(self.lotes) + 1}"
        self.lotes[job] = {"modelo": modelo, "itens": itens, "estado": EstadoLote("pendente", "JOB_STATE_PENDING"),
                           "nome": nome}
        for _, texto in itens:
            self.chamadas.append(("lote", modelo, ler_entrada(texto).get("nome_fantasia") or ""))
        return job

    def concluir(self, job: str | None = None, situacao: str = "concluido") -> None:
        alvo = job or list(self.lotes)[-1]
        self.lotes[alvo]["estado"] = EstadoLote(situacao, f"JOB_STATE_{situacao.upper()}")

    def consultar_lote(self, job: str) -> EstadoLote:
        return self.lotes[job]["estado"]

    def resultados_lote(self, job: str) -> dict[str, RespostaLLM | str]:
        lote = self.lotes[job]
        resultados: dict[str, RespostaLLM | str] = {}
        for chave, texto in lote["itens"]:
            try:
                resultados[chave] = self._responder(lote["modelo"], texto)
            except Exception as exc:  # noqa: BLE001
                resultados[chave] = f"erro no lote: {exc}"
        return resultados
