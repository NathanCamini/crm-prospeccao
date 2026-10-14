from datetime import date, timedelta

import pytest

from prospeccao.domain.empresa_nova import e_empresa_nova, intervalo_empresa_nova

REF = date(2026, 10, 9)


@pytest.mark.parametrize(
    ("dias_atras", "esperado"),
    [(0, True), (1, True), (89, True), (90, True), (91, False), (-1, False)],
)
def test_limites_inclusivos_janela_90(dias_atras: int, esperado: bool) -> None:
    assert e_empresa_nova(REF - timedelta(days=dias_atras), REF, 90) is esperado


@pytest.mark.parametrize("dias", [30, 60, 90, 180, 365])
def test_janelas_padrao_incluem_os_dois_extremos(dias: int) -> None:
    inicio, fim = intervalo_empresa_nova(REF, dias)
    assert e_empresa_nova(inicio, REF, dias)
    assert e_empresa_nova(fim, REF, dias)
    assert not e_empresa_nova(inicio - timedelta(days=1), REF, dias)


def test_sem_data_nao_e_nova() -> None:
    assert e_empresa_nova(None, REF) is False


def test_janela_invalida() -> None:
    with pytest.raises(ValueError):
        intervalo_empresa_nova(REF, 0)
