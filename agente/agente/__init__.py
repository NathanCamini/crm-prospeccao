"""Módulo 2 — agente classificador.

Lê o Parquet das empresas novas do mês (contrato `schema/empresas_novas.v1.json`), aplica um
perfil de cliente ideal (YAML em `perfis/`) em duas etapas — filtro SQL barato no DuckDB e LLM só
no que passou —, grava o resultado no banco do agente e exporta o CSV que o módulo 3 entrega ao CRM.
"""
