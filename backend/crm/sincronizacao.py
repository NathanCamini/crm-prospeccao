"""Cópia local dos leads do módulo 3.

O CRM guarda o que recebe do módulo 3 para:
- continuar mostrando leads já trabalhados quando eles saem da base do mês;
- filtrar, ordenar e paginar em SQL (rápido, sem mandar listas de CNPJ ao módulo 3);
- ter relatórios e cadastro manual na mesma tabela.

Lead do módulo 3 que deixou de vir é marcado com `ausente_desde`: some da lista
para quem nunca o trabalhou e continua para quem já tem histórico com ele.
"""

from __future__ import annotations

import json
import logging
import threading
import unicodedata

from fastapi import HTTPException

from .config import Config
from .db import agora, conectar
from .modulo3 import Modulo3

log = logging.getLogger("crm.sincronizacao")
POR_PAGINA = 200


def normalizar_busca(*partes: str | None) -> str:
    texto = " ".join(p for p in partes if p)
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def sem_acento_maiusculo(texto: str | None) -> str | None:
    if not texto:
        return None
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper().strip() or None


def colunas_do_lead(lead: dict) -> dict:
    c = lead.get("classificacao") or {}
    nome = lead.get("nome_fantasia") or lead.get("razao_social") or lead.get("cnpj") or "Sem nome"
    return {
        "cnpj": lead.get("cnpj"),
        "nome": nome,
        "municipio": sem_acento_maiusculo(lead.get("municipio")),
        "uf": (lead.get("uf") or "").upper() or None,
        "bairro": lead.get("bairro"),
        "perfil_id": c.get("perfil_id"),
        "score": c.get("score"),
        "e_alvo": 1 if c.get("e_alvo", True) else 0,
        "data_abertura": lead.get("data_inicio_atividade"),
        "busca": normalizar_busca(lead.get("razao_social"), lead.get("nome_fantasia"), lead.get("cnpj"),
                                  lead.get("municipio"), lead.get("bairro")),
        "dados": json.dumps(lead, ensure_ascii=False, sort_keys=True),
        "mes_referencia": lead.get("mes_referencia"),
    }


def classificacoes(lead: dict) -> list[dict]:
    """Todas as classificações do lead, uma por perfil (contrato v1: `classificacoes`, ou só `classificacao`)."""
    todas = lead.get("classificacoes") or ([lead["classificacao"]] if lead.get("classificacao") else [])
    return [c for c in todas if c.get("perfil_id")]


def _gravar_perfis(conn, chave: str, lead: dict) -> None:
    conn.execute("DELETE FROM lead_perfil WHERE chave = ?", (chave,))
    conn.executemany(
        "INSERT OR REPLACE INTO lead_perfil (chave, perfil_id, score, e_alvo, classificacao) VALUES (?, ?, ?, ?, ?)",
        [(chave, c["perfil_id"], c.get("score"), 1 if c.get("e_alvo", True) else 0, json.dumps(c, ensure_ascii=False))
         for c in classificacoes(lead)])


def gravar_do_modulo3(conn, lead: dict, fonte: str | None) -> str:
    """Insere ou atualiza um lead vindo do módulo 3. Devolve 'novo', 'atualizado' ou 'igual'."""
    col = colunas_do_lead(lead)
    if any(c.get("e_alvo", True) for c in classificacoes(lead)):
        col["e_alvo"] = 1  # alvo de algum perfil; cada empresa confere o dos perfis dela em lead_perfil
    atual = conn.execute("SELECT dados, ausente_desde FROM lead WHERE chave = ?", (lead["cnpj"],)).fetchone()
    if atual is None:
        conn.execute(
            """INSERT INTO lead (chave, origem, empresa_id, cnpj, nome, municipio, uf, bairro, perfil_id, score, e_alvo,
                                 data_abertura, busca, dados, fonte, mes_referencia, sincronizado_em, criado_em, criado_por)
               VALUES (:chave, 'modulo3', NULL, :cnpj, :nome, :municipio, :uf, :bairro, :perfil_id, :score, :e_alvo,
                       :data_abertura, :busca, :dados, :fonte, :mes_referencia, :agora, :agora, 'modulo3')""",
            col | {"chave": lead["cnpj"], "fonte": fonte, "agora": agora()},
        )
        _gravar_perfis(conn, lead["cnpj"], lead)
        return "novo"
    if atual["dados"] == col["dados"] and atual["ausente_desde"] is None:
        conn.execute("UPDATE lead SET sincronizado_em = ?, fonte = ? WHERE chave = ?", (agora(), fonte, lead["cnpj"]))
        return "igual"
    conn.execute(
        """UPDATE lead SET cnpj = :cnpj, nome = :nome, municipio = :municipio, uf = :uf, bairro = :bairro,
                  perfil_id = :perfil_id, score = :score, e_alvo = :e_alvo, data_abertura = :data_abertura,
                  busca = :busca, dados = :dados, fonte = :fonte, mes_referencia = :mes_referencia,
                  sincronizado_em = :agora, ausente_desde = NULL
           WHERE chave = :chave""",
        col | {"chave": lead["cnpj"], "fonte": fonte, "agora": agora()},
    )
    _gravar_perfis(conn, lead["cnpj"], lead)
    return "atualizado"


