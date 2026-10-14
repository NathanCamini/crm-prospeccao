"""Testes da interface Streamlit com o AppTest (sem navegador)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from prospeccao.app_store import AppStore
from prospeccao.config import Config
from prospeccao.ingest.amostra import gerar_amostra
from prospeccao.pipeline import executar_carga

APP = str(Path(__file__).parents[1] / "src" / "prospeccao" / "web" / "app.py")


@pytest.fixture(scope="module")
def pasta_dados(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pasta = tmp_path_factory.mktemp("web")
    cfg = Config(data_dir=pasta, duckdb_memory_limit="512MB", duckdb_threads=2)
    gerar_amostra(cfg.bronze_dir, "2026-09", date.today())
    executar_carga(cfg, "2026-09", baixar=False, data_referencia=date.today())
    AppStore(cfg.app_db).criar_usuario("ana", "senha-da-ana-123")
    return pasta


def _app(pasta: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("PROSPECCAO_DATA_DIR", str(pasta))
    at = AppTest.from_file(APP, default_timeout=60)
    return at.run()


def _login(at: AppTest, senha: str = "senha-da-ana-123") -> AppTest:
    at.text_input(key="login_usuario").input("ana")
    at.text_input(key="login_senha").input(senha)
    at.button[0].click()
    return at.run()


def _textos(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def test_sem_usuario_orienta_a_criar(tmp_path, monkeypatch) -> None:
    at = _app(tmp_path, monkeypatch)
    assert "prospeccao usuario criar" in at.warning[0].value


def test_senha_errada_nao_entra(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch), senha="errada-errada")
    assert at.error and "inválidos" in at.error[0].value
    assert "usuario" not in at.session_state


def test_login_mostra_resultado_padrao_e_data_da_base(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    assert not at.exception
    textos = _textos(at)
    assert "**4** empresas encontradas" in textos  # 90 dias, ativas
    assert "Base: referência 09/2026" in textos
    assert at.metric[0].value == "4"  # painel de indicadores


def test_filtro_por_municipio_e_bairro_condicional(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.multiselect(key="f_municipios").select("9702")
    at = at.run()
    assert at.multiselect(key="f_bairros_9702").options == ["VILA NOVA"]  # só aparece porque há bairro
    at.button(key="f_aplicar").click()
    at = at.run()
    assert "**2** empresas encontradas" in _textos(at)


def test_ficha_pelo_cnpj_mostra_contato_original_sem_alteracao(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.text_input(key="busca_cnpj").input("44.555.666/0001-81")
    at = at.run()
    assert not at.exception
    contato = at.dataframe[1].value  # [0] = tabela de resultados, [1] = contato da Receita
    assert contato["Telefone 1 (Receita)"][0] == " 99999000"
    assert contato["E-mail (Receita)"][0] == "Joao.Silva@Exemplo.com"
    assert "PADARIA SÃO JOÃO" in " ".join(h.value for h in at.subheader)


def test_cnpj_invalido(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.text_input(key="busca_cnpj").input("abc!")
    at = at.run()
    assert any("CNPJ inválido" in e.value for e in at.error)


def _por_rotulo(widgets, rotulo: str):
    return next(w for w in widgets if w.label == rotulo)


def test_registro_manual_de_contato_na_ficha(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.text_input(key="busca_cnpj").input("00012345000165")
    at = at.run()
    _por_rotulo(at.text_input, "Valor encontrado").input("(53) 3999-1234")
    _por_rotulo(at.text_input, "Onde encontrou (URL)").input("https://exemplo.com/alfa")
    _por_rotulo(at.button, "Registrar contato").click()
    at = at.run()
    assert not at.exception
    assert any("Contato registrado" in i.value for i in at.info)
    tabelas = [d.value for d in at.dataframe if "valor" in d.value.columns]
    assert "(53) 3999-1234" in list(tabelas[0]["valor"])
    # contato original da Receita continua igual
    contato = next(d.value for d in at.dataframe if "DDD fax (Receita)" in d.value.columns)
    assert contato["E-mail (Receita)"][0] == "CONTATO@ALFA-OBRAS.COM.BR"


def test_salvar_status_na_ficha_e_coluna_status(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.text_input(key="busca_cnpj").input("44555666000181")
    at = at.run()
    _por_rotulo(at.selectbox, "Status").set_value("em_contato")
    _por_rotulo(at.text_input, "Observação").input("Ligar terça")
    _por_rotulo(at.button, "Salvar status").click()
    at = at.run()
    assert not at.exception
    assert any("Status salvo: Em contato" in s.value for s in at.success)
    tabela = at.dataframe[0].value
    assert dict(zip(tabela["CNPJ"], tabela["Status"]))["44.555.666/0001-81"] == "Em contato"


def test_consulta_salva_executa_e_compara(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.text_input(key="cs_nome").input("Tudo 90 dias")
    at.button(key="cs_salvar").click()
    at = at.run()
    at.button(key="cs_executar").click()
    at = at.run()
    assert any("primeira execução" in i.value for i in at.info)
    at.button(key="cs_executar").click()
    at = at.run()
    assert any("**0** empresa(s) nova(s)" in i.value for i in at.info)


def test_exportar_csv_pela_tela(pasta_dados, monkeypatch) -> None:
    at = _login(_app(pasta_dados, monkeypatch))
    at.button(key="exp_gerar").click()
    at = at.run()
    assert not at.exception
    formato, dados, n = at.session_state["arquivo_exportado"]
    assert formato == "csv" and n == 4 and dados.startswith(b"\xef\xbb\xbf")


def test_interface_passa_a_usar_base_nova_sem_reiniciar(tmp_path, monkeypatch) -> None:
    """Depois de uma carga nova (troca do arquivo da base), a tela mostra os dados novos sem reiniciar."""
    import os
    import time

    cfg = Config(data_dir=tmp_path, duckdb_memory_limit="512MB", duckdb_threads=2)
    gerar_amostra(cfg.bronze_dir, "2026-09", date.today())
    executar_carga(cfg, "2026-09", baixar=False, data_referencia=date.today())
    AppStore(cfg.app_db).criar_usuario("ana", "senha-da-ana-123")
    at = _login(_app(tmp_path, monkeypatch))
    assert "**4** empresas encontradas" in _textos(at)

    outra = tmp_path / "outra"
    cfg2 = Config(data_dir=outra, duckdb_memory_limit="512MB", duckdb_threads=2)
    gerar_amostra(cfg2.bronze_dir, "2026-10", date.today(), volume_extra=50)
    executar_carga(cfg2, "2026-10", baixar=False, data_referencia=date.today())
    time.sleep(0.01)
    os.replace(cfg2.gold_db, cfg.gold_db)  # o que a carga faz ao publicar a base nova

    at = at.run()
    assert not at.exception
    assert "Base: referência 10/2026" in _textos(at)  # lido do arquivo novo (antes da correção: 09/2026)


def test_recarregar_pagina_com_cookie_valido_entra_sem_senha(pasta_dados, monkeypatch) -> None:
    from prospeccao.web import sessao

    store = AppStore(Config(data_dir=pasta_dados).app_db)
    token = store.criar_sessao("ana", 12)
    monkeypatch.setattr(sessao, "ler_cookie", lambda: token)
    at = _app(pasta_dados, monkeypatch)  # página nova, sem login no formulário
    assert not at.exception
    assert at.session_state["usuario"] == "ana"
    assert "empresas encontradas" in _textos(at)

    at.button(key="sair").click()
    at = at.run()
    assert "usuario" not in at.session_state
    assert store.usuario_da_sessao(token) is None  # sair invalida no servidor
    novo = _app(pasta_dados, monkeypatch)  # mesmo cookie antigo numa página nova
    assert "usuario" not in novo.session_state


def test_cookie_invalido_ou_sessao_desligada_pede_senha(pasta_dados, monkeypatch) -> None:
    from prospeccao.web import sessao

    monkeypatch.setattr(sessao, "ler_cookie", lambda: "codigo-que-nao-existe-123456789")
    assert "usuario" not in _app(pasta_dados, monkeypatch).session_state

    token = AppStore(Config(data_dir=pasta_dados).app_db).criar_sessao("ana", 12)
    monkeypatch.setattr(sessao, "ler_cookie", lambda: token)
    monkeypatch.setenv("SESSAO_HORAS", "0")
    assert "usuario" not in _app(pasta_dados, monkeypatch).session_state


def test_login_grava_cookie(pasta_dados, monkeypatch) -> None:
    from prospeccao.web import sessao

    gravados = []
    monkeypatch.setattr(sessao, "gravar_cookie", lambda token, horas: gravados.append((token, horas)))
    at = _login(_app(pasta_dados, monkeypatch))
    assert not at.exception
    (token, horas), = gravados
    assert horas == 12
    assert AppStore(Config(data_dir=pasta_dados).app_db).usuario_da_sessao(token) == "ana"
