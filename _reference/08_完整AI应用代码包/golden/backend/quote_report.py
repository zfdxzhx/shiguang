"""Small, source-labelled PDF for the independent classroom quote feature."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf_report import (
    AMBER,
    AMBER_PALE,
    BLUE,
    BLUE_PALE,
    GREEN,
    GREEN_PALE,
    INK,
    LINE,
    MUTED,
    PAPER,
    RED,
    RED_PALE,
    _register_font,
)


def _text(value: object, *, limit: int | None = None) -> str:
    raw = str(value or "-").strip()
    raw = raw.replace("本参考包", "本参考报价")
    raw = raw.replace("本批一次性输入", "本批课堂假设（待确认）")
    raw = raw.replace(
        "材料、工时、费率和附加费用由 AI 结合带来源的课堂参考包自动补齐",
        "材料、工时、费率和附加费用为系统自动补齐的课堂假设",
    )
    if limit and len(raw) > limit:
        raw = raw[: max(1, limit - 3)].rstrip("，；、,. ") + "..."
    return escape(raw).replace("\n", "<br/>")


def _money(value: object) -> str:
    try:
        return f"¥{float(value):,.2f}"
    except (TypeError, ValueError):
        return "¥0.00"


def _source_label(source: dict) -> str:
    known = {
        "nbs-ppi-2026-06": "国家统计局 PPI",
        "nist-cost-guide": "NIST 制造成本指南",
        "shfe-daily-market": "上期所市场数据",
        "iso-286-2": "ISO 286-2 公差标准",
    }
    source_id = str(source.get("id") or "")
    return known.get(source_id, str(source.get("title") or source_id or "未命名来源"))


def build_quote_report_pdf(output: dict, destination: Path) -> Path:
    quote = output.get("prequote") or {}
    document = output.get("document") or {}
    profile = output.get("reference_profile") or {}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    font = _register_font()
    body = ParagraphStyle("QuoteBody", fontName=font, fontSize=8.8, leading=12.2, textColor=INK, wordWrap="CJK", alignment=TA_LEFT)
    small = ParagraphStyle("QuoteSmall", parent=body, fontSize=7.4, leading=9.8, textColor=MUTED)
    title = ParagraphStyle("QuoteTitle", parent=body, fontSize=21, leading=26, textColor=INK)
    h2 = ParagraphStyle("QuoteH2", parent=body, fontSize=12.5, leading=16, textColor=GREEN, spaceBefore=3.2 * mm, spaceAfter=1.3 * mm)
    centered = ParagraphStyle("QuoteCenter", parent=body, alignment=TA_CENTER)
    badge_style = ParagraphStyle("QuoteBadge", parent=centered, fontSize=7.8, leading=10.5, textColor=GREEN)
    money_style = ParagraphStyle("QuoteMoney", parent=body, alignment=TA_RIGHT)

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, 12 * mm, A4[0] - 15 * mm, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(font, 7)
        canvas.drawString(15 * mm, 7.5 * mm, "AI 参考报价单 · 课堂估算")
        canvas.drawRightString(A4[0] - 15 * mm, 7.5 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    title_row = Table(
        [[Paragraph("AI 参考报价单", title), Paragraph("公式已计算<br/>输入待确认", badge_style)]],
        colWidths=[134 * mm, 46 * mm],
    )
    title_row.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), GREEN_PALE),
        ("BOX", (1, 0), (1, 0), 0.7, GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 5),
        ("RIGHTPADDING", (1, 0), (1, 0), 5),
        ("TOPPADDING", (1, 0), (1, 0), 5),
        ("BOTTOMPADDING", (1, 0), (1, 0), 5),
    ]))
    story: list = [
        title_row,
        Spacer(1, 1.5 * mm),
        Paragraph("AI 提取图纸信息，课堂假设补齐计算条件；金额由固定公式计算", small),
        Spacer(1, 2.5 * mm),
    ]
    notice = Table([[Paragraph("<b>课堂估算</b> · 未含税 · 不对客户生效", body)]], colWidths=[180 * mm])
    notice.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_PALE),
        ("BOX", (0, 0), (-1, -1), 0.7, AMBER),
        ("TEXTCOLOR", (0, 0), (-1, -1), AMBER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([notice, Spacer(1, 2.5 * mm)])

    family_labels = {
        "cnc_machining": "CNC 机加工",
        "sheet_metal": "钣金加工",
        "injection_molding": "注塑成型",
        "assembly": "装配",
    }
    family = family_labels.get(profile.get("manufacturing_family"), profile.get("manufacturing_family"))

    meta = [
        [Paragraph("来源图纸", small), Paragraph(_text(document.get("filename")), body), Paragraph("估算数量", small), Paragraph(f"{_text(quote.get('quantity'))} 件", body)],
        [Paragraph("参考场景", small), Paragraph(_text(family), body), Paragraph("公式版本", small), Paragraph(_text(quote.get("formula_version")), body)],
        [Paragraph("生成时间", small), Paragraph(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"), body), Paragraph("币种", small), Paragraph(_text(quote.get("currency", "CNY")), body)],
    ]
    meta_table = Table(meta, colWidths=[22 * mm, 68 * mm, 22 * mm, 68 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([meta_table, Paragraph("核心结果", h2)])

    kpis = Table([[Paragraph(f"<b>单件参考报价</b><br/><font size='20' color='#176B4D'>{_money(quote.get('unit_prequote'))}</font>", body), Paragraph(f"<b>估算总成本</b><br/><font size='16'>{_money(quote.get('total_cost'))}</font>", body), Paragraph(f"<b>估算目标收入</b><br/><font size='16'>{_money(quote.get('target_revenue'))}</font>", body)]], colWidths=[60 * mm] * 3)
    kpis.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), GREEN_PALE),
        ("BACKGROUND", (1, 0), (-1, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([kpis, Paragraph("计算链路", h2)])

    quantity = quote.get("quantity") or 0
    target_margin = (quote.get("inputs") or {}).get("target_margin_pct") or 0
    formula = Table(
        [[
            Paragraph(
                f"<b>直接成本</b> {_money(quote.get('direct_cost'))}"
                f" 　+　 <b>管理费</b> {_money(quote.get('overhead_cost'))}"
                f" 　+　 <b>风险费</b> {_money(quote.get('risk_cost'))}"
                f" 　=　 <b>总成本</b> {_money(quote.get('total_cost'))}<br/>"
                f"按 {float(target_margin):g}% 目标毛利率换算目标收入 {_money(quote.get('target_revenue'))}"
                f" ÷ {quantity} 件 = <b>{_money(quote.get('unit_prequote'))}/件</b>",
                centered,
            )
        ]],
        colWidths=[180 * mm],
    )
    formula.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLUE_PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([formula, Paragraph("成本估算明细", h2)])

    rows = [[Paragraph("成本项", small), Paragraph("计算依据（课堂假设，待确认）", small), Paragraph("金额", small)]]
    for item in quote.get("cost_items") or []:
        rows.append([Paragraph(_text(item.get("label")), body), Paragraph(_text(item.get("basis")), small), Paragraph(_money(item.get("amount")), money_style)])
    rows.append([Paragraph("<b>直接成本小计</b>", body), Paragraph("上述成本项合计", small), Paragraph(f"<b>{_money(quote.get('direct_cost'))}</b>", money_style)])
    costs = Table(rows, colWidths=[38 * mm, 104 * mm, 38 * mm], repeatRows=1)
    cost_style = [
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7ECE8")),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("BACKGROUND", (0, -1), (-1, -1), GREEN_PALE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, GREEN),
    ]
    for row_index in range(2, len(rows) - 1, 2):
        cost_style.append(("BACKGROUND", (0, row_index), (-1, row_index), PAPER))
    costs.setStyle(TableStyle(cost_style))
    story.append(costs)

    story.append(Paragraph("假设、风险与来源", h2))
    assumptions = quote.get("assumptions") or output.get("assumptions") or []
    warnings = quote.get("warnings") or []
    guidance = Table(
        [[
            Paragraph("<b>计算假设</b><br/>" + "<br/>".join(f"• {_text(item, limit=64)}" for item in assumptions[:3]), small),
            Paragraph("<b>报价风险</b><br/>" + "<br/>".join(f"• {_text(item, limit=64)}" for item in warnings[:3]), small),
        ]],
        colWidths=[90 * mm, 90 * mm],
    )
    guidance.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), BLUE_PALE),
        ("BACKGROUND", (1, 0), (1, 0), AMBER_PALE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(guidance)
    sources = output.get("sources") or []
    if sources:
        story.append(Spacer(1, 1.2 * mm))
        story.append(Paragraph("<b>公开参考：</b>" + " · ".join(_text(_source_label(item), limit=28) for item in sources[:4]), small))
    boundary = Table(
        [[Paragraph("<b>使用边界</b>", body), Paragraph(_text(output.get("boundary"), limit=220), small)]],
        colWidths=[24 * mm, 156 * mm],
    )
    boundary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RED_PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, RED),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([Spacer(1, 1.7 * mm), boundary])

    doc = SimpleDocTemplate(str(destination), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=17 * mm, title="AI 参考报价单")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return destination
