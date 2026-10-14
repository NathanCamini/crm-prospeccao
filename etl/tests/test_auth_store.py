import sqlite3

import pytest

from prospeccao.app_store import AppStore, UsuarioExistenteError
from prospeccao.auth import SenhaFracaError, gerar_hash, verificar_senha


def test_hash_nao_contem_a_senha_e_usa_sal() -> None:
    h1, h2 = gerar_hash("senha-secreta-1"), gerar_hash("senha-secreta-1")
    assert "senha-secreta-1" not in h1
    assert h1 != h2  # sal aleatório
    assert h1.startswith("scrypt$")


def test_verificacao() -> None:
    h = gerar_hash("senha-secreta-1")
    assert verificar_senha("senha-secreta-1", h)
    assert not verificar_senha("senha-secreta-2", h)
    assert not verificar_senha("qualquer", "lixo$sem$formato")


def test_senha_curta_rejeitada() -> None:
    with pytest.raises(SenhaFracaError):
        gerar_hash("curta")


@pytest.fixture()
def store(tmp_path) -> AppStore:
    return AppStore(tmp_path / "app" / "app.sqlite")


def test_migracao_idempotente(tmp_path) -> None:
    caminho = tmp_path / "app.sqlite"
    v1 = AppStore(caminho).versao_esquema()
    assert AppStore(caminho).versao_esquema() == v1 >= 1


def test_usuario_ciclo_completo(store: AppStore) -> None:
    store.criar_usuario("Ana", "senha-da-ana-123")
    assert store.listar_usuarios() == ["ana"]
    assert store.autenticar("ANA ", "senha-da-ana-123")
    assert not store.autenticar("ana", "errada-errada")
    assert not store.autenticar("ninguem", "senha-da-ana-123")
    with pytest.raises(UsuarioExistenteError):
        store.criar_usuario("ana", "outra-senha-123")
    assert store.alterar_senha("ana", "nova-senha-456")
    assert store.autenticar("ana", "nova-senha-456")
    assert store.remover_usuario("ana")
    assert store.listar_usuarios() == []


def test_senha_guardada_somente_como_hash(store: AppStore) -> None:
    store.criar_usuario("bia", "senha-da-bia-123")
    with sqlite3.connect(store.caminho) as con:
        (valor,) = con.execute("SELECT senha_hash FROM usuario").fetchone()
    assert "senha-da-bia-123" not in valor


def test_cnpjs_com_contato_vazio_antes_da_etapa_4(store: AppStore) -> None:
    assert store.cnpjs_com_contato_complementar() == []


def test_sessao_guarda_so_o_hash_e_expira(store: AppStore) -> None:
    store.criar_usuario("ana", "senha-da-ana-123")
    token = store.criar_sessao("ana", horas=12)
    assert store.usuario_da_sessao(token) == "ana"
    with sqlite3.connect(store.caminho) as con:
        (guardado,) = con.execute("SELECT token_hash FROM sessao").fetchone()
    assert token not in guardado and len(guardado) == 64
    expirada = store.criar_sessao("ana", horas=-1)
    assert store.usuario_da_sessao(expirada) is None
    assert store.usuario_da_sessao("codigo-inventado-qualquer-123456") is None
    assert store.usuario_da_sessao(None) is None


def test_sair_trocar_senha_ou_remover_invalida_sessao(store: AppStore) -> None:
    store.criar_usuario("ana", "senha-da-ana-123")
    t1, t2 = store.criar_sessao("ana", 12), store.criar_sessao("ana", 12)
    store.encerrar_sessao(t1)
    assert store.usuario_da_sessao(t1) is None and store.usuario_da_sessao(t2) == "ana"
    store.alterar_senha("ana", "outra-senha-456")
    assert store.usuario_da_sessao(t2) is None
    t3 = store.criar_sessao("ana", 12)
    store.remover_usuario("ana")
    assert store.usuario_da_sessao(t3) is None
