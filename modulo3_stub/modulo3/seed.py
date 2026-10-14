"""Dados fictícios para testar o CRM enquanto o módulo 3 real não existe.

Nomes, marcas, e-mails e CNPJs são inventados. Os e-mails usam o domínio
reservado `.test`, que não recebe mensagens. Os telefones parecem reais, por
isso o CRM não abre links externos quando a fonte é "simulado".
"""

from __future__ import annotations

import random
import unicodedata
from datetime import date, timedelta

from .modelos import Classificacao, Contato, ContatoReceita, Lead

MES_REFERENCIA = "2026-09"

PERFIS = {
    "amenities_hotel": {
        "nome": "Hotelaria – amenities",
        "produto": "Kit de amenities (sabonete, shampoo, condicionador)",
    },
    "higiene_restaurante": {
        "nome": "Restaurantes – higiene de lavatório",
        "produto": "Sabonete líquido e álcool em gel para lavatórios",
    },
}

MUNICIPIOS = [
    ("GRAMADO", "RS", "54", ["CENTRO", "PLANALTO", "AVENIDA CENTRAL", "CARNIEL"]),
    ("CANELA", "RS", "54", ["CENTRO", "SANTA TEREZINHA", "LARANJEIRAS"]),
    ("BENTO GONCALVES", "RS", "54", ["VALE DOS VINHEDOS", "CENTRO", "CIDADE ALTA"]),
    ("CAMBARA DO SUL", "RS", "54", ["CENTRO"]),
    ("NOVA PETROPOLIS", "RS", "54", ["CENTRO", "LINHA IMPERIAL"]),
    ("TORRES", "RS", "51", ["PRAIA GRANDE", "CENTRO", "PRAINHA"]),
    ("PORTO ALEGRE", "RS", "51", ["MOINHOS DE VENTO", "CIDADE BAIXA", "CENTRO HISTORICO"]),
    ("FLORIANOPOLIS", "SC", "48", ["LAGOA DA CONCEICAO", "JURERE", "CENTRO", "CAMPECHE"]),
    ("BOMBINHAS", "SC", "47", ["MARISCAL", "CENTRO", "QUATRO ILHAS"]),
    ("URUBICI", "SC", "49", ["CENTRO", "WARNOW"]),
    ("BALNEARIO CAMBORIU", "SC", "47", ["CENTRO", "PIONEIROS", "BARRA SUL"]),
    ("GAROPABA", "SC", "48", ["CENTRO", "SILVEIRA"]),
]

NOMES_HOTEL = [
    "Serra Azul", "Recanto das Hortênsias", "Mirante do Lago", "Casa da Figueira",
    "Bela Vista", "Porto das Pedras", "Solar das Araucárias", "Brisa do Mar",
    "Vale Verde", "Jardim de Pedra", "Rota do Sol", "Lagoa Clara", "Campos de Cima",
    "Vila do Bosque", "Ilha Serena", "Pedra Alta", "Morro dos Ventos", "Cerração",
    "Dois Rios", "Encanto da Serra", "Casa Amarela", "Refúgio do Vale", "Maré Mansa",
    "Quinta das Videiras", "Alto da Colina", "Pinhal", "Recanto Sul", "Farol Velho",
]
TIPOS_HOTEL = ["Pousada", "Pousada", "Hotel", "Hotel", "Hotel Boutique", "Hostel", "Apart Hotel"]
REDES_FICTICIAS = ["Rede Atlântica Hotéis", "Sono Bom Express"]

NOMES_RESTAURANTE = [
    "Cantina da Nona", "Bistrô do Largo", "Sabor do Campo", "Fogão Campeiro",
    "Café da Esquina", "Trattoria Bella", "Peixaria do Porto", "Armazém Serrano",
    "Galeto do Vale", "Empório Colonial", "Casa do Fondue", "Cozinha da Praça",
    "Parrilla do Sul", "Panela de Barro", "Bar e Restaurante Mirante", "Sushi da Ilha",
]

