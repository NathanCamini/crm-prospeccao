"""Gera uma amostra FICTÍCIA no formato exato da Receita (zips com CSV Latin-1, ';', sem cabeçalho).

Serve para testes automatizados e para demonstrar a ferramenta sem baixar a base
completa. Nenhum dado aqui corresponde a empresa ou pessoa real: municípios,
nomes, telefones e e-mails são inventados.
"""

from __future__ import annotations

import csv
import io
import random
import zipfile
from datetime import date, timedelta
from pathlib import Path

from prospeccao.domain.cnpj import calcular_dv

MUNICIPIOS = [("9701", "CIDADE ALFA", "RS"), ("9702", "SÃO JOÃO DO TESTE", "RS"), ("9703", "VILA BETA", "SC")]
CNAES = [
    ("4399103", "Obras de alvenaria"),
    ("5611201", "Restaurantes e similares"),
    ("6201501", "Desenvolvimento de programas de computador sob encomenda"),
    ("4781400", "Comércio varejista de artigos do vestuário e acessórios"),
    ("1091102", "Fabricação de produtos de padaria e confeitaria com predominância de produção própria"),
]
NATUREZAS = [("2062", "Sociedade Empresária Limitada"), ("2135", "Empresário (Individual)"),
             ("2054", "Sociedade Anônima Fechada")]
QUALIFICACOES = [("49", "Sócio-Administrador"), ("22", "Sócio"), ("50", "Empresário")]
MOTIVOS = [("00", "SEM MOTIVO"), ("01", "EXTINCAO POR ENCERRAMENTO LIQUIDACAO VOLUNTARIA")]
PAISES = [("105", "BRASIL"), ("249", "ESTADOS UNIDOS")]


def _d(data: date | None) -> str:
    return data.strftime("%Y%m%d") if data else "0"


def _dv(basico: str, ordem: str) -> str:
    return calcular_dv(basico + ordem)


def _estab(basico: str, ordem: str, matriz: str, fantasia: str, situacao: str, inicio: date, cnae: str,
           mun: str, uf: str, bairro: str, ddd: str, tel: str, email: str,
           data_situacao: date | None = None, cnaes_sec: str = "") -> list[str]:
    return [
        basico, ordem, _dv(basico, ordem), matriz, fantasia, situacao, _d(data_situacao or inicio),
        "00" if situacao == "02" else "01", "", "", _d(inicio), cnae, cnaes_sec, "RUA", "DAS FLORES",
        "100", "SALA 2", bairro, "96200000", uf, mun, ddd, tel, "", "", "", "", email, "", "",
    ]


def casos_base(ref: date) -> dict[str, list[list[str]]]:
    """Registros com cenários conhecidos, usados nas asserções dos testes."""
    est = [
        # 1. matriz nova (10 dias), contato completo, bairro informado
        _estab("00012345", "0001", "1", "ALFA OBRAS", "02", ref - timedelta(days=10), "4399103",
               "9701", "RS", "CENTRO", "53", "32345678", "CONTATO@ALFA-OBRAS.COM.BR",
               cnaes_sec="4399199,4330404"),
        # 2. filial da mesma empresa, aberta exatamente no limite de 90 dias, sem bairro
        _estab("00012345", "0002", "2", "ALFA OBRAS FILIAL", "02", ref - timedelta(days=90), "4399103",
               "9702", "RS", "", "53", "32340000", ""),
        # 3. aberta há 91 dias: fora da janela
        _estab("11222333", "0001", "1", "", "02", ref - timedelta(days=91), "5611201",
               "9701", "RS", "CENTRO", "53", "30001111", "restaurante@exemplo.com"),
        # 4. MEI aberto hoje; e-mail com maiúsculas/minúsculas e telefone com espaço (preservar como está)
        _estab("44555666", "0001", "1", "PADARIA SÃO JOÃO", "02", ref, "1091102",
               "9702", "RS", "VILA NOVA", "53", " 99999000", "Joao.Silva@Exemplo.com"),
        # 5. baixada, aberta há 30 dias
        _estab("77888999", "0001", "1", "ENCERRADA", "08", ref - timedelta(days=30), "4781400",
               "9701", "RS", "CENTRO", "", "", "", data_situacao=ref - timedelta(days=5)),
        # 6. CNPJ alfanumérico (novo formato da Receita), aberta há 5 dias
        _estab("A1B2C3D4", "0001", "1", "TECH BETA", "02", ref - timedelta(days=5), "6201501",
               "9703", "SC", "", "47", "33334444", "oi@techbeta.dev"),
        # 7. data de início no futuro (inconsistência da fonte): não é nova
        _estab("55666777", "0001", "1", "FUTURA", "02", ref + timedelta(days=3), "5611201",
               "9703", "SC", "CENTRO", "47", "30002222", ""),
    ]
    est.append(list(est[2]))  # 8. linha duplicada do caso 3 (deve ser removida na silver)

    empresas = [
        ["00012345", "ALFA OBRAS E REFORMAS LTDA", "2062", "49", "150000,00", "01", ""],
        ["11222333", "AÇOUGUE & RESTAURANTE CIA LTDA", "2062", "49", "1500000,50", "05", ""],
        ["44555666", "JOAO DA SILVA 00011122233", "2135", "50", "5000,00", "01", ""],
        ["77888999", "ENCERRADA COMERCIO LTDA", "2062", "49", "10000,00", "03", ""],
        ["A1B2C3D4", "TECH BETA SISTEMAS S.A.", "2054", "49", "0,00", "00", ""],
        ["55666777", "FUTURA ALIMENTOS LTDA", "2062", "49", "20000,00", "01", ""],
    ]
    socios = [
        ["00012345", "2", "MARIA FICTICIA SOUZA", "***123456**", "49", _d(ref - timedelta(days=10)),
         "", "***000000**", "", "00", "4"],
        ["00012345", "2", "JOSÉ INVENTADO LIMA", "***654321**", "22", _d(ref - timedelta(days=10)),
         "", "***000000**", "", "00", "5"],
        ["11222333", "1", "HOLDING EXEMPLO LTDA", "99888777000166", "22", _d(ref - timedelta(days=91)),
         "", "***000000**", "", "00", "0"],
    ]
    simples = [
        ["00012345", "S", _d(ref - timedelta(days=10)), "0", "N", "0", "0"],
        ["44555666", "S", _d(ref), "0", "S", _d(ref), "0"],
        ["11222333", "N", "0", "0", "N", "0", "0"],
    ]
    return {"estabelecimentos": est, "empresas": empresas, "socios": socios, "simples": simples}


