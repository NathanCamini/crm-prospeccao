"""Agente, etapas sem LLM: contrato de dados, perfil, funil SQL, sinais de rede, prompt e regras."""

from __future__ import annotations

import json

import pytest

from agente.contrato import ContratoInvalidoError, validar_mes
from agente.funil import FunilGrandeError, candidatos
from agente.perfil import PerfilInvalidoError, carregar_perfil
from agente.prompt import entrada_empresa, hash_entrada, hash_prompt, texto_entrada
from agente.resultado import ResultadoLLM, aplicar_regras
from agente.sinais import DetectorSinais, Rede, carregar_redes
from tests.apoio import (
    EMPRESAS_PADRAO, MES, NO_FUNIL, PERFIL_TESTE, RAIZ, A, E, G, H, empresa, escrever_mes, montar_cfg,
)


@pytest.fixture()
def cfg(tmp_path):
    configuracao = montar_cfg(tmp_path, PERFIL_TESTE)
    escrever_mes(configuracao.novas_dir, EMPRESAS_PADRAO)
    return configuracao


# ---------------------------------------------------------------- contrato
def test_contrato_valido(cfg) -> None:
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    assert arquivo.total_linhas == len(EMPRESAS_PADRAO) and arquivo.contrato.versao == 1
    assert not arquivo.tem("contato_compartilhado")  # opcional, ausente


def test_contrato_aceita_coluna_opcional_com_tipo_certo(tmp_path) -> None:
    cfg = montar_cfg(tmp_path)
    linhas = [r | {"filial_de_empresa_existente": False} for r in EMPRESAS_PADRAO]
    escrever_mes(cfg.novas_dir, linhas, extras={"filial_de_empresa_existente": "BOOLEAN"})
    assert validar_mes(cfg.novas_dir, MES, cfg.schema_dir).tem("filial_de_empresa_existente")


@pytest.mark.parametrize("ajuste, mensagem", [
    ({"versao": 2}, "Versão de schema desconhecida"),
    ({"manifesto": {"sha256": "0" * 64}}, "SHA-256"),
    ({"manifesto": {"total_linhas": 99}}, "linhas"),
    ({"manifesto": {"mes": "2026-08"}}, "manifesto é do mês"),
    ({"remover": ("cnae_principal",)}, "falta a coluna obrigatória cnae_principal"),
    ({"extras": {"capital_social": "VARCHAR"}}, "capital_social: tipo VARCHAR"),
    ({"extras": {"pessoa_fisica_identificavel": "VARCHAR"}}, "pessoa_fisica_identificavel: tipo VARCHAR"),
])
def test_contrato_recusa_arquivo_fora_do_contrato(tmp_path, ajuste, mensagem) -> None:
    cfg = montar_cfg(tmp_path)
    linhas = [r | {"pessoa_fisica_identificavel": "x"} for r in EMPRESAS_PADRAO] \
        if "pessoa_fisica_identificavel" in str(ajuste) else EMPRESAS_PADRAO
    escrever_mes(cfg.novas_dir, linhas, **ajuste)
    with pytest.raises(ContratoInvalidoError, match=mensagem):
        validar_mes(cfg.novas_dir, MES, cfg.schema_dir)


def test_contrato_sem_arquivo_do_mes(tmp_path) -> None:
    cfg = montar_cfg(tmp_path)
    with pytest.raises(ContratoInvalidoError, match="Rode a exportação"):
        validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    with pytest.raises(ContratoInvalidoError, match="Mês inválido"):
        validar_mes(cfg.novas_dir, "2026-13", cfg.schema_dir)


# ---------------------------------------------------------------- perfil
def test_perfil_do_documento_carrega() -> None:
    perfil = carregar_perfil(RAIZ / "perfis" / "exemplos", "amenities_hotel")
    assert perfil.versao == 4 and perfil.filtro_sql.cnae_prefixos == ["5510", "5590"]
    assert perfil.filtro_sql.capital_social_min is None and not perfil.filtro_sql.excluir_mei  # porte e capital: LLM
    assert perfil.saida_score == "0-100" and perfil.canal_preferido == ["email", "ligacao", "whatsapp_assistido"]


@pytest.mark.parametrize("trecho, mensagem", [
    ("filtro_sql:\n  situacao: [ATIVA]\n", "cnae_prefixos ou cnaes"),
    ("filtro_sql:\n  cnae_prefixos: ['5510']\n  campo_errado: 1\n", "campo_errado"),
    ("filtro_sql:\n  cnae_prefixos: ['5510']\n  situacao: [ABERTA]\n", "situacao"),
    ("filtro_sql:\n  cnaes: ['5510']\n", "7 dígitos"),
])
def test_perfil_invalido_tem_mensagem_clara(tmp_path, trecho, mensagem) -> None:
    yaml_texto = f"id: ruim\nversao: 1\nproduto: produto x\ncriterios_llm: ['a?']\n{trecho}"
    (tmp_path / "ruim.yaml").write_text(yaml_texto, encoding="utf-8")
    with pytest.raises(PerfilInvalidoError, match=mensagem):
        carregar_perfil(tmp_path, "ruim")


