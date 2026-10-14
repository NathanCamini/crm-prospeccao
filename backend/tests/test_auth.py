import io

from fastapi.testclient import TestClient

from crm.cli import main as cli
from crm.db import conectar
from crm.seguranca import COOKIE, conferir


def test_sem_login_retorna_401(anonimo):
    assert anonimo.get("/api/auth/eu").status_code == 401
    assert anonimo.post("/api/leads/busca", json={}).status_code == 401


def test_login_cria_cookie_persistente_httponly(anonimo):
    r = anonimo.post("/api/auth/entrar", json={"usuario": "Ana", "senha": "senha-teste-123"})
    assert r.status_code == 200
    eu = r.json()
    assert eu["usuario"] == "ana" and eu["papel"] == "admin" and eu["empresa"]["nome"] == "Amenities Sul"
    assert "empresas" not in eu
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}=" in cookie and "HttpOnly" in cookie and "Max-Age=2592000" in cookie
    assert "samesite=strict" in cookie.lower()
    assert anonimo.get("/api/auth/eu").json()["usuario"] == "ana"


def test_sessao_sobrevive_a_novo_cliente_com_mesmo_cookie(app, cliente):
    token = cliente.cookies.get(COOKIE)
    outro = TestClient(app, headers={"X-CRM": "1"}, cookies={COOKIE: token})
    assert outro.get("/api/auth/eu").status_code == 200


def test_cookie_guardado_so_como_hash(cfg, cliente):
    token = cliente.cookies.get(COOKIE)
    conn = conectar(cfg.db_path)
    hashes = [r[0] for r in conn.execute("SELECT token_hash FROM sessao")]
    assert token not in hashes and len(hashes) == 1


def test_senha_errada_e_usuario_inexistente(anonimo):
    assert anonimo.post("/api/auth/entrar", json={"usuario": "ana", "senha": "errada-123"}).status_code == 401
    r = anonimo.post("/api/auth/entrar", json={"usuario": "fantasma", "senha": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "Usuário ou senha inválidos."


def test_sair_encerra_sessao(cliente):
    assert cliente.post("/api/auth/sair").status_code == 200
    assert cliente.get("/api/auth/eu").status_code == 401


def test_sem_cabecalho_csrf_recusa_mutacao(app):
    sem = TestClient(app)
    r = sem.post("/api/auth/entrar", json={"usuario": "ana", "senha": "senha-teste-123"})
    assert r.status_code == 403


def test_lembrar_falso_gera_cookie_de_sessao(anonimo):
    r = anonimo.post("/api/auth/entrar", json={"usuario": "ana", "senha": "senha-teste-123", "lembrar": False})
    assert "Max-Age" not in r.headers["set-cookie"]


def test_trocar_senha_mantem_sessao_atual_e_derruba_as_outras(entrar):
    a, b = entrar("bia"), entrar("bia")
    r = a.post("/api/auth/senha", json={"senha_atual": "senha-teste-123", "senha_nova": "nova-senha-456"})
    assert r.status_code == 200
    assert a.get("/api/auth/eu").status_code == 200
    assert b.get("/api/auth/eu").status_code == 401
    assert a.post("/api/auth/senha", json={"senha_atual": "errada", "senha_nova": "outra-789x"}).status_code == 422


def test_senha_provisoria_obriga_troca_antes_de_trabalhar(cliente, entrar):
    cliente.post("/api/equipe", json={"usuario": "dani", "nome_exibicao": "Dani", "senha_inicial": "provisoria-1"})
    dani = entrar("dani", "provisoria-1")
    assert dani.get("/api/auth/eu").json()["deve_trocar_senha"] is True
    r = dani.post("/api/leads/busca", json={})
    assert r.status_code == 403 and "senha provisória" in r.json()["detail"]
    dani.post("/api/auth/senha", json={"senha_atual": "provisoria-1", "senha_nova": "minha-senha-1"})
    assert dani.post("/api/leads/busca", json={}).status_code == 200


def test_cli_superadmin_unico(app, cfg, monkeypatch):
    monkeypatch.setenv("CRM_DADOS_DIR", str(cfg.dados_dir))
    monkeypatch.setattr("sys.stdin", io.StringIO("outra-senha-456\n"))
    try:
        cli(["superadmin", "criar", "intruso"])
        raise AssertionError("deveria recusar um segundo superadmin")
    except SystemExit as e:
        assert "Já existe um superadmin" in str(e)


def test_cli_superadmin_em_banco_novo_e_redefinir_senha(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DADOS_DIR", str(tmp_path / "novo"))
    monkeypatch.setattr("sys.stdin", io.StringIO("senha-do-dono\n"))
    cli(["superadmin", "criar", "Eu", "--nome-exibicao", "Eu Mesmo"])
    conn = conectar(tmp_path / "novo" / "crm.sqlite")
    linha = conn.execute("SELECT papel, empresa_id, senha_hash FROM usuario WHERE nome = 'eu'").fetchone()
    assert linha["papel"] == "superadmin" and linha["empresa_id"] is None
    assert conferir("senha-do-dono", linha["senha_hash"])
    monkeypatch.setattr("sys.stdin", io.StringIO("provisoria-99\n"))
    cli(["usuario", "senha", "eu"])
    conn = conectar(tmp_path / "novo" / "crm.sqlite")
    assert conn.execute("SELECT deve_trocar_senha FROM usuario WHERE nome = 'eu'").fetchone()[0] == 1