def _volume_sintetico(ref: date, n: int, semente: int = 42) -> dict[str, list[list[str]]]:
    """Gera `n` estabelecimentos adicionais para medir tempo e memória."""
    rnd = random.Random(semente)
    est, emp = [], []
    for i in range(n):
        basico = f"{90000000 + i:08d}"[-8:] if i < 10_000_000 else f"{i:08d}"
        mun, _, uf = rnd.choice(MUNICIPIOS)
        inicio = ref - timedelta(days=rnd.randint(0, 3650))
        est.append(_estab(basico, "0001", "1", f"FANTASIA {i}", rnd.choice(["02", "02", "02", "08"]),
                          inicio, rnd.choice(CNAES)[0], mun, uf, rnd.choice(["CENTRO", "", "BAIRRO X"]),
                          "53", f"3{i % 10_000_000:07d}", f"contato{i}@exemplo.com"))
        emp.append([basico, f"EMPRESA FICTICIA {i} LTDA", "2062", "49", "10000,00",
                    rnd.choice(["01", "03", "05"]), ""])
    return {"estabelecimentos": est, "empresas": emp}


def _csv_latin1(linhas: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(linhas)
    return buf.getvalue().encode("latin-1")


def _gravar_zip(pasta: Path, nome_zip: str, nome_interno: str, linhas: list[list[str]]) -> None:
    with zipfile.ZipFile(pasta / nome_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(nome_interno, _csv_latin1(linhas))


def gerar_amostra(bronze_dir: Path, mes: str, referencia: date, volume_extra: int = 0) -> Path:
    """Grava os 37 zips de um mês fictício em bronze/<mes>/zip. Devolve a pasta."""
    pasta = bronze_dir / mes / "zip"
    pasta.mkdir(parents=True, exist_ok=True)
    dados = casos_base(referencia)
    if volume_extra:
        extra = _volume_sintetico(referencia, volume_extra)
        dados["estabelecimentos"] += extra["estabelecimentos"]
        dados["empresas"] += extra["empresas"]

    sufixo = "K3241.K03200Y{n}.D60913."
    for prefixo, chave, interno in (("Estabelecimentos", "estabelecimentos", "ESTABELE"),
                                    ("Empresas", "empresas", "EMPRECSV"),
                                    ("Socios", "socios", "SOCIOCSV")):
        linhas = dados[chave]
        for n in range(10):
            parte = linhas[n::10]
            _gravar_zip(pasta, f"{prefixo}{n}.zip", sufixo.format(n=n) + interno, parte)

    _gravar_zip(pasta, "Simples.zip", "F.K03200$W.SIMPLES.CSV.D60913", dados["simples"])
    for nome, interno, linhas in (
        ("Cnaes", "CNAECSV", CNAES), ("Motivos", "MOTICSV", MOTIVOS),
        ("Municipios", "MUNICCSV", [(c, n) for c, n, _ in MUNICIPIOS]),
        ("Naturezas", "NATJUCSV", NATUREZAS), ("Paises", "PAISCSV", PAISES),
        ("Qualificacoes", "QUALSCSV", QUALIFICACOES),
    ):
        _gravar_zip(pasta, f"{nome}.zip", f"F.K03200$Z.D60913.{interno}", [list(x) for x in linhas])
    return pasta