def test_perfil_inexistente_ou_com_id_trocado(tmp_path) -> None:
    with pytest.raises(PerfilInvalidoError, match="não encontrado"):
        carregar_perfil(tmp_path, "nao_existe")
    (tmp_path / "um_nome.yaml").write_text(PERFIL_TESTE, encoding="utf-8")
    with pytest.raises(PerfilInvalidoError, match="deve ser o nome do arquivo"):
        carregar_perfil(tmp_path, "um_nome")


# ---------------------------------------------------------------- funil
def test_funil_aplica_cnae_situacao_capital_e_mei(cfg) -> None:
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    assert {e["cnpj"] for e in candidatos(arquivo, carregar_perfil(cfg.perfis_dir, "teste_hotel"))} == NO_FUNIL


def test_funil_inclui_cnae_secundario_quando_pedido(cfg) -> None:
    texto = PERFIL_TESTE.replace("excluir_mei: true", "excluir_mei: true\n  incluir_cnaes_secundarios: true")
    (cfg.perfis_dir / "teste_hotel.yaml").write_text(texto, encoding="utf-8")
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    assert {e["cnpj"] for e in candidatos(arquivo, carregar_perfil(cfg.perfis_dir, "teste_hotel"))} == NO_FUNIL | {H}


def test_funil_trava_de_custo(cfg) -> None:
    arquivo = validar_mes(cfg.novas_dir, MES, cfg.schema_dir)
    with pytest.raises(FunilGrandeError, match="limite é 2"):
        candidatos(arquivo, carregar_perfil(cfg.perfis_dir, "teste_hotel"), limite=2)


# ---------------------------------------------------------------- sinais de rede
@pytest.fixture()
def redes():
    return carregar_redes(RAIZ / "perfis" / "marcas_rede.csv")


def test_sinal_marca_no_nome_e_rede_pelo_dominio(redes) -> None:
    sinais = DetectorSinais(redes).sinais(empresa(E, "IBIS BUDGET FLORIPA", "5510801", "Hotéis", 1,
                                                  email_receita="x@accor.com"))
    assert sinais["redes"] == [{"rede": "Accor", "marca": "ibis", "evidencias": [
        "nome fantasia contém 'ibis budget'", "e-mail da Receita no domínio da rede (accor.com)"]}]


def test_sinal_por_socio_holding(redes) -> None:
    alvo = empresa(A, None, "5510801", "Hotéis", 1, razao_social="AHI SPE XYZ LTDA", email_receita="a@gmail.com")
    detector = DetectorSinais(redes, socios_pj_por_basico={A[:8]: ["ATLANTICA HOTELS INTERNATIONAL BRASIL LTDA"]})
    [achada] = detector.sinais(alvo)["redes"]
    assert achada["rede"] == "Atlantica Hospitality" and achada["marca"] is None  # holding indica a rede, não a marca
    assert "holding da rede" in achada["evidencias"][0]


@pytest.mark.parametrize("nome, socio", [
    ("POUSADA COMFORT", None),            # bandeira genérica só pela forma completa
    ("HOTEL QUALITY TIME", None),
    ("HELTON HOSPEDAGEM", None),          # nome de pessoa parecido com Hilton
    ("POUSADA TRANQUILA", "ACCORDE PARTICIPACOES LTDA"),  # "accor" só como palavra inteira
])
def test_sem_falso_positivo(redes, nome, socio) -> None:
    detector = DetectorSinais(redes, socios_pj_por_basico={A[:8]: [socio]} if socio else None)
    assert detector.sinais(empresa(A, nome, "5510801", "Hotéis", 1, email_receita="a@gmail.com"))["redes"] == []


def test_sinal_aproximado_para_erro_de_digitacao(redes) -> None:
    sinais = DetectorSinais(redes).sinais(empresa(A, "HOTEL NOVOTTEL CENTRO", "5510801", "Hotéis", 1))
    assert sinais["redes"][0]["marca"] == "Novotel" and "(aproximado)" in sinais["redes"][0]["evidencias"][0]


