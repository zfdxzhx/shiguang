"""Versioned contracts shared by AI providers, rules, persistence, and APIs."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentType(str, Enum):
    MECHANICAL_DRAWING = "mechanical_drawing"
    ASSEMBLY_DRAWING = "assembly_drawing"
    PROCESS_DOCUMENT = "process_document"
    OTHER = "other"
    UNKNOWN = "unknown"


RequirementCategory = Literal[
    "identity",
    "material",
    "dimension",
    "tolerance",
    "datum",
    "surface",
    "heat_treatment",
    "process_note",
    "inspection",
    "assembly",
    "standard",
    "other",
]

FindingCategory = Literal[
    "source_integrity",
    "requirement_consistency",
    "manufacturability",
    "inspectability",
    "assembly",
    "compliance",
    "other",
]


class Evidence(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    page: int = Field(ge=1)
    region: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2000)
    text: str = Field(default="", max_length=4000)
    bbox: list[float] | None = Field(
        default=None,
        description="Optional normalized [x1,y1,x2,y2] coordinates in the 0..1 range.",
    )

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if len(value) != 4:
            raise ValueError("bbox must contain four normalized coordinates")
        x1, y1, x2, y2 = value
        if not all(0 <= number <= 1 for number in value):
            raise ValueError("bbox coordinates must be within 0..1")
        if x1 >= x2 or y1 >= y2:
            raise ValueError("bbox must have positive width and height")
        return value


class ExtractedField(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=6000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class EngineeringRequirement(StrictModel):
    """One evidence-grounded engineering requirement visible in the source."""

    id: str = Field(min_length=1, max_length=120)
    category: RequirementCategory
    requirement: str = Field(min_length=1, max_length=4000)
    criticality: Literal["critical", "key", "general"] = "general"
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class DraftFinding(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=120)
    field: str = Field(default="", max_length=120)
    conclusion: str = Field(min_length=1, max_length=4000)
    category: FindingCategory = "other"
    impact: str = Field(default="", max_length=4000)
    recommendation: str = Field(default="", max_length=4000)
    confidence: float = Field(ge=0, le=1)
    requires_human_confirmation: bool
    evidence_ids: list[str] = Field(default_factory=list)


class ReviewDraftV2(StrictModel):
    """AI-produced draft. It deliberately has no authoritative review status."""

    schema_version: Literal["2.0"] = "2.0"
    document_type: DocumentType
    summary: str = Field(min_length=1, max_length=6000)
    fields: list[ExtractedField]
    engineering_requirements: list[EngineeringRequirement] = Field(default_factory=list)
    evidence: list[Evidence]
    findings: list[DraftFinding]
    open_questions: list[str]

    @model_validator(mode="after")
    def unique_ids(self) -> "ReviewDraftV2":
        evidence_ids = [item.id for item in self.evidence]
        requirement_ids = [item.id for item in self.engineering_requirements]
        finding_ids = [item.id for item in self.findings]
        field_names = [item.name for item in self.fields]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("engineering requirement ids must be unique")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding ids must be unique")
        required_fields = {"part_name", "revision", "material", "dimensions", "tolerances"}
        if len(field_names) != len(required_fields) or set(field_names) != required_fields:
            raise ValueError("fields must contain each required ReviewDraftV2 field exactly once")
        return self


class RuleIssue(StrictModel):
    id: str
    code: str
    field: str = ""
    severity: Literal["needs_review", "blocked"]
    message: str
    category: FindingCategory = "other"
    impact: str = ""
    recommendation: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    requires_human_confirmation: bool = True


class RuleReport(StrictModel):
    status: Literal["pass", "needs_review", "blocked"]
    issues: list[RuleIssue]
    required_decision_ids: list[str]
    rule_version: str = "2.0"


class EngineeringCoverage(StrictModel):
    area: Literal[
        "source_integrity",
        "requirement_consistency",
        "manufacturability",
        "inspectability",
        "compliance",
    ]
    status: Literal["covered", "needs_review", "insufficient_evidence"]
    conclusion: str = Field(min_length=1, max_length=2000)


class EngineeringReviewIssue(StrictModel):
    id: str
    code: str
    field: str = ""
    category: FindingCategory
    severity: Literal["needs_review", "blocked"]
    problem: str
    impact: str
    recommendation: str
    evidence_ids: list[str] = Field(default_factory=list)
    human_decision: Literal["pending", "confirmed", "corrected", "rejected"] = "pending"
    corrected_value: str = ""
    reviewer: str = ""
    note: str = ""


class EngineeringActionItem(StrictModel):
    priority: Literal["P0", "P1", "P2"]
    action: str = Field(min_length=1, max_length=4000)
    owner_role: str = Field(min_length=1, max_length=120)
    source_issue_ids: list[str] = Field(default_factory=list)


class EngineeringReviewV1(StrictModel):
    """Deterministic, human-gated engineering review view of ReviewDraftV2."""

    contract_version: Literal["1.0"] = "1.0"
    report_stage: Literal["draft", "final"]
    recommended_disposition: Literal["blocked", "conditional", "ready_for_human_release"]
    conclusion: str = Field(min_length=1, max_length=4000)
    blocker_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    requirements: list[EngineeringRequirement]
    coverage: list[EngineeringCoverage]
    issues: list[EngineeringReviewIssue]
    actions: list[EngineeringActionItem]
    open_questions: list[str]
    human_confirmation_required: bool = True


class AnalysisRequest(StrictModel):
    document_id: str = Field(min_length=1, max_length=80)
    feature: Literal["review", "process", "quote"] = "review"
    mode: Literal["mock", "live-training"] = "live-training"
    external_processing_consent: bool = False


class EvidenceLocalizationBenchmarkRequest(StrictModel):
    """Run only the visual localization stage against a fixed prior target set."""

    source_analysis_ids: list[str] = Field(min_length=1, max_length=6)
    external_processing_consent: bool = False

    @field_validator("source_analysis_ids")
    @classmethod
    def unique_source_analysis_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 80 for item in normalized):
            raise ValueError("source analysis ids must be non-empty and at most 80 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source analysis ids must be unique")
        return normalized


class ProviderConfigurationRequest(StrictModel):
    """AI settings. Secrets are never returned and may use macOS Keychain."""

    provider: Literal["kimi-hybrid", "hybrid", "kimi", "gemini", "openai"]
    api_key: SecretStr | None = None
    model: str = Field(min_length=1, max_length=160)
    secondary_api_key: SecretStr | None = None
    secondary_model: str | None = Field(default=None, max_length=160)
    reuse_primary: bool = False
    reuse_secondary: bool = False
    storage: Literal["session", "keychain"] = "session"

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("model must be a non-empty identifier without whitespace")
        return normalized

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if len(value.get_secret_value().strip()) < 12:
            raise ValueError("api_key is too short")
        return value

    @model_validator(mode="after")
    def hybrid_has_deepseek_settings(self) -> "ProviderConfigurationRequest":
        primary_key = (
            self.api_key.get_secret_value().strip()
            if self.api_key is not None
            else ""
        )
        if len(primary_key) < 12 and not self.reuse_primary:
            raise ValueError("provider requires a valid api_key")
        if self.provider not in {"kimi-hybrid", "hybrid"}:
            return self
        secondary_model = (self.secondary_model or "").strip()
        secondary_key = (
            self.secondary_api_key.get_secret_value().strip()
            if self.secondary_api_key is not None
            else ""
        )
        if not secondary_model or any(character.isspace() for character in secondary_model):
            raise ValueError("hybrid provider requires a valid secondary_model")
        if len(secondary_key) < 12 and not self.reuse_secondary:
            raise ValueError("hybrid provider requires a valid secondary_api_key")
        self.secondary_model = secondary_model
        return self


class DecisionRequest(StrictModel):
    decision: Literal["confirmed", "corrected", "rejected"]
    note: str = Field(default="", max_length=4000)
    corrected_value: str | None = Field(
        default=None,
        max_length=6000,
        validation_alias=AliasChoices("corrected_value", "correction"),
    )
    reviewer: str = Field(
        default="课堂用户",
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("reviewer", "reviewer_name"),
    )

    @model_validator(mode="after")
    def correction_has_value(self) -> "DecisionRequest":
        if self.decision == "corrected" and not (self.corrected_value or "").strip():
            raise ValueError("corrected decisions require corrected_value")
        return self


class FieldCorrectionRequest(StrictModel):
    """Explicit human correction for one AI-extracted field."""

    corrected_value: str = Field(
        min_length=1,
        max_length=6000,
        validation_alias=AliasChoices("corrected_value", "correction", "value"),
    )
    reviewer: str = Field(
        default="课堂用户",
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("reviewer", "reviewer_name"),
    )
    note: str = Field(default="", max_length=4000)

    @field_validator("corrected_value")
    @classmethod
    def normalize_corrected_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("corrected_value must not be blank")
        return normalized


class EvidenceLocationRequest(StrictModel):
    """Human-supplied evidence rectangle on one rendered source page."""

    bbox: list[float]
    reviewer: str = Field(
        default="课堂用户",
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("reviewer", "reviewer_name"),
    )
    note: str = Field(default="", max_length=1000)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("bbox must contain four normalized coordinates")
        x1, y1, x2, y2 = value
        if not all(0 <= number <= 1 for number in value):
            raise ValueError("bbox coordinates must be within 0..1")
        if x1 >= x2 or y1 >= y2:
            raise ValueError("bbox must have positive width and height")
        if (x2 - x1) < 0.005 or (y2 - y1) < 0.005:
            raise ValueError("bbox is too small to be a reliable human location")
        return [round(number, 6) for number in value]


class FinalizeRequest(StrictModel):
    reviewer: str = Field(
        default="课堂用户",
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("reviewer", "reviewer_name"),
    )
    reviewer_role: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=4000)
    acknowledgement: bool = False

    @model_validator(mode="after")
    def acknowledged(self) -> "FinalizeRequest":
        if not self.acknowledgement:
            raise ValueError("acknowledgement must be true before finalization")
        return self


class ManufacturingFamily(str, Enum):
    CNC_MACHINING = "cnc_machining"
    SHEET_METAL = "sheet_metal"
    INJECTION_MOLDING = "injection_molding"
    ASSEMBLY = "assembly"


class SourceFact(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=6000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source: Literal["ai_draft", "human_correction"] = "ai_draft"


class DrawingFactsV1(StrictModel):
    """Source-labelled facts that one independent feature run may use."""

    schema_version: Literal["1.0"] = "1.0"
    analysis_id: str = Field(min_length=1, max_length=80)
    document_type: DocumentType
    review_business_status: Literal["pass", "needs_review", "blocked"]
    source_status: Literal["ai_extracted", "human_finalized"] = "ai_extracted"
    facts: list[SourceFact]
    missing_for_process: list[str]
    boundary: str = Field(min_length=1, max_length=1000)


class ProcessPlanRequest(StrictModel):
    manufacturing_family: ManufacturingFamily
    quantity: int = Field(ge=1, le=1_000_000)
    material_form: str = Field(default="待工艺工程师确认", max_length=240)
    equipment_capability: str = Field(default="", max_length=1000)
    inspection_capability: str = Field(default="", max_length=1000)
    special_requirements: str = Field(default="", max_length=2000)


class ProcessStep(StrictModel):
    sequence: int = Field(ge=1, le=100)
    operation: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=1000)
    resource: str = Field(min_length=1, max_length=500)
    control_points: list[str]


class ProcessPlanDraftV1(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    analysis_id: str = Field(min_length=1, max_length=80)
    status: Literal["draft"] = "draft"
    manufacturing_family: ManufacturingFamily
    quantity: int
    material_form: str
    special_requirements: str
    steps: list[ProcessStep]
    assumptions: list[str]
    warnings: list[str]
    generated_by: Literal["controlled-template-v1"] = "controlled-template-v1"
    review_status: Literal["pending", "confirmed"] = "pending"
    reviewed_by: str | None = Field(default=None, max_length=120)
    reviewer_role: str | None = Field(default=None, max_length=120)
    reviewed_at: str | None = Field(default=None, max_length=80)
    review_note: str = Field(default="", max_length=2000)
    human_confirmation_required: Literal[True] = True


class ProcessSourceFactSnapshot(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=6000)
    source: Literal["ai_draft", "human_correction"]
    evidence_ids: list[str] = Field(default_factory=list)


class ProcessParameterRequirement(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=500)
    unit: str = Field(default="", max_length=80)
    source: Literal["drawing_fact", "reference_profile", "human_input", "controlled_rule", "pending"]
    status: Literal["known", "needs_confirmation"]


class ProcessStepV2(StrictModel):
    sequence: int = Field(ge=1, le=100)
    operation: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=1000)
    input_state: str = Field(min_length=1, max_length=1000)
    output_state: str = Field(min_length=1, max_length=1000)
    equipment_capability: str = Field(min_length=1, max_length=1000)
    setup_and_datum: str = Field(min_length=1, max_length=1000)
    tooling_category: str = Field(min_length=1, max_length=1000)
    key_characteristics: list[str]
    quality_checks: list[str]
    parameters: list[ProcessParameterRequirement]
    source_fact_fields: list[str]
    human_confirmation_required: Literal[True] = True


class ProcessRisk(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    level: Literal["high", "medium", "low"]
    concern: str = Field(min_length=1, max_length=1000)
    impact: str = Field(min_length=1, max_length=1000)
    verification_action: str = Field(min_length=1, max_length=1000)
    owner_role: str = Field(min_length=1, max_length=120)


class ProcessPlanDraftV2(StrictModel):
    """Reference route generated from the current feature run's drawing facts."""

    schema_version: Literal["2.0"] = "2.0"
    analysis_id: str = Field(min_length=1, max_length=80)
    status: Literal["draft"] = "draft"
    manufacturing_family: ManufacturingFamily
    quantity: int = Field(ge=1, le=1_000_000)
    material_form: str = Field(min_length=1, max_length=240)
    equipment_capability: str = Field(default="", max_length=1000)
    inspection_capability: str = Field(default="", max_length=1000)
    special_requirements: str = Field(default="", max_length=2000)
    route_summary: str = Field(min_length=1, max_length=3000)
    source_fact_digest: str = Field(min_length=64, max_length=64)
    source_facts: list[ProcessSourceFactSnapshot]
    missing_inputs: list[str]
    open_questions: list[str]
    steps: list[ProcessStepV2]
    risks: list[ProcessRisk]
    inspection_strategy: list[str]
    external_processes: list[str]
    assumptions: list[str]
    warnings: list[str]
    generated_by: Literal["controlled-rule-engine-v2"] = "controlled-rule-engine-v2"
    review_status: Literal["pending", "confirmed"] = "pending"
    reviewed_by: str | None = Field(default=None, max_length=120)
    reviewer_role: str | None = Field(default=None, max_length=120)
    reviewed_at: str | None = Field(default=None, max_length=80)
    review_note: str = Field(default="", max_length=2000)
    review_checklist: list[str] = Field(default_factory=list)
    human_confirmation_required: Literal[True] = True


