from datetime import date

import pytest

from prospeccao.domain.filtros import FiltroInvalidoError, Localidade, Periodo
from prospeccao.web.tela_filtros import EstadoTela, filtros_da_tela, separar_cnaes

ALFA = {"uf": "RS", "codigo": "9701", "nome": "CIDADE ALFA"}


def test_separar_cnaes_exatos_e_prefixos() -> None:
    assert separar_cnaes("4399-1/03, 43; 10 ") == (("4399103",), ("43", "10"))
    assert separar_cnaes("") == ((), ())


def test_padrao_da_tela_e_90_dias_ativas() -> None:
    f = filtros_da_tela(EstadoTela())
    assert f.periodo == Periodo(90)
    assert f.situacoes == ("ATIVA",)
    assert f.localidades == ()


def test_municipio_prevalece_sobre_a_sua_uf_e_outras_ufs_continuam() -> None:
    f = filtros_da_tela(EstadoTela(ufs=["RS", "SC"], municipios=[ALFA], bairros={"9701": ["CENTRO"]}))
    assert f.localidades == (Localidade("RS", "9701", ("CENTRO",)), Localidade("SC"))


def test_periodo_personalizado_e_qualquer_data() -> None:
    d = (date(2026, 9, 1), date(2026, 9, 30))
    assert filtros_da_tela(EstadoTela(periodo="Datas personalizadas", datas=d)).periodo == Periodo(None, *d)
    assert filtros_da_tela(EstadoTela(periodo="Qualquer data")).periodo is None
    with pytest.raises(FiltroInvalidoError):
        filtros_da_tela(EstadoTela(periodo="Datas personalizadas", datas=None))


def test_tres_estados_e_cnaes_combinados() -> None:
    f = filtros_da_tela(EstadoTela(
        simples="Não", tem_email="Sim", matriz_filial="Filial",
        cnae_selecionados=["4399103"], cnae_texto="4399103, 56",
    ))
    assert (f.simples, f.tem_email_receita, f.tem_telefone_receita) == (False, True, None)
    assert f.matriz_filial == "FILIAL"
    assert f.cnae_codigos == ("4399103",)  # sem duplicar
    assert f.cnae_prefixos == ("56",)