# ---------------------------------------------------------------- prompt e minimização
def test_entrada_nao_envia_contatos_nem_cnpj_e_mascara_cpf(redes) -> None:
    g = next(e for e in EMPRESAS_PADRAO if e["cnpj"] == G)
    entrada = entrada_empresa(g, DetectorSinais(redes).sinais(g), {})
    texto = texto_entrada(entrada)
    assert G not in texto and "33334444" not in texto and "@" not in texto
    assert entrada["razao_social"] is None and entrada["titular_pessoa_fisica"] is True  # empresário individual

    a = next(e for e in EMPRESAS_PADRAO if e["cnpj"] == A) | {"razao_social": "POUSADA 123.456.789-01 LTDA"}
    assert entrada_empresa(a, {"redes": []}, {})["razao_social"] == "POUSADA [CPF omitido] LTDA"


def test_hashes_mudam_quando_devem(cfg) -> None:
    a = next(e for e in EMPRESAS_PADRAO if e["cnpj"] == A)
    base = hash_entrada(entrada_empresa(a, {"redes": []}, {}))
    assert base == hash_entrada(entrada_empresa(dict(a), {"redes": []}, {}))
    assert base != hash_entrada(entrada_empresa(a | {"capital_social": 600_000}, {"redes": []}, {}))
    perfil = carregar_perfil(cfg.perfis_dir, "teste_hotel")
    alterado = perfil.model_copy(update={"criterios_llm": [*perfil.criterios_llm, "Tem restaurante?"]})
    assert hash_prompt(perfil) != hash_prompt(alterado)


# ---------------------------------------------------------------- regras sobre a saída do LLM
def _bruto(**campos) -> ResultadoLLM:
    padrao = {"e_alvo": True, "score": 80, "justificativa": "ok", "e_franquia": None, "marca": None,
              "padrao": "medio", "produto_sugerido": None, "confianca": "alta"}
    return ResultadoLLM(**(padrao | campos))


@pytest.fixture()
def perfil(cfg):
    return carregar_perfil(cfg.perfis_dir, "teste_hotel")


def test_franquia_sem_sinal_vira_indeterminada_e_vai_para_revisao(perfil) -> None:
    avaliacao = aplicar_regras(_bruto(score=40, e_alvo=False, e_franquia=True, marca="ibis"), perfil, {"redes": []})
    assert avaliacao.final["e_franquia"] is None and avaliacao.final["marca"] is None
    assert avaliacao.motivo_revisao == "afirmou franquia sem sinal"


def test_marca_so_dos_sinais(perfil) -> None:
    sinais = {"redes": [{"rede": "Accor", "marca": None, "evidencias": ["x"]}]}
    assert aplicar_regras(_bruto(e_franquia=True, marca="accor"), perfil, sinais).final["marca"] == "Accor"
    avaliacao = aplicar_regras(_bruto(e_franquia=True, marca="Hilton"), perfil, sinais)
    assert avaliacao.final["marca"] is None and "não veio dos sinais" in avaliacao.avisos[0]


def test_padrao_e_produto_so_do_perfil(perfil) -> None:
    final = aplicar_regras(_bruto(padrao="luxo", produto_sugerido="Shampoo avulso"), perfil, {"redes": []}).final
    assert final["padrao"] == "indefinido" and final["produto_sugerido"] is None
    assert aplicar_regras(_bruto(produto_sugerido="Kit completo"), perfil, {"redes": []}).final["produto_sugerido"] == "Kit completo"


@pytest.mark.parametrize("campos, alvo, motivo", [
    ({"score": 80}, True, "score alto"),
    ({"score": 30, "e_alvo": False}, False, None),
    ({"score": 30, "e_alvo": False, "confianca": "baixa"}, False, "confiança baixa"),
    ({"score": 50, "e_alvo": True}, False, "e_alvo incoerente com o score"),   # alvo abaixo do limiar
])
def test_alvo_exige_limiar_e_motivos_de_revisao(perfil, campos, alvo, motivo) -> None:
    avaliacao = aplicar_regras(_bruto(**campos), perfil, {"redes": []})
    assert avaliacao.final["e_alvo"] is alvo and avaliacao.motivo_revisao == motivo


def test_saida_fora_do_contrato_e_recusada() -> None:
    with pytest.raises(ValueError):
        ResultadoLLM.model_validate_json(json.dumps({"e_alvo": True, "score": 150}))
    with pytest.raises(ValueError):
        ResultadoLLM.model_validate_json('{"e_alvo": true, "score": 50, "justificativa": "x", "e_franquia": null, '
                                         '"marca": null, "padrao": "medio", "produto_sugerido": null, '
                                         '"confianca": "alta", "extra": 1}')


def test_lista_de_redes_carrega(redes) -> None:
    accor = next(r for r in redes if r.nome == "Accor")
    assert isinstance(accor, Rede) and "accor.com" in accor.dominios and len(accor.marcas) == 6
    comfort = dict(next(r for r in redes if r.nome == "Atlantica Hospitality").marcas)["Comfort"]
    assert "comfort" not in comfort  # bandeira genérica: só formas completas
