"""Testes da consulta com todos os filtros da seção 4.4, sobre a amostra fictícia.

Referência: 2026-10-09. Estabelecimentos da amostra:
  A 00012345000165  matriz, aberta há 10 dias, CIDADE ALFA/RS, bairro CENTRO, 4399103, ME, Simples, tel+e-mail
  B 00012345000246  filial, há 90 dias, SÃO JOÃO DO TESTE/RS, sem bairro, 4399103, ME, Simples, só telefone
  C 11222333000181  há 91 dias, CIDADE ALFA/RS, 5611201, DEMAIS, capital 1.500.000,50, não Simples
  D 44555666000181  hoje, SÃO JOÃO DO TESTE/RS, VILA NOVA, 1091102 (padaria), MEI, natureza 2135
  E 77888999000181  BAIXADA, há 30 dias, CIDADE ALFA/RS, sem contato
  F A1B2C3D4000193  há 5 dias, VILA BETA/SC, 6201501, porte não informado, capital 0, sem Simples
  G 55666777000181  data futura (+3 dias), VILA BETA/SC
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from prospeccao.consulta import COLUNAS_PARQUET, Repositorio
from prospeccao.domain.filtros import Filtros, FiltroInvalidoError, Localidade, Periodo
from tests.conftest import REFERENCIA

A, B, C, D = "00012345000165", "00012345000246", "11222333000181", "44555666000181"
E, F, G = "77888999000181", "A1B2C3D4000193", "55666777000181"
TODAS_SITUACOES = ("ATIVA", "SUSPENSA", "INAPTA", "BAIXADA", "NULA")


@pytest.fixture()
def repo(carga_amostra):
    cfg, _ = carga_amostra
    with Repositorio(cfg.gold_db) as r:
        yield r


def cnpjs(repo: Repositorio, **kw) -> set[str]:
    return set(repo.cnpjs(Filtros(**kw), referencia=REFERENCIA))


def sem_periodo(**kw) -> dict:
    return {"periodo": None, "situacoes": TODAS_SITUACOES, **kw}


# ---------- período (limites inclusivos) ----------

def test_padrao_90_dias_somente_ativas(repo) -> None:
    assert cnpjs(repo) == {A, B, D, F}


def test_datas_personalizadas_inclusivas(repo) -> None:
    ini, fim = REFERENCIA - timedelta(days=90), REFERENCIA - timedelta(days=10)
    assert cnpjs(repo, periodo=Periodo(None, ini, fim)) == {A, B}


def test_intervalo_de_um_unico_dia_no_limite(repo) -> None:
    dia = REFERENCIA - timedelta(days=90)
    assert cnpjs(repo, periodo=Periodo(None, dia, dia)) == {B}


def test_janela_91_dias_inclui_limite_seguinte(repo) -> None:
    assert C in cnpjs(repo, periodo=Periodo(91))
    assert C not in cnpjs(repo, periodo=Periodo(90))


def test_data_futura_nunca_entra_na_janela(repo) -> None:
    assert G not in cnpjs(repo, periodo=Periodo(365))


def test_sem_periodo_e_todas_situacoes_traz_tudo(repo) -> None:
    assert cnpjs(repo, **sem_periodo()) == {A, B, C, D, E, F, G}


def test_periodo_invalido(repo) -> None:
    with pytest.raises(FiltroInvalidoError):
        repo.contar(Filtros(periodo=Periodo(None, REFERENCIA, REFERENCIA - timedelta(days=1))))
    with pytest.raises(FiltroInvalidoError):
        repo.contar(Filtros(periodo=Periodo(30, REFERENCIA, None)))


# ---------- localidade ----------

def test_municipio_retorna_apenas_o_municipio(repo) -> None:
    assert cnpjs(repo, **sem_periodo(localidades=(Localidade("RS", "9701"),))) == {A, C, E}
    assert cnpjs(repo, localidades=(Localidade("RS", "9701"),)) == {A}


def test_multiplas_localidades_somam(repo) -> None:
    locs = (Localidade(codigo_municipio="9701"), Localidade(uf="SC"))
    assert cnpjs(repo, **sem_periodo(localidades=locs)) == {A, C, E, F, G}


def test_uf_inteira(repo) -> None:
    assert cnpjs(repo, localidades=(Localidade("SC"),)) == {F}


def test_bairro_sem_acento_e_minusculo(repo) -> None:
    assert cnpjs(repo, localidades=(Localidade("RS", "9702", ("vila nova",)),)) == {D}


def test_bairro_exige_municipio() -> None:
    with pytest.raises(FiltroInvalidoError):
        Localidade("RS", None, ("CENTRO",)).validar()


# ---------- CNAE ----------

def test_cnae_exato_com_mascara(repo) -> None:
    assert cnpjs(repo, cnae_codigos=("4399-1/03",)) == {A, B}


@pytest.mark.parametrize("prefixo", ["43", "4399", "4399-1"])
def test_cnae_prefixo(repo, prefixo: str) -> None:
    assert cnpjs(repo, cnae_prefixos=(prefixo,)) == {A, B}


def test_cnae_palavra_chave_sem_acento(repo) -> None:
    assert cnpjs(repo, cnae_palavra="padaria") == {D}
    assert cnpjs(repo, cnae_palavra="COMPUTADOR") == {F}


def test_cnae_secundario_so_quando_pedido(repo) -> None:
    assert cnpjs(repo, cnae_codigos=("4330404",)) == set()
    assert cnpjs(repo, cnae_codigos=("4330404",), incluir_cnaes_secundarios=True) == {A}


def test_criterios_de_cnae_combinam_com_ou(repo) -> None:
    assert cnpjs(repo, cnae_codigos=("1091102",), cnae_prefixos=("62",)) == {D, F}


# ---------- demais filtros ----------

def test_porte(repo) -> None:
    assert cnpjs(repo, portes=("MEI",)) == {D}
    assert cnpjs(repo, portes=("NÃO INFORMADO", "ME")) == {A, B, F}


def test_natureza_juridica(repo) -> None:
    assert cnpjs(repo, naturezas=("2135",)) == {D}


def test_situacao(repo) -> None:
    assert cnpjs(repo, situacoes=("BAIXADA",)) == {E}


def test_capital_social(repo) -> None:
    assert cnpjs(repo, capital_min=100000) == {A, B}
    assert cnpjs(repo, capital_max=0) == {F}


def test_matriz_filial(repo) -> None:
    assert cnpjs(repo, matriz_filial="FILIAL") == {B}


def test_simples(repo) -> None:
    assert cnpjs(repo, simples=True) == {A, B, D}
    assert cnpjs(repo, simples=False) == {F}


def test_presenca_de_contato_da_receita(repo) -> None:
    assert cnpjs(repo, tem_email_receita=True) == {A, D, F}
    assert cnpjs(repo, tem_email_receita=False) == {B}
    assert cnpjs(repo, **sem_periodo(tem_telefone_receita=False)) == {E}


def test_palavra_chave_nome_sem_acento(repo) -> None:
    assert cnpjs(repo, palavra_chave="sao joao") == {D}
    assert cnpjs(repo, palavra_chave="Alfa") == {A, B}


def test_combinacao_de_varios_filtros(repo) -> None:
    f = dict(localidades=(Localidade("RS"),), portes=("ME",), tem_email_receita=True, cnae_prefixos=("43",))
    assert cnpjs(repo, **f) == {A}


def test_contato_complementar_exige_banco_do_usuario(repo) -> None:
    with pytest.raises(ValueError, match="AppStore"):
        repo.contar(Filtros(tem_contato_complementar=True))


def test_contato_complementar_com_banco_do_usuario(carga_amostra, tmp_path) -> None:
    from prospeccao.app_store import AppStore, ContatoNovo

    cfg, _ = carga_amostra
    store = AppStore(tmp_path / "app.sqlite")
    store.adicionar_contato(ContatoNovo(D, "site", "https://padaria.com.br", "padaria.com.br", "busca manual", "media"))
    with Repositorio(cfg.gold_db, app=store) as r:
        assert cnpjs(r, tem_contato_complementar=True) == {D}
        assert cnpjs(r, tem_contato_complementar=False) == {A, B, F}


# ---------- indicadores e ficha ----------

def test_indicadores_do_resultado(repo) -> None:
    ind = repo.indicadores(Filtros(), referencia=REFERENCIA)
    assert ind["total"] == 4
    assert ind["top_cnae"][0] == {"codigo": "4399103", "descricao": "Obras de alvenaria", "total": 2}
    assert {p["porte"]: p["total"] for p in ind["por_porte"]} == {"ME": 2, "MEI": 1, "NÃO INFORMADO": 1}
    assert sum(m["total"] for m in ind["por_mes"]) == 4
    assert ind["municipios_distintos"] == 3 and len(ind["por_municipio"]) == 3


def test_indicador_por_municipio_so_com_mais_de_um(repo) -> None:
    ind = repo.indicadores(Filtros(localidades=(Localidade(codigo_municipio="9703"),)), referencia=REFERENCIA)
    assert ind["total"] == 1 and ind["por_municipio"] == []


def test_ficha_completa(repo) -> None:
    ficha = repo.ficha(A)
    assert ficha["razao_social"] == "ALFA OBRAS E REFORMAS LTDA"
    assert {s["nome_socio"] for s in ficha["socios"]} == {"MARIA FICTICIA SOUZA", "JOSÉ INVENTADO LIMA"}
    assert "cnpj_cpf_socio" not in ficha["socios"][0]
    assert [o["cnpj"] for o in ficha["outros_estabelecimentos"]] == [B]
    assert ficha["email_receita"] == "CONTATO@ALFA-OBRAS.COM.BR"
    assert repo.ficha("99999999999999") is None


# ---------- segurança, paginação, ordenação ----------

def test_texto_malicioso_e_tratado_como_valor(repo) -> None:
    assert cnpjs(repo, palavra_chave="'; DROP TABLE empresa; --") == set()
    assert repo.contar(Filtros(periodo=None)) > 0


def test_ordenacao_e_colunas_so_da_lista_permitida(repo) -> None:
    with pytest.raises(ValueError):
        repo.buscar(Filtros(), ordenar_por="cnpj; DROP TABLE empresa")
    with pytest.raises(ValueError):
        repo.buscar(Filtros(), colunas=("cnpj", "1 AS x"))


def test_paginacao_e_ordenacao(repo) -> None:
    p1 = repo.buscar(Filtros(), pagina=1, tamanho=3, referencia=REFERENCIA)
    p2 = repo.buscar(Filtros(), pagina=2, tamanho=3, referencia=REFERENCIA)
    assert p1.total == 4 and p1.total_paginas == 2
    assert [l["cnpj"] for l in p1.linhas] == [D, F, A]  # mais recentes primeiro
    assert [l["cnpj"] for l in p2.linhas] == [B]
    asc = repo.buscar(Filtros(), ordenar_por="razao_social", decrescente=False, referencia=REFERENCIA)
    assert asc.linhas[0]["razao_social"].startswith("ALFA")


def test_contato_original_na_listagem_sem_alteracao(repo) -> None:
    pagina = repo.buscar(Filtros(palavra_chave="padaria"), referencia=REFERENCIA)
    assert pagina.linhas[0]["telefone_receita_1"] == " 99999000"
    assert pagina.linhas[0]["email_receita"] == "Joao.Silva@Exemplo.com"


# ---------- listas auxiliares ----------

def test_autocomplete_municipio_sem_acento(repo) -> None:
    res = repo.municipios("sao jo")
    assert res[0]["codigo"] == "9702" and res[0]["nome"] == "SÃO JOÃO DO TESTE"
    assert [m["codigo"] for m in repo.municipios("", uf="SC")] == ["9703"]


def test_bairros_do_municipio(repo) -> None:
    assert repo.bairros("9702") == ["VILA NOVA"]
    assert repo.bairros("9999") == []


def test_cnaes_e_naturezas(repo) -> None:
    assert repo.cnaes("4399")[0]["codigo"] == "4399103"
    assert repo.cnaes("restaurante")[0]["codigo"] == "5611201"
    assert {n["codigo"] for n in repo.naturezas()} == {"2062", "2135", "2054"}


def test_ultima_carga(repo) -> None:
    assert repo.ultima_carga()["mes_referencia"] == "2026-09"


def test_filtros_serializam_para_json_e_voltam() -> None:
    f = Filtros(
        localidades=(Localidade("RS", "9701", ("CENTRO",)),),
        periodo=Periodo(None, REFERENCIA - timedelta(days=5), REFERENCIA),
        cnae_prefixos=("43",), portes=("ME",), simples=True, palavra_chave="alfa",
    )
    assert Filtros.de_dict(json.loads(json.dumps(f.para_dict()))) == f


def test_contato_original_nao_muda_com_contatos_complementares(carga_amostra, tmp_path) -> None:
    from prospeccao.app_store import AppStore, ContatoNovo

    cfg, _ = carga_amostra
    store = AppStore(tmp_path / "app.sqlite")
    with Repositorio(cfg.gold_db, app=store) as r:
        antes = {k: v for k, v in r.ficha(D).items() if "receita" in k}
        store.adicionar_contato(ContatoNovo(D, "email", "outro@padaria.com.br", "outro@padaria.com.br", "busca manual", "alta"))
        store.adicionar_contato(ContatoNovo(D, "telefone", "(53) 3999-0000", "5339990000", "busca manual", "alta"))
        depois = {k: v for k, v in r.ficha(D).items() if "receita" in k}
    assert antes == depois
    assert depois["email_receita"] == "Joao.Silva@Exemplo.com"


def test_exportar_parquet_somente_ativas_ordenado(repo, tmp_path) -> None:
    destino = tmp_path / "sub" / "base.parquet"
    total = repo.exportar_parquet(Filtros(periodo=None), destino, referencia=REFERENCIA)
    cur = repo.con.execute("SELECT * FROM read_parquet(?)", [destino.as_posix()])
    colunas = [d[0] for d in cur.description]
    linhas = [dict(zip(colunas, r)) for r in cur.fetchall()]
    assert tuple(colunas) == COLUNAS_PARQUET
    assert total == len(linhas)
    assert {l["cnpj"] for l in linhas} == cnpjs(repo, periodo=None) and E not in {l["cnpj"] for l in linhas}
    chaves = [(l["uf"], l["municipio"], l["data_inicio_atividade"]) for l in linhas]
    assert chaves == sorted(chaves)
    assert list(destino.parent.iterdir()) == [destino]  # sem temporário esquecido


def test_exportar_parquet_amostra_diversifica_municipio_e_cnae(repo, tmp_path) -> None:
    destino = tmp_path / "amostra.parquet"
    total = repo.exportar_parquet(Filtros(periodo=None), destino, referencia=REFERENCIA, limite=3)
    cur = repo.con.execute("SELECT * FROM read_parquet(?)", [destino.as_posix()])
    colunas = [d[0] for d in cur.description]
    linhas = [dict(zip(colunas, r)) for r in cur.fetchall()]
    assert tuple(colunas) == COLUNAS_PARQUET
    assert total == len(linhas) == 3
    assert {l["cnpj"] for l in linhas} <= cnpjs(repo, periodo=None)
    # há 3 municípios e 4+ CNAEs entre as ativas: a primeira rodada não repete nenhum dos dois
    assert len({(l["uf"], l["municipio"]) for l in linhas}) == 3
    assert len({l["cnae_principal"] for l in linhas}) == 3
    with pytest.raises(ValueError):
        repo.exportar_parquet(Filtros(periodo=None), destino, limite=0)
