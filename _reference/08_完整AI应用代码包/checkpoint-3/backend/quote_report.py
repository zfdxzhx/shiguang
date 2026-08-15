"""Small, source-labelled PDF for the independent classroom quote feature."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf_report import GREEN, GREEN_PALE, INK, LINE, MUTED, _register_font


def _text(value: object) -> str:
    return escape(str(value or "-").strip()).replace("\n", "<br/>")


def _money(value: object) -> str:
    try:
        return f"¥{float(value):,.2f}"
    except (TypeError, ValueError):
        return "¥0.00"


def build_quote_report_pdf(output: dict, destination: Path) -> Path:
    quote = output.get("prequote") or {}
    document = output.get("document") or {}
    profile = output.get("reference_profile") or {}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    font = _register_font()
    body = ParagraphStyle("QuoteBody", fontName=font, fontSize=9, leading=13, textColor=INK, wordWrap="CJK", alignment=TA_LEFT)
    small = ParagraphStyle("QuoteSmall", parent=body, fontSize=7.5, leading=10, textColor=MUTED)
    title = ParagraphStyle("QuoteTitle", parent=body, fontSize=22, leading=27, textColor=INK)
    h2 = ParagraphStyle("QuoteH2", parent=body, fontSize=12, leading=16, textColor=GREEN, spaceBefore=4 * mm, spaceAfter=1.5 * mm)

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, 12 * mm, A4[0] - 15 * mm, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(font, 7)
        canvas.drawString(15 * mm, 7.5 * mm, "AI 参考报价单 · 非正式报价")
        canvas.drawRightString(A4[0] - 15 * mm, 7.5 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    story: list = [
        Paragraph("AI 参考报价单", title),
        Paragraph("AI＋公开资料补齐参考条件，金额由确定性公式计算", small),
        Spacer(1, 3 * mm),
    ]
    badge = Table([[Paragraph("课堂估算 · 不对客户生效", body)]], colWidths=[180 * mm])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_PALE),
        ("BOX", (0, 0), (-1, -1), 0.7, GREEN),
        ("TEXTCOLOR", (0, 0), (-1, -1), GREEN),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([badge, Spacer(1, 3 * mm)])

    meta = [
        [Paragraph("来源图纸", small), Paragraph(_text(document.get("filename")), body), Paragraph("估算数量", small), Paragraph(f"{_text(quote.get('quantity'))} 件", body)],
        [Paragraph("匹配场景", small), Paragraph(_text(profile.get("manufacturing_family")), body), Paragraph("公式版本", small), Paragraph(_text(quote.get("formula_version")), body)],
        [Paragraph("生成时间", small), Paragraph(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"), body), Paragraph("币种", small), Paragraph(_text(quote.get("currency", "CNY")), body)],
    ]
    meta_table = Table(meta, colWidths=[22 * mm, 68 * mm, 22 * mm, 68 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([meta_table, Paragraph("估算结果", h2)])

    kpis = Table([[Paragraph(f"<b>单件参考报价</b><br/><font size='20'>{_money(quote.get('unit_prequote'))}</font>", body), Paragraph(f"<b>本批总成本</b><br/><font size='16'>{_money(quote.get('total_cost'))}</font>", body), Paragraph(f"<b>目标收入</b><br/><font size='16'>{_money(quote.get('target_revenue'))}</font>", body)]], colWidths=[60 * mm] * 3)
    kpis.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F6F3")),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([kpis, Paragraph("成本明细", h2)])

    rows = [[Paragraph("成本项", small), Paragraph("计算依据", small), Paragraph("金额", small)]]
    for item in quote.get("cost_items") or []:
        rows.append([Paragraph(_text(item.get("label")), body), Paragraph(_text(item.get("basis")), small), Paragraph(_money(item.get("amount")), body)])
    costs = Table(rows, colWidths=[38 * mm, 104 * mm, 38 * mm], repeatRows=1)
    costs.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7ECE8")),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(costs)

    story.append(Paragraph("估算依据与边界", h2))
    for assumption in output.get("assumptions") or []:
        story.append(Paragraph(f"• {_text(assumption)}", small))
    sources = output.get("sources") or []
    if sources:
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph("公开资料来源：" + "；".join(_text(item.get("title")) for item in sources[:4]), small))
    story.extend([Spacer(1, 2 * mm), Paragraph(_text(output.get("boundary")), small)])

    doc = SimpleDocTemplate(str(destination), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=17 * mm, title="AI 参考报价单")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return destination
