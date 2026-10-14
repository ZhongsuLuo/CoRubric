from __future__ import annotations
from typing import Any, Dict, Literal, List, Mapping, Optional, Sequence
from pydantic import BaseModel, ConfigDict, Field, model_validator


SCORE_BUDGET_EPSILON = 1e-6


class FewShotExample(BaseModel):
    example_input: str
    example_output_description: str
    example_output_score: float


class DeductionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    condition: str
    deduct: float
    note: Optional[str] = None


class ScoringPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point_id: str
    summary: str
    full_credit_condition: Optional[str] = None
    deduction_rules: List[DeductionRule] = Field(min_length=1)
    max_deduction: Optional[float] = None
    aggregation: Optional[str] = None
    applies_when: Optional[str] = None


class ScoringPointScore(BaseModel):
    point_id: str
    score: float
    max_score: Optional[float] = None
    reason: Optional[str] = None


class ScoringAggregation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "sum_deductions_capped"
    cap: Optional[float] = None
    score_formula: Optional[str] = None


def _as_scoring_point(point: "ScoringPoint | Mapping[str, object]") -> "ScoringPoint":
    return point if isinstance(point, ScoringPoint) else ScoringPoint.model_validate(point)


def _as_scoring_aggregation(
    aggregation: "ScoringAggregation | Mapping[str, object] | None",
) -> "ScoringAggregation":
    if aggregation is None:
        return ScoringAggregation()
    return aggregation if isinstance(aggregation, ScoringAggregation) else ScoringAggregation.model_validate(aggregation)


def structured_point_deduction_capacity(point: "ScoringPoint | Mapping[str, object]") -> float:
    """Return the largest deduction a single structured point can contribute."""
    normalized = _as_scoring_point(point)
    deductions = [rule.deduct for rule in normalized.deduction_rules]
    capacity = max(deductions, default=0.0) if normalized.aggregation == "max_triggered" else sum(deductions)
    if normalized.max_deduction is not None:
        capacity = min(capacity, normalized.max_deduction)
    return max(capacity, 0.0)


def structured_deduction_capacity(
    points: Sequence["ScoringPoint | Mapping[str, object]"],
    aggregation: "ScoringAggregation | Mapping[str, object] | None" = None,
) -> float:
    """Return the maximum possible deduction represented by a criterion's rules."""
    capacities = [structured_point_deduction_capacity(point) for point in points]
    normalized = _as_scoring_aggregation(aggregation)
    capacity = max(capacities, default=0.0) if normalized.mode == "max_triggered" else sum(capacities)
    if normalized.cap is not None:
        capacity = min(capacity, normalized.cap)
    return max(capacity, 0.0)


def validate_structured_score_budget(criterion: "CriteriaItem | Mapping[str, object]") -> None:
    """Require a structured criterion to allocate exactly its declared score budget."""
    normalized = criterion if isinstance(criterion, CriteriaItem) else CriteriaItem.model_validate(criterion)
    capacity = structured_deduction_capacity(normalized.scoring_points, normalized.scoring_aggregation)
    if abs(capacity - normalized.max_score) > SCORE_BUDGET_EPSILON:
        raise ValueError(
            f"评分项 {normalized.criteria_id} 的结构化评分点最多扣分为 {capacity:g} 分，"
            f"必须与评分项总分 {normalized.max_score:g} 分一致；如需调整，请同时修改总分。"
        )


def validate_structured_score_budgets(criteria_data: "CriteriaSchema | Mapping[str, object]") -> None:
    normalized = criteria_data if isinstance(criteria_data, CriteriaSchema) else CriteriaSchema.model_validate(criteria_data)
    for criterion in normalized.criteria:
        validate_structured_score_budget(criterion)


class CriteriaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria_id: str
    criteria_name: str
    description: str
    original_description: str
    max_score: float
    section: Optional[str] = None
    few_shot_examples: List[FewShotExample] = Field(default_factory=list)
    scoring_points: List[ScoringPoint] = Field(min_length=1)
    scoring_aggregation: ScoringAggregation

    @model_validator(mode="after")
    def validate_score_budget(self) -> "CriteriaItem":
        validate_structured_score_budget(self)
        return self


class CriteriaSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["structured-rubric-v1"]
    title: str
    background: str
    language: str
    base_score: float = 100
    criteria: List[CriteriaItem]


class GradingItem(BaseModel):
    criteria_id: str
    criteria_name: Optional[str] = None
    description: str
    original_description: str
    max_score: float
    criteria_score: float
    file_original_content: str
    section: str
    target_section: Optional[str]
    score_reason: str
    anchor_text: Optional[str] = None
    evidence_anchors: List["EvidenceAnchor"] = Field(default_factory=list)
    graded_by: Optional[str] = None
    acceptance_status: Optional[str] = None
    scoring_points: List[ScoringPoint]
    scoring_aggregation: ScoringAggregation
    matched_scoring_points: List[str] = Field(default_factory=list)
    matched_deduction_rules: List[str] = Field(default_factory=list)
    scoring_point_scores: List[ScoringPointScore] = Field(default_factory=list)


