"""《图纸 AI 审核报告》PDF 生成。

结论统一为「待工程确认」；报告标注为课堂审核草稿，非正式工程批准。
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from .models import ReviewResult

_REPORT_TITLE = "图纸 AI 审核报告"
_CONCLUSION = "待工程确认"

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

STYLES = getSampleStyleSheet()
TITLE = ParagraphStyle("CNTitle", parent=STYLES["Title"], fontName="STSong-Light", fontSize=20, leading=26)
H2 = ParagraphStyle("CNH2", parent=STYLES["Heading2"], fontName="STSong-Light", fontSize=13, leading=18)
BODY = ParagraphStyle("CNBody", parent=STYLES["BodyText"], fontName="STSong-Light", fontSize=10, leading=15)
MUTED = ParagraphStyle("CNMuted", parent=BODY, textColor=colors.grey, fontSize=9, leading=13)


def build_report_pdf(result: ReviewResult) -> bytes:
    """把审核结果渲染为 PDF 字节流。"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"{_REPORT_TITLE}｜{result.filename}",
    )

    story: list = []
    story.append(Paragraph(_REPORT_TITLE, TITLE))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"文件：{result.filename}", BODY))
    story.append(Paragraph(f"页数：{result.page_count}　|　视觉模型：{'Gemini' if result.provider == 'gemini' else 'K3'}", BODY))
    story.append(Paragraph(f"审核时间：{result.reviewed_at}", BODY))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("审核结论", H2))
    story.append(
        Paragraph(
            f"共 {len(result.findings)} 条待确认问题；所有结论统一为「{_CONCLUSION}」。"
            "本报告为课堂审核草稿，不构成正式工程批准，请工程师逐条复核确认。",
            BODY,
        )
    )
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("问题清单（每条含页码与证据）", H2))
    header = ["#", "页码", "问题", "证据摘录", "结论"]
    rows: list[list] = [header]
    for idx, f in enumerate(result.findings, start=1):
        rows.append(
            [
                str(idx),
                str(f.page),
                f.title,
                f.evidence,
                f.conclusion,
            ]
        )
    table = Table(rows, colWidths=[8 * mm, 12 * mm, 45 * mm, 75 * mm, 20 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("WORDWRAP", (0, 0), (-1, -1), "CJK"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            "免责声明：本报告由 AI 基于图纸识别生成，证据与页码仅供辅助定位；"
            "缺少证据或页码异常的项目未纳入有效结果。最终以工程师确认结论为准。",
            MUTED,
        )
    )

    doc.build(story)
    return buffer.getvalue()