def sincronizar(cfg: Config, m3: Modulo3) -> dict:
    """Copia todos os leads do módulo 3. Lento só na primeira vez; depois atualiza o que mudou."""
    from .auditoria import registrar_sistema

    conn = conectar(cfg.db_path)
    inicio = agora()
    sid = conn.execute("INSERT INTO sincronizacao (inicio) VALUES (?)", (inicio,)).lastrowid
    conn.commit()
    contagem = {"lidos": 0, "novos": 0, "atualizados": 0, "ausentes": 0}
    fonte = None
    try:
        vistos: set[str] = set()
        pagina = 1
        while True:
            resposta = m3.buscar({"so_alvos": False, "ordenar": "nome", "pagina": pagina, "por_pagina": POR_PAGINA})
            fonte = resposta.get("fonte")
            for lead in resposta["itens"]:
                vistos.add(lead["cnpj"])
                r = gravar_do_modulo3(conn, lead, fonte)
                contagem["lidos"] += 1
                if r == "novo":
                    contagem["novos"] += 1
                elif r == "atualizado":
                    contagem["atualizados"] += 1
            conn.commit()
            if pagina * POR_PAGINA >= resposta["total"] or not resposta["itens"]:
                break
            pagina += 1
        presentes = {r["chave"] for r in conn.execute("SELECT chave FROM lead WHERE origem = 'modulo3' AND ausente_desde IS NULL")}
        sumiram = presentes - vistos
        for chave in sumiram:
            conn.execute("UPDATE lead SET ausente_desde = ? WHERE chave = ?", (agora(), chave))
        contagem["ausentes"] = len(sumiram)
        conn.execute(
            "UPDATE sincronizacao SET fim = ?, lidos = ?, novos = ?, atualizados = ?, ausentes = ?, fonte = ? WHERE id = ?",
            (agora(), contagem["lidos"], contagem["novos"], contagem["atualizados"], contagem["ausentes"], fonte, sid),
        )
        conn.commit()
        registrar_sistema(cfg.db_path, "sincronizacao.concluida", detalhe=contagem | {"fonte": fonte})
        return contagem | {"fonte": fonte}
    except HTTPException as erro:
        conn.rollback()
        conn.execute("UPDATE sincronizacao SET fim = ?, erro = ? WHERE id = ?", (agora(), str(erro.detail), sid))
        conn.commit()
        registrar_sistema(cfg.db_path, "sincronizacao.falhou", detalhe={"erro": erro.detail}, resultado="erro")
        raise
    finally:
        conn.close()


_SINCRONIZANDO = threading.Lock()


def sincronizar_em_segundo_plano(cfg: Config, m3: Modulo3) -> bool:
    """Copia agora, numa thread, sem segurar a requisição. Se já houver uma cópia rodando, não começa outra."""
    if not _SINCRONIZANDO.acquire(blocking=False):
        return False

    def rodar() -> None:
        try:
            sincronizar(cfg, m3)
        except HTTPException as erro:
            log.warning("Sincronização com o módulo 3 falhou: %s", erro.detail)
        except Exception:  # noqa: BLE001 — a thread não pode derrubar nada
            log.exception("Erro inesperado na sincronização")
        finally:
            _SINCRONIZANDO.release()

    threading.Thread(target=rodar, name="sincronizacao-produto", daemon=True).start()
    return True


def sincronizar_periodicamente(cfg: Config, m3: Modulo3, parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            r = sincronizar(cfg, m3)
            log.info("Sincronização com o módulo 3: %s", r)
        except HTTPException as erro:
            log.warning("Sincronização com o módulo 3 falhou: %s", erro.detail)
        except Exception:  # noqa: BLE001 — a thread não pode morrer
            log.exception("Erro inesperado na sincronização")
        parar.wait(cfg.sincronizar_minutos * 60)


def buscar_e_gravar(conn, m3: Modulo3, chave: str) -> bool:
    """Lead que ainda não chegou pela sincronização: busca direto e guarda na mesma conexão."""
    try:
        lead = m3.lead(chave)
        fonte = m3.saude().get("fonte")
    except HTTPException:
        return False
    gravar_do_modulo3(conn, lead, fonte)
    return True