ProcessPlanDraft = ProcessPlanDraftV1 | ProcessPlanDraftV2


class ProcessPlanConfirmationRequest(StrictModel):
    reviewer: str = Field(min_length=1, max_length=120)
    reviewer_role: str = Field(default="工艺复核人", min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    route_checked: bool = False
    equipment_checked: bool = False
    quality_checked: bool = False
    acknowledgement: bool = False

    @model_validator(mode="after")
    def acknowledged(self) -> "ProcessPlanConfirmationRequest":
        if not self.acknowledgement:
            raise ValueError("acknowledgement must be true before process-plan confirmation")
        if not (self.route_checked and self.equipment_checked and self.quality_checked):
            raise ValueError("route, equipment, and quality checks must all be completed")
        return self


class PreQuoteRequest(StrictModel):
    net_weight_kg: float = Field(gt=0, le=1_000_000)
    material_unit_price: float = Field(ge=0, le=10_000_000)
    material_loss_rate_pct: float = Field(ge=0, le=500)
    setup_hours: float = Field(ge=0, le=100_000)
    processing_minutes_per_part: float = Field(gt=0, le=100_000)
    machine_hourly_rate: float = Field(ge=0, le=10_000_000)
    tooling_cost: float = Field(ge=0, le=1_000_000_000)
    outsourcing_cost: float = Field(ge=0, le=1_000_000_000)
    inspection_packaging_per_part: float = Field(ge=0, le=10_000_000)
    logistics_cost: float = Field(ge=0, le=1_000_000_000)
    overhead_rate_pct: float = Field(ge=0, le=500)
    risk_rate_pct: float = Field(ge=0, le=500)
    target_margin_pct: float = Field(ge=0, lt=95)
    currency: Literal["CNY"] = "CNY"


class ReferenceSource(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    publisher: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=1000)
    accessed_at: str = Field(min_length=10, max_length=32)
    role: str = Field(min_length=1, max_length=240)
    note: str = Field(min_length=1, max_length=1000)


class ReferenceParameterBasis(StrictModel):
    fields: list[str] = Field(min_length=1)
    basis: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(default_factory=list)


class ClassroomReferenceProfile(StrictModel):
    """Versioned, source-labelled inputs for a non-binding classroom run."""

    schema_version: Literal["1.0"] = "1.0"
    catalog_version: Literal["classroom-reference-2026.08"] = "classroom-reference-2026.08"
    analysis_id: str = Field(min_length=1, max_length=80)
    manufacturing_family: ManufacturingFamily
    match_confidence: float = Field(ge=0, le=1)
    match_reasons: list[str]
    quantity: int = Field(ge=1, le=1_000_000)
    material_form: str = Field(min_length=1, max_length=240)
    equipment_capability: str = Field(min_length=1, max_length=1000)
    inspection_capability: str = Field(min_length=1, max_length=1000)
    special_requirements: str = Field(default="", max_length=2000)
    quote_inputs: PreQuoteRequest
    parameter_basis: list[ReferenceParameterBasis]
    sources: list[ReferenceSource]
    assumptions: list[str]
    boundary: str = Field(min_length=1, max_length=2000)
    generated_by: Literal["ai-facts-public-reference-v1"] = "ai-facts-public-reference-v1"
    human_confirmation_required: Literal[True] = True


class CostItem(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    amount: float = Field(ge=0)
    basis: str = Field(min_length=1, max_length=500)


class PreQuoteV1(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    analysis_id: str = Field(min_length=1, max_length=80)
    process_plan_version: Literal["1.0", "2.0"] = "1.0"
    status: Literal["prequote"] = "prequote"
    quantity: int
    currency: Literal["CNY"] = "CNY"
    inputs: PreQuoteRequest
    cost_items: list[CostItem]
    direct_cost: float
    overhead_cost: float
    risk_cost: float
    total_cost: float
    target_revenue: float
    unit_prequote: float
    formula_version: Literal["deterministic-cost-v1"] = "deterministic-cost-v1"
    assumptions: list[str]
    warnings: list[str]
    human_confirmation_required: Literal[True] = True


class BusinessArtifactsV1(StrictModel):
    contract_version: Literal["1.0"] = "1.0"
    drawing_facts: DrawingFactsV1
    process_plan: ProcessPlanDraft | None = None
    prequote: PreQuoteV1 | None = None
