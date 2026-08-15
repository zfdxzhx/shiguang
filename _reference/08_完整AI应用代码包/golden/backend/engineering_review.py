"""Build an engineering-oriented review view without inventing source facts."""

from __future__ import annotations

import re

from .models import (
    EngineeringActionItem,
    EngineeringCoverage,
    EngineeringRequirement,
    EngineeringReviewIssue,
    EngineeringReviewV1,
    ReviewDraftV2,
    RuleReport,
)


FIELD_REQUIREMENT_META = {
    "part_name": ("identity", "零件/总成名称", "key"),
    "revision": ("identity", "有效版本", "critical"),
    "material": ("material", "材料要求", "critical"),
    "dimensions": ("dimension", "尺寸要求", "key"),
    "tolerances": ("tolerance", "公差与形位要求", "key"),
}

FIELD_REFERENCE_TERMS = {
    "part_name": ("part_name", "零件名称", "零件名", "名称"),
    "revision": ("revision", "受控版本", "版本", "版次"),
    "material": ("material", "材料牌号", "材料", "材质"),
    "dimensions": ("dimensions", "尺寸"),
    "tolerances": ("tolerances", "公差", "形位"),
}

CODE_GUIDANCE = {
    "SOURCE_SET_INCOMPLETE": (
        "无法证明图纸集合完整，跨页技术要求、尺寸或更改信息可能缺失。",
        "补齐标题栏声明的完整页集，并按同一版本重新执行审核。",
    ),
    "PAGE_COUNT_INCONSISTENCY": (
        "页数标识冲突会破坏文件完整性和版本追溯，不能确认当前输入是否为同一受控图纸集。",
        "由文控或设计人员确认有效总页数，统一页码后重新发布受控文件。",
    ),
    "REFERENCED_DATA_NOT_SUPPLIED": (
        "缺少被引用的 3D 数据、标准或规范，无法完成尺寸、装配或符合性判定。",
        "提供所有被引用的模型、标准和规范，并确认其有效版本。",
    ),
    "DOCUMENT_TYPE_ROUTED": (
        "当前规则包与文档类型不匹配，继续使用可能产生错误的审核结论。",
        "切换到匹配的文档审核流程，或由工程师确认该文件的受控用途。",
    ),
    "DOCUMENT_TYPE_INCONSISTENCY": (
        "零件图与装配图的审核范围不同，类型不一致会遗漏装配关系或零件特性。",
        "确认文档类型，并采用对应的零件图或装配图审核清单。",
    ),
    "EVIDENCE_INCOMPLETE": (
        "结论缺少可回到源图的完整证据，不能作为工程决定依据。",
        "重新定位页码、区域和证据，证据完整后再由人工确认。",
    ),
    "LOW_CONFIDENCE": (
        "识别可信度不足，直接使用可能造成材料、尺寸或公差理解错误。",
        "对照原图人工核对该字段；必要时修正为受控值。",
    ),
    "MISSING_REQUIRED_FIELD": (
        "关键工程信息缺失，可能阻断版本控制、选材、制造或检验。",
        "由设计人员补充缺失字段并重新发布图纸。",
    ),
    "AMBIGUOUS_VALUE": (
        "同一要求存在多种解释，可能造成制造与检验采用不同口径。",
        "由设计责任人明确唯一有效值，并消除冲突标注。",
    ),
    "DIMENSION_INCONSISTENCY": (
        "尺寸关系可能不一致，存在加工、装配或检验误判风险。",
        "核对对应视图、尺寸链和基准，确认后修订冲突标注。",
    ),
    "DIMENSION_REQUIREMENT_MISMATCH": (
        "图形尺寸与技术要求未形成一致、完整的工程定义。",
        "逐项核对尺寸与技术要求，补充缺失值或修订不一致内容。",
    ),
    "MATERIAL_DIMENSION_RELATION_UNVERIFIED": (
        "材料、壁厚或装配关系不明确，无法充分评估成形、变形和连接风险。",
        "补充各材料对应区域、壁厚和装配关系，并由工艺工程师复核。",
    ),
}


