"""Formal engineering drawing review report PDF."""

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
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


INK = colors.HexColor("#202923")
MUTED = colors.HexColor("#647068")
GREEN = colors.HexColor("#176B4D")
GREEN_PALE = colors.HexColor("#EDF7F1")
AMBER = colors.HexColor("#A76517")
AMBER_PALE = colors.HexColor("#FFF6E6")
RED = colors.HexColor("#A53B31")
RED_PALE = colors.HexColor("#FBEDEA")
BLUE = colors.HexColor("#315F83")
BLUE_PALE = colors.HexColor("#EAF2F8")
LINE = colors.HexColor("#D7DDD8")
PAPER = colors.HexColor("#FAFAF6")


class AnnotatedDrawingPage(Flowable):
    """Render one source page with deterministic numbered evidence boxes."""

    def __init__(
        self,
        image_path: Path,
        markers: list[dict],
        *,
        available_width: float,
        max_height: float = 122 * mm,
    ):
        super().__init__()
        self.image_path = Path(image_path)
        self.markers = markers
        self.available_width = available_width
        image = ImageReader(str(self.image_path))
        source_width, source_height = image.getSize()
        scale = min(available_width / source_width, max_height / source_height)
        self.image_width = source_width * scale
        self.image_height = source_height * scale
        self.width = available_width
        self.height = self.image_height

    def draw(self) -> None:
        canvas = self.canv
        image_x = (self.available_width - self.image_width) / 2
        occupied_badges: list[tuple[float, float, float, float]] = []
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.rect(image_x, 0, self.image_width, self.image_height, stroke=1, fill=0)
        canvas.drawImage(
            str(self.image_path),
            image_x,
            0,
            width=self.image_width,
            height=self.image_height,
            preserveAspectRatio=True,
            mask="auto",
        )
        for marker in sorted(
            self.markers,
            key=lambda item: tuple(item.get("bbox") or [0, 0, 0, 0]),
        ):
            bbox = marker.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = bbox
            left = image_x + x1 * self.image_width
            bottom = (1 - y2) * self.image_height
            width = (x2 - x1) * self.image_width
            height = (y2 - y1) * self.image_height
            accent = RED if marker.get("severity") == "blocked" else AMBER
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(1.6)
            canvas.rect(left, bottom, width, height, stroke=1, fill=0)
            numbers = marker.get("numbers") or [marker.get("number")]
            numbers = [int(number) for number in numbers if number is not None]
            if not numbers:
                continue
            badge_label = " / ".join(f"#{number}" for number in numbers)
            badge_height = 6.8 * mm
            badge_width = max(
                11 * mm,
                pdfmetrics.stringWidth(badge_label, "Helvetica-Bold", 8.2) + 4.5 * mm,
            )
            badge_left = max(
                image_x,
                min(left, image_x + self.image_width - badge_width),
            )
            desired_bottom = min(
                bottom + height + 1.8 * mm,
                self.image_height - badge_height,
            )
            candidate_bottoms = [
                desired_bottom,
                desired_bottom - 8 * mm,
                desired_bottom + 8 * mm,
                desired_bottom - 16 * mm,
                desired_bottom + 16 * mm,
            ]
            badge_bottom = max(0, min(desired_bottom, self.image_height - badge_height))
            for candidate in candidate_bottoms:
                candidate = max(0, min(candidate, self.image_height - badge_height))
                candidate_box = (
                    badge_left,
                    candidate,
                    badge_left + badge_width,
                    candidate + badge_height,
                )
                if not any(
                    candidate_box[0] < existing[2] + 1.2 * mm
                    and candidate_box[2] + 1.2 * mm > existing[0]
                    and candidate_box[1] < existing[3] + 1.2 * mm
                    and candidate_box[3] + 1.2 * mm > existing[1]
                    for existing in occupied_badges
                ):
                    badge_bottom = candidate
                    break
            occupied_badges.append(
                (
                    badge_left,
                    badge_bottom,
                    badge_left + badge_width,
                    badge_bottom + badge_height,
                )
            )

            canvas.setStrokeColor(accent)
            canvas.setLineWidth(0.8)
            canvas.line(
                left,
                bottom + height,
                badge_left + badge_width / 2,
                badge_bottom,
            )
            canvas.setFillColor(colors.white)
            canvas.roundRect(
                badge_left,
                badge_bottom,
                badge_width,
                badge_height,
                2.2 * mm,
                stroke=1,
                fill=1,
            )
            canvas.setFillColor(accent)
            canvas.setFont("Helvetica-Bold", 8.2)
            canvas.drawCentredString(
                badge_left + badge_width / 2,
                badge_bottom + 2.25 * mm,
                badge_label,
            )
        canvas.restoreState()


