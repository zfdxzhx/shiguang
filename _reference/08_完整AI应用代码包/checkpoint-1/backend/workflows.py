"""Review-only workflow surface used before Checkpoint 3.

The review feature needs correction-safe derived fields. Process planning and
quotation are intentionally absent so learners implement them in CP2 -> CP3.
"""

from __future__ import annotations

from .models import ReviewDraftV2, RuleReport


FIELD_CORRECTION_PREFIX = "field:"


def human_field_corrections(*, rules: RuleReport, decisions: list[dict]) -> dict[str, str]:
    issues_by_id = {item.id: item for item in rules.issues}
    corrections: dict[str, str] = {}
    for decision in decisions:
        corrected = (decision.get("corrected_value") or "").strip()
        finding_id = str(decision.get("finding_id") or "")
        issue = issues_by_id.get(finding_id)
        if decision.get("decision") != "corrected" or not corrected:
            continue
        if issue and issue.field:
            corrections[issue.field] = corrected
        if finding_id.startswith(FIELD_CORRECTION_PREFIX):
            corrections[finding_id.removeprefix(FIELD_CORRECTION_PREFIX)] = corrected
    return corrections


def build_effective_review_draft(
    *, draft: ReviewDraftV2, rules: RuleReport, decisions: list[dict]
) -> ReviewDraftV2:
    effective = draft.model_copy(deep=True)
    corrections = human_field_corrections(rules=rules, decisions=decisions)
    for field in effective.fields:
        if field.name in corrections:
            field.value = corrections[field.name]
            field.confidence = 1.0
    return effective


def _checkpoint_3_required(*_: object, **__: object):
    raise NotImplementedError(
        "TODO CP3: implement independent process planning and deterministic quotation"
    )


build_drawing_facts = _checkpoint_3_required
build_process_plan = _checkpoint_3_required
build_prequote = _checkpoint_3_required
build_artifacts = _checkpoint_3_required