def _infer_category(code: str, field: str) -> str:
    normalized = f"{code} {field}".upper()
    if any(token in normalized for token in ("SOURCE", "PAGE_COUNT", "DOCUMENT_TYPE", "REFERENCED_DATA", "EVIDENCE")):
        return "source_integrity"
    if any(token in normalized for token in ("INSPECT", "MEASURE", "GAUGE", "DATUM")):
        return "inspectability"
    if any(token in normalized for token in ("ASSEMB", "INTERFERENCE", "FIT")):
        return "assembly"
    if any(token in normalized for token in ("MANUFACTUR", "PROCESS", "MACHIN", "MOLD", "WALL", "MATERIAL_DIMENSION")):
        return "manufacturability"
    if any(token in normalized for token in ("STANDARD", "COMPLIANCE", "SPECIFICATION")):
        return "compliance"
    if any(token in normalized for token in ("DIMENSION", "TOLERANCE", "MATERIAL", "REVISION", "MISSING", "AMBIGUOUS", "INCONSIST")):
        return "requirement_consistency"
    return "other"


def _fallback_guidance(code: str, field: str, problem: str) -> tuple[str, str]:
    if code in CODE_GUIDANCE:
        return CODE_GUIDANCE[code]
    category = _infer_category(code, field)
    if category == "manufacturability":
        return (
            "该问题可能影响加工、成形、装配稳定性或生产一致性。",
            "由设计与工艺工程师联合核对制造能力，并形成明确的图纸修订或工艺约束。",
        )
    if category == "inspectability":
        return (
            "要求可能缺少可执行的检验基准或方法，导致验收结果不可重复。",
            "明确检验基准、量具/设备和判定方法，并将其写入受控要求。",
        )
    if category == "compliance":
        return (
            "缺少明确规范依据会导致符合性和客户验收口径不一致。",
            "确认适用标准及版本，并补齐可追溯的规范依据。",
        )
    return (
        f"该问题尚未形成完整工程影响判断：{problem[:160]}",
        "由对应专业工程师核对原图与证据，确认影响后给出处置意见。",
    )


