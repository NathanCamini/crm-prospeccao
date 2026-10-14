"""Status de prospecção e consultas salvas (banco do usuário) e seu uso nos filtros."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from prospeccao.app_store import AppStore, ConsultaExistenteError
from prospeccao.config import Config
from prospeccao.consulta import Repositorio
from prospeccao.domain.filtros import Filtros, FiltroInvalidoError, Localidade
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.pipeline import executar_carga
from tests.conftest import MES, REFERENCIA

A, B, D, F = "00012345000165", "00012345000246", "44555666000181", "A1B2C3D4000193"


@pytest.fixture()
def store(tmp_path) -> AppStore:
    return AppStore(tmp_path / "app.sqlite")


# ---------------------------------------------------------------- status
def test_status_com_historico(store: AppStore) -> None:
    store.definir_status(D, "em_contato", "Ligar terça", "ana")
    store.definir_status(D, "cliente", "Fechou contrato", "bia")
    atual = store.status_de([D, A])
    assert atual[D]["status"] == "cliente" and atual[D]["atualizado_por"] == "bia"
    assert A not in atual  # sem registro = "não prospectada"
    historico = store.historico_status(D)
    assert [h["status"] for h in historico] == ["cliente", "em_contato"]
    assert historico[1]["observacao"] == "Ligar terça"


def test_status_invalido(store: AppStore) -> None:
    with pytest.raises(ValueError):
        store.definir_status(D, "inventado")
    with pytest.raises(FiltroInvalidoError):
        Filtros(status=("inventado",)).validar()


def test_filtro_por_status(carga_amostra, store: AppStore) -> None:
    cfg, _ = carga_amostra
    store.definir_status(D, "em_contato")
    store.definir_status(A, "descartada")
    store.definir_status(B, "nao_prospectada", "voltou para a fila")
    with Repositorio(cfg.gold_db, app=store) as r:
        assert set(r.cnpjs(Filtros(status=("em_contato",)), REFERENCIA)) == {D}
        assert set(r.cnpjs(Filtros(status=("nao_prospectada",)), REFERENCIA)) == {B, F}
        assert set(r.cnpjs(Filtros(status=("em_contato", "descartada")), REFERENCIA)) == {A, D}


def test_status_sobrevive_a_nova_carga_mensal(tmp_path) -> None:
    cfg = Config(data_dir=tmp_path, duckdb_memory_limit="512MB", duckdb_threads=2)
    store = AppStore(cfg.app_db)
    gerar_amostra(cfg.bronze_dir, "2026-08", REFERENCIA - timedelta(days=30))
    executar_carga(cfg, "2026-08", baixar=False, data_referencia=REFERENCIA - timedelta(days=30))
    store.definir_status(A, "contatada", "Retornar em novembro")
    gerar_amostra(cfg.bronze_dir, MES, REFERENCIA)
    executar_carga(cfg, MES, baixar=False, data_referencia=REFERENCIA)
    with duckdb.connect(str(cfg.gold_db), read_only=True) as con:
        assert con.execute("SELECT max(mes_referencia_base) FROM empresa").fetchone()[0] == MES
    with Repositorio(cfg.gold_db, app=store) as r:
        assert r.cnpjs(Filtros(status=("contatada",)), REFERENCIA) == [A]
    assert AppStore(cfg.app_db).status_de([A])[A]["observacao"] == "Retornar em novembro"


# ---------------------------------------------------------------- consultas salvas
def test_salvar_listar_e_nome_unico(store: AppStore) -> None:
    f = Filtros(localidades=(Localidade("RS", "9701"),), portes=("ME",))
    cid = store.salvar_consulta("Obras em Alfa", f.para_dict(), "ana")
    with pytest.raises(ConsultaExistenteError):
        store.salvar_consulta("Obras em Alfa", {}, "ana")
    assert store.salvar_consulta("Obras em Alfa", Filtros().para_dict(), sobrescrever=True) == cid
    (salva,) = store.listar_consultas()
    assert Filtros.de_dict(salva["filtros"]) == Filtros()


def test_comparacao_entre_execucoes(store: AppStore) -> None:
    cid = store.salvar_consulta("teste", Filtros().para_dict())
    primeira = store.registrar_execucao(cid, [A, B], "2026-08")
    assert primeira.anterior_em is None and primeira.novos == [A, B]
    segunda = store.registrar_execucao(cid, [A, B, D], "2026-09")
    assert segunda.anterior_em == primeira.executado_em and segunda.novos == [D]
    terceira = store.registrar_execucao(cid, [B, D], "2026-09")
    assert terceira.novos == []
    with store.conexao() as con:  # guarda só as duas últimas execuções
        assert con.execute("SELECT count(*) FROM consulta_execucao").fetchone()[0] == 2


def test_excluir_consulta_apaga_execucoes(store: AppStore) -> None:
    cid = store.salvar_consulta("x", {})
    store.registrar_execucao(cid, [A], None)
    assert store.excluir_consulta(cid)
    with store.conexao() as con:
        assert con.execute("SELECT count(*) FROM consulta_execucao_cnpj").fetchone()[0] == 0


def test_reexecutar_consulta_salva_com_base_mais_recente(carga_amostra, store: AppStore) -> None:
    """Período relativo é recalculado a cada execução (não fica preso à data em que foi salvo)."""
    cfg, _ = carga_amostra
    cid = store.salvar_consulta("RS 90 dias", Filtros(localidades=(Localidade("RS"),)).para_dict())
    filtros = Filtros.de_dict(store.obter_consulta(cid)["filtros"])
    with Repositorio(cfg.gold_db, app=store) as r:
        hoje = set(r.cnpjs(filtros, REFERENCIA))
        daqui_dez_dias = set(r.cnpjs(filtros, REFERENCIA + timedelta(days=10)))
    assert hoje == {A, B, D}
    assert daqui_dez_dias == {A, D}  # B (aberta há 90 dias) saiu da janela


def test_somente_cnpjs_restringe_resultado(carga_amostra) -> None:
    cfg, _ = carga_amostra
    with Repositorio(cfg.gold_db) as r:
        assert r.contar(Filtros(), REFERENCIA, somente_cnpjs=[D, "99999999999999"]) == 1
        assert r.buscar(Filtros(), referencia=REFERENCIA, somente_cnpjs=[]).total == 0