class GradingResult(BaseModel):
    file_name: str
    language: str
    base_score: float
    file_score: float
    criteria: List[GradingItem]


class EvidenceAnchor(BaseModel):
    anchor_type: Literal["text", "image", "absence"] = "text"
    text: Optional[str] = Field(default=None, max_length=8000)
    occurrence: Optional[int] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    image_src: Optional[str] = Field(default=None, max_length=2048)
    image_alt: Optional[str] = Field(default=None, max_length=2000)
    description: Optional[str] = Field(default=None, max_length=8000)
    region: Optional[Dict[str, float]] = None
    expected_location: Optional[str] = Field(default=None, max_length=2000)
    missing_item: Optional[str] = Field(default=None, max_length=2000)


class CaseReportGradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria_id: str
    criteria_score: float
    score_reason: str = ""
    anchor_text: str = ""
    evidence_anchors: List[EvidenceAnchor] = Field(default_factory=list)
    target_section: Optional[str] = None
    matched_scoring_points: List[str] = Field(default_factory=list)
    matched_deduction_rules: List[str] = Field(default_factory=list)
    scoring_point_scores: List[ScoringPointScore] = Field(default_factory=list)
    criteria_file_name: str


class CaseReportGradeDeleteRequest(BaseModel):
    criteria_id: str
    criteria_file_name: Optional[str] = None


class CaseReportModelGradeAcceptRequest(BaseModel):
    criteria_id: str
    criteria_file_name: Optional[str] = None
    accepted_by: Optional[str] = None


class ConvertRequest(BaseModel):
    pdf_path: str
    output_dir: str
    enable_audit: bool = True
    case_id: Optional[str] = None
    report_name: Optional[str] = None
    criteria_file_name: Optional[str] = None


class GradeRequest(BaseModel):
    submission_md_path: str
    criteria_path: str
    output_dir: str


class CriteriaUpdateRequest(BaseModel):
    criteria_path: str
    criteria_id: str
    few_shot_example: FewShotExample


class CriteriaFileRequest(BaseModel):
    file_name: str
    data: CriteriaSchema
    case_id: Optional[str] = None


class CriteriaCategoryAssignment(BaseModel):
    criteria_id: str
    section: str = Field(min_length=1)


class CriteriaCategoryApplyRequest(BaseModel):
    source_content_hash: str = Field(min_length=1)
    assignments: List[CriteriaCategoryAssignment] = Field(min_length=1)


class ResultFileRequest(BaseModel):
    file_name: str
    data: GradingResult


class ApiResponse(BaseModel):
    status: Literal["ok", "error"]
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class AlignmentEvaluateRequest(BaseModel):
    criteria_id: Optional[str] = None
    criteria_file_name: Optional[str] = None


class AlignmentAuditRequest(BaseModel):
    criteria_id: Optional[str] = None
    criteria_ids: Optional[List[str]] = None
    criteria_file_name: Optional[str] = None


class CohortSynthesisRequest(BaseModel):
    criteria_file_name: Optional[str] = None
    criterion_id: Optional[str] = None


class AutoGradeRequest(BaseModel):
    criteria_file_name: Optional[str] = None
    criteria_ids: Optional[List[str]] = None
    reason: Optional[str] = None


class AutoGradeBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria_file_name: str
    created_by: str = Field(default="teacher", max_length=200)
    report_names: List[str] = Field(min_length=1)
    start_report_name: str = Field(min_length=1, max_length=255)


class AutoGradeBatchConfirmRequest(BaseModel):
    confirmed_by: str = Field(default="teacher", max_length=200)


class AutoGradeBatchCancelRequest(BaseModel):
    cancelled_by: str = Field(default="teacher", max_length=200)


class DebugCaseGradeResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_case_id: str = Field(min_length=1, max_length=255)


class DebugExperimentResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_case_id: str = Field(min_length=1, max_length=255)
    criteria_file_name: str = Field(min_length=1, max_length=500)


class ConflictReassessRequest(BaseModel):
    teacher_feedback: str = Field(default="", max_length=4000)


class ConflictResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_pattern_id: str = Field(min_length=1, max_length=500)
    teacher_reason: str = Field(default="", max_length=4000)


class CriterionAuthorizationRequest(BaseModel):
    criteria_file_name: Optional[str] = None
    authorized_by: str = Field(default="teacher", max_length=200)


