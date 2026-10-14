"""Relatório PDF (ReportLab, sem dependências de sistema): resumo, filtros, data da base,
total, gráficos de distribuição (CNAE, porte, mês de abertura) e tabela dos top N prospects."""

from __future__ import annotations

from typing import Any, BinaryIO

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from prospeccao.domain.cnpj import formatar_cnpj
from prospeccao.reports.exportacao import AVISO_LGPD, Metadados

COR = colors.HexColor("#1f4e79")
_ESTILOS = getSampleStyleSheet()
TITULO = ParagraphStyle("titulo", parent=_ESTILOS["Title"], textColor=COR, fontSize=18, spaceAfter=4)
SUBTITULO = ParagraphStyle("sub", parent=_ESTILOS["Heading2"], textColor=COR, fontSize=12, spaceBefore=10, spaceAfter=4)
NORMAL = ParagraphStyle("normal", parent=_ESTILOS["Normal"], fontSize=9, leading=11)
CELULA = ParagraphStyle("celula", parent=NORMAL, fontSize=7.5, leading=9)
NOTA = ParagraphStyle("nota", parent=NORMAL, fontSize=7.5, textColor=colors.HexColor("#555555"))


def _esc(texto: Any) -> str:
    return "" if texto is None else str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tabela_simples(linhas: list[tuple[str, str]], larguras: tuple[float, float]) -> Table:
    dados = [[Paragraph(f"<b>{_esc(a)}</b>", NORMAL), Paragraph(_esc(b), NORMAL)] for a, b in linhas]
    t = Table(dados, colWidths=larguras, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _passo(valores: list[int]) -> int:
    """Passo inteiro do eixo (evita marcas repetidas como 0, 0, 1, 1)."""
    maior = max(valores or [1])
    return max(1, -(-maior // 5))


def _grafico_barras_h(titulo: str, rotulos: list[str], valores: list[int], largura: float, altura: float) -> Drawing:
    d = Drawing(largura, altura)
    d.add(String(0, altura - 12, titulo, fontName="Helvetica-Bold", fontSize=9, fillColor=COR))
    if not valores:
        d.add(String(0, altura - 30, "Sem dados", fontName="Helvetica", fontSize=8))
        return d
    g = HorizontalBarChart()
    g.x, g.y = largura * 0.45, 15
    g.width, g.height = largura * 0.5, altura - 40
    g.data = [list(reversed(valores))]
    g.categoryAxis.categoryNames = list(reversed([r[:48] for r in rotulos]))
    g.categoryAxis.labels.fontSize = 6.5
    g.categoryAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.boxAnchor = "e"
    g.valueAxis.valueMin = 0
    g.valueAxis.valueStep = _passo(valores)
    g.valueAxis.labels.fontSize = 6.5
    g.valueAxis.labels.fontName = "Helvetica"
    g.valueAxis.labelTextFormat = "%d"
    g.bars[0].fillColor = COR
    g.bars[0].strokeColor = None
    g.barLabelFormat = "%d"
    g.barLabels.fontSize = 6
    g.barLabels.fontName = "Helvetica"
    g.barLabels.nudge = 7
    d.add(g)
    return d


def _grafico_barras_v(titulo: str, rotulos: list[str], valores: list[int], largura: float, altura: float) -> Drawing:
    d = Drawing(largura, altura)
    d.add(String(0, altura - 12, titulo, fontName="Helvetica-Bold", fontSize=9, fillColor=COR))
    if not valores:
        d.add(String(0, altura - 30, "Sem dados", fontName="Helvetica", fontSize=8))
        return d
    g = VerticalBarChart()
    g.x, g.y = 30, 30
    g.width, g.height = largura - 40, altura - 55
    g.data = [valores]
    g.categoryAxis.categoryNames = rotulos
    g.categoryAxis.labels.fontSize = 6
    g.categoryAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.angle = 45 if len(rotulos) > 12 else 0
    g.categoryAxis.labels.boxAnchor = "ne" if len(rotulos) > 12 else "n"
    g.valueAxis.valueMin = 0
    g.valueAxis.valueStep = _passo(valores)
    g.valueAxis.labels.fontSize = 6.5
    g.valueAxis.labels.fontName = "Helvetica"
    g.valueAxis.labelTextFormat = "%d"
    g.bars[0].fillColor = COR
    g.bars[0].strokeColor = None
    d.add(g)
    return d


def _rodape(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(1.5 * cm, 0.8 * cm, "Prospecção de empresas novas · dados públicos do CNPJ (Receita Federal)")
    canvas.drawRightString(doc.pagesize[0] - 1.5 * cm, 0.8 * cm, f"Página {doc.page}")
    canvas.restoreState()


def escrever_pdf(destino: BinaryIO, meta: Metadados, indicadores: dict[str, Any], top: list[dict[str, Any]]) -> None:
    pagina = landscape(A4)
    largura = pagina[0] - 3 * cm
    doc = SimpleDocTemplate(destino, pagesize=pagina, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.3 * cm, bottomMargin=1.4 * cm, title="Relatório de prospecção",
                            author=meta.usuario or "prospeccao")
    h: list[Any] = [Paragraph("Relatório de prospecção – empresas recém-abertas", TITULO)]
    h.append(Paragraph(
        f"Gerado em {meta.gerado_em:%d/%m/%Y %H:%M}" + (f" por {_esc(meta.usuario)}" if meta.usuario else ""), NOTA))

    h.append(Paragraph("Resumo", SUBTITULO))
    h.append(_tabela_simples(meta.linhas()[:2] + meta.linhas()[4:], (6 * cm, largura - 6 * cm)))
    h.append(Paragraph("Filtros aplicados", SUBTITULO))
    h.append(_tabela_simples(meta.filtros, (6 * cm, largura - 6 * cm)))

    metade = largura / 2 - 0.3 * cm
    cnae = indicadores.get("top_cnae", [])
    porte = indicadores.get("por_porte", [])
    graf_cnae = _grafico_barras_h("Top 10 atividades (CNAE principal)",
                                  [f"{c['codigo']} {c['descricao'] or ''}" for c in cnae], [c["total"] for c in cnae],
                                  metade, 150)
    graf_porte = _grafico_barras_h("Por porte", [p["porte"] for p in porte], [p["total"] for p in porte], metade, 150)
    meses = indicadores.get("por_mes", [])[-36:]
    graf_mes = _grafico_barras_v("Por mês de abertura" + (" (últimos 36 meses)" if len(indicadores.get("por_mes", [])) > 36 else ""),
                                 [m["mes"] for m in meses], [m["total"] for m in meses], largura, 140)
    h.append(KeepTogether([Paragraph("Distribuição", SUBTITULO),
                           Table([[graf_cnae, graf_porte]], colWidths=(metade + 0.3 * cm, metade + 0.3 * cm))]))
    h.append(KeepTogether([Spacer(1, 6), graf_mes]))

    h.append(Paragraph(f"Top {len(top)} prospects (aberturas mais recentes)", SUBTITULO))
    cab = ["CNPJ", "Empresa", "Abertura", "CNAE / atividade", "Município/UF", "DDD", "Telefone<br/>(Receita)",
           "E-mail (Receita)", "Status"]
    dados: list[list[Any]] = [[Paragraph(f"<b>{c}</b>", CELULA) for c in cab]]
    for l in top:
        nome = l.get("nome_fantasia") or l.get("razao_social") or ""
        dados.append([
            Paragraph(formatar_cnpj(l["cnpj"]), CELULA),
            Paragraph(_esc(nome) + (f"<br/><font color='#777777'>{_esc(l.get('razao_social'))}</font>"
                                    if l.get("nome_fantasia") else ""), CELULA),
            Paragraph(f"{l['data_inicio_atividade']:%d/%m/%Y}" if l.get("data_inicio_atividade") else "", CELULA),
            Paragraph(f"{_esc(l.get('cnae_principal'))} {_esc(l.get('cnae_principal_descricao'))}", CELULA),
            Paragraph(f"{_esc(l.get('municipio'))}/{_esc(l.get('uf'))}", CELULA),
            Paragraph(_esc(l.get("ddd_receita_1")), CELULA),
            Paragraph(_esc(l.get("telefone_receita_1")), CELULA),  # exatamente como na Receita
            Paragraph(_esc(l.get("email_receita")), CELULA),
            Paragraph(_esc(l.get("status_prospeccao")), CELULA),
        ])
    larguras = [3.4, 4.4, 2.0, 4.6, 3.2, 1.2, 2.2, 4.4, 2.4]
    escala = largura / (sum(larguras) * cm)
    tabela = Table(dados, colWidths=[w * cm * escala for w in larguras], repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ]))
    h.append(tabela)
    h.append(Spacer(1, 10))
    h.append(Paragraph(f"<b>Uso dos dados (LGPD).</b> {AVISO_LGPD}", NOTA))
    h.append(Paragraph(
        "A data de início de atividade é a da empresa; em mudanças de sede ou de município ela pode ser anterior "
        "ao cadastro atual.", NOTA))
    doc.build(h, onFirstPage=_rodape, onLaterPages=_rodape)