CNAE_HOTEL = [("5510801", "Hotéis"), ("5510802", "Apart-hotéis"), ("5590601", "Albergues, exceto assistenciais")]
CNAE_RESTAURANTE = [("5611201", "Restaurantes e similares"), ("5611203", "Lanchonetes, casas de chá, de sucos e similares")]
LOGRADOUROS = ["RUA DAS FLORES", "AVENIDA BORGES DE MEDEIROS", "RUA COBERTA", "ESTRADA GERAL",
               "RUA DOS IMIGRANTES", "AVENIDA ATLANTICA", "RUA SAO PEDRO", "RODOVIA SC 401"]


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "".join(c for c in sem_acento.lower() if c.isalnum())


def _dv_cnpj(base12: str) -> str:
    """Dígitos verificadores, aceitando CNPJ alfanumérico (valor = ASCII − 48)."""
    valores = [ord(c) - 48 for c in base12]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    r = sum(v * p for v, p in zip(valores, pesos1)) % 11
    d1 = 0 if r < 2 else 11 - r
    r = sum(v * p for v, p in zip(valores + [d1], pesos2)) % 11
    d2 = 0 if r < 2 else 11 - r
    return f"{d1}{d2}"


def _cnpj(rng: random.Random, filial: bool = False) -> str:
    basico = f"{rng.randint(60_000_000, 69_999_999)}"
    ordem = "0002" if filial else "0001"
    base = basico + ordem
    return base + _dv_cnpj(base)


def _telefone(rng: random.Random, celular: bool) -> str:
    if celular:
        return f"9{rng.randint(8100, 9999)}{rng.randint(1000, 9999)}"
    return f"3{rng.randint(200, 999)}{rng.randint(1000, 9999)}"


def _canal(contatos: list[Contato], receita: ContatoReceita) -> str | None:
    if any(c.tipo == "email" and c.confianca == "alta" for c in contatos):
        return "email"
    if any(c.tipo == "whatsapp" for c in contatos):
        return "whatsapp"
    if receita.email and "contabil" not in receita.email:
        return "email"
    if any(c.tipo == "formulario" for c in contatos):
        return "formulario"
    if receita.telefone_1:
        return "telefone"
    return None