def _group_page_markers(markers: list[dict]) -> list[dict]:
    """Merge identical evidence boxes so every linked problem number stays visible."""

    grouped: dict[tuple[float, ...], dict] = {}
    for marker in markers:
        bbox = marker.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            key = tuple(round(float(value), 4) for value in bbox)
            number = int(marker["number"])
        except (KeyError, TypeError, ValueError):
            continue
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                "bbox": list(bbox),
                "number": number,
                "numbers": [number],
                "severity": marker.get("severity"),
            }
            continue
        if number not in existing["numbers"]:
            existing["numbers"].append(number)
        if marker.get("severity") == "blocked":
            existing["severity"] = "blocked"
    return list(grouped.values())

def _register_font() -> str:
    font_name = "EngineeringReviewCJK"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = (
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(candidate)))
            return font_name
        except Exception:
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def _safe(value: object, *, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    if limit and len(text) > limit:
        text = text[: limit - 6].rstrip() + " [截取]"
    return escape(text).replace("\n", "<br/>")


CUSTOMER_TERM_REPLACEMENTS = (
    (
        "本结论为 AI 与规则生成的审核草案，尚未形成正式工程决定。",
        "以上为 AI 辅助发现，需由负责工程师确认后生效。",
    ),
    ("未发现程序性阻断", "系统没有发现必须立即停止处理的问题"),
    ("三坐标和平面度测量", "三坐标测量机和表面平整度测量"),
    ("检验结果不可重复", "不同人员可能得到不同检查结果"),
    ("设计与工艺工程师", "设计和工艺负责人"),
    ("设计/文控工程师", "设计/文件管理负责人"),
    ("文控或设计人员", "文件管理或设计负责人"),
    ("质量/计量工程师", "质量/测量负责人"),
    ("工艺工程师", "工艺负责人"),
    ("责任工程师", "工程负责人"),
    ("最终尺寸验收状态", "最终尺寸按处理前还是处理后验收"),
    ("必填字段 revision 缺失", "图纸版本信息缺失"),
    ("必填字段 material 缺失", "材料信息缺失"),
    (
        "是否应报告'零件名称'或'修订版本'等字段缺失？但由于这些字段未被提供，可能因隐私原因被有意排除，因此未报告。",
        "请确认当前资料是否有意省略了零件名称或图纸版本；如非有意，请补充。",
    ),
    ("规则包与文档类型不匹配", "检查规则不适用于这类文件"),
    ("尚未形成完整工程影响判断", "还需要工程师判断具体影响"),
    ("同一受控图纸集", "同一套正式图纸"),
    ("图纸集合", "整套图纸"),
    ("有效总页数", "正确总页数"),
    ("版本追溯", "版本来源核查"),
    ("符合性判定", "判断是否符合要求"),
    ("可制造性风险", "可能难以制造的风险"),
    ("可制造性", "是否便于制造"),
    ("3D数模", "3D 模型"),
    ("3D 数模", "3D 模型"),
    ("阳极氧化膜厚", "阳极氧化层厚度"),
    ("前处理尺寸", "表面处理前尺寸"),
    ("遮蔽范围", "保护范围（不做表面处理的区域）"),
    ("是否遮蔽", "是否需要保护（不做表面处理）"),
    ("工艺评审", "工艺方案确认"),
    ("首件测量", "第一件产品的测量"),
    ("抽样方案", "抽查数量和方法"),
    ("受控文件", "正式文件"),
    ("判定方法", "如何判断合格"),
    ("优化结构或留量", "优化结构或预留加工余量"),
    ("复核签署", "负责人确认"),
    ("人工复核", "负责人确认"),
    ("人工确认", "负责人确认"),
    ("人工核对", "负责人核对"),
    ("审核草案", "待确认报告"),
    ("人工定稿", "负责人已确认"),
    ("授权工程师", "负责工程师"),
    ("进入后续工程流转", "进入后续工程流程"),
    ("后续工程流转", "后续工程流程"),
    ("工程流转", "后续工程流程"),
    ("受控图纸集", "同一套正式图纸"),
    ("受控用途", "正式用途"),
    ("必填字段 part_name 缺失", "零件/总成名称缺失"),
    ("必填字段 dimensions 缺失", "尺寸信息缺失"),
    ("必填字段 tolerances 缺失", "公差要求缺失"),
    ("字段 part_name", "零件/总成名称"),
    ("字段 revision", "图纸版本"),
    ("字段 material", "材料信息"),
    ("字段 dimensions", "尺寸信息"),
    ("字段 tolerances", "公差要求"),
    ("证据链", "判断依据"),
    ("程序性阻断", "必须立即停止处理的问题"),
    ("可能阻断", "可能造成后续工作暂停："),
    ("条件性流转", "确认问题后可继续"),
    ("停止流转", "暂时不要进入下一步"),
    ("项必须先解决问题", "项必须先解决的问题"),
    ("项阻断问题", "项必须先解决的问题"),
    ("项复核问题", "项需要确认的问题"),
    ("项再次确认问题", "项需要确认的问题"),
    ("关键特性", "关键尺寸和特征"),
    ("H7孔", "H7 精密配合孔"),
    ("H7 孔", "H7 精密配合孔"),
    ("孔位度", "孔的位置精度"),
    ("平面度", "表面平整度"),
    ("检验方法", "检查方法"),
    ("塞规", "塞规（孔径专用量具）"),
    ("装夹", "夹持固定"),
    ("检验", "检查"),
    ("膜层", "表面处理层"),
    ("超差", "尺寸超出允许范围"),
    ("遮蔽", "保护"),
    ("阻断", "必须先解决"),
    ("必须先解决问题", "必须先解决的问题"),
    ("流转", "进入下一步"),
    ("处置", "处理"),
    ("复核", "再次确认"),
    ("证据", "依据"),
    ("受控", "正式"),
    ("文控", "文件管理"),
)


def _customer_friendly_text(value: object) -> str:
    """Translate internal and specialist wording for customer-facing PDF copy."""

    text = "" if value is None else str(value)
    for internal, customer_friendly in CUSTOMER_TERM_REPLACEMENTS:
        text = text.replace(internal, customer_friendly)
    # Generic terminology replacements can create this duplicate phrase from
    # model wording such as “正式受控版本”; normalize it after the full pass.
    text = text.replace("正式正式版本", "正式版本")
    text = text.replace("正式正式版", "正式版")
    return text


def _paragraph(value: object, style: ParagraphStyle, *, limit: int | None = None) -> Paragraph:
    return Paragraph(_safe(value, limit=limit) or "-", style)


def _short_report_number(value: object) -> str:
    text = str(value or "")
    if text.startswith("run-"):
        return f"run-{text[4:16]}"
    return text[:16] or "-"


def _analysis_method_for_display(
    analysis: dict,
    provider_metadata: dict | None = None,
) -> str:
    """Describe the actual analysis route without exposing configuration secrets."""

    provider = str(analysis.get("provider") or "").strip().lower()
    model = str(analysis.get("model") or "").strip()
    if provider == "mock":
        return "教学模拟（未调用真实 AI）"
    if provider in {"hybrid", "kimi-hybrid"}:
        metadata = provider_metadata or {}
        visual_stage = metadata.get("visual_stage") or {}
        secondary_stage = metadata.get("secondary_stage") or {}
        secondary_review = metadata.get("secondary_review") or {}
        secondary_review_mode = str(secondary_review.get("mode") or "").strip()
        visual_model = str(visual_stage.get("model") or "").strip()
        secondary_model = str(secondary_stage.get("model") or "").strip()
        visual_provider_label = "Kimi K3 high" if provider == "kimi-hybrid" else "Gemini"
        if secondary_stage.get("status") == "skipped":
            visual_label = f"{visual_provider_label} 看图分析（{visual_model}）" if visual_model else f"{visual_provider_label} 看图分析"
            if secondary_review_mode == "never":
                return f"{visual_label}；DeepSeek 本次不复核"
            return f"{visual_label}；DeepSeek 按需复核未触发"
        if secondary_stage.get("status") == "completed":
            review_label = "强制复核" if secondary_review_mode == "always" else "按需复核"
            if provider == "kimi-hybrid" and visual_model == "k3" and secondary_model == "deepseek-v4-flash":
                return f"Kimi K3 high 看图 + DeepSeek V4 Flash {review_label}"
            if visual_model == "gemini-3.6-flash" and secondary_model == "deepseek-v4-flash":
                return f"Gemini 3.6 Flash 看图 + DeepSeek V4 Flash {review_label}"
            route = " + ".join(value for value in (visual_model, secondary_model) if value)
            return f"{visual_provider_label} 看图 + DeepSeek {review_label}（{route}）" if route else f"{visual_provider_label} 看图 + DeepSeek {review_label}"
        if provider == "kimi-hybrid" and model == "k3 → deepseek-v4-flash":
            return "Kimi K3 high 看图 + DeepSeek V4 Flash 文字复核"
        if model == "gemini-3.6-flash → deepseek-v4-flash":
            return "Gemini 3.6 Flash 看图 + DeepSeek V4 Flash 文字复核"
        return f"{visual_provider_label} 看图 + DeepSeek 文字复核（{model}）" if model else f"{visual_provider_label} 看图 + DeepSeek 文字复核"
    if provider == "kimi":
        return f"Kimi K3 high 看图分析（{model}）" if model and model != "k3" else "Kimi K3 high 看图分析"
    if provider == "gemini":
        return f"Gemini 看图分析（{model}）" if model else "Gemini 看图分析"
    if provider == "openai":
        return f"OpenAI 看图分析（{model}）" if model else "OpenAI 看图分析"
    return model or "未记录"


def _datetime_for_display(value: object) -> str:
    text = str(value or "")
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        local = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        return local.strftime("%Y-%m-%d %H:%M:%S（北京时间）")
    except (TypeError, ValueError):
        return text


def build_review_report_pdf(
    payload: dict,
    output_path: Path,
    *,
    page_paths: list[Path] | None = None,
) -> None:
    """Build a source-preserving annotated review draft or finalized report."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font = _register_font()
    page_width, page_height = A4
    content_width = page_width - 34 * mm

    body = ParagraphStyle(
        "EngineeringBodyCJK",
        fontName=font,
        fontSize=9,
        leading=13.4,
        textColor=INK,
        wordWrap="CJK",
        alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "EngineeringSmallCJK",
        parent=body,
        fontSize=7.5,
        leading=11,
        textColor=MUTED,
    )
    title = ParagraphStyle(
        "EngineeringTitleCJK",
        parent=body,
        fontSize=22,
        leading=28,
        textColor=INK,
        spaceAfter=0,
    )
    subtitle = ParagraphStyle(
        "EngineeringSubtitleCJK",
        parent=body,
        fontSize=9.5,
        leading=14,
        textColor=MUTED,
        spaceAfter=6 * mm,
    )
    heading = ParagraphStyle(
        "EngineeringHeadingCJK",
        parent=body,
        fontSize=14,
        leading=19,
        textColor=GREEN,
        spaceBefore=4.5 * mm,
        spaceAfter=2.5 * mm,
    )
    location_heading = ParagraphStyle(
        "EngineeringLocationHeadingCJK",
        parent=heading,
        spaceBefore=0,
    )
    subheading = ParagraphStyle(
        "EngineeringSubheadingCJK",
        parent=body,
        fontSize=10.2,
        leading=15,
        textColor=GREEN,
    )
    centered = ParagraphStyle(
        "EngineeringCenteredCJK",
        parent=body,
        alignment=TA_CENTER,
        fontSize=8.5,
        leading=12,
    )
    status_value = ParagraphStyle(
        "EngineeringStatusValueCJK",
        parent=centered,
        fontSize=11.5,
        leading=15,
        textColor=INK,
    )
    badge = ParagraphStyle(
        "EngineeringBadgeCJK",
        parent=centered,
        fontSize=8.2,
        leading=11,
        textColor=AMBER if payload.get("report_stage") == "draft" else GREEN,
    )
    label = ParagraphStyle(
        "EngineeringLabelCJK",
        parent=small,
        textColor=GREEN,
        fontSize=7.4,
        leading=10,
    )

    document = payload["document"]
    analysis = payload["analysis"]
    draft = payload["draft"]
    review = payload.get("engineering_review") or {}
    finalization = payload.get("review_finalization") or {}
    report_stage = payload.get("report_stage") or review.get("report_stage") or "draft"
    is_draft = report_stage == "draft"
    is_ai_report = payload.get("product_report_type") == "ai_review"
    badge.textColor = BLUE if is_ai_report else AMBER if is_draft else GREEN
    report_number = analysis["id"]
    report_title = "图纸 AI 审核报告" if is_ai_report else "图纸工程审核报告"
    report_stage_label = "AI 生成\n工程参考" if is_ai_report else "等待确认\n工程参考" if is_draft else "负责人确认\n已记录"
    report_stage_short = "AI 生成·工程参考" if is_ai_report else "等待确认·工程参考" if is_draft else "负责人确认·已记录"
    document_title = f"{report_title}（{report_stage_short}）"

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=20 * mm,
        bottomMargin=17 * mm,
        title=document_title,
        author="图纸 AI 分析工作台",
        subject=f"来源文件：{document['filename']}",
    )

    def page_chrome(canvas, current_doc) -> None:
        canvas.saveState()
        if is_draft:
            canvas.setFillColor(colors.HexColor("#F1F3F1"))
            canvas.setFont(font, 34)
            canvas.translate(page_width / 2, page_height / 2)
            canvas.rotate(32)
            canvas.drawCentredString(0, 0, "AI生成·工程参考" if is_ai_report else "等待负责人确认")
            canvas.rotate(-32)
            canvas.translate(-page_width / 2, -page_height / 2)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(17 * mm, page_height - 12 * mm, page_width - 17 * mm, page_height - 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(font, 7.2)
        canvas.drawString(17 * mm, page_height - 9 * mm, document_title)
        canvas.drawRightString(
            page_width - 17 * mm,
            page_height - 9 * mm,
            f"报告编号：{_short_report_number(report_number)}",
        )
        canvas.line(17 * mm, 11 * mm, page_width - 17 * mm, 11 * mm)
        footer = (
            "AI 自动审核报告仅供工程参考，不构成工程放行、生产批准或客户验收。"
            if is_ai_report
            else "本报告由 AI 辅助生成；请由负责工程师确认后再用于后续生产或交付。"
            if is_draft
            else "负责人确认记录不等同于生产批准或客户验收。"
        )
        canvas.drawString(17 * mm, 7 * mm, footer)
        canvas.drawRightString(page_width - 17 * mm, 7 * mm, f"第 {current_doc.page} 页")
        canvas.restoreState()

    disposition = review.get("recommended_disposition") or "conditional"
    disposition_labels = {
        "blocked": "先解决问题，再继续",
        "conditional": "确认问题后可继续",
        "ready_for_human_release": "负责人确认后可继续",
    }
    disposition_color = {
        "blocked": RED_PALE,
        "conditional": AMBER_PALE,
        "ready_for_human_release": GREEN_PALE,
    }.get(disposition, BLUE_PALE)

    issues = review.get("issues") or []
    human_labels = {
        "pending": "需要负责人确认",
        "confirmed": "负责人已确认",
        "corrected": "已修改",
        "rejected": "不采纳",
    }
    raw_field_lookup = {
        str(item.get("name")): item.get("value") or "未识别"
        for item in draft.get("fields") or []
    }
    correction_by_field = {
        str(item.get("field_name")): item
        for item in payload.get("field_corrections") or []
        if item.get("field_name") and item.get("corrected_value")
    }
    field_lookup = dict(raw_field_lookup)
    for field_name, correction in correction_by_field.items():
        field_lookup[field_name] = correction.get("corrected_value") or "未识别"

    def field_value_for_report(field_name: str) -> str:
        value = str(field_lookup.get(field_name) or "未识别")
        return f"{value}（人工修正）" if field_name in correction_by_field else value

    evidence_lookup = {
        str(item.get("id")): item
        for item in draft.get("evidence") or []
    }
    owner_by_issue: dict[str, str] = {}
    for action in review.get("actions") or []:
        for source_issue_id in action.get("source_issue_ids") or []:
            owner_by_issue.setdefault(str(source_issue_id), str(action.get("owner_role") or "责任工程师"))

    page_issue_groups: dict[int, list[tuple[int, dict]]] = {}
    page_markers: dict[int, list[dict]] = {}
    issue_locations: dict[int, str] = {}
    for index, issue in enumerate(issues, start=1):
        issue_evidence = [
            evidence_lookup[evidence_id]
            for evidence_id in issue.get("evidence_ids") or []
            if evidence_id in evidence_lookup
        ]
        pages = sorted({
            int(item.get("page") or 0)
            for item in issue_evidence
            if int(item.get("page") or 0) > 0
        })
        located = False
        for page_number in pages:
            page_issue_groups.setdefault(page_number, []).append((index, issue))
            for evidence in issue_evidence:
                if int(evidence.get("page") or 0) != page_number or not evidence.get("bbox"):
                    continue
                located = True
                page_markers.setdefault(page_number, []).append({
                    "number": index,
                    "severity": issue.get("severity"),
                    "bbox": evidence.get("bbox"),
                })
        if located:
            issue_locations[index] = "、".join(f"第{page}页" for page in pages)
        elif pages:
            issue_locations[index] = "需要标出"
        else:
            issue_locations[index] = "需要补充依据"
    page_markers = {
        page_number: _group_page_markers(markers)
        for page_number, markers in page_markers.items()
    }

    story: list = []
    story.append(Spacer(1, 7 * mm))
    title_row = Table(
        [[_paragraph(report_title, title), _paragraph(report_stage_label, badge)]],
        colWidths=[130 * mm, 46 * mm],
    )
    title_row.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), BLUE_PALE if is_ai_report else AMBER_PALE if is_draft else GREEN_PALE),
        ("BOX", (1, 0), (1, 0), 0.7, BLUE if is_ai_report else AMBER if is_draft else GREEN),
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
    story.append(title_row)
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph("客户易读版 · 先看结论，再看位置和处理建议", subtitle))

    identity_rows = [
        [
            _paragraph("原图文件", label),
            _paragraph(document.get("filename"), body, limit=500),
            _paragraph("零件/总成", label),
            _paragraph(field_value_for_report("part_name"), body, limit=500),
        ],
        [
            _paragraph("图纸版本", label),
            _paragraph(field_value_for_report("revision"), body),
            _paragraph("材料", label),
            _paragraph(field_value_for_report("material"), body, limit=500),
        ],
        [
            _paragraph("AI 分析方式", label),
            _paragraph(
                _analysis_method_for_display(analysis, payload.get("provider_metadata")),
                body,
                limit=500,
            ),
            "",
            "",
        ],
    ]
    identity_spans: list[tuple] = [("SPAN", (1, 2), (3, 2))]
    if correction_by_field:
        field_labels = {
            "part_name": "零件/总成",
            "revision": "图纸版本",
            "material": "材料",
            "dimensions": "尺寸",
            "tolerances": "公差",
        }
        correction_lines = []
        for field_name, correction in correction_by_field.items():
            original = str(raw_field_lookup.get(field_name) or "未识别")
            corrected = str(correction.get("corrected_value") or "未识别")
            reviewer = str(correction.get("reviewer") or "未记录")
            correction_lines.append(
                f"{field_labels.get(field_name, field_name)}：AI 原值“{original}” → 人工值“{corrected}”；复核人：{reviewer}"
            )
        correction_row = len(identity_rows)
        identity_rows.append([
            _paragraph("修正记录", label),
            _paragraph("<br/>".join(correction_lines), small, limit=1600),
            "",
            "",
        ])
        identity_spans.append(("SPAN", (1, correction_row), (3, correction_row)))
    identity_table = Table(identity_rows, colWidths=[23 * mm, 65 * mm, 23 * mm, 65 * mm])
    identity_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PAPER),
        ("BACKGROUND", (2, 0), (2, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        *identity_spans,
    ]))
    story.append(identity_table)

    story.append(_paragraph("一、审核结论", heading))
    status_table = Table(
        [
            [
                _paragraph("下一步建议", label),
                _paragraph("必须先解决", label),
                _paragraph("还需确认", label),
                _paragraph("报告状态" if is_ai_report else "负责人确认", label),
            ],
            [
                _paragraph(disposition_labels.get(disposition, disposition), status_value),
                _paragraph(review.get("blocker_count", 0), status_value),
                _paragraph(review.get("review_count", 0), status_value),
                _paragraph("报告已生成，结论待工程确认" if is_ai_report else "等待负责人确认" if is_draft else "负责人已确认", status_value),
            ],
        ],
        colWidths=[content_width / 4] * 4,
    )
    status_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PAPER),
        ("BACKGROUND", (0, 1), (0, 1), disposition_color),
        ("BACKGROUND", (1, 1), (1, 1), RED_PALE),
        ("BACKGROUND", (2, 1), (2, 1), AMBER_PALE),
        ("BACKGROUND", (3, 1), (3, 1), BLUE_PALE if is_draft or is_ai_report else GREEN_PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(status_table)
    story.append(Spacer(1, 3 * mm))

    conclusion = Table(
        [[Paragraph(
            "<b>审核摘要</b><br/>"
            + _safe(_customer_friendly_text(review.get("conclusion") or draft.get("summary")), limit=900),
            body,
        )]],
        colWidths=[content_width],
    )
    conclusion.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), disposition_color),
        ("BOX", (0, 0), (-1, -1), 0.8, RED if disposition == "blocked" else AMBER if disposition == "conditional" else GREEN),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(conclusion)

    story.append(_paragraph("重点问题一览", heading))
    overview_rows = [[
        _paragraph("编号", label),
        _paragraph("问题", label),
        _paragraph("可能造成的影响", label),
        _paragraph("位置/进度", label),
    ]]
    for index, issue in enumerate(issues[:5], start=1):
        priority = "先解决" if issue.get("severity") == "blocked" else "需确认"
        overview_rows.append([
            _paragraph(f"#{index}\n{priority}", centered),
            _paragraph(_customer_friendly_text(issue.get("problem")), body, limit=500),
            _paragraph(_customer_friendly_text(issue.get("impact")), small, limit=500),
            _paragraph(
                f"{issue_locations.get(index, '需要标出')}\n"
                f"{'建议人工核验' if is_ai_report else human_labels.get(issue.get('human_decision'), issue.get('human_decision') or '需要负责人确认')}",
                small,
            ),
        ])
    if len(overview_rows) == 1:
        overview_rows.append([
            _paragraph("-", centered),
            _paragraph("本次 AI 审核没有列出问题。" if is_ai_report else "系统没有列出问题；仍需负责工程师确认后才能继续。", body),
            _paragraph("-", centered),
            _paragraph("报告已生成，结论待工程确认" if is_ai_report else "等待负责人确认", centered),
        ])
    overview_table = Table(overview_rows, colWidths=[17 * mm, 64 * mm, 65 * mm, 30 * mm], repeatRows=1)
    overview_style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE_PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_index, issue in enumerate(issues[:5], start=1):
        overview_style.append((
            "BACKGROUND",
            (0, row_index),
            (0, row_index),
            RED_PALE if issue.get("severity") == "blocked" else AMBER_PALE,
        ))
    overview_table.setStyle(TableStyle(overview_style))
    story.append(overview_table)
    if len(issues) > 5:
        story.append(Spacer(1, 2 * mm))
        story.append(_paragraph(f"另有 {len(issues) - 5} 项问题，详见“三、问题与处理建议”。", small))

    if issues:
        story.append(_paragraph("建议处理顺序", heading))
        action_cells = []
        for index, issue in enumerate(issues[:3], start=1):
            priority = "先解决" if issue.get("severity") == "blocked" else "再确认"
            action_cells.append(Paragraph(
                f"<b>#{index} {priority}</b><br/>"
                f"{_safe(_customer_friendly_text(issue.get('recommendation')), limit=260)}",
                small,
            ))
        action_table = Table(
            [action_cells],
            colWidths=[content_width / len(action_cells)] * len(action_cells),
        )
        action_style = [
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]
        for column, issue in enumerate(issues[:3]):
            action_style.append((
                "BACKGROUND",
                (column, 0),
                (column, 0),
                RED_PALE if issue.get("severity") == "blocked" else AMBER_PALE,
            ))
        action_table.setStyle(TableStyle(action_style))
        story.append(action_table)

    if page_issue_groups:
        for page_index, page_number in enumerate(sorted(page_issue_groups), start=1):
            story.append(PageBreak())
            location_section = [Spacer(1, 1.5 * mm), _paragraph(
                "二、原图问题定位" if page_index == 1 else "二、原图问题定位（续）",
                location_heading,
            )]
            page_issues = page_issue_groups[page_number]
            markers = page_markers.get(page_number) or []
            located_numbers = {
                int(number)
                for marker in markers
                for number in (marker.get("numbers") or [marker.get("number")])
                if number is not None
            }
            page_header = Table(
                [[
                    _paragraph(f"原图第 {page_number} 页", subheading),
                    _paragraph(
                        f"问题 {len(page_issues)} 项 · 已标出 {len(located_numbers)} 项 · "
                        f"需要标出 {len(page_issues) - len(located_numbers)} 项",
                        small,
                    ),
                ]],
                colWidths=[54 * mm, 122 * mm],
            )
            page_header.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), BLUE_PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            location_section.append(page_header)
            location_section.append(Spacer(1, 3 * mm))
            if page_paths and 1 <= page_number <= len(page_paths):
                location_section.append(AnnotatedDrawingPage(
                    Path(page_paths[page_number - 1]),
                    markers,
                    available_width=content_width,
                    max_height=150 * mm,
                ))
            else:
                location_section.append(_paragraph("本页原图暂时无法显示；请返回系统按问题编号查看。", body))
            location_section.append(Spacer(1, 4 * mm))
            location_rows = [[
                _paragraph("编号", label),
                _paragraph("问题说明", label),
                _paragraph("标注进度", label),
            ]]
            for index, issue in page_issues:
                location_rows.append([
                    _paragraph(f"#{index}", centered),
                    _paragraph(_customer_friendly_text(issue.get("problem")), body, limit=500),
                    _paragraph(
                        "已标出" if index in located_numbers else "需要标出",
                        centered,
                    ),
                ])
            location_table = Table(location_rows, colWidths=[18 * mm, 128 * mm, 30 * mm], repeatRows=1)
            location_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            location_section.append(location_table)
            location_section.append(Spacer(1, 2 * mm))
            location_section.append(_paragraph(
                "图上编号与问题清单一一对应；系统没有可靠位置时不会在原图上猜测。",
                small,
            ))
            story.append(KeepTogether(location_section))
    else:
        story.append(PageBreak())
        story.append(_paragraph("二、原图问题定位", heading))
        story.append(_paragraph("本次没有需要标注的问题。" if is_ai_report else "本次没有需要标注的问题；仍需负责人确认后才能继续。", body))

    story.append(PageBreak())
    story.append(_paragraph("三、逐项处理建议", heading))
    story.append(_paragraph("每项按“发现 - 影响 - 建议”展开；真实业务使用前请由专业人员核验。" if is_ai_report else "请逐项确认问题、可能影响、建议处理方式、负责人和当前进度。", small))
    story.append(Spacer(1, 2 * mm))
    for index, issue in enumerate(issues, start=1):
        priority = "先解决" if issue.get("severity") == "blocked" else "需确认"
        owner = _customer_friendly_text(owner_by_issue.get(str(issue.get("id")), "责任工程师"))
        progress = "建议人工核验" if is_ai_report else human_labels.get(
            issue.get("human_decision"),
            issue.get("human_decision") or "需要负责人确认",
        )
        issue_card = Table(
            [
                [
                    _paragraph(f"#{index}", centered),
                    _paragraph(priority, body),
                    Paragraph(f"{_safe(owner)}<br/>{_safe(progress)}", small),
                ],
                [_paragraph("发现", label), _paragraph(_customer_friendly_text(issue.get("problem")), body, limit=900), ""],
                [_paragraph("影响", label), _paragraph(_customer_friendly_text(issue.get("impact")), body, limit=900), ""],
                [_paragraph("建议", label), _paragraph(_customer_friendly_text(issue.get("recommendation")), body, limit=900), ""],
            ],
            colWidths=[20 * mm, 108 * mm, 48 * mm],
        )
        issue_card.setStyle(TableStyle([
            ("SPAN", (1, 1), (2, 1)),
            ("SPAN", (1, 2), (2, 2)),
            ("SPAN", (1, 3), (2, 3)),
            ("BACKGROUND", (0, 0), (0, 0), RED_PALE if issue.get("severity") == "blocked" else AMBER_PALE),
            ("BACKGROUND", (1, 0), (2, 0), PAPER),
            ("BACKGROUND", (0, 1), (0, -1), PAPER),
            ("BOX", (0, 0), (-1, -1), 0.7, RED if issue.get("severity") == "blocked" else AMBER),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether([issue_card, Spacer(1, 3 * mm)]))
    if not issues:
        empty_card = Table(
            [[_paragraph("本次没有形成问题项；真实业务使用前仍请由专业人员核验。", body)]],
            colWidths=[content_width],
        )
        empty_card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_PALE),
            ("BOX", (0, 0), (-1, -1), 0.6, GREEN),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(empty_card)

    questions = review.get("open_questions") or []
    if questions:
        story.append(_paragraph("确认前需要回答", heading))
        question_rows = [
            [_paragraph(f"Q{index}", centered), _paragraph(_customer_friendly_text(question), body, limit=700)]
            for index, question in enumerate(questions, start=1)
        ]
        question_table = Table(question_rows, colWidths=[16 * mm, 160 * mm])
        question_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), BLUE_PALE),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(question_table)

    signoff_rows = ([
        [
            _paragraph("报告性质", label),
            _paragraph("AI 自动审核报告", body),
            _paragraph("输出状态", label),
            _paragraph("报告已生成，结论待工程确认", body),
        ],
        [
            _paragraph("使用边界", label),
            _paragraph("仅供课堂演示和工程参考，不构成正式工程批准。", body),
            _paragraph("建议动作", label),
            _paragraph("用于真实业务前，由相应专业人员核对。", body),
        ],
    ] if is_ai_report else [
        [
            _paragraph("确认人", label),
            _paragraph(finalization.get("reviewer") or "待填写", body),
            _paragraph("职责", label),
            _paragraph(_customer_friendly_text(finalization.get("reviewer_role") or "待填写"), body),
        ],
        [
            _paragraph("确认时间", label),
            _paragraph(_datetime_for_display(finalization.get("recorded_at") or analysis.get("finalized_at")), small),
            _paragraph("最终处理结果", label),
            _paragraph("等待负责人确认" if is_draft else disposition_labels.get(disposition, disposition), body),
        ],
        [
            _paragraph("处理备注", label),
            _paragraph(_customer_friendly_text(finalization.get("note") or "-"), body),
            _paragraph("确认状态", label),
            _paragraph("未确认" if is_draft else "已确认并记录", body),
        ],
    ])
    signoff_table = Table(signoff_rows, colWidths=[24 * mm, 64 * mm, 24 * mm, 64 * mm])
    signoff_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PAPER),
        ("BACKGROUND", (2, 0), (2, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    source_hash = str(document.get("sha256") or "-")
    source_hash_short = f"{source_hash[:16]}…" if len(source_hash) > 16 else source_hash
    trace_text = (
        f"报告信息：编号 {_short_report_number(report_number)} · "
        f"原图校验码 {source_hash_short} · "
        f"生成 {_datetime_for_display(payload.get('generated_at'))}"
    )
    boundary = (
        "本报告由 AI 自动生成，可直接作为课堂结果查看和下载；它不是正式工程批准，"
        "不得直接用于生产、报价承诺或客户验收。"
        if is_ai_report
        else "本报告由 AI 辅助生成，所有问题和建议均需由负责工程师确认；"
        "确认前不得用于生产、报价承诺或客户验收。"
    )
    story.append(KeepTogether([
        _paragraph("四、使用边界" if is_ai_report else "四、负责人确认", heading),
        signoff_table,
        Spacer(1, 3 * mm),
        _paragraph(trace_text, small, limit=1200),
        Spacer(1, 2 * mm),
        _paragraph(boundary, small, limit=800),
    ]))

    doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
