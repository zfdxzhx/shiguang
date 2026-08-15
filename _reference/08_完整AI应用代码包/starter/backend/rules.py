"""Deterministic rules derive the official status from an untrusted AI draft."""

from __future__ import annotations

import re

from .models import DocumentType, ReviewDraftV2, RuleIssue, RuleReport


REQUIRED_FIELDS = ("part_name", "revision", "material", "dimensions", "tolerances")
DRAWING_TYPES = {DocumentType.MECHANICAL_DRAWING, DocumentType.ASSEMBLY_DRAWING}
BLOCKING_CODES = {
    "SOURCE_SET_INCOMPLETE",
    "REFERENCED_DATA_NOT_SUPPLIED",
    "MISSING_REQUIRED_FIELD",
    "EVIDENCE_INCOMPLETE",
    "DOCUMENT_TYPE_ROUTED",
}


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:100] or "item"


def evaluate_draft(draft: ReviewDraftV2, *, page_count: int, min_confidence: float = 0.85) -> RuleReport:
    evidence_by_id = {item.id: item for item in draft.evidence}
    issues: list[RuleIssue] = []
    seen: set[tuple[str, str, str, str, tuple[str, ...]]] = set()

    def add(issue: RuleIssue) -> None:
        # Only exact repeats are removed. Two findings may legitimately share a
        # code and field while pointing to different source evidence.
        key = (issue.id, issue.code, issue.field, issue.message, tuple(issue.evidence_ids))
        if key not in seen:
            seen.add(key)
            issues.append(issue)

    if draft.document_type not in DRAWING_TYPES:
        add(
            RuleIssue(
                id="rule-document-type-routed",
                code="DOCUMENT_TYPE_ROUTED",
                severity="blocked",
                message="文档类型不是当前支持的机械图或装配图。",
                category="source_integrity",
                impact="当前审核规则与文档类型不匹配，继续使用可能产生错误结论。",
                recommendation="切换到匹配的文档审核流程，或由工程师确认该文件的受控用途。",
                requires_human_confirmation=True,
            )
        )

    for evidence in draft.evidence:
        if evidence.page > page_count:
            add(
                RuleIssue(
                    id=f"rule-evidence-page-{_safe_id(evidence.id)}",
                    code="EVIDENCE_INCOMPLETE",
                    severity="blocked",
                    message=f"证据 {evidence.id} 引用第 {evidence.page} 页，超出实际页码 1..{page_count}。",
                    category="source_integrity",
                    impact="结论引用了不存在的源页，无法追溯和复核。",
                    recommendation="重新定位有效页码与区域后，再生成审核草稿。",
                    evidence_ids=[evidence.id],
                )
            )

    fields = {item.name: item for item in draft.fields}
    part_name = fields.get("part_name")
    if (
        draft.document_type == DocumentType.MECHANICAL_DRAWING
        and part_name
        and re.search(r"总成|装配|assembly", part_name.value, flags=re.IGNORECASE)
    ):
        add(
            RuleIssue(
                id="rule-document-type-inconsistency",
                code="DOCUMENT_TYPE_INCONSISTENCY",
                field="document_type",
                severity="needs_review",
                message="标题或零件名中出现“总成/装配”，但模型分类为 mechanical_drawing；使用前请确认文档类型。",
                category="source_integrity",
                impact="零件图和装配图审核范围不同，类型错误会遗漏装配关系或零件特性。",
                recommendation="确认受控文档类型，并采用对应审核清单重新检查。",
                evidence_ids=[item for item in part_name.evidence_ids if item in evidence_by_id],
            )
        )

    for name in REQUIRED_FIELDS:
        field = fields.get(name)
        valid_ids = [item for item in field.evidence_ids if item in evidence_by_id] if field else []
        if field is None or not field.value.strip():
            add(
                RuleIssue(
                    id=f"rule-missing-{_safe_id(name)}",
                    code="MISSING_REQUIRED_FIELD",
                    field=name,
                    severity="blocked",
                    message=f"必填字段 {name} 缺失。",
                    category="requirement_consistency",
                    impact="关键工程信息缺失，可能阻断版本控制、选材、制造或检验。",
                    recommendation="由设计人员补充该字段并重新发布受控图纸。",
                    evidence_ids=valid_ids,
                )
            )
            continue
        if not valid_ids or len(valid_ids) != len(field.evidence_ids):
            add(
                RuleIssue(
                    id=f"rule-evidence-{_safe_id(name)}",
                    code="EVIDENCE_INCOMPLETE",
                    field=name,
                    severity="blocked",
                    message=f"字段 {name} 没有完整的证据引用。",
                    category="source_integrity",
                    impact="该字段不能回到源图复核，不能作为工程决定依据。",
                    recommendation="补齐有效证据 ID、页码和区域后再确认字段。",
                    evidence_ids=valid_ids,
                )
            )
        if field.confidence < min_confidence:
            add(
                RuleIssue(
                    id=f"rule-confidence-{_safe_id(name)}",
                    code="LOW_CONFIDENCE",
                    field=name,
                    severity="needs_review",
                    message=f"字段 {name} 置信度 {field.confidence:.2f} 低于阈值 {min_confidence:.2f}。",
                    category="requirement_consistency",
                    impact="识别可信度不足，直接使用可能造成工程要求理解错误。",
                    recommendation="对照原图人工核对，并在必要时修正为受控值。",
                    evidence_ids=valid_ids,
                )
            )

    for requirement in draft.engineering_requirements:
        valid_ids = [item for item in requirement.evidence_ids if item in evidence_by_id]
        if not valid_ids or len(valid_ids) != len(requirement.evidence_ids):
            add(
                RuleIssue(
                    id=f"rule-requirement-evidence-{_safe_id(requirement.id)}",
                    code="EVIDENCE_INCOMPLETE",
                    field=requirement.id,
                    severity="blocked",
                    message=f"工程要求 {requirement.id} 没有完整的证据引用。",
                    category="source_integrity",
                    impact="该工程要求不能回到源图复核，无法进入正式审核结论。",
                    recommendation="补齐有效证据 ID、页码和区域后重新生成审核草稿。",
                    evidence_ids=valid_ids,
                )
            )

    for finding in draft.findings:
        valid_ids = [item for item in finding.evidence_ids if item in evidence_by_id]
        invalid_reference = len(valid_ids) != len(finding.evidence_ids) or not valid_ids
        if invalid_reference:
            add(
                RuleIssue(
                    id=f"rule-finding-evidence-{_safe_id(finding.id)}",
                    code="EVIDENCE_INCOMPLETE",
                    field=finding.field,
                    severity="blocked",
                    message=f"问题 {finding.id} 没有完整的证据引用。",
                    category="source_integrity",
                    impact="问题结论缺少完整证据，不能用于工程判断。",
                    recommendation="重新关联已有证据；没有证据时改为开放问题。",
                    evidence_ids=valid_ids,
                )
            )
        severity = "blocked" if finding.code in BLOCKING_CODES else "needs_review"
        if finding.confidence < min_confidence:
            severity = "needs_review" if severity != "blocked" else severity
        # A model finding is untrusted input. The deterministic rule layer owns
        # severity and the human gate; the model cannot suppress a finding by
        # setting requires_human_confirmation=false.
        add(
            RuleIssue(
                id=finding.id,
                code=finding.code,
                field=finding.field,
                severity=severity,
                message=finding.conclusion,
                category=finding.category,
                impact=finding.impact,
                recommendation=finding.recommendation,
                evidence_ids=valid_ids,
                requires_human_confirmation=True,
            )
        )

    severities = {issue.severity for issue in issues}
    if "blocked" in severities:
        status = "blocked"
    elif "needs_review" in severities:
        status = "needs_review"
    else:
        status = "pass"
    required = [issue.id for issue in issues if issue.requires_human_confirmation]
    return RuleReport(status=status, issues=issues, required_decision_ids=required, rule_version="2.3")


def normalize_document_type(value: str) -> DocumentType:
    """Normalize legacy/model labels before they enter ReviewDraftV2."""

    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    aliases = {
        "mechanical_drawing": DocumentType.MECHANICAL_DRAWING,
        "part_drawing": DocumentType.MECHANICAL_DRAWING,
        "assembly_drawing": DocumentType.ASSEMBLY_DRAWING,
        "multi_page_assembly_drawing": DocumentType.ASSEMBLY_DRAWING,
        "process_document": DocumentType.PROCESS_DOCUMENT,
        "process_sheet": DocumentType.PROCESS_DOCUMENT,
    }
    return aliases.get(normalized, DocumentType.UNKNOWN)