class CriterionRepairConfirmRequest(BaseModel):
    criteria_file_name: Optional[str] = None
    confirmed_by: str = Field(default="teacher", max_length=200)


class SourceGradeAssignment(BaseModel):
    """Structured score mapping retained while an implicit grade becomes a rubric proposal."""

    implicit_grade_id: str
    # A model draft shown during proposal review when the teacher did not write
    # a reason. It remains distinct from teacher-authored notes.
    generated_score_reason: Optional[str] = Field(default=None, max_length=4000)
    scoring_point_scores: List[ScoringPointScore] = Field(default_factory=list)
    matched_scoring_points: List[str] = Field(default_factory=list)
    matched_deduction_rules: List[str] = Field(default_factory=list)
    allocation_source: Literal["model", "teacher", "derived"] = "model"


class ImplicitGradeCreateRequest(BaseModel):
    report_name: str = Field(max_length=255)
    anchor: EvidenceAnchor
    score: float
    max_score: Optional[float] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    temporary_label: Optional[str] = Field(default=None, max_length=500)
    matched_scoring_points: List[str] = Field(default_factory=list)
    matched_deduction_rules: List[str] = Field(default_factory=list)
    scoring_point_scores: List[ScoringPointScore] = Field(default_factory=list)
    source: Literal["teacher"] = "teacher"
    criteria_file_name: Optional[str] = Field(default=None, max_length=255)
    request_id: Optional[str] = Field(default=None, max_length=255)


class ImplicitGradeUpdateRequest(BaseModel):
    anchor: Optional[EvidenceAnchor] = None
    score: Optional[float] = None
    max_score: Optional[float] = None
    notes: Optional[str] = None
    temporary_label: Optional[str] = None
    status: Optional[Literal["uninduced", "queued", "inducing", "proposal_ready", "confirmed", "archived", "failed"]] = None
    proposal_id: Optional[str] = None


class ImplicitGradeItem(BaseModel):
    implicit_grade_id: str
    case_id: str
    report_name: str
    source: str
    teacher_id: Optional[str] = None
    criteria_file_name: Optional[str] = None
    anchor: EvidenceAnchor
    score: float
    max_score: Optional[float] = None
    notes: Optional[str] = None
    temporary_label: Optional[str] = None
    matched_scoring_points: List[str] = Field(default_factory=list)
    matched_deduction_rules: List[str] = Field(default_factory=list)
    scoring_point_scores: List[ScoringPointScore] = Field(default_factory=list)
    status: Literal["uninduced", "queued", "inducing", "proposal_ready", "confirmed", "archived", "failed"] = "uninduced"
    proposal_id: Optional[str] = None
    promoted_criterion_id: Optional[str] = None
    confirmed_event_id: Optional[str] = None
    source_digest: str
    failure_reason: Optional[str] = None
    created_at: str
    updated_at: str


class CriterionProposalItem(BaseModel):
    proposal_id: str
    proposal_version: int = 1
    case_id: str
    proposal_type: Literal["create_criterion", "update_criterion"]
    target_criterion_id: Optional[str] = None
    target_criterion_version_at_proposal: Optional[str] = None
    source_implicit_grade_ids: List[str] = Field(default_factory=list)
    source_grade_digests: List[str] = Field(default_factory=list)
    source_event_ids: List[str] = Field(default_factory=list)
    source_event_digests: List[str] = Field(default_factory=list)
    source_teacher_event_ids: List[str] = Field(default_factory=list)
    source_model_event_ids: List[str] = Field(default_factory=list)
    scoring_diff: Optional[Dict[str, Any]] = None
    source_grade_assignments: List[SourceGradeAssignment] = Field(default_factory=list)
    status: Literal["pending", "confirmed", "rejected", "superseded", "failed"] = "pending"
    criteria_file_name: str = "criteria.json"
    criteria_file_version_at_proposal: Optional[str] = None
    suggested_criterion: CriteriaItem
    rubric_patch: Optional[Dict[str, Any]] = None
    rationale: Optional[str] = None
    model_id: Optional[str] = None
    confidence: Optional[float] = None
    confirmed_event_ids: List[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    created_at: str
    updated_at: str
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None


class CriterionProposalConfirmRequest(BaseModel):
    proposal_version: int
    suggested_criterion: CriteriaItem
    # When provided by proposal review, this explicitly chooses an existing
    # target criterion. A provided null means create a new criterion instead.
    target_criterion_id: Optional[str] = None
    criteria_file_name: Optional[str] = None
    confirmed_by: Optional[str] = None
    source_grade_assignments: Optional[List[SourceGradeAssignment]] = None


class CriterionProposalRejectRequest(BaseModel):
    proposal_version: int
    reason: Optional[str] = None
