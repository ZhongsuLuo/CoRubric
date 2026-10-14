from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import alignment, services
from backend.app.schemas import (
    CaseReportGradeRequest,
    CriteriaItem,
    FewShotExample,
    ScoringPointScore,
)
from backend.libs import update_criteria
from tests.test_alignment import _case, FakeEvaluator, FakeModel
from tests.test_implicit_grading import _patch_roots, _implicit_request
from backend.app import implicit_grading
from backend.app.schemas import CriterionProposalConfirmRequest


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeCompletion(self.content)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def _structured_point(criteria_id: str = "1", deduction: float = 10):
    return {
        "point_id": f"{criteria_id}.p1",
        "summary": "Evidence interpretation",
        "full_credit_condition": "Selected evidence is interpreted correctly.",
        "deduction_rules": [
            {"rule_id": f"{criteria_id}.p1.r1", "condition": "interpretation lacks justification", "deduct": deduction},
        ],
        "max_deduction": deduction,
        "aggregation": "max_triggered",
    }


def test_schema_requires_structured_criteria_and_detects_changes():
    with pytest.raises(ValidationError):
        CriteriaItem(
            criteria_id="1",
            criteria_name="Missing structure",
            description="description",
            original_description="description",
            max_score=4,
        )

    structured = CriteriaItem(
        criteria_id="1",
        criteria_name="Structured",
        description="summary",
        original_description="summary",
        max_score=4,
        scoring_points=[_structured_point("1", 4)],
        scoring_aggregation={"mode": "sum_deductions_capped", "cap": 4},
    )
    assert structured.scoring_points[0].deduction_rules[0].rule_id == "1.p1.r1"

    before = {"criteria": []}
    after = {"criteria": [structured.model_dump()]}
    assert services._changed_criteria_ids(before, after) == ["1"]


def test_legacy_scoring_point_score_without_reason_still_loads():
    score = ScoringPointScore.model_validate({"point_id": "1.p1", "score": 8})

    assert score.reason is None
    assert score.model_dump(exclude_none=True) == {"point_id": "1.p1", "score": 8.0}


