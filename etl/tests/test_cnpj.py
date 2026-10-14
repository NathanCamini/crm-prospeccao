import pytest

from prospeccao.domain.cnpj import (
    CNPJInvalidoError,
    calcular_dv,
    cnpj_valido,
    formatar_cnpj,
    montar_cnpj,
    normalizar_cnpj,
)


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("11.222.333/0001-81", "11222333000181"),
        ("1222333000181", "01222333000181"),       # zero à esquerda perdido em planilha
        ("  00012345000165 ", "00012345000165"),
        ("12.abc.345/01de-35", "12ABC34501DE35"),   # alfanumérico, minúsculas -> maiúsculas
        ("191", "00000000000191"),
    ],
)
def test_normalizar_preserva_14_caracteres_com_zeros(entrada: str, esperado: str) -> None:
    assert normalizar_cnpj(entrada) == esperado
    assert len(normalizar_cnpj(entrada)) == 14


def test_normalizar_rejeita_numero_inteiro() -> None:
    with pytest.raises(TypeError):
        normalizar_cnpj(12345678000195)  # type: ignore[arg-type]


@pytest.mark.parametrize("entrada", ["", "123456789012345", "11.222.333/0001-8!", "ÇÇ"])
def test_normalizar_rejeita_invalidos(entrada: str) -> None:
    with pytest.raises(CNPJInvalidoError):
        normalizar_cnpj(entrada)


def test_montar_cnpj_completa_partes() -> None:
    assert montar_cnpj("12345", "1", "9") == "00012345000109"


def test_dv_numerico_conhecido() -> None:
    assert calcular_dv("112223330001") == "81"
    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("11.222.333/0001-82")


def test_dv_alfanumerico_exemplo_oficial() -> None:
    # Exemplo publicado pela Receita para o CNPJ alfanumérico: 12.ABC.345/01DE-35
    assert calcular_dv("12ABC34501DE") == "35"
    assert cnpj_valido("12.ABC.345/01DE-35")


def test_cnpj_com_todos_iguais_e_invalido() -> None:
    assert not cnpj_valido("00000000000000")


def test_formatar() -> None:
    assert formatar_cnpj("11222333000181") == "11.222.333/0001-81"
