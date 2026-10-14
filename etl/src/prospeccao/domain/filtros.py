"""Filtros de consulta (seção 4.4 do escopo) e tradução para SQL parametrizado.

Regras gerais:
- critérios diferentes combinam com E;
- valores de um mesmo critério combinam com OU (ex.: dois portes, duas localidades);
- todo valor vindo do usuário vai como parâmetro, nunca concatenado no SQL;
- textos são comparados sem acento e sem diferenciar maiúsculas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from prospeccao.domain.empresa_nova import JANELA_PADRAO_DIAS, intervalo_empresa_nova
from prospeccao.domain.status import STATUS

SITUACOES = ("ATIVA", "SUSPENSA", "INAPTA", "BAIXADA", "NULA")
PORTES = ("MEI", "ME", "EPP", "DEMAIS", "NÃO INFORMADO")
MATRIZ_FILIAL = ("MATRIZ", "FILIAL")
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE",
    "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "EX",
)


class FiltroInvalidoError(ValueError):
    """Combinação ou valor de filtro inválido."""


def normalizar_texto(valor: str) -> str:
    """Minúsculas, sem acento e sem espaços nas pontas (mesma regra usada no SQL)."""
    sem_acento = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor)


@dataclass(frozen=True)
class Localidade:
    """Uma localidade: UF inteira, um município ou bairros de um município."""

    uf: str | None = None
    codigo_municipio: str | None = None
    bairros: tuple[str, ...] = ()

    def validar(self) -> None:
        if self.uf is None and self.codigo_municipio is None:
            raise FiltroInvalidoError("Localidade precisa de UF ou município")
        if self.uf is not None and self.uf.upper() not in UFS:
            raise FiltroInvalidoError(f"UF inválida: {self.uf}")
        if self.codigo_municipio is not None and not re.fullmatch(r"[0-9]{1,4}", self.codigo_municipio):
            raise FiltroInvalidoError(f"Código de município inválido: {self.codigo_municipio}")
        if self.bairros and self.codigo_municipio is None:
            raise FiltroInvalidoError("Filtro de bairro exige um município")


@dataclass(frozen=True)
class Periodo:
    """Período de abertura. Relativo (últimos N dias) ou com datas fixas; sempre inclusivo."""

    janela_dias: int | None = JANELA_PADRAO_DIAS
    inicio: date | None = None
    fim: date | None = None

    def resolver(self, referencia: date) -> tuple[date | None, date | None]:
        if self.janela_dias is not None:
            return intervalo_empresa_nova(referencia, self.janela_dias)
        return self.inicio, self.fim

    def validar(self) -> None:
        if self.janela_dias is not None and (self.inicio or self.fim):
            raise FiltroInvalidoError("Use janela em dias OU datas personalizadas, não os dois")
        if self.janela_dias is not None and self.janela_dias <= 0:
            raise FiltroInvalidoError("A janela deve ter pelo menos 1 dia")
        if self.inicio and self.fim and self.inicio > self.fim:
            raise FiltroInvalidoError("Data inicial maior que a final")


@dataclass(frozen=True)
class Filtros:
    localidades: tuple[Localidade, ...] = ()
    periodo: Periodo | None = field(default_factory=Periodo)
    cnae_codigos: tuple[str, ...] = ()
    cnae_prefixos: tuple[str, ...] = ()
    cnae_palavra: str | None = None
    incluir_cnaes_secundarios: bool = False
    portes: tuple[str, ...] = ()
    naturezas: tuple[str, ...] = ()
    situacoes: tuple[str, ...] = ("ATIVA",)
    capital_min: float | None = None
    capital_max: float | None = None
    matriz_filial: str | None = None
    simples: bool | None = None
    tem_telefone_receita: bool | None = None
    tem_email_receita: bool | None = None
    tem_contato_complementar: bool | None = None
    palavra_chave: str | None = None
    status: tuple[str, ...] = ()  # status de prospecção (chaves de app_store.STATUS)

    def validar(self) -> None:
        for loc in self.localidades:
            loc.validar()
        if self.periodo:
            self.periodo.validar()
        _dentro(self.portes, PORTES, "porte")
        _dentro(self.situacoes, SITUACOES, "situação cadastral")
        _dentro(self.status, tuple(STATUS), "status de prospecção")
        if self.matriz_filial is not None:
            _dentro((self.matriz_filial,), MATRIZ_FILIAL, "matriz/filial")
        for codigo in self.cnae_codigos:
            if len(so_digitos(codigo)) != 7:
                raise FiltroInvalidoError(f"CNAE exato deve ter 7 dígitos: {codigo}")
        for prefixo in self.cnae_prefixos:
            if not 1 <= len(so_digitos(prefixo)) <= 7:
                raise FiltroInvalidoError(f"Prefixo de CNAE inválido: {prefixo}")
        for natureza in self.naturezas:
            if not re.fullmatch(r"[0-9]{4}", natureza):
                raise FiltroInvalidoError(f"Natureza jurídica deve ter 4 dígitos: {natureza}")
        if self.capital_min is not None and self.capital_max is not None and self.capital_min > self.capital_max:
            raise FiltroInvalidoError("Capital mínimo maior que o máximo")

    # Serialização para consultas salvas (Etapa 5)
    def para_dict(self) -> dict[str, Any]:
        dados = asdict(self)
        if self.periodo:
            dados["periodo"] = {
                "janela_dias": self.periodo.janela_dias,
                "inicio": self.periodo.inicio.isoformat() if self.periodo.inicio else None,
                "fim": self.periodo.fim.isoformat() if self.periodo.fim else None,
            }
        return dados

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> Filtros:
        dados = dict(dados)
        dados["localidades"] = tuple(
            Localidade(l.get("uf"), l.get("codigo_municipio"), tuple(l.get("bairros", ())))
            for l in dados.get("localidades", ())
        )
        p = dados.get("periodo")
        dados["periodo"] = None if p is None else Periodo(
            p.get("janela_dias"),
            date.fromisoformat(p["inicio"]) if p.get("inicio") else None,
            date.fromisoformat(p["fim"]) if p.get("fim") else None,
        )
        for chave in ("cnae_codigos", "cnae_prefixos", "portes", "naturezas", "situacoes", "status"):
            if chave in dados:
                dados[chave] = tuple(dados[chave])
        return cls(**dados)


def _dentro(valores: tuple[str, ...], permitidos: tuple[str, ...], nome: str) -> None:
    for v in valores:
        if v not in permitidos:
            raise FiltroInvalidoError(f"{nome.capitalize()} inválido(a): {v}. Use: {', '.join(permitidos)}")


def _sem_branco(coluna: str) -> str:
    return f"NULLIF(trim({coluna}), '') IS NOT NULL"


def _norm_sql(coluna: str) -> str:
    return f"strip_accents(lower({coluna}))"


class _Montador:
    def __init__(self) -> None:
        self.condicoes: list[str] = []
        self.params: list[Any] = []

    def add(self, sql: str, *params: Any) -> None:
        self.condicoes.append(sql)
        self.params.extend(params)

    def lista(self, valores: tuple[Any, ...] | list[Any]) -> str:
        self.params.extend(valores)
        return ", ".join("?" for _ in valores)


def _localidades(m: _Montador, locs: tuple[Localidade, ...]) -> None:
    """Municípios sem bairro viram um único IN (o DuckDB usa isso para pular blocos);
    UFs inteiras e municípios com bairro entram como condições adicionais em OU."""
    so_municipio = [l.codigo_municipio.zfill(4) for l in locs if l.codigo_municipio and not l.bairros]
    so_uf = [l.uf.upper() for l in locs if l.uf and not l.codigo_municipio]
    com_bairro = [l for l in locs if l.codigo_municipio and l.bairros]
    partes: list[str] = []
    if so_municipio:
        partes.append(f"codigo_municipio_receita IN ({m.lista(so_municipio)})")
    if so_uf:
        partes.append(f"uf IN ({m.lista(so_uf)})")
    for loc in com_bairro:
        nomes = [normalizar_texto(b) for b in loc.bairros]
        m.params.append(loc.codigo_municipio.zfill(4))
        partes.append(
            f"(codigo_municipio_receita = ? AND {_norm_sql('trim(bairro)')} IN ({m.lista(nomes)}))"
        )
    m.condicoes.append("(" + " OR ".join(partes) + ")")


def regra_cnae_sql(f: Filtros) -> tuple[str, list[Any]] | None:
    """Condição sobre a tabela de domínio `cnae` para prefixos e palavra-chave (None se não houver)."""
    regras: list[str] = []
    params: list[Any] = []
    for prefixo in f.cnae_prefixos:
        regras.append("starts_with(codigo, ?)")
        params.append(so_digitos(prefixo))
    if f.cnae_palavra:
        regras.append(f"contains({_norm_sql('descricao')}, ?)")
        params.append(normalizar_texto(f.cnae_palavra))
    return (" OR ".join(regras), params) if regras else None


def _cnae(m: _Montador, f: Filtros, cnaes_alvo: list[str]) -> None:
    """`cnaes_alvo` = códigos já resolvidos (exatos + prefixos + palavra), vindos da tabela de domínio."""
    if not cnaes_alvo:
        m.condicoes.append("false")
        return
    condicao = f"cnae_principal IN ({m.lista(cnaes_alvo)})"
    if f.incluir_cnaes_secundarios:
        condicao = f"({condicao} OR list_has_any(cnaes_secundarios, ?::VARCHAR[]))"
        m.params.append(cnaes_alvo)
    m.condicoes.append(condicao)


def _contatos(m: _Montador, f: Filtros, cnpjs_com_contato: list[str] | None) -> None:
    if f.tem_telefone_receita is not None:
        tem = f"({_sem_branco('telefone_receita_1')} OR {_sem_branco('telefone_receita_2')})"
        m.add(tem if f.tem_telefone_receita else f"NOT {tem}")
    if f.tem_email_receita is not None:
        tem = _sem_branco("email_receita")
        m.add(tem if f.tem_email_receita else f"NOT ({tem})")
    if f.tem_contato_complementar is not None:
        if cnpjs_com_contato is None:
            raise FiltroInvalidoError("Filtro de contato complementar precisa da lista de CNPJs (cnpjs_com_contato)")
        existe = "list_contains(?::VARCHAR[], cnpj)"
        m.add(existe if f.tem_contato_complementar else f"NOT {existe}", cnpjs_com_contato)


@dataclass(frozen=True)
class ContextoConsulta:
    """Dados de fora da base gold necessários a alguns filtros (resolvidos pelo repositório)."""

    cnaes_alvo: list[str] | None = None
    cnpjs_com_contato: list[str] | None = None
    cnpjs_por_status: dict[str, list[str]] | None = None
    somente_cnpjs: list[str] | None = None  # ex.: "só as novas desde a última execução"


def _status(m: _Montador, status: tuple[str, ...], mapa: dict[str, list[str]] | None) -> None:
    if mapa is None:
        raise FiltroInvalidoError("Filtro de status precisa do mapa de status (cnpjs_por_status)")
    partes: list[str] = []
    for s in status:
        if s == "nao_prospectada":
            com_outro_status = [c for chave, lista in mapa.items() if chave != "nao_prospectada" for c in lista]
            partes.append("NOT list_contains(?::VARCHAR[], cnpj)")
            m.params.append(com_outro_status)
        else:
            partes.append("list_contains(?::VARCHAR[], cnpj)")
            m.params.append(mapa.get(s, []))
    m.condicoes.append("(" + " OR ".join(partes) + ")")


def usa_cnae(f: Filtros) -> bool:
    return bool(f.cnae_codigos or f.cnae_prefixos or f.cnae_palavra)


def montar_where(f: Filtros, referencia: date, ctx: ContextoConsulta | None = None) -> tuple[str, list[Any]]:
    """Devolve (cláusula WHERE sem a palavra WHERE, parâmetros). '1=1' se não houver filtro.

    Se houver filtro de CNAE, os códigos já resolvidos devem vir
    em `ctx.cnaes_alvo` (ver `Repositorio.resolver_cnaes`): uma lista fixa é muito mais rápida que subconsulta.
    """
    f.validar()
    ctx = ctx or ContextoConsulta()
    cnaes_alvo, cnpjs_com_contato = ctx.cnaes_alvo, ctx.cnpjs_com_contato
    m = _Montador()
    if f.localidades:
        _localidades(m, f.localidades)
    if f.periodo:
        inicio, fim = f.periodo.resolver(referencia)
        if inicio:
            m.add("data_inicio_atividade >= ?", inicio)
        if fim:
            m.add("data_inicio_atividade <= ?", fim)
    if usa_cnae(f):
        if cnaes_alvo is None:
            raise FiltroInvalidoError("Filtro de CNAE precisa dos códigos resolvidos (cnaes_alvo)")
        _cnae(m, f, cnaes_alvo)
    if f.portes:
        m.add(f"porte IN ({m.lista(f.portes)})")
    if f.naturezas:
        m.add(f"natureza_juridica_codigo IN ({m.lista(f.naturezas)})")
    if f.situacoes:
        m.add(f"situacao_cadastral IN ({m.lista(f.situacoes)})")
    if f.capital_min is not None:
        m.add("capital_social >= ?", f.capital_min)
    if f.capital_max is not None:
        m.add("capital_social <= ?", f.capital_max)
    if f.matriz_filial:
        m.add("matriz_filial = ?", f.matriz_filial)
    if f.simples is not None:
        m.add("opcao_simples IS TRUE" if f.simples else "opcao_simples IS NOT TRUE")
    _contatos(m, f, cnpjs_com_contato)
    if f.status:
        _status(m, f.status, ctx.cnpjs_por_status)
    if ctx.somente_cnpjs is not None:
        m.add("list_contains(?::VARCHAR[], cnpj)", ctx.somente_cnpjs)
    if f.palavra_chave and f.palavra_chave.strip():
        termo = normalizar_texto(f.palavra_chave)
        m.add(
            f"(contains({_norm_sql('razao_social')}, ?) OR contains({_norm_sql('nome_fantasia')}, ?))",
            termo, termo,
        )
    return (" AND ".join(m.condicoes) or "1=1"), m.params