def test_structured_score_budget_must_match_criterion_max_score(tmp_path, monkeypatch):
    _, _, criteria_dir = _case(tmp_path, monkeypatch)
    path = criteria_dir / "criteria.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    invalid = json.loads(json.dumps(original))
    invalid["criteria"][0]["scoring_points"] = [_structured_point("1", 4)]
    invalid["criteria"][0]["scoring_aggregation"] = {"mode": "sum_deductions_capped", "cap": 10}

    with pytest.raises(ValueError, match="必须与评分项总分"):
        services.save_criteria_file("criteria.json", invalid)

    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_teacher_grade_event_and_projection_preserve_structured_rubric(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    path = criteria_dir / "criteria.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["criteria"][0]["scoring_points"] = [_structured_point("1")]
    data["criteria"][0]["scoring_aggregation"] = {"mode": "sum_deductions_capped", "cap": 10}
    path.write_text(json.dumps(data), encoding="utf-8")

    result = services.save_case_report_grade(
        case_id,
        report,
        CaseReportGradeRequest(
            criteria_id="1",
            criteria_score=8,
            score_reason="same",
            anchor_text="validated pendulum evidence",
            scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
            criteria_file_name="criteria.json",
        ),
    )
    store = alignment._store(case_id)
    event = store["events"][0]

    assert event["criterion_snapshot"]["scoring_points"][0]["point_id"] == "1.p1"
    assert event["authored_grade"]["scoring_points"][0]["deduction_rules"][0]["rule_id"] == "1.p1.r1"
    projected = result["criteria"][0]
    assert projected["scoring_points"][0]["point_id"] == "1.p1"
    assert result["file_score"] == 98


def test_teacher_can_save_explicit_score_for_each_structured_point(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    path = criteria_dir / "criteria.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    point = _structured_point("1")
    point["deduction_rules"][0]["deduct"] = 10
    point["max_deduction"] = 10
    data["criteria"][0]["scoring_points"] = [point]
    data["criteria"][0]["scoring_aggregation"] = {"mode": "sum_deductions_capped", "cap": 10}
    path.write_text(json.dumps(data), encoding="utf-8")

    result = services.save_case_report_grade(
        case_id,
        report,
        CaseReportGradeRequest(
            criteria_id="1",
            criteria_score=8,
            score_reason="教师直接给出该评分点 8 分。",
            anchor_text="validated pendulum evidence",
            criteria_file_name="criteria.json",
            scoring_point_scores=[{"point_id": "1.p1", "score": 8, "max_score": 10}],
        ),
    )

    assert result["criteria"][0]["scoring_point_scores"] == [{"point_id": "1.p1", "score": 8.0, "max_score": 10.0}]
    with pytest.raises(ValueError, match="must equal criterion score"):
        services.save_case_report_grade(
            case_id,
            report,
            CaseReportGradeRequest(
                criteria_id="1",
                criteria_score=8,
                score_reason="总分与评分点得分不一致。",
                anchor_text="validated pendulum evidence",
                criteria_file_name="criteria.json",
                scoring_point_scores=[{"point_id": "1.p1", "score": 7, "max_score": 10}],
            ),
        )


def test_new_structured_grade_rejects_missing_allocation_and_unlocatable_evidence(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="must include every"):
        services.save_case_report_grade(
            case_id,
            report,
            CaseReportGradeRequest(
                criteria_id="1",
                criteria_score=8,
                score_reason="missing allocation",
                anchor_text="validated pendulum evidence",
                criteria_file_name="criteria.json",
            ),
        )
    with pytest.raises(ValueError, match="could not be located"):
        services.save_case_report_grade(
            case_id,
            report,
            CaseReportGradeRequest(
                criteria_id="1",
                criteria_score=8,
                score_reason="bad evidence",
                anchor_text="not in this report",
                scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
                criteria_file_name="criteria.json",
            ),
        )


def test_absence_evidence_uses_a_trustworthy_boundary(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    result = services.save_case_report_grade(
        case_id,
        report,
        CaseReportGradeRequest(
            criteria_id="1",
            criteria_score=8,
            score_reason="required section is absent",
            evidence_anchors=[
                {
                    "anchor_type": "absence",
                    "expected_location": "document:end",
                    "missing_item": "limitations section",
                }
            ],
            scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
            criteria_file_name="criteria.json",
        ),
    )
    anchor = result["criteria"][0]["evidence_anchors"][0]
    assert anchor["anchor_type"] == "absence"
    assert anchor["missing_item"] == "limitations section"


def test_image_evidence_rejects_unsafe_source(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unsafe image source"):
        services.save_case_report_grade(
            case_id,
            report,
            CaseReportGradeRequest(
                criteria_id="1",
                criteria_score=8,
                score_reason="unsafe image evidence",
                evidence_anchors=[{
                    "anchor_type": "image",
                    "image_src": "images/../secret.png",
                    "description": "invented",
                }],
                scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
                criteria_file_name="criteria.json",
            ),
        )


def test_conflict_event_refs_expose_structured_point_and_rule_ids(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    path = criteria_dir / "criteria.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["criteria"][0]["scoring_points"] = [_structured_point("1")]
    path.write_text(json.dumps(data), encoding="utf-8")
    services.save_case_report_grade(
        case_id,
        report,
        CaseReportGradeRequest(
            criteria_id="1",
            criteria_score=8,
            score_reason="same",
            anchor_text="validated pendulum evidence",
            criteria_file_name="criteria.json",
            matched_scoring_points=["1.p1"],
            matched_deduction_rules=["1.p1.r1"],
            scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
        ),
    )
    store = alignment._store(case_id)
    event_id = store["events"][0]["event_id"]
    store["audit_artifacts"][event_id] = {
        "matched_rules": ["1.p1", "1.p1.r1"],
        "fit": "needs_review",
        "issue_type": "score_range_mismatch",
        "expected_score_range": [6, 7],
        "recommended_score": 6,
        "reason": "Selected evidence triggers rule 1.p1.r1, so the teacher score 8 is above the expected 6–7 range.",
    }

    ref = alignment._conflict_event_refs(store, [event_id])[0]

    assert ref["matched_scoring_points"] == ["1.p1"]
    assert ref["matched_deduction_rules"] == ["1.p1.r1"]
    assert ref["audited_rule_ids"] == ["1.p1", "1.p1.r1"]
    assert ref["audit_reason"].startswith("Selected evidence triggers")
    assert ref["expected_score_range"] == [6, 7]
    assert ref["recommended_score"] == 6


def test_alignment_summary_hides_legacy_single_event_conflicts(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(
        case_id,
        report,
        CaseReportGradeRequest(
            criteria_id="1",
            criteria_score=8,
            score_reason="same",
            anchor_text="validated pendulum evidence",
            scoring_point_scores=[{"point_id": "1.p1", "score": 8}],
            criteria_file_name="criteria.json",
        ),
    )
    store = alignment._store(case_id)
    event_id = store["events"][0]["event_id"]
    store["conflicts"] = [{
        "conflict_id": "persisted-conflict",
        "criterion_id": "1",
        "status": "open",
        "suspect_event_ids": [event_id],
        "supporting_event_ids": [],
        "affected_event_ids": [event_id],
        "reason": "persisted conflict missing event refs",
    }]

    summary = alignment.alignment_summary(case_id, store)

    assert summary["open_conflicts"] == []


def test_update_criteria_with_few_shot_returns_structured_diff(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    point = _structured_point("1")
    point["deduction_rules"][0]["deduct"] = 10
    point["max_deduction"] = 10
    content = json.dumps(
        {
            "description": "Refined summary.",
            "scoring_points": [point],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
        },
        ensure_ascii=False,
    )
    completions = _FakeCompletions(content)
    monkeypatch.setattr(update_criteria, "get_chat_client", lambda provider: _FakeClient(completions))

    response = services.update_criteria_with_few_shot(
        str(criteria_dir / "criteria.json"),
        "1",
        FewShotExample(example_input="x", example_output_description="reason", example_output_score=8),
    )

    assert response["updated"] is True
    assert response["description_changed"] is True
    assert response["structured_changed"] is True
    assert response["after_scoring_points"][0]["point_id"] == "1.p1"
    assert response["after_criterion"]["scoring_aggregation"]["cap"] == 10


class StructuredInductionModel:
    def propose(self, context):
        self.context = context
        return {
            "proposal_type": "create_criterion",
            "target_criterion_id": None,
            "criteria_name": "Structured implicit criterion",
            "description": "Score selected evidence interpretation.",
            "original_description": "Structured implicit criterion",
            "max_score": 4,
            "section": "analysis",
            "scoring_points": [_structured_point("structured_implicit_criterion", 4)],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
            "rationale": "Teacher examples imply structured deductions.",
            "confidence": 0.9,
        }


def test_implicit_induction_preserves_model_structured_points_on_confirm(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", StructuredInductionModel())

    assert proposal["suggested_criterion"]["scoring_points"][0]["point_id"] == "structured_implicit_criterion.p1"
    suggested = CriteriaItem(**proposal["suggested_criterion"])
    confirmed = implicit_grading.confirm_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=proposal["proposal_version"],
            suggested_criterion=suggested,
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )

    criteria = json.loads((criteria_dir / "criteria.json").read_text(encoding="utf-8"))
    saved = next(item for item in criteria["criteria"] if item["criteria_id"] == suggested.criteria_id)
    assert saved["scoring_points"][0]["deduction_rules"][0]["rule_id"].endswith(".r1")
    assert confirmed["confirmed_event_ids"]
