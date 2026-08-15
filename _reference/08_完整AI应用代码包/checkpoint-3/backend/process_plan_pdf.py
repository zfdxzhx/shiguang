"""Two-page, engineer-facing process route card built from ProcessPlan data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


FAMILY_LABELS = {
    "cnc_machining": "CNC 机加工",
    "sheet_metal": "钣金加工",
    "injection_molding": "注塑成型",
    "assembly": "装配",
}

FACT_LABELS = {
    "part_name": "零件/总成名称",
    "revision": "图纸版本",
    "material": "材料",
    "dimensions": "主要尺寸",
    "tolerances": "公差要求",
}

RISK_LABELS = {"high": "优先处理", "medium": "需要确认", "low": "持续关注"}
MISSING_LABELS = {
    "validated_geometry": "3D 模型及干涉/可达性验证",
    "machine_and_tooling_capability": "现场设备、工装与刀具能力",
    "inspection_capability": "现场量具、检具与检测能力",
    "material": "材料牌号、状态及材质证明要求",
    "dimensions": "完整尺寸信息",
    "tolerances": "完整公差及测量基准",
}


def _text(value: object, *, limit: int | None = None) -> str:
    raw = "" if value is None else str(value).strip()
    if limit and len(raw) > limit:
        raw = raw[: max(1, limit - 3)].rstrip("，；、,. ") + "..."
    return escape(raw or "-").replace("\n", "<br/>")


def _page_footer(font_name: str):
    def draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(14 * mm, 12 * mm, A4[0] - 14 * mm, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(font_name, 7)
        canvas.drawString(14 * mm, 7.5 * mm, "加工工艺路线卡（AI 草案）")
        canvas.drawRightString(A4[0] - 14 * mm, 7.5 * mm, f"第 {doc.page} / 2 页")
        canvas.restoreState()

    return draw


def _table(
    data,
    widths,
    *,
    font_name: str,
    header: bool = True,
    font_size: float = 7.5,
    padding: float = 4,
    alternate_rows: bool = True,
) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7ECE8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
            ]
        )
    if alternate_rows:
        for row in range(1 if header else 0, len(data)):
            if row % 2 == 0:
                commands.append(("BACKGROUND", (0, row), (-1, row), PAPER))
    table.setStyle(TableStyle(commands))
    return table


def _facts(plan: dict) -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("value") or "").strip()
        for item in plan.get("source_facts") or []
    }


def _status(analysis_status: str | None, reviewed: bool) -> tuple[str, colors.Color, colors.Color]:
    if analysis_status == "blocked":
        return "暂停使用：先关闭图纸中的明确问题", RED, RED_PALE
    if analysis_status == "needs_review":
        return "仅供工艺讨论：图纸事项仍待工程师关闭", AMBER, AMBER_PALE
    if reviewed:
        return "路线内容已核对：等待试制验证与正式放行", GREEN, GREEN_PALE
    return "AI 草案：等待工艺负责人核对", BLUE, BLUE_PALE


def _first_equipment(value: object) -> str:
    text = str(value or "").strip()
    for marker in ("；现场已知能力：", "；具体型号、", "; 现场已知能力："):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text or "设备待确认"


def _joined(items: list[str], *, limit: int) -> str:
    return _text("、".join(str(item).strip() for item in items if str(item).strip()) or "待确认", limit=limit)


def _review_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "待填写"
    try:
        return datetime.fromisoformat(raw).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


def build_process_plan_pdf(payload: dict, destination: Path) -> Path:
    artifacts = payload.get("business_artifacts") or {}
    plan = artifacts.get("process_plan")
    if not plan:
        raise ValueError("process plan is required")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    font = _register_font()
    body = ParagraphStyle(
        "RouteCardBody",
        fontName=font,
        fontSize=8.3,
        leading=11.4,
        textColor=INK,
        wordWrap="CJK",
        alignment=TA_LEFT,
    )
    body_tight = ParagraphStyle("RouteCardBodyTight", parent=body, fontSize=7.5, leading=9.8)
    small = ParagraphStyle("RouteCardSmall", parent=body, fontSize=7.2, leading=9.4, textColor=MUTED)
    title = ParagraphStyle("RouteCardTitle", parent=body, fontSize=20, leading=24, textColor=INK, spaceAfter=1.5 * mm)
    h1 = ParagraphStyle("RouteCardH1", parent=body, fontSize=13, leading=17, textColor=GREEN, spaceBefore=3 * mm, spaceAfter=1.5 * mm)
    h2 = ParagraphStyle("RouteCardH2", parent=body, fontSize=10, leading=13, textColor=INK, spaceAfter=1 * mm)
    centered = ParagraphStyle("RouteCardCenter", parent=body, alignment=TA_CENTER)
    flow = ParagraphStyle("RouteCardFlow", parent=centered, fontSize=7.7, leading=10)

    document = payload.get("document") or {}
    analysis = payload.get("analysis") or {}
    fact_values = _facts(plan)
    reviewed = plan.get("review_status") == "confirmed"
    review_label = "路线内容已核对" if reviewed else "待工艺负责人核对"
    status_text, status_ink, status_fill = _status(analysis.get("business_status"), reviewed)
    family = FAMILY_LABELS.get(plan.get("manufacturing_family"), plan.get("manufacturing_family", "-"))
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    steps = plan.get("steps") or []

    story: list = [
        Paragraph("加工工艺路线卡（AI 草案）", title),
        Paragraph("工程师主视图：先看路线，再看需要确认的事项", small),
        Spacer(1, 2.5 * mm),
    ]

    status_table = Table([[Paragraph(f"<b>{_text(status_text)}</b>", centered)]], colWidths=[182 * mm])
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), status_fill),
                ("TEXTCOLOR", (0, 0), (-1, -1), status_ink),
                ("BOX", (0, 0), (-1, -1), 0.7, status_ink),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([status_table, Spacer(1, 2.5 * mm)])

    meta_rows = [
        [Paragraph("来源图纸", small), Paragraph(_text(document.get("filename", "-"), limit=54), body), Paragraph("零件名称", small), Paragraph(_text(fact_values.get("part_name", "-"), limit=32), body)],
        [Paragraph("图纸版本", small), Paragraph(_text(fact_values.get("revision", "-"), limit=16), body), Paragraph("材料", small), Paragraph(_text(fact_values.get("material", "-"), limit=42), body)],
        [Paragraph("毛坯/来料", small), Paragraph(_text(plan.get("material_form", "-"), limit=46), body), Paragraph("批量", small), Paragraph(f"{_text(plan.get('quantity', '-'))} 件", body)],
        [Paragraph("制造类型", small), Paragraph(_text(family), body), Paragraph("路线状态", small), Paragraph(review_label, body)],
    ]
    story.extend([_table(meta_rows, [22 * mm, 69 * mm, 22 * mm, 69 * mm], font_name=font, header=False, font_size=7.8, padding=4.2), Paragraph("路线总览", h1)])

    if steps:
        flow_cells = []
        for step in steps[:6]:
            flow_cells.append(Paragraph(f"<b>{int(step.get('sequence', 0)):02d}</b><br/>{_text(step.get('operation'), limit=18)}", flow))
        flow_table = Table([flow_cells], colWidths=[182 * mm / len(flow_cells)] * len(flow_cells), hAlign="LEFT")
        flow_style = [
            ("BOX", (0, 0), (-1, -1), 0.5, BLUE),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("BACKGROUND", (0, 0), (-1, -1), BLUE_PALE),
        ]
        flow_table.setStyle(TableStyle(flow_style))
        story.extend([flow_table, Paragraph("工序明细", h1)])

    route_rows = [[
        Paragraph("序", small),
        Paragraph("工序与主要工作", small),
        Paragraph("设备与工装", small),
        Paragraph("定位与基准", small),
        Paragraph("关键控制与检验", small),
    ]]
    for step in steps:
        equipment = _first_equipment(step.get("equipment_capability") or step.get("resource"))
        tooling = step.get("tooling_category") or "工装/刀具待确认"
        characteristics = step.get("key_characteristics") or step.get("control_points") or []
        checks = step.get("quality_checks") or []
        pending_parameters = [
            str(item.get("name"))
            for item in step.get("parameters") or []
            if item.get("status") == "needs_confirmation" and item.get("name")
        ]
        route_rows.append(
            [
                Paragraph(f"<b>{int(step.get('sequence', 0)):02d}</b>", centered),
                Paragraph(f"<b>{_text(step.get('operation'), limit=22)}</b><br/>{_text(step.get('purpose'), limit=52)}", body_tight),
                Paragraph(f"<b>设备</b> {_text(equipment, limit=34)}<br/><b>工装</b> {_text(tooling, limit=42)}", body_tight),
                Paragraph(_text(step.get("setup_and_datum"), limit=62), body_tight),
                Paragraph(
                    f"<b>控制</b> {_joined(characteristics[:3], limit=46)}<br/>"
                    f"<b>检验</b> {_joined(checks[:2], limit=50)}"
                    + (f"<br/><font color='#9A641A'><b>待定</b> {_joined(pending_parameters[:3], limit=34)}</font>" if pending_parameters else ""),
                    body_tight,
                ),
            ]
        )
    story.append(_table(route_rows, [9 * mm, 39 * mm, 38 * mm, 39 * mm, 57 * mm], font_name=font, font_size=7.5, padding=4.1))
    story.extend(
        [
            Spacer(1, 2 * mm),
            Paragraph(
                f"路线依据：本次图纸提取事实与参考条件 · 事实摘要 {_text(plan.get('source_fact_digest', '-'), limit=18)} · 生成 {generated_at}",
                small,
            ),
            PageBreak(),
            Paragraph("待确认事项与放行条件", title),
            Paragraph("这里只保留会影响试制、质量或成本的事项；详细证据仍可在应用中追溯。", small),
            Spacer(1, 2.5 * mm),
        ]
    )

    risk_rows = [[Paragraph("优先级", small), Paragraph("需要确认的事项及影响", small), Paragraph("建议动作", small), Paragraph("责任角色", small)]]
    for risk in (plan.get("risks") or [])[:5]:
        risk_rows.append(
            [
                Paragraph(f"<b>{_text(RISK_LABELS.get(risk.get('level'), risk.get('level')))}</b>", body_tight),
                Paragraph(f"{_text(risk.get('concern'), limit=82)}<br/><font color='#647068'>影响：{_text(risk.get('impact'), limit=70)}</font>", body_tight),
                Paragraph(_text(risk.get("verification_action"), limit=88), body_tight),
                Paragraph(_text(risk.get("owner_role"), limit=24), body_tight),
            ]
        )
    if len(risk_rows) == 1:
        risk_rows.append([Paragraph("无", body), Paragraph("当前未形成额外风险项", body), Paragraph("按企业流程复核", body), Paragraph("工艺负责人", body)])
    story.extend([Paragraph("1. 试制前需要关闭的问题", h1), _table(risk_rows, [20 * mm, 71 * mm, 65 * mm, 26 * mm], font_name=font, font_size=7.6, padding=4.5)])

    missing = [MISSING_LABELS.get(str(item), str(item)) for item in plan.get("missing_inputs") or []]
    questions = [str(item) for item in plan.get("open_questions") or []]
    left_items = missing[:5] or ["当前未列出缺失输入，仍需按现场流程核对"]
    right_items = questions[:5] or ["确认路线与现场设备、工装和检测条件匹配"]
    two_column = Table(
        [[
            Paragraph("<b>还需补齐的资料</b><br/>" + "<br/>".join(f"• {_text(item, limit=62)}" for item in left_items), body),
            Paragraph("<b>试制前必须回答</b><br/>" + "<br/>".join(f"• {_text(item, limit=72)}" for item in right_items), body),
        ]],
        colWidths=[88 * mm, 94 * mm],
    )
    two_column.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AMBER_PALE), ("BOX", (0, 0), (-1, -1), 0.6, AMBER), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([Paragraph("2. 输入与现场确认", h1), two_column])

    inspection_items = [str(item) for item in plan.get("inspection_strategy") or []][:3]
    external_items = [str(item) for item in plan.get("external_processes") or []][:2]
    special = str(plan.get("special_requirements") or "").strip()
    if special:
        external_items.append(f"人工补充要求：{special}")
    quality_table = Table(
        [[
            Paragraph("<b>检验安排</b><br/>" + "<br/>".join(f"• {_text(item, limit=88)}" for item in inspection_items), body),
            Paragraph("<b>特殊过程/外协</b><br/>" + "<br/>".join(f"• {_text(item, limit=88)}" for item in (external_items or ["无明确特殊过程；以图纸和评审结论为准"])), body),
        ]],
        colWidths=[91 * mm, 91 * mm],
    )
    quality_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREEN_PALE), ("BOX", (0, 0), (-1, -1), 0.5, GREEN), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([Paragraph("3. 检验与特殊过程", h1), quality_table, Paragraph("4. 人工核对记录", h1)])

    review_rows = [
        [Paragraph("路线状态", small), Paragraph(review_label, body), Paragraph("复核人/角色", small), Paragraph(f"{_text(plan.get('reviewed_by') or '待填写', limit=24)} / {_text(plan.get('reviewer_role') or '待填写', limit=20)}", body)],
        [Paragraph("复核时间", small), Paragraph(_text(_review_time(plan.get("reviewed_at"))), body), Paragraph("复核说明", small), Paragraph(_text(plan.get("review_note") or "待填写", limit=90), body)],
    ]
    story.append(_table(review_rows, [22 * mm, 62 * mm, 25 * mm, 73 * mm], font_name=font, header=False, font_size=7.6, padding=4.2))

    boundary = Table(
        [[Paragraph("使用边界", h2), Paragraph("本卡可用于工艺讨论、现场核对和试制准备；不能直接替代 NC 程序、刀路、已验证参数、正式工艺文件或投产批准。所有待确认项关闭后，仍须按企业流程完成试制、首件检验和正式放行。", body)]],
        colWidths=[24 * mm, 158 * mm],
    )
    boundary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RED_PALE), ("BOX", (0, 0), (-1, -1), 0.6, RED), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([Spacer(1, 2.5 * mm), boundary, Spacer(1, 2 * mm), Paragraph(f"来源任务：{_text(analysis.get('id', '-'), limit=42)} · 来源文件 SHA256：{_text(document.get('sha256', '-'), limit=24)}", small)])

    doc = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=15 * mm,
        title="加工工艺路线卡（AI 草案）",
        author="图纸 AI 解析审核助手",
        subject="ProcessPlan V2 engineer route card",
    )
    page_footer = _page_footer(font)
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return destination