def gerar(quantidade_hotel: int = 28, quantidade_restaurante: int = 16, semente: int = 7) -> list[Lead]:
    rng = random.Random(semente)
    hoje = date(2026, 9, 30)
    leads: list[Lead] = []

    def montar(perfil_id: str, idx: int) -> Lead:
        municipio, uf, ddd, bairros = rng.choice(MUNICIPIOS)
        bairro = rng.choice(bairros)
        if perfil_id == "amenities_hotel":
            tipo = rng.choice(TIPOS_HOTEL)
            nome = NOMES_HOTEL[idx % len(NOMES_HOTEL)]
            rede = rng.choice(REDES_FICTICIAS) if rng.random() < 0.18 else None
            fantasia = f"{rede.split()[0]} {municipio.title()}" if rede and rede.startswith("Sono") else f"{tipo} {nome}"
            cnae = CNAE_HOTEL[2] if tipo == "Hostel" else (CNAE_HOTEL[1] if tipo == "Apart Hotel" else CNAE_HOTEL[0])
            capital = rng.choice([50_000, 120_000, 200_000, 350_000, 600_000, 1_200_000, 2_500_000])
            padrao = "alto" if capital >= 1_000_000 or tipo == "Hotel Boutique" else ("econômico" if tipo == "Hostel" else "médio")
            quartos = {"alto": "30 a 60", "médio": "12 a 25", "econômico": "6 a 15"}[padrao]
        else:
            nome = NOMES_RESTAURANTE[idx % len(NOMES_RESTAURANTE)]
            fantasia = nome
            rede = None
            cnae = rng.choice(CNAE_RESTAURANTE)
            capital = rng.choice([20_000, 60_000, 100_000, 180_000, 400_000])
            padrao = "alto" if capital >= 300_000 else "médio"
            quartos = None

        razao = f"{fantasia.upper()} {'HOTELARIA' if perfil_id == 'amenities_hotel' else 'ALIMENTOS'} LTDA"
        razao = unicodedata.normalize("NFKD", razao).encode("ascii", "ignore").decode()
        filial = bool(rede) and rng.random() < 0.5
        abertura = hoje - timedelta(days=rng.randint(3, 85))
        porte = "DEMAIS" if capital >= 1_000_000 else ("EPP" if capital >= 300_000 else "ME")
        dominio = f"{_slug(fantasia)}.test"

        receita = ContatoReceita(ddd_1=ddd, telefone_1=_telefone(rng, celular=rng.random() < 0.5))
        sorteio = rng.random()
        if sorteio < 0.45:
            receita.email = f"contato@{dominio}"
        elif sorteio < 0.65:
            receita.email = f"fiscal@contabilidade{_slug(municipio)}.test"
        elif sorteio < 0.85:
            receita.email = f"{_slug(fantasia)[:12]}@mail.test"

        contatos: list[Contato] = []
        tem_site = rng.random() < 0.7
        if tem_site:
            site = f"https://www.{dominio}"
            contatos.append(Contato(tipo="site", valor=site, fonte="site oficial", url=site, confianca="alta"))
            setor = "reservas" if perfil_id == "amenities_hotel" else "contato"
            contatos.append(Contato(tipo="email", valor=f"{setor}@{dominio}", fonte="site oficial",
                                    url=f"{site}/contato", confianca="alta"))
            if rng.random() < 0.55:
                contatos.append(Contato(tipo="whatsapp", valor=f"{ddd}{_telefone(rng, celular=True)}",
                                        fonte="site oficial (link wa.me)", url=f"{site}/contato", confianca="alta"))
            if rng.random() < 0.4:
                contatos.append(Contato(tipo="formulario", valor=f"{site}/fale-conosco", fonte="site oficial",
                                        url=f"{site}/fale-conosco", confianca="alta"))
            if rng.random() < 0.5:
                contatos.append(Contato(tipo="rede_social", valor=f"https://instagram.com/{_slug(fantasia)[:20]}",
                                        fonte="site oficial", url=site, confianca="media"))
        if receita.email and "contabilidade" in receita.email:
            contatos.append(Contato(tipo="email", valor=receita.email, fonte="Receita Federal",
                                    confianca="media", observacao="pode ser do contador"))

        score = rng.randint(55, 97)
        if perfil_id == "amenities_hotel":
            singular = {"5510801": "Hotel", "5510802": "Apart-hotel", "5590601": "Albergue"}[cnae[0]]
            partes = [f"{singular} com quartos ({quartos} estimados)", f"padrão {padrao}"]
            if rede:
                partes.append(f"unidade da {rede}: fornecedores podem ser definidos pela rede")
                score = min(score, 70)
            produto = "Kit de amenities com marca própria do hotel" if padrao == "alto" else PERFIS[perfil_id]["produto"]
        else:
            partes = [f"{cnae[1].lower()}", f"capital de R$ {capital:,.0f}".replace(",", ".")]
            produto = PERFIS[perfil_id]["produto"]
        justificativa = "; ".join(partes) + "."

        lead = Lead(
            cnpj=_cnpj(rng, filial=filial),
            razao_social=razao,
            nome_fantasia=fantasia.upper(),
            matriz_filial="FILIAL" if filial else "MATRIZ",
            situacao_cadastral="ATIVA",
            data_inicio_atividade=abertura,
            cnae_principal=cnae[0],
            cnae_descricao=cnae[1],
            porte=porte,
            capital_social=float(capital),
            logradouro=rng.choice(LOGRADOUROS),
            numero=str(rng.randint(10, 2400)),
            bairro=bairro,
            municipio=municipio,
            uf=uf,
            cep=f"{rng.randint(88000, 95999)}{rng.randint(0, 999):03d}",
            contato_receita=receita,
            contatos=contatos,
            classificacao=Classificacao(
                perfil_id=perfil_id,
                perfil_versao=3 if perfil_id == "amenities_hotel" else 1,
                score=score,
                e_alvo=score >= 60,
                justificativa=justificativa,
                e_franquia=bool(rede) if perfil_id == "amenities_hotel" else None,
                marca=rede,
                padrao=padrao,
                produto_sugerido=produto,
                classificado_em="2026-10-02T03:14:00",
            ),
            mes_referencia=MES_REFERENCIA,
        )
        lead.canal_sugerido = _canal(contatos, receita)
        return lead

    vistos: set[str] = set()
    for perfil_id, quantidade in (("amenities_hotel", quantidade_hotel), ("higiene_restaurante", quantidade_restaurante)):
        for i in range(quantidade):
            lead = montar(perfil_id, i)
            if lead.cnpj not in vistos:
                vistos.add(lead.cnpj)
                leads.append(lead)
    return leads
