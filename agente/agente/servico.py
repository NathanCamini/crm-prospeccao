"""Orquestra uma classificação: contrato → funil SQL → sinais → LLM (triagem e revisão) → tabela.

Modo `lote` (padrão da classificação mensal) é retomável: cada etapa vira um lote registrado no
banco antes do envio; rodar o comando de novo consulta a API, processa o que terminou e segue para
a próxima etapa. Com `aguardar`, o próprio comando espera até o fim (útil no agendador).
Modo `online` faz as chamadas uma a uma, para execuções pequenas e testes do perfil.

Só é reclassificado o que mudou: a chave é CNPJ × perfil × versão, e uma linha existente é refeita
apenas se os dados da empresa (entrada_hash) ou o prompt (prompt_hash) mudaram — ou com `forcar`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from agente.armazenamento import ClassificacaoStore, Lote, Registro
from agente.config import AgenteConfig
from agente.contrato import ArquivoDoMes, validar_mes
from agente.funil import candidatos, descricoes_cnae
from agente.llm import RespostaLLM, custo_usd
from agente.perfil import Perfil, carregar_perfil
from agente.prompt import entrada_empresa, hash_entrada, hash_prompt, instrucao_sistema, texto_entrada
from agente.resultado import ResultadoLLM, aplicar_regras, schema_saida
from agente.sinais import DetectorSinais, Rede

log = logging.getLogger("agente")
MODOS = ("lote", "online")
GRAVAR_A_CADA = 50  # modo online: grava em blocos, para uma queda não perder o que já foi pago


class ErroLLMPermanente(RuntimeError):
    """Chave inválida, sem permissão ou modelo inexistente: repetir em cada empresa só gastaria tempo."""


def _erro_permanente(exc: Exception) -> bool:
    from google.genai import errors

    return isinstance(exc, errors.ClientError) and (
        exc.code in (401, 403, 404) or "API_KEY_INVALID" in str(exc) or "API key not valid" in str(exc))


@dataclass
class Resumo:
    perfil_id: str
    perfil_versao: int
    mes: str
    modo: str
    candidatos: int = 0
    inalterados: int = 0
    enviados_triagem: int = 0
    enviados_revisao: int = 0
    gravados: int = 0
    alvos: int = 0
    custo_usd: float = 0.0
    falhas: list[str] = field(default_factory=list)
    lote_pendente: str | None = None

    def linhas(self) -> list[str]:
        saida = [
            f"Perfil {self.perfil_id} v{self.perfil_versao} · mês {self.mes} · modo {self.modo}",
            f"  candidatos no funil SQL: {self.candidatos} (sem mudança desde a última classificação: {self.inalterados})",
            f"  enviados ao LLM: triagem {self.enviados_triagem}, revisão {self.enviados_revisao}",
            f"  gravados nesta execução: {self.gravados} (alvos: {self.alvos}) · custo estimado US$ {self.custo_usd:.4f}",
        ]
        if self.lote_pendente:
            saida.append(f"  aguardando: {self.lote_pendente}. Rode o mesmo comando mais tarde para continuar.")
        saida += [f"  falha: {f}" for f in self.falhas[:20]]
        if len(self.falhas) > 20:
            saida.append(f"  ... e mais {len(self.falhas) - 20} falhas")
        return saida


class Classificador:
    def __init__(self, cfg: AgenteConfig, store: ClassificacaoStore, cliente: Any,
                 redes: list[Rede] | None = None,
                 buscar_socios: Callable[[list[str]], dict[str, list[str]]] | None = None,
                 dormir: Callable[[float], None] = time.sleep, relogio: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.store = store
        self.cliente = cliente
        self.redes = redes or []
        self.buscar_socios = buscar_socios
        self.dormir = dormir
        self.relogio = relogio

    # ---------------------------------------------------------------- preparação (sem LLM)
    def preparar(self, mes: str, perfil: Perfil, limite: int | None = None) -> tuple[ArquivoDoMes, list[dict[str, Any]]]:
        arquivo = validar_mes(self.cfg.novas_dir, mes, self.cfg.schema_dir)
        empresas = candidatos(arquivo, perfil, limite)
        descricoes = descricoes_cnae(arquivo)
        socios = self.buscar_socios([e["cnpj_basico"] for e in empresas]) if self.buscar_socios and empresas else {}
        detector = DetectorSinais(self.redes, socios)
        itens = []
        for empresa in empresas:
            entrada = entrada_empresa(empresa, detector.sinais(empresa), descricoes)
            itens.append({"cnpj": empresa["cnpj"], "entrada": entrada, "entrada_hash": hash_entrada(entrada)})
        return arquivo, itens

    # ---------------------------------------------------------------- execução
    def executar(self, mes: str, perfil_id: str, modo: str = "lote", forcar: bool = False, limite: int | None = None,
                 aguardar: bool = False, intervalo_s: float = 60, espera_maxima_s: float = 26 * 3600,
                 amostra: int | None = None) -> Resumo:
        """`limite` é a trava de custo do funil; `amostra` classifica só as N primeiras pendentes (teste do perfil)."""
        if modo not in MODOS:
            raise ValueError(f"Modo inválido: {modo}. Use: {', '.join(MODOS)}")
        if amostra is not None and amostra < 1:
            raise ValueError("amostra precisa ser pelo menos 1")
        perfil = carregar_perfil(self.cfg.perfis_dir, perfil_id)
        resumo = Resumo(perfil.id, perfil.versao, mes, modo)
        self.store.encerrar_lotes_de_versoes_antigas(perfil.id, perfil.versao, mes)

        if self.store.lote_aberto(perfil.id, perfil.versao, mes) is None:
            _, itens = self.preparar(mes, perfil, limite)
            prompt_hash = hash_prompt(perfil)
            resumo.candidatos = len(itens)
            existentes = self.store.existentes(perfil.id, perfil.versao, [i["cnpj"] for i in itens])
            pendentes = [i for i in itens
                         if forcar or existentes.get(i["cnpj"]) != (i["entrada_hash"], prompt_hash)]
            resumo.inalterados = len(itens) - len(pendentes)
            pendentes = pendentes[:amostra] if amostra else pendentes
            if not pendentes:
                return resumo
            if modo == "online":
                self._online(perfil, mes, prompt_hash, pendentes, resumo)
                return resumo
            lote_id = self.store.criar_lote(perfil.id, perfil.versao, mes, "triagem", self.cfg.modelo_triagem,
                                            prompt_hash, pendentes)
            resumo.enviados_triagem += len(pendentes)
            self._submeter(perfil, lote_id, "triagem", self.cfg.modelo_triagem, pendentes, resumo)
        elif modo == "online":
            log.warning("Há um lote aberto para %s v%d em %s: continuando o lote em vez do modo online.",
                        perfil.id, perfil.versao, mes)

        inicio = self.relogio()
        while (lote := self.store.lote_aberto(perfil.id, perfil.versao, mes)) is not None:
            if self._continuar(perfil, lote, resumo):
                continue
            if not aguardar or self.relogio() - inicio > espera_maxima_s:
                resumo.lote_pendente = f"lote {lote.id} ({lote.etapa}, {lote.total} empresas, {lote.modelo})"
                break
            self.dormir(intervalo_s)
        return resumo

    # ---------------------------------------------------------------- lote
    def _submeter(self, perfil: Perfil, lote_id: int, etapa: str, modelo: str, itens: list[dict[str, Any]],
                  resumo: Resumo) -> bool:
        """Envia o lote; numa falha ele fica 'criando' e é reenviado na próxima execução."""
        nome = f"{perfil.id}-v{perfil.versao}-{resumo.mes}-{etapa}-{lote_id}"
        try:
            job = self.cliente.enviar_lote(modelo, instrucao_sistema(perfil), schema_saida(perfil),
                                           [(i["cnpj"], texto_entrada(i["entrada"])) for i in itens], nome)
        except Exception as exc:  # noqa: BLE001 — rede/API: tenta de novo na próxima execução
            resumo.falhas.append(f"envio do lote {lote_id} ({etapa}): {exc}")
            return False
        self.store.marcar_lote(lote_id, "enviado", job=job)
        return True

    def _continuar(self, perfil: Perfil, lote: Lote, resumo: Resumo) -> bool:
        """Avança um lote aberto. True = o lote terminou (processado, falhou ou expirou)."""
        if lote.estado == "criando":
            self._submeter(perfil, lote.id, lote.etapa, lote.modelo, self.store.itens_lote(lote.id), resumo)
            return False
        estado = self.cliente.consultar_lote(lote.job)
        if estado.situacao == "pendente":
            return False
        if estado.situacao in ("falhou", "expirado"):
            self.store.marcar_lote(lote.id, estado.situacao, detalhe=estado.detalhe)
            resumo.falhas.append(f"lote {lote.id} ({lote.etapa}) {estado.situacao}: {estado.detalhe}. "
                                 "As empresas serão reenviadas na próxima execução.")
            return True

        respostas = self.cliente.resultados_lote(lote.job)
        registros: list[Registro] = []
        revisar: list[dict[str, Any]] = []
        for item in self.store.itens_lote(lote.id):
            etapa = self._interpretar(perfil, item, respostas.get(item["cnpj"], "sem resposta no resultado do lote"),
                                      lote.modelo, em_lote=True)
            self._decidir(perfil, resumo.mes, lote.prompt_hash, item, etapa, lote.etapa, registros, revisar, resumo)

        with self.store.conexao() as con:  # resultados + fim do lote + próximo lote: tudo ou nada
            self.store.gravar(registros, con=con)
            self.store.marcar_lote(lote.id, "processado", detalhe=f"{len(registros)} gravados, {len(revisar)} para revisão",
                                   con=con)
            novo_id = self.store.criar_lote(perfil.id, perfil.versao, resumo.mes, "revisao", self.cfg.modelo_revisao,
                                            lote.prompt_hash, revisar, con=con) if revisar else None
        self._contabilizar(registros, resumo)
        if novo_id is not None:
            resumo.enviados_revisao += len(revisar)
            self._submeter(perfil, novo_id, "revisao", self.cfg.modelo_revisao, revisar, resumo)
        return True

    # ---------------------------------------------------------------- online
    def _online(self, perfil: Perfil, mes: str, prompt_hash: str, itens: list[dict[str, Any]], resumo: Resumo) -> None:
        registros: list[Registro] = []
        try:
            for item in itens:
                resumo.enviados_triagem += 1
                triagem = self._chamar(perfil, item, self.cfg.modelo_triagem)
                revisar: list[dict[str, Any]] = []
                self._decidir(perfil, mes, prompt_hash, item, triagem, "triagem", registros, revisar, resumo)
                for pendente in revisar:
                    resumo.enviados_revisao += 1
                    revisao = self._chamar(perfil, pendente, self.cfg.modelo_revisao)
                    self._decidir(perfil, mes, prompt_hash, pendente, revisao, "revisao", registros, [], resumo)
                if len(registros) >= GRAVAR_A_CADA:
                    self.store.gravar(registros)
                    self._contabilizar(registros, resumo)
                    registros = []
        finally:  # também num erro permanente: o que já foi pago fica gravado
            self.store.gravar(registros)
            self._contabilizar(registros, resumo)

    def _chamar(self, perfil: Perfil, item: dict[str, Any], modelo: str) -> dict[str, Any]:
        try:
            resposta: RespostaLLM | str = self.cliente.gerar(modelo, instrucao_sistema(perfil), texto_entrada(item["entrada"]),
                                                             schema_saida(perfil))
        except Exception as exc:  # noqa: BLE001 — falha de rede/API vira falha do item, não da execução
            if _erro_permanente(exc):
                raise ErroLLMPermanente(
                    f"O LLM recusou a chamada ({str(exc)[:200]}). Confira GEMINI_API_KEY e os modelos configurados "
                    f"({self.cfg.modelo_triagem}, {self.cfg.modelo_revisao}); a execução parou para não repetir o erro "
                    "em todas as empresas.") from exc
            resposta = f"chamada ao LLM falhou: {exc}"
        return self._interpretar(perfil, item, resposta, modelo, em_lote=False)

    # ---------------------------------------------------------------- regras comuns
    def _interpretar(self, perfil: Perfil, item: dict[str, Any], resposta: RespostaLLM | str, modelo: str,
                     em_lote: bool) -> dict[str, Any]:
        """Resposta do LLM → resultado validado e corrigido pelas regras (ou o erro, com o custo já gasto)."""
        if isinstance(resposta, str):
            return {"modelo": modelo, "erro": resposta, "tokens": 0, "custo_usd": 0.0}
        tokens = resposta.tokens_entrada + resposta.tokens_saida
        custo = custo_usd(modelo, resposta.tokens_entrada, resposta.tokens_saida, em_lote)
        try:
            bruto = ResultadoLLM.model_validate_json(resposta.texto)
        except ValidationError as exc:
            primeiro = exc.errors()[0]
            return {"modelo": modelo, "erro": f"resposta fora do contrato: {primeiro.get('loc')} {primeiro.get('msg')}",
                    "tokens": tokens, "custo_usd": custo}
        avaliacao = aplicar_regras(bruto, perfil, item["entrada"]["sinais"])
        return {"modelo": modelo, "bruto": bruto.model_dump(), "final": avaliacao.final, "avisos": list(avaliacao.avisos),
                "motivo_revisao": avaliacao.motivo_revisao, "tokens": tokens, "custo_usd": custo}

    def _decidir(self, perfil: Perfil, mes: str, prompt_hash: str, item: dict[str, Any], resultado: dict[str, Any],
                 etapa: str, registros: list[Registro], revisar: list[dict[str, Any]], resumo: Resumo) -> None:
        """Triagem: grava, ou manda para revisão (score alto, dúvida ou erro). Revisão: grava o resultado final."""
        if etapa == "triagem":
            if "erro" not in resultado and resultado["motivo_revisao"] is None:
                registros.append(self._registro(perfil, mes, prompt_hash, item, resultado, None))
            else:
                motivo = resultado.get("motivo_revisao") or "falha na triagem"
                revisar.append({**item, "triagem": {**resultado, "motivo_revisao": motivo}})
            return
        triagem = item.get("triagem") or {}
        if "erro" not in resultado:
            registros.append(self._registro(perfil, mes, prompt_hash, item, resultado, triagem))
        elif "final" in triagem:  # revisão falhou: fica a triagem, marcada como não confirmada
            triagem = {**triagem, "avisos": [*triagem.get("avisos", []), f"revisão falhou: {resultado['erro']}"]}
            registros.append(self._registro(perfil, mes, prompt_hash, item, triagem, None, revisao_falhou=resultado))
        else:
            resumo.falhas.append(f"{item['cnpj']}: {triagem.get('erro', 'triagem sem resultado')} / {resultado['erro']}")

    def _registro(self, perfil: Perfil, mes: str, prompt_hash: str, item: dict[str, Any], final: dict[str, Any],
                  triagem: dict[str, Any] | None, revisao_falhou: dict[str, Any] | None = None) -> Registro:
        etapas = [e for e in (triagem, revisao_falhou) if e]
        custos = [final.get("custo_usd"), *(e.get("custo_usd") for e in etapas)]
        detalhes = {"avisos": final.get("avisos", []), "motivo_revisao": (triagem or final).get("motivo_revisao"),
                    "bruto": final.get("bruto")}
        if triagem:
            detalhes["triagem"] = {k: triagem.get(k) for k in ("modelo", "bruto", "avisos", "erro")}
        if revisao_falhou:
            detalhes["revisao_falhou"] = revisao_falhou.get("erro")
        return Registro(
            cnpj=item["cnpj"], perfil_id=perfil.id, perfil_versao=perfil.versao, mes_referencia=mes,
            modelo=final["modelo"], prompt_hash=prompt_hash, entrada_hash=item["entrada_hash"], final=final["final"],
            detalhes=detalhes, revisado=triagem is not None,
            custo_tokens=final.get("tokens", 0) + sum(e.get("tokens", 0) for e in etapas),
            custo_usd=None if any(c is None for c in custos) else round(sum(custos), 6),
        )

    @staticmethod
    def _contabilizar(registros: list[Registro], resumo: Resumo) -> None:
        resumo.gravados += len(registros)
        resumo.alvos += sum(1 for r in registros if r.final["e_alvo"])
        resumo.custo_usd += sum(r.custo_usd or 0.0 for r in registros)