def _customer_problem(issue) -> str:
    label = FIELD_REQUIREMENT_META.get(issue.field, ("", issue.field or "综合信息", ""))[1]
    if issue.code == "MISSING_REQUIRED_FIELD":
        return f"{label}缺失。"
    if issue.code == "LOW_CONFIDENCE":
        values = re.findall(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", issue.message)
        if len(values) >= 2:
            confidence = round(float(values[0]) * 100)
            threshold = round(float(values[1]) * 100)
            return f"{label}识别置信度为 {confidence}%，低于 {threshold}% 阈值。"
        return f"{label}识别置信度不足。"
    return issue.message


def _owner_role(category: str) -> str:
    return {
        "source_integrity": "设计/文控工程师",
        "requirement_consistency": "设计工程师",
        "manufacturability": "工艺工程师",
        "inspectability": "质量/计量工程师",
        "assembly": "产品/装配工程师",
        "compliance": "质量/标准化工程师",
        "other": "责任工程师",
    }.get(category, "责任工程师")


def _merge_overlapping_missing_issues(
    issues: list[EngineeringReviewIssue],
) -> list[EngineeringReviewIssue]:
    """Collapse rule-only missing fields already covered by one AI issue.

    Rule checks remain available in ``rules_json`` for auditability.  The
    customer-facing review should not repeat "material missing" and "title
    block material missing" as separate actions when the model supplied one
    better, located issue that already covers both.
    """

    missing = [item for item in issues if item.code == "MISSING_REQUIRED_FIELD"]
    if not missing:
        return issues

    absorbed: set[str] = set()
    replacements: dict[str, EngineeringReviewIssue] = {}
    for issue in issues:
        if issue.code == "MISSING_REQUIRED_FIELD":
            continue
        text = f"{issue.problem} {issue.recommendation}".lower()
        has_missing_signal = "MISSING" in issue.code.upper() or any(
            token in text for token in ("缺失", "缺少", "未明确", "未标注", "未填写", "为空")
        )
        if not has_missing_signal:
            continue
        overlaps = []
        for candidate in missing:
            terms = FIELD_REFERENCE_TERMS.get(candidate.field, (candidate.field,))
            if candidate.field == issue.field or any(term.lower() in text for term in terms):
                overlaps.append(candidate)
        if not overlaps:
            continue
        absorbed.update(item.id for item in overlaps)
        evidence_ids = list(dict.fromkeys([
            *issue.evidence_ids,
            *(evidence_id for item in overlaps for evidence_id in item.evidence_ids),
        ]))
        replacements[issue.id] = issue.model_copy(update={
            "severity": "blocked" if any(item.severity == "blocked" for item in overlaps) else issue.severity,
            "evidence_ids": evidence_ids,
            "field": "title_block" if "TITLE_BLOCK" in issue.code.upper() else issue.field,
        })

    return [
        replacements.get(item.id, item)
        for item in issues
        if item.id not in absorbed
    ]


def _requirements(draft: ReviewDraftV2) -> list[EngineeringRequirement]:
    requirements: list[EngineeringRequirement] = []
    seen: set[str] = set()

    # Identity is always shown even when the live model already returned a
    # detailed requirement register. Other legacy fields become the register
    # only when the older record has no explicit requirements.
    fields = {item.name: item for item in draft.fields}
    field_names = ("part_name", "revision") if draft.engineering_requirements else FIELD_REQUIREMENT_META
    for name in field_names:
        field = fields.get(name)
        if not field or not field.value.strip():
            continue
        category, label, criticality = FIELD_REQUIREMENT_META[name]
        item = EngineeringRequirement(
            id=f"field-{name}",
            category=category,
            requirement=f"{label}：{field.value.strip()}",
            criticality=criticality,
            confidence=field.confidence,
            evidence_ids=field.evidence_ids,
        )
        requirements.append(item)
        seen.add(re.sub(r"\s+", "", item.requirement).lower())

    for item in draft.engineering_requirements:
        key = re.sub(r"\s+", "", item.requirement).lower()
        if key in seen:
            continue
        requirements.append(item)
        seen.add(key)
    return requirements


def _coverage(
    requirements: list[EngineeringRequirement],
    issues: list[EngineeringReviewIssue],
) -> list[EngineeringCoverage]:
    requirement_categories = {item.category for item in requirements}
    issue_categories = {item.category for item in issues}
    areas = [
        ("source_integrity", "来源与版本完整性"),
        ("requirement_consistency", "工程要求一致性"),
        ("manufacturability", "可制造性"),
        ("inspectability", "可检验性"),
        ("compliance", "标准与符合性"),
    ]
    support = {
        "source_integrity": bool(requirement_categories & {"identity"}) or "source_integrity" in issue_categories,
        "requirement_consistency": bool(requirement_categories & {"material", "dimension", "tolerance", "datum", "surface", "assembly"}),
        "manufacturability": "manufacturability" in issue_categories or bool(requirement_categories & {"heat_treatment", "process_note"}),
        "inspectability": "inspectability" in issue_categories or bool(requirement_categories & {"inspection", "datum"}),
        "compliance": "compliance" in issue_categories or "standard" in requirement_categories,
    }
    result: list[EngineeringCoverage] = []
    for area, label in areas:
        related = [item for item in issues if item.category == area]
        if related:
            status = "needs_review"
            conclusion = f"{label}发现 {len(related)} 项待处理问题，不能以“未发现”解释为已通过。"
        elif support[area]:
            status = "covered"
            conclusion = f"已提取与{label}相关的证据和要求，仍需授权工程师复核。"
        else:
            status = "insufficient_evidence"
            conclusion = f"当前草稿没有足够证据证明已完成{label}审核，需人工补充检查。"
        result.append(EngineeringCoverage(area=area, status=status, conclusion=conclusion))
    return result


def build_engineering_review(
    *,
    draft: ReviewDraftV2,
    rules: RuleReport,
    decisions: list[dict],
    report_stage: str,
) -> EngineeringReviewV1:
    """Assemble the report contract from model evidence, rules, and people."""

    decision_by_id = {item.get("finding_id"): item for item in decisions}
    finding_by_id = {item.id: item for item in draft.findings}
    issues: list[EngineeringReviewIssue] = []
    for issue in rules.issues:
        finding = finding_by_id.get(issue.id)
        category = issue.category
        if category == "other" and finding is not None:
            category = finding.category
        if category == "other":
            category = _infer_category(issue.code, issue.field)
        impact = (issue.impact or (finding.impact if finding else "")).strip()
        recommendation = (issue.recommendation or (finding.recommendation if finding else "")).strip()
        if not impact or not recommendation:
            fallback_impact, fallback_recommendation = _fallback_guidance(
                issue.code, issue.field, issue.message
            )
            impact = impact or fallback_impact
            recommendation = recommendation or fallback_recommendation
        decision = decision_by_id.get(issue.id) or {}
        human_decision = decision.get("decision") or "pending"
        if human_decision not in {"pending", "confirmed", "corrected", "rejected"}:
            human_decision = "pending"
        issues.append(
            EngineeringReviewIssue(
                id=issue.id,
                code=issue.code,
                field=issue.field,
                category=category,
                severity=issue.severity,
                problem=_customer_problem(issue),
                impact=impact,
                recommendation=recommendation,
                evidence_ids=issue.evidence_ids,
                human_decision=human_decision,
                corrected_value=decision.get("corrected_value") or decision.get("correction") or "",
                reviewer=decision.get("reviewer") or "",
                note=decision.get("note") or "",
            )
        )

    if report_stage == "draft":
        issues = _merge_overlapping_missing_issues(issues)

    actions: list[EngineeringActionItem] = []
    for issue in issues:
        if issue.human_decision == "rejected":
            action = "人工已驳回该草稿结论；保留证据及驳回理由，不按 AI 建议执行。"
        elif issue.human_decision == "corrected" and issue.corrected_value:
            action = f"按人工修正结果更新受控事实：{issue.corrected_value}"
        else:
            action = issue.recommendation
        actions.append(
            EngineeringActionItem(
                priority="P0" if issue.severity == "blocked" else "P1",
                action=action,
                owner_role=_owner_role(issue.category),
                source_issue_ids=[issue.id],
            )
        )
    for question in draft.open_questions[:20]:
        actions.append(
            EngineeringActionItem(
                priority="P2",
                action=f"回答开放问题：{question}",
                owner_role="责任工程师",
                source_issue_ids=[],
            )
        )

    blocker_count = sum(item.severity == "blocked" for item in issues)
    review_count = sum(item.severity == "needs_review" for item in issues)
    if blocker_count:
        disposition = "blocked"
        conclusion = (
            f"发现 {blocker_count} 项必须解决的问题和 {review_count} 项待确认问题。"
            "补齐资料或修订图纸并重新审核前，不能作为正式工程批准。"
        )
    elif review_count:
        disposition = "conditional"
        conclusion = (
            f"没有必须立即解决的问题，但仍有 {review_count} 项问题需要工程人员逐项确认。"
        )
    else:
        disposition = "ready_for_human_release"
        conclusion = "当前规则未发现阻断或复核项；仍需授权工程师对图纸和证据完成最终确认。"
    if report_stage == "draft":
        conclusion += " 报告已生成，结论仍需工程人员确认。"

    requirements = _requirements(draft)
    return EngineeringReviewV1(
        report_stage=report_stage,
        recommended_disposition=disposition,
        conclusion=conclusion,
        blocker_count=blocker_count,
        review_count=review_count,
        requirements=requirements,
        coverage=_coverage(requirements, issues),
        issues=issues,
        actions=actions,
        open_questions=draft.open_questions,
    )
