from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import alignment, implicit_grading, main, services
from backend.app.grading import auto_grade as auto_grade_domain
from backend.app.grading.proposal_sources import proposal_source_event_digest
from backend.app.schemas import CaseReportGradeDeleteRequest, CaseReportGradeRequest, CaseReportModelGradeAcceptRequest, ConvertRequest, CriteriaItem, CriteriaSchema, CriterionProposalConfirmRequest, CriterionProposalRejectRequest


class FakeModel:
    def __init__(self, reason_ok: bool = True):
        self.reason_ok = reason_ok

    def grade(self, report_text, criterion, boundary, examples):
        reference = (examples[-1].get("authored_grade") if examples else None) or {
            "criteria_score": 8,
            "score_reason": "same",
            "anchor_text": "validated pendulum evidence",
        }
        anchor = str(reference["anchor_text"])
        if alignment._normalized(anchor) not in alignment._normalized(report_text):
            anchor = next(
                (line.strip() for line in report_text.splitlines() if line.strip()),
                "",
            )
        point = criterion["scoring_points"][0]
        point_id = str(point["point_id"])
        max_score = float(point.get("max_deduction") or criterion.get("max_score") or 0)
        score = float(reference["criteria_score"])
        deducted = score < max_score - 1e-6
        rule_id = str((point.get("deduction_rules") or [{}])[0].get("rule_id") or "")
        return {
            "criteria_score": reference["criteria_score"],
            "score_reason": reference["score_reason"] if self.reason_ok else "different conclusion",
            "anchor_text": anchor,
            "target_section": criterion.get("section"),
            "matched_scoring_points": [point_id] if deducted else [],
            "matched_deduction_rules": [rule_id] if deducted and rule_id else [],
            "scoring_point_scores": [
                {
                    "point_id": point_id,
                    "score": reference["criteria_score"],
                    "reason": (
                        f"The report evidence is incomplete for {point_id}, triggering {rule_id}."
                        if deducted
                        else f"The report evidence satisfies {point_id}."
                    ),
                }
            ],
        }


class CriterionAwareFakeModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        result = super().grade(report_text, criterion, boundary, examples)
        result["criteria_score"] = 8 if str(criterion.get("criteria_id")) == "1" else 4
        point_score = result["scoring_point_scores"][0]
        point_score["score"] = result["criteria_score"]
        max_score = float(criterion["scoring_points"][0].get("max_deduction") or 0)
        if point_score["score"] < max_score - 1e-6:
            point_id = point_score["point_id"]
            rule_id = criterion["scoring_points"][0]["deduction_rules"][0]["rule_id"]
            result["matched_scoring_points"] = [point_id]
            result["matched_deduction_rules"] = [rule_id]
            point_score["reason"] = f"The report is incomplete for {point_id}, triggering {rule_id}."
        else:
            result["matched_scoring_points"] = []
            result["matched_deduction_rules"] = []
            point_score["reason"] = f"The report satisfies {point_score['point_id']}."
        return result


class RecoverableAnchorModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        result = super().grade(report_text, criterion, boundary, examples)
        result["evidence_anchors"] = [
            {
                "anchor_type": "text",
                "text": result["anchor_text"],
                "occurrence": 99,
            }
        ]
        return result


class UngroundedEvidenceModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        result = super().grade(report_text, criterion, boundary, examples)
        result["anchor_text"] = "invented evidence not present in the report"
        result["evidence_anchors"] = [
            {
                "anchor_type": "text",
                "text": "another invented evidence region",
                "occurrence": 0,
            }
        ]
        return result


class UngroundedImageWithValidLegacyTextModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        result = super().grade(report_text, criterion, boundary, examples)
        result["evidence_anchors"] = [
            {
                "anchor_type": "image",
                "image_src": "images/missing.png",
                "description": "an image that is not in the report",
            }
        ]
        return result


class MissingContentModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        missing_rule_id = str(
            criterion["scoring_points"][0]["deduction_rules"][-1]["rule_id"]
        )
        return {
            "criteria_score": 0,
            "score_reason": "required content is absent",
            "anchor_text": "",
            "target_section": criterion.get("section"),
            "matched_scoring_points": [str(criterion["scoring_points"][0]["point_id"])],
            "matched_deduction_rules": [missing_rule_id],
            "scoring_point_scores": [
                {
                    "point_id": str(criterion["scoring_points"][0]["point_id"]),
                    "score": 0,
                    "reason": (
                        "The required criterion content is absent, triggering "
                        f"{missing_rule_id}."
                    ),
                }
            ],
            "evidence_anchors": [
                {
                    "anchor_type": "absence",
                    "expected_location": "document:end",
                    "missing_item": "required criterion content",
                }
            ],
        }


class RepairingInvalidModel(FakeModel):
    def __init__(self, repair_succeeds: bool = True):
        super().__init__()
        self.repair_succeeds = repair_succeeds
        self.repair_calls = []

    def grade(self, report_text, criterion, boundary, examples):
        result = super().grade(report_text, criterion, boundary, examples)
        result["criteria_score"] = 0
        result["score_reason"] = "Initial inconsistent total of 0/10."
        return result

    def repair_grade(
        self,
        report_text,
        criterion,
        boundary,
        invalid_grade,
        validation_error,
    ):
        self.repair_calls.append({
            "invalid_grade": deepcopy(invalid_grade),
            "validation_error": validation_error,
        })
        repaired = deepcopy(invalid_grade)
        repaired["score_reason"] = (
            "The point retains 8 points, for a corrected total of 8/10."
        )
        repaired["criteria_score"] = 8 if self.repair_succeeds else 0
        return repaired


class FakeEvaluator:
    def compare_teacher_cases(self, criterion, prior_text, prior_grade, new_text, new_grade):
        comparable = alignment._normalized(prior_grade.get("anchor_text")) == alignment._normalized(new_grade.get("anchor_text"))
        return {"comparable": comparable, "reason_evidence_consistent": prior_grade.get("score_reason") == new_grade.get("score_reason"), "reason": "fake audit"}

    def reason_and_evidence_agree(self, criterion, expected, candidate):
        agree = (
            alignment._normalized(expected.get("score_reason")) == alignment._normalized(candidate.get("score_reason"))
            and alignment._normalized(expected.get("anchor_text")) == alignment._normalized(candidate.get("anchor_text"))
        )
        return {"agree": agree, "reason": "fake semantic check"}

    def admit(self, report_text, criterion, boundary):
        decision = "inside" if "validated" in report_text else "outside"
        return {"decision": decision, "matched_rule_ids": boundary.get("covered_rule_ids", []) if decision == "inside" else [], "reason": "fake boundary"}


def test_revision_regrade_bypasses_selective_readiness_admission(monkeypatch):
    criterion = {
        "criteria_id": "1",
        "criteria_name": "c1",
        "description": "updated complete Card",
        "max_score": 10,
        "section": "analysis",
        "scoring_points": [
            {
                "point_id": "1.p1",
                "summary": "updated point",
                "deduction_rules": [
                    {
                        "rule_id": "1.p1.r1",
                        "condition": "partially incomplete",
                        "deduct": 2,
                    },
                    {
                        "rule_id": "1.p1.r2",
                        "condition": "missing",
                        "deduct": 10,
                    },
                ],
                "max_deduction": 10,
                "aggregation": "max_triggered",
            }
        ],
    }
    persisted_candidates = []
    monkeypatch.setattr(
        alignment,
        "_persist_rubric_only_candidate",
        lambda _case_id, snapshot, _rubric_version: persisted_candidates.append(snapshot)
        or {"candidate_hash": "candidate-hash"},
    )
    monkeypatch.setattr(
        alignment,
        "_load_candidate_snapshot",
        lambda _case_id, _criterion_id, _state: criterion,
    )
    item = {
        "criterion": criterion,
        "state": {
            "lifecycle": "collecting",
            "boundary": {},
        },
        "revision_regrade": True,
        "repair_blocked": False,
        "full_batch_blocked": False,
    }
    working_store = {"readiness": {"criteria": {}}}

    auto_grade_domain._attach_confirmed_revision_candidate(
        "case-a", item, "new-rubric-version", working_store
    )

    outcome = auto_grade_domain._compute_grade_item(
        item,
        case_id="case-a",
        report_text="validated pendulum evidence",
        rubric_version="new-rubric-version",
        model=FakeModel(),
        evaluator=FakeEvaluator(),
    )

    assert outcome["status"] == "done"
    assert outcome["criterion_id"] == "1"
    assert persisted_candidates == [criterion]
    assert working_store["readiness"]["criteria"]["1"]["candidate"] == {
        "candidate_hash": "candidate-hash"
    }


class SectionAwareEvaluator(FakeEvaluator):
    def admit(self, report_text, criterion, boundary):
        if boundary.get("allowed_sections") and "SECTION: analysis" not in report_text:
            return {"decision": "outside", "matched_rule_ids": [], "reason": "section boundary excluded header evidence"}
        return super().admit(report_text, criterion, boundary)


class AlwaysOutsideEvaluator(FakeEvaluator):
    def admit(self, report_text, criterion, boundary):
        return {
            "decision": "outside",
            "matched_rule_ids": [],
            "reason": "positive evidence was not found",
        }


class UnavailableEvaluator(FakeEvaluator):
    def compare_teacher_cases(self, *args, **kwargs):
        raise RuntimeError("judge offline")


class FakeConsistencyAuditEvaluator:
    def __init__(self, suspect_scores: set[float] | None = None):
        self.suspect_scores = suspect_scores or set()

    def interpret_grade(self, report_text, current_criterion, event):
        authored = event.get("authored_grade") or {}
        has_anchor = bool(alignment._normalized(authored.get("anchor_text")))
        needs_review = float(authored.get("criteria_score") or 0) in self.suspect_scores
        return {
            "evidence_source": "teacher_selected" if has_anchor else "inferred",
            "inferred_evidence": "" if has_anchor else "inferred report evidence",
            "fit": "needs_review" if needs_review else "consistent",
            "confidence": 0.91 if needs_review else (0.86 if has_anchor else 0.6),
            "issue_type": "calculation_error",
            "severity": "major",
            "matched_rules": [str(current_criterion.get("criteria_id"))],
            "reason": "score cannot be explained by current rubric" if needs_review else "fits current rubric",
        }


class BoundaryAuditEvaluator(FakeConsistencyAuditEvaluator):
    def compare_teacher_cases(
        self,
        criterion,
        prior_text,
        prior_grade,
        new_text,
        new_grade,
    ):
        comparable = alignment._normalized(prior_grade.get("anchor_text")) == alignment._normalized(
            new_grade.get("anchor_text")
        )
        return {
            "comparable": comparable,
            "same_grading_pattern": comparable,
            "confidence": 0.93,
            "reason": "The teacher-selected evidence represents the same grading pattern.",
        }


class FakeConflictReassessmentEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self, diagnosis: str):
        super().__init__()
        self.diagnosis = diagnosis

    def reassess_conflict(self, report_text, current_criterion, event, audit_artifact, teacher_feedback):
        return {
            "diagnosis": self.diagnosis,
            "reason": f"independent second pass: {self.diagnosis}",
            "confidence": 0.9,
            "suggested_criterion": None,
        }


class StructuredAllocationAuditEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self, invalid: bool = False):
        super().__init__()
        self.invalid = invalid

    def interpret_grade(self, report_text, current_criterion, event):
        result = super().interpret_grade(report_text, current_criterion, event)
        score = float((event.get("authored_grade") or {}).get("criteria_score") or 0)
        result.update({
            "scoring_point_scores": [
                {
                    "point_id": "unknown" if self.invalid else "1.p1",
                    "score": score,
                    "reason": "Teacher evidence triggers 1.p1.r1.",
                },
            ],
            "matched_scoring_points": ["1.p1"],
            "matched_deduction_rules": ["1.p1.r1"],
        })
        return result


class GeneratedReasonAuditEvaluator(FakeConsistencyAuditEvaluator):
    def interpret_grade(self, report_text, current_criterion, event):
        result = super().interpret_grade(report_text, current_criterion, event)
        result["suggested_score_reason"] = "选中的证据满足当前评分项，因此保留该教师给分。"
        return result


class FakeFullAlignmentEvaluator(FakeEvaluator, FakeConsistencyAuditEvaluator):
    pass


class RefinementProposalEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self):
        super().__init__({7.0})
        self.interpret_calls = 0
        self.context_events = None

    def propose_criterion_update(self, report_text, current_criterion, event, context_events=None):
        self.context_events = context_events
        suggested = dict(current_criterion)
        suggested["description"] = "rule one refined with teacher-selected edge case"
        suggested["original_description"] = "rule one refined"
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": "The selected evidence exposes a missing scoring boundary.",
            "confidence": 0.86,
        }

    def interpret_grade(self, report_text, current_criterion, event):
        self.interpret_calls += 1
        return super().interpret_grade(report_text, current_criterion, event)


class AndToOrRefinementEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self):
        super().__init__()
        self.comparison = None
        self.interpret_calls = 0

    def propose_criterion_update(self, report_text, current_criterion, event, context_events=None):
        self.comparison = event.get("model_comparison")
        suggested = json.loads(json.dumps(current_criterion))
        second_point = suggested["scoring_points"][1]
        second_point["summary"] = second_point["summary"].replace("AND", "OR")
        second_point["deduction_rules"][0]["condition"] = second_point["deduction_rules"][0]["condition"].replace("AND", "OR")
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": "The teacher awarded the second point when either alternative was present, so the conjunction must be a disjunction.",
            "confidence": 0.92,
        }

    def interpret_grade(self, report_text, current_criterion, event):
        self.interpret_calls += 1
        return super().interpret_grade(report_text, current_criterion, event)


class CaseBasedPartialRefinementEvaluator(RefinementProposalEvaluator):
    def propose_criterion_update(self, report_text, current_criterion, event, context_events=None):
        self.context_events = context_events
        suggested = dict(current_criterion)
        suggested["description"] = (
            "要求：实验报告标题或姓名需完整。\n"
            "扣分规则：标题或姓名完全缺失，得0分；标题和姓名完整且实验名称明确，得4分；"
            "标题或实验名称表达不规范但仍可识别身份与报告主题，给1分。"
        )
        suggested["original_description"] = "实验报告标题或姓名：缺失扣分。"
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": "多个4分案例都有清晰标题和姓名；1分案例有身份信息但标题/实验名称表达不够规范，因此需要补充部分分档位。",
            "confidence": 0.84,
        }


class RefinementThenCleanEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self):
        super().__init__()
        self.proposal_calls = 0
        self.interpret_calls = 0

    def propose_criterion_update(self, report_text, current_criterion, event, context_events=None):
        self.proposal_calls += 1
        if "teacher-selected edge case" in str(current_criterion.get("description") or ""):
            return {
                "decision": "no_update",
                "suggested_criterion": None,
                "rationale": "The confirmed rubric already covers this teacher grade.",
                "confidence": 0.91,
            }
        suggested = dict(current_criterion)
        suggested["description"] = "rule one refined with teacher-selected edge case"
        suggested["original_description"] = "rule one refined"
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": "The selected evidence exposes a missing scoring boundary.",
            "confidence": 0.88,
        }

    def interpret_grade(self, report_text, current_criterion, event):
        self.interpret_calls += 1
        return {
            "evidence_source": "teacher_selected",
            "inferred_evidence": "",
            "fit": "consistent",
            "confidence": 0.9,
            "issue_type": "none",
            "severity": "info",
            "matched_rules": [str(current_criterion.get("criteria_id"))],
            "reason": "confirmed rubric explains the teacher score",
        }


class NoUpdateNeedsReviewEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self):
        super().__init__({7.0})

    def propose_criterion_update(self, report_text, current_criterion, event, context_events=None):
        return {
            "decision": "no_update",
            "suggested_criterion": None,
            "rationale": "The rubric is already specific enough.",
            "confidence": 0.9,
        }


class FirstEventSuspectEvaluator(FakeConsistencyAuditEvaluator):
    def interpret_grade(self, report_text, current_criterion, event):
        result = super().interpret_grade(report_text, current_criterion, event)
        if not hasattr(self, "_first_event_id"):
            self._first_event_id = event["event_id"]
        if event["event_id"] == self._first_event_id:
            result["fit"] = "needs_review"
            result["confidence"] = 0.91
            result["reason"] = "first event cannot be explained by current rubric"
        return result


class BlockingConsistencyAuditEvaluator(FakeConsistencyAuditEvaluator):
    def __init__(self, entered: threading.Event, release: threading.Event):
        super().__init__()
        self.entered = entered
        self.release = release

    def interpret_grade(self, report_text, current_criterion, event):
        self.entered.set()
        assert self.release.wait(5)
        return super().interpret_grade(report_text, current_criterion, event)


class BlockingModel(FakeModel):
    def __init__(self, entered: threading.Event, release: threading.Event):
        super().__init__()
        self.entered = entered
        self.release = release

    def grade(self, report_text, criterion, boundary, examples):
        self.entered.set()
        assert self.release.wait(5)
        return super().grade(report_text, criterion, boundary, examples)


class ConcurrentCriterionModel(CriterionAwareFakeModel):
    def __init__(self):
        super().__init__()
        self.barrier = threading.Barrier(2)

    def grade(self, report_text, criterion, boundary, examples):
        self.barrier.wait(timeout=2)
        return super().grade(report_text, criterion, boundary, examples)


class ProgressiveCriterionModel(CriterionAwareFakeModel):
    def __init__(self, slow_started: threading.Event, release_slow: threading.Event):
        super().__init__()
        self.slow_started = slow_started
        self.release_slow = release_slow

    def grade(self, report_text, criterion, boundary, examples):
        if str(criterion.get("criteria_id")) == "2":
            self.slow_started.set()
            assert self.release_slow.wait(5)
        else:
            assert self.slow_started.wait(2)
        return super().grade(report_text, criterion, boundary, examples)


class CandidateObservingModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.seen_confirmed_examples = []

    def grade(self, report_text, criterion, boundary, examples):
        self.seen_confirmed_examples = [
            item
            for item in criterion.get("few_shot_examples") or []
            if item.get("source_grader_type") == "model"
            and item.get("source_decision_status") == "accepted_model"
        ]
        return super().grade(report_text, criterion, boundary, examples)


class ConfirmedPositiveFollowingModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        confirmed = next(
            event
            for event in examples
            if event.get("source_grader_type") == "model"
            and event.get("source_decision_status") == "accepted_model"
        )
        authored = confirmed["authored_grade"]
        return {
            "criteria_score": authored["criteria_score"],
            "score_reason": authored["score_reason"],
            "anchor_text": authored["anchor_text"],
            "target_section": criterion.get("section"),
            "scoring_point_scores": authored["scoring_point_scores"],
        }


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case_root = tmp_path / "case"
    criteria_dir = tmp_path / "criteria"
    case_id = "case-a"
    report = "report-a"
    report_dir = case_root / case_id / "markdown" / report
    report_dir.mkdir(parents=True)
    (case_root / case_id / f"{report}.pdf").write_bytes(b"pdf")
    evidence_catalog = (
        "validated pendulum evidence\nevidence A\nevidence B\nteacher selected span A\n"
        "teacher selected edge\nsame anchor\nsame anchor C\nteacher selected\n"
        "浙江大学 实验报告 姓名：张三\n"
    )
    (report_dir / f"{report}.md").write_text(f"# report\n{evidence_catalog}", encoding="utf-8")
    (report_dir / f"{report}_text_only.md").write_text(evidence_catalog, encoding="utf-8")
    criteria_dir.mkdir(parents=True)
    criteria = {
        "schema_version": "structured-rubric-v1",
        "title": "Rubric", "background": "", "language": "zh", "base_score": 100,
        "criteria": [
            {
                "criteria_id": "1", "criteria_name": "c1", "description": "rule one", "original_description": "rule one", "max_score": 10, "section": "analysis", "few_shot_examples": [],
                "scoring_points": [{"point_id": "1.p1", "summary": "rule one", "deduction_rules": [{"rule_id": "1.p1.r1", "condition": "rule one is partially met", "deduct": 2}, {"rule_id": "1.p1.r2", "condition": "rule one is not met", "deduct": 10}], "max_deduction": 10, "aggregation": "max_triggered"}],
                "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
            },
            {
                "criteria_id": "2", "criteria_name": "c2", "description": "rule two", "original_description": "rule two", "max_score": 5, "section": "discussion", "few_shot_examples": [],
                "scoring_points": [{"point_id": "2.p1", "summary": "rule two", "deduction_rules": [{"rule_id": "2.p1.r1", "condition": "rule two is partially met", "deduct": 1}, {"rule_id": "2.p1.r2", "condition": "rule two is not met", "deduct": 5}], "max_deduction": 5, "aggregation": "max_triggered"}],
                "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 5},
            },
        ],
    }
    canonical_criteria = CriteriaSchema.model_validate(criteria).model_dump(mode="json")
    (criteria_dir / "criteria.json").write_text(json.dumps(canonical_criteria), encoding="utf-8")
    monkeypatch.setattr(alignment, "CASE_ROOT", case_root)
    monkeypatch.setattr(alignment, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(services, "CASE_ROOT", case_root)
    monkeypatch.setattr(services, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(main, "CASE_ROOT", case_root)
    monkeypatch.setattr(main, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(implicit_grading, "CASE_ROOT", case_root)
    monkeypatch.setattr(implicit_grading, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(alignment, "LiveGradingModel", lambda: FakeModel())
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: FakeEvaluator())
    return case_id, report, criteria_dir


def _payload(score: float, reason: str = "same", anchor: str = "validated pendulum evidence"):
    return CaseReportGradeRequest(
        criteria_id="1",
        criteria_score=score,
        score_reason=reason,
        anchor_text=anchor,
        scoring_point_scores=[{"point_id": "1.p1", "score": score}],
        criteria_file_name="criteria.json",
    )


def _model_payload(
    score_reason: str = "same",
    anchor: str = "validated pendulum evidence",
):
    return {
        "criteria_score": 8,
        "score_reason": score_reason,
        "anchor_text": anchor,
        "matched_scoring_points": ["1.p1"],
        "matched_deduction_rules": ["1.p1.r1"],
        "scoring_point_scores": [{
            "point_id": "1.p1",
            "score": 8,
            "reason": "The report is partially complete for 1.p1, triggering 1.p1.r1.",
        }],
    }


def test_teacher_supporting_context_is_bounded_and_anchor_centered():
    report = "HEADER_ONLY\n" + ("x" * 1000) + "\nselected evidence\n" + ("y" * 1000) + "\nUNRELATED_SCORING_FACT"
    event = {"authored_grade": {"anchor_text": "selected evidence"}}
    context = alignment._teacher_supporting_context(report, event)
    assert "selected evidence" in context
    assert len(context) <= alignment.MAX_TEACHER_SUPPORTING_CONTEXT_CHARS
    assert "HEADER_ONLY" not in context
    assert "UNRELATED_SCORING_FACT" not in context


def test_teacher_supporting_context_locates_browser_rendered_markdown():
    report = (
        "HEADER_ONLY\n"
        + ("x" * 1000)
        + "\n| 浓度 | 吸光度 |\n|---|---|\n| 0.000 | 0.365 |\n"
        + ("y" * 1000)
        + "\nTRAILER_ONLY"
    )
    event = {
        "authored_grade": {
            "anchor_text": "0.000 0.365",
            "evidence_anchors": [
                {
                    "anchor_type": "text",
                    "text": "0.000 0.365",
                    "occurrence": 0,
                }
            ],
        }
    }

    context = alignment._teacher_supporting_context(report, event)

    assert "| 0.000 | 0.365 |" in context
    assert len(context) <= alignment.MAX_TEACHER_SUPPORTING_CONTEXT_CHARS
    assert "HEADER_ONLY" not in context
    assert "TRAILER_ONLY" not in context


def test_teacher_grade_save_accepts_browser_rendered_markdown_evidence(
    tmp_path, monkeypatch
):
    case_id, report_name, _ = _case(tmp_path, monkeypatch)
    report_path = (
        tmp_path
        / "case"
        / case_id
        / "markdown"
        / report_name
        / f"{report_name}_text_only.md"
    )
    report_path.write_text(
        "| 浓度 (mg/L) | 吸光度 |\n|---:|---:|\n| 0.000 | 0.365 |\n",
        encoding="utf-8",
    )
    payload = _payload(8, "table evidence").model_copy(
        update={
            "anchor_text": "",
            "evidence_anchors": [
                {
                    "anchor_type": "text",
                    "text": "0.000 0.365",
                    "occurrence": 0,
                }
            ],
        }
    )

    result = services.save_case_report_grade(case_id, report_name, payload)

    assert result["criteria"][0]["anchor_text"] == "0.000 0.365"
    events = json.loads(
        (tmp_path / "case" / case_id / "alignment" / "events.json").read_text()
    )
    assert events[-1]["authored_grade"]["evidence_anchors"] == [
        {"anchor_type": "text", "text": "0.000 0.365", "occurrence": 0}
    ]


def _add_report(tmp_path: Path, case_id: str, report: str, text: str = "validated pendulum evidence"):
    report_dir = tmp_path / "case" / case_id / "markdown" / report
    report_dir.mkdir(parents=True)
    evidence_catalog = (
        "\nevidence A\nevidence B\nteacher selected span A\nteacher selected edge\n"
        "same anchor\nsame anchor C\nteacher selected\n浙江大学 实验报告 姓名：张三\n"
    )
    report_text = f"{text}{evidence_catalog}"
    (report_dir / f"{report}.md").write_text(report_text, encoding="utf-8")
    (report_dir / f"{report}_text_only.md").write_text(report_text, encoding="utf-8")


def test_persisted_batch_covers_every_report_and_criterion_after_one_time_calibration(
    tmp_path, monkeypatch
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")

    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")

    assert batch["status"] == "awaiting_calibration"
    assert batch["model_id"] == alignment.GRADING_MODEL_ID == "qwen3.8-max"
    assert batch["progress"] == {
        "total": 4,
        "completed": 0,
        "percent": 0.0,
        "pending": 4,
        "running": 0,
        "done": 0,
        "abstained": 0,
        "failed": 0,
        "stale": 0,
    }
    with pytest.raises(ValueError, match="calibration"):
        alignment.run_auto_grade_batch(case_id, batch["batch_id"])

    calls = []

    def fake_auto_grade(case_id_arg, report_name, criteria_file_name, **kwargs):
        calls.append(
            (
                case_id_arg,
                report_name,
                criteria_file_name,
                kwargs.get("criteria_ids"),
                kwargs.get("scheduling_reason"),
            )
        )
        return {
            "grades": [
                {"criteria_id": criterion_id}
                for criterion_id in kwargs.get("criteria_ids") or []
            ],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", fake_auto_grade)
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"], "teacher-a")
    completed = alignment.run_auto_grade_batch(case_id, batch["batch_id"])

    assert completed["status"] == "completed"
    assert completed["progress"]["done"] == 4
    assert completed["progress"]["completed"] == 4
    assert calls == [
        (case_id, "report-a", "criteria.json", ["1", "2"], "batch"),
        (case_id, "report-b", "criteria.json", ["1", "2"], "batch"),
    ]
    persisted = alignment.get_auto_grade_batch(case_id, batch["batch_id"])
    assert persisted["progress"]["percent"] == 100.0

    second = alignment.create_auto_grade_batch(case_id, "criteria.json")
    assert second["status"] == "queued"
    assert second["calibration"]["required"] is False
    assert second["calibration"]["status"] == "confirmed"


def test_batch_excludes_effective_teacher_grades_from_auto_grade_targets(
    tmp_path, monkeypatch
):
    case_id, report_name, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    services.save_case_report_grade(case_id, report_name, _payload(8))

    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")

    assert batch["progress"]["total"] == 3
    assert {
        (target["report_name"], target["criterion_id"])
        for target in batch["targets"]
    } == {
        ("report-a", "2"),
        ("report-b", "1"),
        ("report-b", "2"),
    }


def test_batch_preserves_teacher_grade_added_after_batch_creation(
    tmp_path, monkeypatch
):
    case_id, report_name, _ = _case(tmp_path, monkeypatch)
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    services.save_case_report_grade(case_id, report_name, _payload(8))
    calls = []

    def fake_auto_grade(_case_id, _report_name, _criteria_file_name, **kwargs):
        calls.append(kwargs.get("criteria_ids"))
        return {
            "grades": [
                {"criteria_id": criterion_id}
                for criterion_id in kwargs.get("criteria_ids") or []
            ],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", fake_auto_grade)
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    completed = alignment.run_auto_grade_batch(case_id, batch["batch_id"])

    assert calls == [["2"]]
    assert completed["progress"]["done"] == 2
    preserved = next(
        target for target in completed["targets"] if target["criterion_id"] == "1"
    )
    assert preserved["reason"] == "teacher_grade_preserved"


def test_internal_batch_fallback_uses_natural_numeric_order(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-10")
    _add_report(tmp_path, case_id, "report-2")
    _add_report(tmp_path, case_id, "report-1")

    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")

    assert batch["report_names"] == ["report-1", "report-2", "report-10", "report-a"]


def test_batch_uses_teacher_visible_order_starting_at_active_report(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    visible_order = ["穆高山", "王金杰", "胡思源", "倪彬锐", "report-a"]
    for report_name in visible_order[:-1]:
        _add_report(tmp_path, case_id, report_name)

    batch = alignment.create_auto_grade_batch(
        case_id,
        "criteria.json",
        report_names=visible_order,
        start_report_name="穆高山",
    )

    assert batch["start_report_name"] == "穆高山"
    assert batch["report_names"] == visible_order
    assert [target["report_name"] for target in batch["targets"][:2]] == ["穆高山", "穆高山"]

    worker_calls = []

    def fake_auto_grade(_case_id, report_name, _criteria_file_name, **kwargs):
        worker_calls.append(report_name)
        return {
            "grades": [
                {"criteria_id": criterion_id}
                for criterion_id in kwargs.get("criteria_ids") or []
            ],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", fake_auto_grade)
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    alignment.run_auto_grade_batch(case_id, batch["batch_id"])
    assert worker_calls == visible_order


def test_batch_rotates_visible_order_from_current_report(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    visible_order = ["report-a", "report-b", "report-c", "report-d"]
    for report_name in visible_order[1:]:
        _add_report(tmp_path, case_id, report_name)

    batch = alignment.create_auto_grade_batch(
        case_id,
        "criteria.json",
        report_names=visible_order,
        start_report_name="report-c",
    )

    assert batch["report_names"] == ["report-c", "report-d", "report-a", "report-b"]


@pytest.mark.parametrize(
    ("report_names", "start_report_name", "message"),
    [
        (["report-a", "report-b"], "report-a", "exactly match"),
        (["report-a", "report-a", "report-b", "report-c"], "report-a", "duplicates"),
        (["report-a", "report-b", "report-c", "outside"], "report-a", "exactly match"),
        (["report-a", "report-b", "report-c"], "outside", "present in report_names"),
    ],
)
def test_batch_rejects_invalid_visible_report_order(
    tmp_path, monkeypatch, report_names, start_report_name, message
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    _add_report(tmp_path, case_id, "report-c")

    with pytest.raises(ValueError, match=message):
        alignment.create_auto_grade_batch(
            case_id,
            "criteria.json",
            report_names=report_names,
            start_report_name=start_report_name,
        )


def test_api_batch_create_requires_and_persists_visible_report_order(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    _add_report(tmp_path, case_id, "report-c")
    client = TestClient(main.app)

    missing_order = client.post(
        f"/cases/{case_id}/auto-grade-batches",
        json={"criteria_file_name": "criteria.json"},
    )
    assert missing_order.status_code == 422

    response = client.post(
        f"/cases/{case_id}/auto-grade-batches",
        json={
            "criteria_file_name": "criteria.json",
            "report_names": ["report-a", "report-b", "report-c"],
            "start_report_name": "report-b",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["batch"]["report_names"] == [
        "report-b",
        "report-c",
        "report-a",
    ]


def test_batch_resume_requeues_interrupted_targets(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    batch = alignment.create_auto_grade_batch(
        case_id,
        "criteria.json",
        report_names=["report-a", "report-b"],
        start_report_name="report-b",
    )
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    with alignment._lock_for(case_id):
        store = alignment._store(case_id)
        persisted = store["auto_grade_batches"][batch["batch_id"]]
        persisted["status"] = "running"
        persisted["targets"][0]["status"] = "running"
        alignment._write_store(case_id, store)

    seen = []

    def fake_auto_grade(_case_id, report_name, _criteria_file_name, **kwargs):
        seen.append((report_name, tuple(kwargs.get("criteria_ids") or [])))
        return {
            "grades": [
                {"criteria_id": criterion_id}
                for criterion_id in kwargs.get("criteria_ids") or []
            ],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", fake_auto_grade)
    resumed = alignment.run_auto_grade_batch(case_id, batch["batch_id"])

    assert resumed["status"] == "completed"
    assert seen == [("report-b", ("1", "2")), ("report-a", ("1", "2"))]


def test_cancel_queued_batch_marks_every_target_cancelled(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")

    cancelled = alignment.cancel_auto_grade_batch(case_id, batch["batch_id"], "teacher-a")

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_by"] == "teacher-a"
    assert cancelled["progress"]["cancelled"] == 4
    assert cancelled["progress"]["completed"] == 4
    assert {target["status"] for target in cancelled["targets"]} == {"cancelled"}
    assert alignment.cancel_auto_grade_batch(case_id, batch["batch_id"])["status"] == "cancelled"


def test_cancel_running_batch_finishes_current_report_but_never_starts_next(
    tmp_path, monkeypatch
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    entered = threading.Event()
    release = threading.Event()
    seen = []

    def blocking_auto_grade(_case_id, report_name, _criteria_file_name, **kwargs):
        seen.append(report_name)
        entered.set()
        assert release.wait(5)
        return {
            "grades": [
                {"criteria_id": criterion_id}
                for criterion_id in kwargs.get("criteria_ids") or []
            ],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", blocking_auto_grade)
    worker = threading.Thread(
        target=alignment.run_auto_grade_batch,
        args=(case_id, batch["batch_id"]),
    )
    worker.start()
    assert entered.wait(2)

    cancelling = alignment.cancel_auto_grade_batch(case_id, batch["batch_id"])
    assert cancelling["status"] == "cancelling"
    release.set()
    worker.join(timeout=5)

    cancelled = alignment.get_auto_grade_batch(case_id, batch["batch_id"])
    assert cancelled["status"] == "cancelled"
    assert seen == ["report-a"]
    by_report = {
        report_name: {
            target["status"]
            for target in cancelled["targets"]
            if target["report_name"] == report_name
        }
        for report_name in cancelled["report_names"]
    }
    assert by_report["report-a"] == {"done"}
    assert by_report["report-b"] == {"cancelled"}


def test_cancel_batch_api_is_persisted(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    client = TestClient(main.app)

    response = client.post(
        f"/cases/{case_id}/auto-grade-batches/{batch['batch_id']}/cancel",
        json={"cancelled_by": "teacher-a"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["batch"]["status"] == "cancelled"


def test_interrupted_cancelling_batch_is_finalized_instead_of_resumed(tmp_path, monkeypatch):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    with alignment._lock_for(case_id):
        store = alignment._store(case_id)
        persisted = store["auto_grade_batches"][batch["batch_id"]]
        persisted["status"] = "cancelling"
        persisted["targets"][0]["status"] = "running"
        alignment._write_store(case_id, store)

    finalized = alignment.get_auto_grade_batch(case_id, batch["batch_id"])

    assert finalized["status"] == "cancelled"
    assert finalized["progress"]["cancelled"] == finalized["progress"]["total"]
    assert {target["status"] for target in finalized["targets"]} == {"cancelled"}


def _audit_clean(case_id: str, criteria_id: str = "1"):
    return alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", criteria_id, FakeConsistencyAuditEvaluator())


def _append_and_accept_model(
    case_id: str,
    report_name: str,
    *,
    candidate_hash: str = "accepted-candidate",
):
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    event = alignment._append_model_event(
        store,
        case_id,
        report_name,
        "1",
        "criteria.json",
        rubric_version,
        criteria_data["criteria"][0],
        _model_payload(),
        candidate_hash,
    )
    alignment._write_store(case_id, store)
    alignment.accept_model_grade_event(
        case_id,
        report_name,
        CaseReportModelGradeAcceptRequest(
            criteria_id="1", criteria_file_name="criteria.json"
        ),
    )
    return event, rubric_version


def test_teacher_event_is_immutable_idempotent_and_server_attributed(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    result = services.save_case_report_grade(case_id, report, _payload(8, "good"))
    assert result["criteria"][0]["graded_by"] == "user"
    root = tmp_path / "case" / case_id / "alignment"
    events = json.loads((root / "events.json").read_text())
    assert events[0]["grader_type"] == "teacher"
    assert events[0]["grader_id"] == "default-teacher"
    assert events[0]["authored_grade"]["criteria_score"] == 8
    services.save_case_report_grade(case_id, report, _payload(8, "good"))
    assert json.loads((root / "events.json").read_text()) == events


def test_projection_is_scoped_by_criteria_file(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    other = json.loads((criteria_dir / "criteria.json").read_text())
    other["title"] = "Other Rubric"
    other["criteria"][0]["description"] = "other rubric rule"
    (criteria_dir / "other.json").write_text(json.dumps(other), encoding="utf-8")
    services.save_case_report_grade(case_id, report, _payload(8, "main rubric").model_copy(update={"criteria_file_name": "criteria.json"}))
    services.save_case_report_grade(case_id, report, _payload(5, "other rubric").model_copy(update={"criteria_file_name": "other.json"}))

    main = alignment.materialize_projection(case_id, report, criteria_file_name="criteria.json")
    scoped_other = alignment.materialize_projection(case_id, report, criteria_file_name="other.json")
    assert len(main["criteria"]) == 1
    assert len(scoped_other["criteria"]) == 1
    assert main["criteria"][0]["score_reason"] == "main rubric"
    assert scoped_other["criteria"][0]["score_reason"] == "other rubric"
    assert main["criteria"][0]["rubric_file_name"] == "criteria.json"
    assert scoped_other["criteria"][0]["rubric_file_name"] == "other.json"


def test_materialize_projection_is_read_only_unless_persisted(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8, "main rubric"))
    projection_path = tmp_path / "case" / case_id / "grade_result" / f"{report}_grade.json"
    assert projection_path.exists()

    projection_path.unlink()
    result = alignment.materialize_projection(case_id, report, criteria_file_name="criteria.json")
    assert result["criteria"][0]["score_reason"] == "main rubric"
    assert not projection_path.exists()

    alignment.materialize_projection(case_id, report, criteria_file_name="criteria.json", persist=True)
    assert projection_path.exists()


def test_deleted_teacher_grade_can_be_readded_with_same_payload(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    first = services.save_case_report_grade(case_id, report, _payload(8, "same reason", "validated pendulum evidence"))
    assert len(first["criteria"]) == 1
    deleted = services.delete_case_report_grade(
        case_id,
        report,
        CaseReportGradeDeleteRequest(criteria_id="1", criteria_file_name="criteria.json"),
    )
    assert deleted["criteria"] == []

    second = services.save_case_report_grade(case_id, report, _payload(8, "same reason", "validated pendulum evidence"))
    assert len(second["criteria"]) == 1
    events = json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())
    authored = [
        event for event in events
        if event.get("event_type") == "grade_authored" and event.get("criterion_id") == "1"
    ]
    assert len(authored) == 2
    assert authored[0]["idempotency_key"] == authored[1]["idempotency_key"]


def test_comparable_teacher_scores_create_teacher_teacher_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "first teacher pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report2, _payload(4, "second teacher pattern"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )

    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]
    assert conflict["conflict_type"] == "teacher_teacher"
    assert len(conflict["teacher_event_ids"]) == 2
    assert conflict["current_pattern"]["criteria_score"] == 8
    assert conflict["alternative_pattern"]["criteria_score"] == 4
    store = alignment._store(case_id)
    events = {event["event_id"]: event for event in store["events"]}
    assert {events[event_id]["grader_type"] for event_id in conflict["teacher_event_ids"]} == {
        "teacher"
    }


def test_single_teacher_vertical_disagreement_never_creates_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "edge case"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator({7.0})
    )

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_teacher_model_score_change_routes_to_update_without_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    model_event, _ = _append_and_accept_model(case_id, report)
    services.save_case_report_grade(
        case_id, report, _payload(7, "teacher changes the model score")
    )
    teacher_event = next(
        event
        for event in reversed(alignment._store(case_id)["events"])
        if event.get("grader_type") == "teacher"
    )

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", RefinementProposalEvaluator()
    )

    proposals = json.loads(
        (tmp_path / "case" / case_id / "criterion_proposals.json").read_text()
    )
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    assert proposals[0]["status"] == "pending"
    assert proposals[0]["source_event_ids"] == [
        teacher_event["event_id"],
        model_event["event_id"],
    ]


def test_selecting_current_teacher_pattern_directly_corrects_only_alternative(
    tmp_path, monkeypatch
):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report2, _payload(4, "alternative pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]
    before_rubric = (criteria_dir / "criteria.json").read_text(encoding="utf-8")

    result = alignment.resolve_conflict(
        case_id,
        conflict["conflict_id"],
        conflict["current_pattern"]["pattern_id"],
        "The established 8-point pattern is correct.",
    )

    store = alignment._store(case_id)
    replacement = next(
        event
        for event in reversed(store["events"])
        if event.get("source_conflict_id") == conflict["conflict_id"]
    )
    assert result["conflict"]["resolution_type"] == "keep_current_pattern"
    assert replacement["grader_type"] == "teacher"
    assert replacement["authorship"] == "teacher_conflict_adjudication"
    assert replacement["authored_grade"]["criteria_score"] == 8
    assert alignment._event_status(
        conflict["alternative_event_id"], store["decisions"]
    ) == "superseded"
    assert alignment.materialize_projection(
        case_id, report2, criteria_file_name="criteria.json"
    )["criteria"][0]["criteria_score"] == 8
    assert (criteria_dir / "criteria.json").read_text(encoding="utf-8") == before_rubric
    assert not store.get("criterion_revisions")


def test_selecting_alternative_teacher_pattern_updates_rubric_and_schedules_full_regrade(
    tmp_path, monkeypatch
):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report2, _payload(4, "alternative pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]
    monkeypatch.setattr(
        implicit_grading, "ensure_criterion_revision_workers_for_jobs", lambda jobs: None
    )

    result = alignment.resolve_conflict(
        case_id,
        conflict["conflict_id"],
        conflict["alternative_pattern"]["pattern_id"],
        "The 4-point pattern is the intended standard.",
        RefinementProposalEvaluator(),
    )

    rubric = json.loads((criteria_dir / "criteria.json").read_text(encoding="utf-8"))
    store = alignment._store(case_id)
    revision = next(iter(store["criterion_revisions"].values()))
    assert result["conflict"]["resolution_type"] == "adopt_alternative_pattern"
    assert result["conflict"]["selected_pattern_id"] == conflict[
        "alternative_pattern"
    ]["pattern_id"]
    assert result["conflict"]["source_rubric_version"] == conflict[
        "audited_rubric_version"
    ]
    assert result["conflict"]["target_rubric_version"] == revision[
        "target_rubric_version"
    ]
    assert result["conflict"]["revision_id"] == revision["revision_id"]
    assert result["conflict"]["selected_event_ids"] == conflict[
        "alternative_pattern"
    ]["event_ids"]
    assert result["proposal"]["status"] == "confirmed"
    assert rubric["criteria"][0]["description"] == (
        "rule one refined with teacher-selected edge case"
    )
    assert revision["criterion_id"] == "1"
    assert revision["report_names"] == [report, report2]
    assert revision["status"] == "awaiting_audit"
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_failed_alternative_pattern_update_keeps_conflict_open_and_rubric_unchanged(
    tmp_path, monkeypatch
):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report2, _payload(4, "alternative pattern"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]
    before = (criteria_dir / "criteria.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="valid rubric update"):
        alignment.resolve_conflict(
            case_id,
            conflict["conflict_id"],
            conflict["alternative_pattern"]["pattern_id"],
            evaluator=NoUpdateNeedsReviewEvaluator(),
        )

    assert (criteria_dir / "criteria.json").read_text(encoding="utf-8") == before
    assert alignment.alignment_summary(case_id)["open_conflicts"][0][
        "conflict_id"
    ] == conflict["conflict_id"]
    assert not alignment._store(case_id).get("criterion_revisions")


def test_noncomparable_teacher_cases_do_not_create_false_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8, "case A", "evidence A"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2, "validated but different behavior")
    services.save_case_report_grade(case_id, report2, _payload(3, "case B", "evidence B"))
    summary = alignment.alignment_summary(case_id)
    assert summary["open_conflicts"] == []


def test_synchronous_evaluator_failure_does_not_block_teacher_grade(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    alignment.append_teacher_grade_event(case_id, report, _payload(8, "case A", "evidence A"), FakeEvaluator(), FakeModel())
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    event = alignment.append_teacher_grade_event(case_id, report2, _payload(8, "case B", "evidence B"), UnavailableEvaluator(), FakeModel())["event"]
    store = alignment._store(case_id)
    assert alignment._event_status(event["event_id"], store["decisions"]) == "trusted"
    assert event["event_id"] in {item["event_id"] for item in alignment._trusted_teacher_events(store)}


def test_unknown_criterion_is_rejected_without_event_mutation(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unknown criterion"):
        alignment.append_teacher_grade_event(case_id, report, _payload(8).model_copy(update={"criteria_id": "missing"}), FakeEvaluator(), FakeModel())
    assert not (tmp_path / "case" / case_id / "alignment" / "events.json").exists()


def test_two_samples_activate_only_after_leave_one_out_score_and_reason_agreement(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2, "validated pendulum evidence")
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"
    assert len(state["validation"]["checks"]) == 2
    assert all(check["score_agrees"] and check["reason_evidence_agrees"] for check in state["validation"]["checks"])
    assert state["candidate"]["construction_event_ids"]
    assert set(state["candidate"]["construction_event_ids"]).isdisjoint(state["candidate"]["validation_event_ids"])
    candidate_files = list((tmp_path / "case" / case_id / "alignment" / "candidates" / "1").glob("*.manifest.json"))
    assert candidate_files


def test_zero_evidence_never_activates_and_rubric_change_invalidates_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 1)
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    empty = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert empty["lifecycle"] == "collecting"
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())
    assert alignment.alignment_summary(case_id)["readiness"]["criteria"]["1"]["lifecycle"] == "active_selective"
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "changed rule"
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    changed = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert changed["lifecycle"] == "collecting"


def test_zero_sample_policy_bootstraps_rubric_only_pregrade(tmp_path, monkeypatch):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, _, _ = _case(tmp_path, monkeypatch)

    state = alignment.evaluate_alignment(
        case_id,
        "1",
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
    )["readiness"]["criteria"]["1"]

    assert state["lifecycle"] == "active_selective"
    assert state["reason"] == "rubric_only_bootstrap_enabled"
    assert state["validation"]["strategy"] == "rubric_only_bootstrap"
    assert state["readiness_policy_version"] == alignment.READINESS_POLICY_VERSION
    assert state["candidate"]["construction_event_ids"] == []
    assert state["candidate"]["validation_event_ids"] == []
    assert state["boundary"]["allowed_sections"] == []

    auto = alignment.auto_grade_report(
        case_id,
        "report-a",
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
    )
    assert len(auto["grades"]) == 1
    assert auto["grades"][0]["criteria_id"] == "1"


def test_readiness_policy_version_invalidates_persisted_legacy_boundary(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, _, _ = _case(tmp_path, monkeypatch)
    alignment.evaluate_alignment(
        case_id,
        "1",
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
    )
    store = alignment._store(case_id)
    legacy = store["readiness"]["criteria"]["1"]
    legacy.pop("readiness_policy_version")
    legacy["boundary"]["allowed_sections"] = ["analysis"]
    alignment._write_store(case_id, store)

    refreshed = alignment.evaluate_alignment(
        case_id,
        "1",
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
    )["readiness"]["criteria"]["1"]

    assert refreshed["readiness_policy_version"] == alignment.READINESS_POLICY_VERSION
    assert refreshed["boundary"]["allowed_sections"] == []


def test_rubric_only_scope_allows_model_to_grade_missing_content(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        MissingContentModel(),
        AlwaysOutsideEvaluator(),
        ["1"],
    )

    assert not result["abstentions"]
    assert not result["failures"]
    assert result["grades"][0]["criteria_score"] == 0
    assert result["grades"][0]["evidence_anchors"][0]["anchor_type"] == "absence"


def test_auto_grade_repairs_recoverable_model_anchor_occurrence(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        RecoverableAnchorModel(),
        FakeEvaluator(),
        ["1"],
    )

    assert not result["failures"]
    assert len(result["grades"]) == 1
    model_event = next(
        event
        for event in alignment._store(case_id)["events"]
        if event["grader_type"] == "model"
    )
    assert model_event["authored_grade"]["evidence_anchors"] == [
        {
            "anchor_type": "text",
            "text": "validated pendulum evidence",
            "occurrence": 0,
        }
    ]


def test_ungrounded_model_evidence_abstains_instead_of_reporting_failed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        UngroundedEvidenceModel(),
        FakeEvaluator(),
        ["1"],
    )

    assert not result["grades"]
    assert not result["failures"]
    assert result["abstentions"] == [
        {
            "criterion_id": "1",
            "decision": "uncertain",
            "matched_rule_ids": [],
            "reason": "model_evidence_unavailable",
        }
    ]
    job = next(
        item
        for item in alignment.alignment_summary(case_id)["auto_grade_jobs"]
        if item["report_name"] == report and item["criteria_id"] == "1"
    )
    assert job["status"] == "abstained"


def test_invalid_structured_image_does_not_fall_back_to_unrelated_text(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        UngroundedImageWithValidLegacyTextModel(),
        FakeEvaluator(),
        ["1"],
    )

    assert not result["grades"]
    assert not result["failures"]
    assert result["abstentions"][0]["reason"] == "model_evidence_unavailable"


def test_model_grade_requires_every_scoring_point_allocation(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, _ = alignment._load_criteria("criteria.json")
    criterion = {
        "criteria_id": "multi",
        "criteria_name": "multi-point criterion",
        "description": "two independently scored points",
        "original_description": "two independently scored points",
        "max_score": 10,
        "section": "analysis",
        "few_shot_examples": [],
        "scoring_points": [
            {
                "point_id": "multi.p1",
                "summary": "first",
                "max_deduction": 4,
                "aggregation": "max_triggered",
                "deduction_rules": [
                    {
                        "rule_id": "multi.p1.r1",
                        "condition": "first point is absent",
                        "deduct": 4,
                    }
                ],
            },
            {
                "point_id": "multi.p2",
                "summary": "second",
                "max_deduction": 6,
                "aggregation": "max_triggered",
                "deduction_rules": [
                    {
                        "rule_id": "multi.p2.r1",
                        "condition": "second point is absent",
                        "deduct": 6,
                    }
                ],
            },
        ],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
    }
    incomplete = {
        "criteria_score": 8,
        "score_reason": "same",
        "anchor_text": "validated pendulum evidence",
        "scoring_point_scores": [{
            "point_id": "multi.p1",
            "score": 4,
            "reason": "The report satisfies multi.p1.",
        }],
    }

    with pytest.raises(ValueError, match="missing scores for scoring points: multi.p2"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "multi",
            "criteria.json",
            rubric_version,
            criterion,
            incomplete,
            "candidate-incomplete",
        )

    complete = {
        **incomplete,
        "criteria_score": 4,
        "matched_scoring_points": ["multi.p2"],
        "matched_deduction_rules": ["multi.p2.r1"],
        "scoring_point_scores": [
            {
                "point_id": "multi.p1",
                "score": 4,
                "reason": "The report satisfies multi.p1.",
            },
            {
                "point_id": "multi.p2",
                "score": 0,
                "reason": "The second requirement is absent, triggering multi.p2.r1.",
            },
        ],
    }
    event = alignment._append_model_event(
        store,
        case_id,
        report,
        "multi",
        "criteria.json",
        rubric_version,
        criterion,
        complete,
        "candidate-complete",
    )
    assert [item["point_id"] for item in event["authored_grade"]["scoring_point_scores"]] == [
        "multi.p1",
        "multi.p2",
    ]


def test_auto_grade_repairs_invalid_model_structure_once_and_records_diagnostic(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)
    model = RepairingInvalidModel()

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        model,
        FakeEvaluator(),
        ["1"],
    )

    assert not result["failures"]
    assert result["grades"][0]["criteria_score"] == 8
    assert len(model.repair_calls) == 1
    assert "must equal criterion score" in model.repair_calls[0]["validation_error"]
    diagnostics_path = (
        tmp_path
        / "case"
        / case_id
        / "alignment"
        / "model_grade_diagnostics.json"
    )
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert len(diagnostics) == 1
    assert diagnostics[0]["outcome"] == "repaired"
    assert diagnostics[0]["repair_attempted"] is True
    assert diagnostics[0]["initial_response"]["criteria_score"] == 0
    assert diagnostics[0]["repair_response"]["criteria_score"] == 8


def test_auto_grade_fails_after_one_invalid_repair_and_records_both_responses(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)
    model = RepairingInvalidModel(repair_succeeds=False)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        model,
        FakeEvaluator(),
        ["1"],
    )

    assert not result["grades"]
    assert len(result["failures"]) == 1
    assert "model grade repair failed" in result["failures"][0]["error"]
    assert len(model.repair_calls) == 1
    diagnostics = alignment._store(case_id)["model_grade_diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["outcome"] == "repair_validation_failed"
    assert diagnostics[0]["initial_response"]["criteria_score"] == 0
    assert diagnostics[0]["repair_response"]["criteria_score"] == 0
    assert "must equal criterion score" in diagnostics[0]["repair_validation_error"]


def test_model_grade_requires_each_point_reason_and_matching_total(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    criterion = criteria_data["criteria"][0]
    base_grade = {
        "criteria_score": 8,
        "score_reason": "The report partially satisfies the criterion for 8/10.",
        "anchor_text": "validated pendulum evidence",
        "matched_scoring_points": ["1.p1"],
        "matched_deduction_rules": ["1.p1.r1"],
    }

    with pytest.raises(ValueError, match="reason is required for scoring point: 1.p1"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "1",
            "criteria.json",
            rubric_version,
            criterion,
            {
                **base_grade,
                "scoring_point_scores": [{"point_id": "1.p1", "score": 8}],
            },
            "candidate-missing-reason",
        )

    with pytest.raises(ValueError, match="must equal criterion score"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "1",
            "criteria.json",
            rubric_version,
            criterion,
            {
                **base_grade,
                "scoring_point_scores": [{
                    "point_id": "1.p1",
                    "score": 7,
                    "reason": "The evidence is incomplete, triggering 1.p1.r1.",
                }],
            },
            "candidate-wrong-total",
        )


def test_model_grade_rejects_rule_missing_from_point_reason(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")

    with pytest.raises(ValueError, match="reason for 1.p1 must mention"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "1",
            "criteria.json",
            rubric_version,
            criteria_data["criteria"][0],
            {
                "criteria_score": 8,
                "score_reason": "The report partially satisfies the criterion for 8/10.",
                "anchor_text": "validated pendulum evidence",
                "matched_scoring_points": ["1.p1"],
                "matched_deduction_rules": ["1.p1.r1"],
                "scoring_point_scores": [{
                    "point_id": "1.p1",
                    "score": 8,
                    "reason": "The selected report evidence is incomplete.",
                }],
            },
            "candidate-reason-rule-mismatch",
        )

    with pytest.raises(ValueError, match="inconsistent with its matched deduction rules"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "1",
            "criteria.json",
            rubric_version,
            criteria_data["criteria"][0],
            {
                "criteria_score": 7,
                "score_reason": "The report partially satisfies the criterion for 7/10.",
                "anchor_text": "validated pendulum evidence",
                "matched_scoring_points": ["1.p1"],
                "matched_deduction_rules": ["1.p1.r1"],
                "scoring_point_scores": [{
                    "point_id": "1.p1",
                    "score": 7,
                    "reason": "The selected evidence is incomplete, triggering 1.p1.r1.",
                }],
            },
            "candidate-score-rule-mismatch",
        )


def test_model_grade_canonicalizes_known_rule_label_prefix(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    criterion = criteria_data["criteria"][0]

    event = alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        rubric_version,
        criterion,
        {
            "criteria_score": 8,
            "score_reason": "The report partially satisfies the criterion for 8/10.",
            "anchor_text": "validated pendulum evidence",
            "matched_scoring_points": ["1.p1"],
            "matched_deduction_rules": ["Rule 1.p1.r1"],
            "scoring_point_scores": [{
                "point_id": "1.p1",
                "score": 8,
                "reason": "The evidence is incomplete, triggering Rule 1.p1.r1.",
            }],
        },
        "candidate-prefixed-rule-id",
    )

    assert event["authored_grade"]["matched_deduction_rules"] == ["1.p1.r1"]


def test_model_grade_still_rejects_unknown_prefixed_rule_id(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")

    with pytest.raises(ValueError, match="unknown matched deduction rules"):
        alignment._append_model_event(
            store,
            case_id,
            report,
            "1",
            "criteria.json",
            rubric_version,
            criteria_data["criteria"][0],
            {
                "criteria_score": 8,
                "score_reason": "The report partially satisfies the criterion for 8/10.",
                "anchor_text": "validated pendulum evidence",
                "matched_scoring_points": ["1.p1"],
                "matched_deduction_rules": ["Rule 1.p1.unknown"],
                "scoring_point_scores": [{
                    "point_id": "1.p1",
                    "score": 8,
                    "reason": "The evidence is incomplete, triggering Rule 1.p1.unknown.",
                }],
            },
            "candidate-unknown-prefixed-rule-id",
        )


def test_model_point_reasons_survive_event_projection_and_api(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text(encoding="utf-8"))
    criterion = rubric["criteria"][0]
    criterion["max_score"] = 8
    criterion["scoring_points"] = [
        {
            "point_id": "1.p1",
            "summary": "Standard curve result",
            "full_credit_condition": "The standard curve result is supported by the report.",
            "deduction_rules": [{
                "rule_id": "1.p1.wrong",
                "condition": "The standard curve result is missing or wrong.",
                "deduct": 4,
            }],
            "max_deduction": 4,
            "aggregation": "max_triggered",
        },
        {
            "point_id": "1.p2",
            "summary": "Standard addition result",
            "full_credit_condition": "The standard addition result is reliable.",
            "deduction_rules": [{
                "rule_id": "1.p2.unreliable",
                "condition": "The standard addition result is missing, wrong, or unreliable.",
                "deduct": 4,
            }],
            "max_deduction": 4,
            "aggregation": "max_triggered",
        },
    ]
    criterion["scoring_aggregation"] = {
        "mode": "sum_deductions_capped",
        "cap": 8,
    }
    canonical = CriteriaSchema.model_validate(rubric).model_dump(mode="json")
    (criteria_dir / "criteria.json").write_text(
        json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
    )
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    current_criterion = criteria_data["criteria"][0]
    point_reasons = [
        "报告给出标准曲线法结果 1.9 mg/L，计算过程完整，满足 1.p1 的要求。",
        "报告给出标准加入法结果 2.8 mg/L，且 R² 为 0.2480，结果不可靠，触发 1.p2.unreliable。",
    ]
    summary = "标准曲线结果可靠，标准加入结果不可靠，综合得分为 4/8。"
    store = alignment._store(case_id)
    event = alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        rubric_version,
        current_criterion,
        {
            "criteria_score": 4,
            "score_reason": summary,
            "anchor_text": "validated pendulum evidence",
            "matched_scoring_points": ["1.p2"],
            "matched_deduction_rules": ["1.p2.unreliable"],
            "scoring_point_scores": [
                {"point_id": "1.p1", "score": 4, "reason": point_reasons[0]},
                {"point_id": "1.p2", "score": 0, "reason": point_reasons[1]},
            ],
        },
        "candidate-with-point-reasons",
        report_text="validated pendulum evidence",
    )
    alignment._write_store(case_id, store)
    alignment.materialize_projection(
        case_id, report, store, "criteria.json", persist=True
    )

    persisted = alignment._store(case_id)["events"][0]["authored_grade"]
    assert [item["reason"] for item in persisted["scoring_point_scores"]] == point_reasons
    audit_ref = alignment._conflict_event_refs(store, [event["event_id"]])[0]
    assert [item["reason"] for item in audit_ref["scoring_point_scores"]] == point_reasons
    audit_scores, audit_status = alignment._model_scoring_point_explanation(
        current_criterion,
        persisted["scoring_point_scores"],
        persisted["criteria_score"],
    )
    assert audit_status == "valid"
    assert [item["reason"] for item in audit_scores] == point_reasons

    response = TestClient(main.app).get(
        f"/cases/{case_id}/reports/{report}",
        params={"criteria_file_name": "criteria.json"},
    )
    assert response.status_code == 200, response.text
    api_item = response.json()["data"]["result"]["criteria"][0]
    assert [item["reason"] for item in api_item["scoring_point_scores"]] == point_reasons
    assert api_item["score_reason"] == summary
    assert summary not in point_reasons
    assert summary != "".join(point_reasons)


def test_report_auto_grade_runs_independent_criteria_with_bounded_concurrency(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    monkeypatch.setattr(alignment, "AUTO_GRADE_MAX_WORKERS", 2)
    case_id, report, _ = _case(tmp_path, monkeypatch)

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        ConcurrentCriterionModel(),
        FakeEvaluator(),
        ["1", "2"],
    )

    assert not result["failures"]
    assert [item["criteria_id"] for item in result["grades"]] == ["1", "2"]


def test_report_auto_grade_persists_each_criterion_as_it_completes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    monkeypatch.setattr(alignment, "AUTO_GRADE_MAX_WORKERS", 2)
    case_id, report, _ = _case(tmp_path, monkeypatch)
    slow_started = threading.Event()
    release_slow = threading.Event()
    first_persisted = threading.Event()
    completions = []
    result_holder = {}

    def on_item_persisted(_store, completion):
        completions.append(dict(completion))
        first_persisted.set()

    def run_grading():
        result_holder["result"] = alignment.auto_grade_report(
            case_id,
            report,
            "criteria.json",
            ProgressiveCriterionModel(slow_started, release_slow),
            FakeEvaluator(),
            ["1", "2"],
            on_item_persisted=on_item_persisted,
        )

    worker = threading.Thread(target=run_grading)
    worker.start()
    assert first_persisted.wait(3)

    partial = json.loads(
        (tmp_path / "case" / case_id / "grade_result" / f"{report}_grade.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["criteria_id"] for item in partial["criteria"]] == ["1"]
    jobs = alignment._store(case_id)["auto_grade_jobs"]
    statuses = {
        str(job["criteria_id"]): job["status"] for job in jobs.values()
    }
    assert statuses == {"1": "done", "2": "running"}
    assert worker.is_alive()

    release_slow.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert [item["criterion_id"] for item in completions] == ["1", "2"]
    assert [item["criteria_id"] for item in result_holder["result"]["grades"]] == [
        "1",
        "2",
    ]


def test_batch_exposes_each_target_completion_before_report_finishes(
    tmp_path, monkeypatch
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    first_persisted = threading.Event()
    release_second = threading.Event()

    def progressive_auto_grade(case_id_arg, report_name, _criteria_file_name, **kwargs):
        callback = kwargs["on_item_persisted"]
        with alignment._lock_for(case_id_arg):
            store = alignment._store(case_id_arg)
            callback(store, {"criterion_id": "1", "status": "done"})
            alignment._write_store(case_id_arg, store)
        first_persisted.set()
        assert release_second.wait(5)
        with alignment._lock_for(case_id_arg):
            store = alignment._store(case_id_arg)
            callback(store, {"criterion_id": "2", "status": "done"})
            alignment._write_store(case_id_arg, store)
        return {
            "grades": [{"criteria_id": "1"}, {"criteria_id": "2"}],
            "abstentions": [],
            "failures": [],
            "result": None,
        }

    monkeypatch.setattr(alignment, "auto_grade_report", progressive_auto_grade)
    worker = threading.Thread(
        target=alignment.run_auto_grade_batch,
        args=(case_id, batch["batch_id"]),
    )
    worker.start()
    assert first_persisted.wait(2)

    running = alignment.get_auto_grade_batch(case_id, batch["batch_id"])
    assert [target["status"] for target in running["targets"]] == ["done", "running"]

    release_second.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    completed = alignment.get_auto_grade_batch(case_id, batch["batch_id"])
    assert [target["status"] for target in completed["targets"]] == ["done", "done"]


def test_reason_evidence_disagreement_blocks_activation_even_when_score_matches(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(reason_ok=False), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "evaluating"
    assert state["reason"] == "out_of_construction_agreement_failed"


def test_auto_grade_is_model_only_and_boundary_can_abstain(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    auto = alignment.auto_grade_report(case_id, report2, "criteria.json", FakeModel(), FakeEvaluator())
    assert auto["grades"]
    jobs = alignment.alignment_summary(case_id)["auto_grade_jobs"]
    job = next(item for item in jobs if item["report_name"] == report2 and item["criteria_id"] == "1")
    assert job["status"] == "done"
    decisions = json.loads((tmp_path / "case" / case_id / "alignment" / "decisions.json").read_text())
    assert any(decision["decision_type"] == "model_only" for decision in decisions)
    model_event = next(event for event in alignment._store(case_id)["events"] if event["grader_type"] == "model")
    assert model_event["candidate_hash"]
    outside = "report-outside"
    _add_report(tmp_path, case_id, outside, "uncovered behavior")
    abstained = alignment.auto_grade_report(case_id, outside, "criteria.json", FakeModel(), FakeEvaluator())
    assert not abstained["grades"]
    assert abstained["abstentions"][0]["decision"] == "outside"
    jobs = alignment.alignment_summary(case_id)["auto_grade_jobs"]
    job = next(item for item in jobs if item["report_name"] == outside and item["criteria_id"] == "1")
    assert job["status"] == "abstained"


def test_accept_is_confirmed_positive_construction_only_and_auto_grade_consumes_it(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    client = TestClient(main.app)

    auto_response = client.post(
        f"/cases/{case_id}/reports/{report3}/auto-grade",
        json={"criteria_file_name": "criteria.json", "criteria_ids": ["1"]},
    )
    assert auto_response.status_code == 200, auto_response.text
    assert auto_response.json()["data"]["grades"]
    store = alignment._store(case_id)
    store["audit_jobs"] = {}
    alignment._write_store(case_id, store)
    event_count = len(store["events"])
    decision_count = len(store["decisions"])

    accept_response = client.post(
        f"/cases/{case_id}/reports/{report3}/grade/accept-model",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert accept_response.status_code == 200, accept_response.text
    data = accept_response.json()["data"]
    item = next(item for item in data["result"]["criteria"] if item["criteria_id"] == "1")
    assert item["criteria_score"] == 8
    assert item["graded_by"] == "user"
    assert item["acceptance_status"] == "accepted_model"
    assert item["provenance"]["grader_type"] == "model"
    assert item["provenance"]["decision_status"] == "accepted_model"

    accepted_store = alignment._store(case_id)
    accepted_event = next(
        event for event in accepted_store["events"]
        if event["report_name"] == report3 and event["grader_type"] == "model"
    )
    assert len(accepted_store["events"]) == event_count
    assert len(accepted_store["decisions"]) == decision_count + 1
    accepted_decision = next(
        decision
        for decision in accepted_store["decisions"]
        if decision["decision_type"] == "accepted_model"
    )
    assert accepted_decision["accepted_by"] == "default-teacher"
    assert accepted_decision["created_at"]
    assert not [
        event for event in accepted_store["events"]
        if event["report_name"] == report3 and event["criterion_id"] == "1" and event["grader_type"] == "teacher"
    ]
    assert accepted_event["grader_type"] == "model"
    assert accepted_event["grader_id"] == alignment.GRADING_MODEL_ID
    assert accepted_event["model_version"] == alignment.GRADING_MODEL_ID
    _, rubric_version, _ = alignment._load_criteria("criteria.json")
    assert accepted_event not in alignment._learning_teacher_events(
        accepted_store, "1", rubric_version
    )
    assert alignment._confirmed_model_learning_events(
        accepted_store, "1", rubric_version
    ) == [accepted_event]
    consistency_outcomes = sum(
        len(record.get("outcomes") or {})
        for record in accepted_store["consistency"]["criteria"].values()
    )
    assert consistency_outcomes == 1
    assert accepted_store["audit_jobs"] == {}
    stale = alignment.alignment_summary(case_id)["readiness"]["criteria"]["1"]
    assert stale["lifecycle"] == "evaluating"
    assert stale["reason"] == "readiness_basis_changed_requires_revalidation"

    duplicate_response = client.post(
        f"/cases/{case_id}/reports/{report3}/grade/accept-model",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert duplicate_response.status_code == 200, duplicate_response.text
    assert duplicate_response.json()["data"]["duplicate"] is True
    duplicate_store = alignment._store(case_id)
    assert len(duplicate_store["events"]) == event_count
    assert len(duplicate_store["decisions"]) == decision_count + 1
    assert sum(
        len(record.get("outcomes") or {})
        for record in duplicate_store["consistency"]["criteria"].values()
    ) == consistency_outcomes
    assert duplicate_store["audit_jobs"] == {}

    state = alignment.evaluate_alignment(
        case_id, "1", "criteria.json", FakeModel(), FakeEvaluator()
    )["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"
    assert state["readiness_basis_hash"] == alignment._readiness_basis_hash(
        alignment._store(case_id), rubric_version
    )
    assert accepted_event["event_id"] in state["candidate"]["construction_event_ids"]
    assert accepted_event["event_id"] not in state["candidate"]["validation_event_ids"]
    assert state["candidate"]["source_events_relabelled"] is False
    assert all(
        check["held_out_event_id"]
        in {
            event["event_id"]
            for event in alignment._learning_teacher_events(
                alignment._store(case_id), "1", rubric_version
            )
        }
        for check in state["validation"]["checks"]
    )
    assert state["boundary"]["observed_score_range"] == [8.0, 8.0]
    snapshot = alignment._load_candidate_snapshot(case_id, "1", state)
    confirmed_examples = [
        example
        for example in snapshot["few_shot_examples"]
        if example.get("source_event_id") == accepted_event["event_id"]
    ]
    assert confirmed_examples == [
        {
            "example_input": "validated pendulum evidence",
            "example_output_description": "same",
            "example_output_score": 8.0,
            "source_event_id": accepted_event["event_id"],
            "source_grader_type": "model",
            "source_decision_status": "accepted_model",
        }
    ]

    report4 = "report-d"
    _add_report(tmp_path, case_id, report4)
    observing_model = CandidateObservingModel()
    later = alignment.auto_grade_report(
        case_id,
        report4,
        "criteria.json",
        observing_model,
        FakeEvaluator(),
        ["1"],
    )
    assert later["grades"]
    assert [
        example["source_event_id"]
        for example in observing_model.seen_confirmed_examples
    ] == [accepted_event["event_id"]]


def test_boundary_does_not_treat_rubric_group_as_evidence_section(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)

    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), SectionAwareEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"
    assert state["boundary"]["allowed_sections"] == []
    assert state["boundary"]["required_evidence_signals"] == ["text"]

    report3 = "report-c"
    _add_report(tmp_path, case_id, report3, "validated header evidence without a named section")
    auto = alignment.auto_grade_report(case_id, report3, "criteria.json", FakeModel(), SectionAwareEvaluator(), criteria_ids=["1"])
    assert auto["grades"]
    assert not auto["abstentions"]


def test_accepted_positive_alone_cannot_activate_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 0)
    case_id, report, _ = _case(tmp_path, monkeypatch)
    auto = alignment.auto_grade_report(
        case_id, report, "criteria.json", FakeModel(), FakeEvaluator(), ["1"]
    )
    assert auto["grades"]
    alignment.accept_model_grade_event(
        case_id,
        report,
        CaseReportModelGradeAcceptRequest(
            criteria_id="1", criteria_file_name="criteria.json"
        ),
    )

    state = alignment.evaluate_alignment(
        case_id, "1", "criteria.json", FakeModel(), FakeEvaluator()
    )["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "collecting"
    assert state["reason"] == "zero_teacher_grounded_evidence"
    assert state.get("teacher_sample_count", 0) == 0


def test_wrong_accepted_positive_fails_independent_teacher_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(alignment, "AUTO_GRADE_MIN_TEACHER_SAMPLES", 1)
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(
        case_id, report, _payload(7, "teacher truth")
    )
    _audit_clean(case_id)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    wrong = alignment._append_model_event(
        store,
        case_id,
        report2,
        "1",
        "criteria.json",
        rubric_version,
        criteria_data["criteria"][0],
        _model_payload("wrong accepted conclusion"),
        "wrong-candidate",
    )
    alignment._write_store(case_id, store)
    alignment.accept_model_grade_event(
        case_id,
        report2,
        CaseReportModelGradeAcceptRequest(
            criteria_id="1", criteria_file_name="criteria.json"
        ),
    )

    state = alignment.evaluate_alignment(
        case_id,
        "1",
        "criteria.json",
        ConfirmedPositiveFollowingModel(),
        FakeEvaluator(),
    )["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "evaluating"
    assert state["reason"] == "out_of_construction_agreement_failed"
    assert state["validation"]["checks"][0]["held_out_event_id"] != wrong["event_id"]
    assert wrong["event_id"] in state["validation"]["checks"][0][
        "construction_event_ids"
    ]
    assert state["boundary"]["observed_score_range"] == [7.0, 7.0]


def test_teacher_correction_removes_confirmed_positive_from_next_candidate(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    accepted, rubric_version = _append_and_accept_model(case_id, report2)
    assert alignment._confirmed_model_learning_events(
        alignment._store(case_id), "1", rubric_version
    ) == [accepted]

    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    store = alignment._store(case_id)
    assert alignment._event_status(
        accepted["event_id"], store["decisions"]
    ) == "superseded"
    assert alignment._confirmed_model_learning_events(
        store, "1", rubric_version
    ) == []
    teacher_events = alignment._learning_teacher_events(
        store, "1", rubric_version
    )
    assert {event["report_name"] for event in teacher_events} == {report, report2}
    projection = alignment.materialize_projection(
        case_id, report2, store, "criteria.json"
    )
    item = projection["criteria"][0]
    assert item["criteria_score"] == 8
    assert item["provenance"]["grader_type"] == "teacher"

    state = alignment.evaluate_alignment(
        case_id, "1", "criteria.json", FakeModel(), FakeEvaluator()
    )["readiness"]["criteria"]["1"]
    snapshot = alignment._load_candidate_snapshot(case_id, "1", state)
    assert accepted["event_id"] not in state["candidate"]["construction_event_ids"]
    assert accepted["event_id"] not in {
        example.get("source_event_id")
        for example in snapshot.get("few_shot_examples") or []
    }


def test_confirmed_positive_is_version_scoped_and_deduplicated_per_report(
    tmp_path, monkeypatch
):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    first, old_version = _append_and_accept_model(case_id, report)
    second, _ = _append_and_accept_model(
        case_id, report, candidate_hash="accepted-candidate-newer"
    )
    selected = alignment._confirmed_model_learning_events(
        alignment._store(case_id), "1", old_version
    )
    assert [event["event_id"] for event in selected] == [second["event_id"]]
    assert first["event_id"] != second["event_id"]
    store = alignment._store(case_id)
    stored_second = next(
        event for event in store["events"] if event["event_id"] == second["event_id"]
    )
    original_allocation = stored_second["authored_grade"]["scoring_point_scores"]
    stored_second["authored_grade"]["scoring_point_scores"] = []
    assert alignment._confirmed_model_learning_events(
        store, "1", old_version
    ) == []
    stored_second["authored_grade"]["scoring_point_scores"] = original_allocation

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "new rubric version"
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    _, new_version, _ = alignment._load_criteria("criteria.json")
    assert new_version != old_version
    assert alignment._confirmed_model_learning_events(
        alignment._store(case_id), "1", new_version
    ) == []


@pytest.mark.parametrize(
    "invalid_state",
    ["invalid_evidence", "invalid_allocation", "superseded", "quarantined", "conflict"],
)
def test_confirmed_positive_selector_isolates_invalid_or_inactive_events(
    tmp_path, monkeypatch, invalid_state
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    event, rubric_version = _append_and_accept_model(case_id, report)
    store = alignment._store(case_id)
    if invalid_state == "invalid_evidence":
        event = next(
            item for item in store["events"] if item["event_id"] == event["event_id"]
        )
        event["authored_grade"]["anchor_text"] = "not in report"
        event["authored_grade"]["evidence_anchors"] = []
    elif invalid_state == "invalid_allocation":
        event = next(
            item for item in store["events"] if item["event_id"] == event["event_id"]
        )
        event["authored_grade"]["scoring_point_scores"] = []
    elif invalid_state in {"superseded", "quarantined"}:
        alignment._append_decision(
            store,
            invalid_state,
            [event["event_id"]],
            f"test {invalid_state}",
        )
    else:
        store["conflicts"].append(
            {
                "conflict_id": "conf-model",
                "criterion_id": "1",
                "status": "open",
                "event_ids": [event["event_id"]],
            }
        )
    assert alignment._confirmed_model_learning_events(
        store, "1", rubric_version
    ) == []


def test_unaccepted_model_event_is_not_confirmed_positive(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        rubric_version,
        criteria_data["criteria"][0],
        _model_payload(),
        "unaccepted-candidate",
    )
    assert alignment._confirmed_model_learning_events(
        store, "1", rubric_version
    ) == []


def test_legacy_candidate_snapshot_without_source_fields_still_loads(
    tmp_path, monkeypatch
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    criterion = criteria_data["criteria"][0]
    old_snapshot = {
        **criterion,
        "few_shot_examples": [
            {
                "example_input": "legacy evidence",
                "example_output_description": "legacy reason",
                "example_output_score": 8,
            }
        ],
    }
    construction_ids = ["evt-legacy-construction"]
    validation_ids = ["evt-legacy-validation"]
    candidate_hash = alignment._sha256_data(
        {
            "parent_rubric_version": rubric_version,
            "criterion": old_snapshot,
            "construction_event_ids": construction_ids,
            "validation_event_ids": validation_ids,
        }
    )
    candidate_dir = (
        tmp_path / "case" / case_id / "alignment" / "candidates" / "1"
    )
    alignment._atomic_write_json(
        candidate_dir / f"{candidate_hash}.json", old_snapshot
    )
    state = {
        "rubric_version": rubric_version,
        "candidate": {
            "candidate_hash": candidate_hash,
            "construction_event_ids": construction_ids,
            "validation_event_ids": validation_ids,
        },
    }
    assert alignment._load_candidate_snapshot(case_id, "1", state) == old_snapshot


def test_background_alignment_does_not_block_teacher_save(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def run_alignment():
        try:
            alignment.evaluate_alignment(case_id, "1", "criteria.json", BlockingModel(entered, release), FakeEvaluator())
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_alignment)
    thread.start()
    assert entered.wait(2)
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    saved = threading.Event()

    def save_teacher_grade():
        services.save_case_report_grade(case_id, report3, _payload(7, "new teacher sample"))
        saved.set()

    save_thread = threading.Thread(target=save_teacher_grade)
    save_thread.start()
    assert saved.wait(1)
    release.set()
    thread.join(2)
    save_thread.join(2)
    assert not errors


def test_stale_consistency_audit_requeues_pending_job(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def run_audit():
        try:
            alignment.run_consistency_audit_for_criterion(
                case_id,
                "criteria.json",
                "1",
                BlockingConsistencyAuditEvaluator(entered, release),
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_audit)
    thread.start()
    assert entered.wait(2)
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    services.save_case_report_grade(case_id, report3, _payload(8))
    release.set()
    thread.join(2)
    assert not errors
    job = next(iter(alignment._store(case_id)["audit_jobs"].values()))
    assert job["status"] == "pending"
    assert job["reason"] == "teacher_events_changed_during_audit"


def test_background_auto_grade_does_not_block_teacher_save(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    _audit_clean(case_id)
    alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def run_auto_grade():
        try:
            alignment.auto_grade_report(case_id, report2, "criteria.json", BlockingModel(entered, release), FakeEvaluator(), ["1"])
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_auto_grade)
    thread.start()
    assert entered.wait(2)
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    saved = threading.Event()

    def save_teacher_grade():
        services.save_case_report_grade(case_id, report3, _payload(7, "new teacher sample"))
        saved.set()

    save_thread = threading.Thread(target=save_teacher_grade)
    save_thread.start()
    assert saved.wait(1)
    release.set()
    thread.join(2)
    save_thread.join(2)
    assert not errors


def test_verified_conversion_triggers_but_forged_identity_does_not(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    output_dir = tmp_path / "case" / case_id / "markdown" / report
    calls = []
    monkeypatch.setattr(main, "convert_pdf", lambda *_: {"output_dir": str(output_dir), "md_path": str(output_dir / f"{report}.md"), "text_only_path": str(output_dir / f"{report}_text_only.md")})
    monkeypatch.setattr(main, "auto_grade_report", lambda *args, **kwargs: calls.append(args) or {"grades": [], "abstentions": [], "failures": [], "result": None})
    req = ConvertRequest(pdf_path=str(tmp_path / "case" / case_id / f"{report}.pdf"), output_dir=str(output_dir), case_id=case_id, report_name=report, criteria_file_name="criteria.json")
    data = main.convert(req).data
    assert data["auto_grade_triggered"] is True
    assert calls
    forged = req.model_copy(update={"pdf_path": str(tmp_path / "elsewhere.pdf")})
    assert main.convert(forged).data["auto_grade_triggered"] is False
    assert len(calls) == 1


def test_api_alignment_endpoint(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    client = TestClient(main.app)
    response = client.post(f"/cases/{case_id}/reports/{report}/grade", json=_payload(7, "api").model_dump())
    assert response.status_code == 200, response.text
    response = client.get(f"/cases/{case_id}/alignment")
    assert response.status_code == 200
    assert "readiness" in response.json()["data"]


def test_debug_clear_case_grades_resets_only_derived_scoring_data(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    case_dir = tmp_path / "case" / case_id
    report_markdown = case_dir / "markdown" / report / f"{report}.md"
    rubric_path = criteria_dir / "criteria.json"

    store = alignment._store(case_id)
    store["events"] = [{"event_id": "event-a"}]
    store["decisions"] = [{"decision_id": "decision-a"}]
    store["auto_grade_jobs"] = {"job-a": {"status": "done"}}
    store["auto_grade_batches"] = {"batch-a": {"status": "completed"}}
    store["consistency_audits"] = [{"audit_id": "audit-a"}]
    alignment._write_store(case_id, store)
    alignment._atomic_write_json(
        case_dir / "implicit_grades.json",
        [{"implicit_grade_id": "implicit-a", "status": "proposal_ready"}],
    )
    alignment._atomic_write_json(
        case_dir / "criterion_proposals.json",
        [{"proposal_id": "proposal-a", "status": "pending"}],
    )
    alignment._atomic_write_json(
        case_dir / "grade_result" / f"{report}_grade.json",
        {"file_name": f"{report}.md", "criteria": []},
    )
    alignment._atomic_write_json(
        case_dir / "alignment" / "candidates" / "1" / "candidate.json",
        {"candidate_hash": "candidate-a"},
    )

    client = TestClient(main.app)
    mismatch = client.post(
        f"/cases/{case_id}/debug/clear-grades",
        json={"confirm_case_id": "another-case"},
    )
    assert mismatch.status_code == 400
    assert alignment._store(case_id)["events"]

    response = client.post(
        f"/cases/{case_id}/debug/clear-grades",
        json={"confirm_case_id": case_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["cleared"]["events"] == 1
    assert response.json()["data"]["cleared"]["grade_files"] == 1

    cleared_store = alignment._store(case_id)
    assert cleared_store["events"] == []
    assert cleared_store["decisions"] == []
    assert cleared_store["auto_grade_jobs"] == {}
    assert cleared_store["auto_grade_batches"] == {}
    assert cleared_store["consistency"]["criteria"] == {}
    assert json.loads((case_dir / "implicit_grades.json").read_text()) == []
    assert json.loads((case_dir / "criterion_proposals.json").read_text()) == []
    assert not (case_dir / "grade_result" / f"{report}_grade.json").exists()
    assert not (case_dir / "alignment" / "candidates").exists()
    assert report_markdown.exists()
    assert rubric_path.exists()


def test_default_teacher_save_queues_audit_without_conflict_evaluator(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, ""))
    store = alignment._store(case_id)
    assert len(store["events"]) == 1
    assert store["decisions"][-1]["decision_type"] == "pending"
    assert store["audit_jobs"]
    assert next(iter(store["audit_jobs"].values()))["status"] == "pending"
    assert store["conflicts"] == []


def test_pending_audit_samples_do_not_activate_readiness(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "collecting"


def test_missing_reason_preserves_teacher_anchor_in_audit_artifact(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "", "teacher selected span A"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    artifact = next(iter(alignment._store(case_id)["audit_artifacts"].values()))
    assert artifact["teacher_reason"] == ""
    assert artifact["teacher_anchor"] == "teacher selected span A"
    assert artifact["evidence_source"] == "teacher_selected"
    assert artifact["audit_status"] == "trusted_clean"


def test_audit_supplements_missing_reason_without_overwriting_teacher_authored_reason(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "", "teacher selected span A"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", GeneratedReasonAuditEvaluator())

    store = alignment._store(case_id)
    event = store["events"][0]
    artifact = store["audit_artifacts"][event["event_id"]]
    projection = alignment.materialize_projection(case_id, report, store, "criteria.json")
    assert event["authored_grade"]["score_reason"] == ""
    assert event["model_score_reason"] == "选中的证据满足当前评分项，因此保留该教师给分。"
    assert artifact["model_score_reason"] == event["model_score_reason"]
    assert projection["criteria"][0]["score_reason"] == event["model_score_reason"]


def test_audit_model_allocates_teacher_total_to_structured_points_without_mutating_teacher_grade(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "teacher total", "teacher selected span A"))
    legacy_store = alignment._store(case_id)
    legacy_store["events"][0]["authored_grade"]["scoring_point_scores"] = []
    alignment._write_store(case_id, legacy_store)

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", StructuredAllocationAuditEvaluator(),
    )

    store = alignment._store(case_id)
    event = store["events"][0]
    artifact = store["audit_artifacts"][event["event_id"]]
    assert event["authored_grade"]["criteria_score"] == 7.0
    assert event["authored_grade"]["scoring_point_scores"] == []
    assert artifact["model_allocation_status"] == "valid"
    assert artifact["model_scoring_point_scores"] == [
        {
            "point_id": "1.p1",
            "score": 7.0,
            "max_score": 10.0,
            "reason": "Teacher evidence triggers 1.p1.r1.",
        },
    ]
    assert artifact["model_matched_scoring_points"] == ["1.p1"]


def test_invalid_audit_model_allocation_is_discarded_without_changing_teacher_total(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "teacher total", "teacher selected span A"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", StructuredAllocationAuditEvaluator(invalid=True),
    )

    store = alignment._store(case_id)
    event = store["events"][0]
    artifact = store["audit_artifacts"][event["event_id"]]
    assert event["authored_grade"]["criteria_score"] == 7.0
    assert artifact["model_allocation_status"] == "invalid"
    assert artifact["model_scoring_point_scores"] == []


def test_teacher_only_grade_does_not_create_update_or_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "edge case", "teacher selected edge"))
    evaluator = RefinementProposalEvaluator()

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", evaluator)

    store = alignment._store(case_id)
    artifact = next(iter(store["audit_artifacts"].values()))
    assert alignment._read_proposals(case_id) == []
    assert artifact["audit_status"] == "trusted_clean"
    assert store["conflicts"] == []
    assert evaluator.interpret_calls == 1


def test_pending_update_hides_and_defers_a_preexisting_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    services.save_case_report_grade(case_id, report2, _payload(7, "alternative", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    assert alignment.alignment_summary(case_id)["open_conflicts"]

    proposal_path = tmp_path / "case" / case_id / "criterion_proposals.json"
    proposal_path.write_text(json.dumps([{
        "proposal_id": "prop_pending_update",
        "proposal_type": "update_criterion",
        "target_criterion_id": "1",
        "criteria_file_name": "criteria.json",
        "status": "pending",
    }]), encoding="utf-8")

    # Summary is safe even if the proposal was produced by a different worker
    # before it had an opportunity to retire the older conflict record.
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    alignment.defer_conflicts_for_pending_update(case_id, "criteria.json", "1", "prop_pending_update")
    assert alignment._store(case_id)["conflicts"][-1]["status"] == "deferred"


def test_no_refinement_then_vertical_needs_review_stays_teacher_authoritative(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "edge case", "teacher selected edge"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", NoUpdateNeedsReviewEvaluator())

    store = alignment._store(case_id)
    artifact = next(iter(store["audit_artifacts"].values()))
    assert store["conflicts"] == []
    assert artifact["fit"] == "needs_review"
    assert artifact["audit_status"] == "trusted_clean"


def test_unexplained_teacher_score_alone_does_not_create_binary_update(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "要求：实验报告标题或姓名缺失。\n扣分规则：缺失则扣10分。"
    rubric["criteria"][0]["original_description"] = "实验报告标题或姓名：缺失扣分。"
    rubric["criteria"][0]["max_score"] = 10
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    services.save_case_report_grade(case_id, report, _payload(7, "", "teacher selected edge"))
    evaluator = RefinementProposalEvaluator()

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", evaluator)

    store = alignment._store(case_id)
    artifact = next(iter(store["audit_artifacts"].values()))
    assert evaluator.interpret_calls == 1
    assert alignment._read_proposals(case_id) == []
    assert artifact["audit_status"] == "trusted_clean"
    assert store["conflicts"] == []


def test_pending_update_proposal_defers_current_conflict_check(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "", "teacher selected edge"))
    store = alignment._store(case_id)
    event = store["events"][0]
    criteria_name, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    criterion = alignment._require_criterion(criteria_data, "1")
    alignment._store_event_update_proposal(
        case_id,
        criteria_name,
        rubric_version,
        criteria_data,
        criterion,
        event,
        RefinementProposalEvaluator().propose_criterion_update("", criterion, event),
    )

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", NoUpdateNeedsReviewEvaluator())

    proposals = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())
    store = alignment._store(case_id)
    assert proposals[0]["status"] == "pending"
    assert store["conflicts"] == []
    artifact = next(iter(store["audit_artifacts"].values()))
    assert artifact["audit_status"] == "deferred_pending_update"
    assert artifact["proposal_id"] == proposals[0]["proposal_id"]


def test_teacher_point_correction_without_reason_proposes_and_to_or_rubric_update(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    criterion = rubric["criteria"][0]
    criterion["max_score"] = 8
    criterion["scoring_points"] = [
        {
            "point_id": "1.p1",
            "summary": "Evidence A is present",
            "deduction_rules": [
                {"rule_id": "1.p1.r1", "condition": "Evidence A is absent", "deduct": 4}
            ],
            "max_deduction": 4,
            "aggregation": "max_triggered",
        },
        {
            "point_id": "1.p2",
            "summary": "Evidence A AND evidence B is present",
            "deduction_rules": [
                {
                    "rule_id": "1.p2.r1",
                    "condition": "Evidence A AND evidence B is not both present",
                    "deduct": 4,
                }
            ],
            "max_deduction": 4,
            "aggregation": "max_triggered",
        },
    ]
    criterion["scoring_aggregation"] = {"mode": "sum_deductions_capped", "cap": 8}
    canonical = CriteriaSchema.model_validate(rubric).model_dump(mode="json")
    (criteria_dir / "criteria.json").write_text(json.dumps(canonical), encoding="utf-8")

    store = alignment._store(case_id)
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    current_criterion = alignment._require_criterion(criteria_data, "1")
    model_event = alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        rubric_version,
        current_criterion,
        {
            "criteria_score": 4,
            "score_reason": "The second point requires both alternatives.",
            "anchor_text": "evidence A",
            "matched_scoring_points": ["1.p2"],
            "matched_deduction_rules": ["1.p2.r1"],
            "scoring_point_scores": [
                {
                    "point_id": "1.p1",
                    "score": 4,
                    "reason": "The report contains evidence A, satisfying 1.p1.",
                },
                {
                    "point_id": "1.p2",
                    "score": 0,
                    "reason": "The report lacks evidence B, triggering 1.p2.r1.",
                },
            ],
        },
        "and-to-or-model-candidate",
    )
    alignment._write_store(case_id, store)
    teacher_payload = _payload(8, "", "evidence A").model_copy(
        update={
            "scoring_point_scores": [
                {"point_id": "1.p1", "score": 4},
                {"point_id": "1.p2", "score": 4},
            ]
        }
    )
    services.save_case_report_grade(case_id, report, teacher_payload)
    evaluator = AndToOrRefinementEvaluator()

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", evaluator)

    events = alignment._store(case_id)["events"]
    teacher_event = next(event for event in events if event.get("grader_type") == "teacher")
    proposals = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())
    proposal = proposals[0]
    artifact = next(iter(alignment._store(case_id)["audit_artifacts"].values()))
    assert teacher_event["compared_model_event_id"] == model_event["event_id"]
    assert evaluator.comparison["model_event_id"] == model_event["event_id"]
    assert evaluator.comparison["model_score"] == 4.0
    assert evaluator.comparison["teacher_score"] == 8.0
    assert evaluator.comparison["total_score_changed"] is True
    assert evaluator.comparison["scoring_point_scores_changed"] is True
    assert proposal["status"] == "pending"
    assert proposal["proposal_type"] == "update_criterion"
    assert proposal["target_criterion_id"] == "1"
    assert " OR " in proposal["suggested_criterion"]["scoring_points"][1]["summary"]
    assert " AND " not in proposal["suggested_criterion"]["scoring_points"][1]["summary"]
    assert evaluator.interpret_calls == 0
    assert artifact["audit_status"] == "deferred_pending_update"
    assert artifact["update_proposed"] is True
    assert proposal["source_event_ids"] == [
        teacher_event["event_id"],
        model_event["event_id"],
    ]
    assert proposal["source_event_digests"] == [
        proposal_source_event_digest(teacher_event),
        proposal_source_event_digest(model_event),
    ]
    confirmed = implicit_grading.confirm_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
            criteria_file_name="criteria.json",
        ),
    )
    assert confirmed["status"] == "confirmed"
    updated_rubric = json.loads((criteria_dir / "criteria.json").read_text())
    assert " OR " in updated_rubric["criteria"][0]["scoring_points"][1]["summary"]


def test_rejected_refinement_signature_suppresses_repeat_update_prompt(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _append_and_accept_model(case_id, report)
    services.save_case_report_grade(case_id, report, _payload(7, "edge case", "teacher selected edge"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", RefinementProposalEvaluator())
    proposal = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())[0]
    implicit_grading.reject_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalRejectRequest(
            proposal_version=proposal["proposal_version"],
            reason="Teacher only wants this criterion to check the original two fields.",
        ),
    )

    evaluator = RefinementProposalEvaluator()
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", evaluator)

    proposals = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())
    store = alignment._store(case_id)
    assert [item["status"] for item in proposals] == ["rejected"]
    assert store["refinement_rejections"]
    assert evaluator.context_events is None
    assert evaluator.interpret_calls == 1
    assert store["conflicts"] == []
    assert next(iter(store["audit_artifacts"].values()))["audit_status"] == "trusted_clean"


def test_unexplained_partial_score_for_binary_rule_does_not_update_without_model(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "要求：实验报告标题或姓名缺失。\n扣分规则：缺失则扣4分。"
    rubric["criteria"][0]["original_description"] = "实验报告标题或姓名：缺失扣分。"
    rubric["criteria"][0]["max_score"] = 4
    rubric["criteria"][0]["scoring_points"][0]["deduction_rules"][0]["deduct"] = 4
    rubric["criteria"][0]["scoring_points"][0]["max_deduction"] = 4
    rubric["criteria"][0]["scoring_aggregation"]["cap"] = 4
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    services.save_case_report_grade(case_id, report, _payload(1, "", "浙江大学 实验报告 姓名：张三"))
    evaluator = CaseBasedPartialRefinementEvaluator()

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", evaluator)

    store = alignment._store(case_id)
    artifact = next(iter(store["audit_artifacts"].values()))
    assert evaluator.interpret_calls == 1
    assert alignment._read_proposals(case_id) == []
    assert artifact["audit_status"] == "trusted_clean"
    assert store["conflicts"] == []


def test_contradictory_teacher_only_proposal_is_superseded_without_regeneration(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "要求：实验报告标题或姓名缺失。\n扣分规则：缺失则扣4分。"
    rubric["criteria"][0]["max_score"] = 4
    rubric["criteria"][0]["scoring_points"][0]["deduction_rules"][0]["deduct"] = 4
    rubric["criteria"][0]["scoring_points"][0]["max_deduction"] = 4
    rubric["criteria"][0]["scoring_aggregation"]["cap"] = 4
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    services.save_case_report_grade(case_id, report, _payload(1, "", "浙江大学 实验报告 姓名：张三"))
    store = alignment._store(case_id)
    event = store["events"][0]
    criteria_name, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    criterion = alignment._require_criterion(criteria_data, "1")
    wrong = dict(criterion)
    wrong["description"] = "仅出现标题、缺少姓名，给1分。"
    alignment._store_event_update_proposal(
        case_id,
        criteria_name,
        rubric_version,
        criteria_data,
        criterion,
        event,
        {
            "decision": "update_criterion",
            "suggested_criterion": wrong,
            "rationale": "当前案例仅出现标题而缺姓名。",
            "confidence": 0.9,
        },
    )

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", CaseBasedPartialRefinementEvaluator())

    proposals = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())
    assert proposals[0]["status"] == "superseded"
    assert len(proposals) == 1


def test_confirm_event_update_proposal_updates_rubric_without_rewriting_teacher_score(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    _append_and_accept_model(case_id, report)
    services.save_case_report_grade(case_id, report, _payload(7, "edge case", "teacher selected edge"))
    before_events = alignment._store(case_id)["events"]
    source_event_id = next(
        event["event_id"] for event in before_events if event.get("grader_type") == "teacher"
    )

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", RefinementProposalEvaluator())
    proposal = json.loads((tmp_path / "case" / case_id / "criterion_proposals.json").read_text())[0]
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )

    confirmed = implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)
    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    events = alignment._store(case_id)["events"]

    assert confirmed["status"] == "confirmed"
    assert source_event_id in confirmed["confirmed_event_ids"]
    assert criteria["criteria"][0]["description"] == "rule one refined with teacher-selected edge case"
    assert len(events) == 2
    assert next(
        event for event in events if event.get("grader_type") == "teacher"
    )["authored_grade"]["criteria_score"] == 7
    assert next(iter(alignment._store(case_id)["audit_jobs"].values()))["status"] == "pending"


def test_diagnostic_audit_creates_two_event_teacher_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric check", "same anchor"))
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    services.save_case_report_grade(case_id, report3, _payload(10, "outlier", "same anchor"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    store = alignment._store(case_id)
    conflict = store["conflicts"][-1]
    assert conflict["diagnosis"] == "teacher_teacher_conflict"
    assert len(conflict["suspect_event_ids"]) == 1
    assert len(conflict["supporting_event_ids"]) == 1
    assert len(conflict["suspect_events"]) == 1
    assert conflict["suspect_events"][0]["report_name"] == report3
    assert conflict["suspect_events"][0]["anchor_text"] == "same anchor"
    assert {ref["report_name"] for ref in conflict["affected_events"]} == {report2, report3}

    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"
    trusted_ids = set(state["validation"]["trusted_event_ids"])
    assert conflict["suspect_event_ids"][0] not in trusted_ids


def test_deleting_conflict_source_supersedes_open_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric check", "same anchor"))
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    services.save_case_report_grade(case_id, report3, _payload(10, "outlier", "same anchor"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    assert alignment.alignment_summary(case_id)["open_conflicts"]

    services.delete_case_report_grade(
        case_id,
        report3,
        CaseReportGradeDeleteRequest(criteria_id="1", criteria_file_name="criteria.json"),
    )
    store = alignment._store(case_id)
    assert alignment.alignment_summary(case_id, store)["open_conflicts"] == []
    assert store["conflicts"][-1]["status"] == "superseded"
    suspect_id = store["conflicts"][-1]["suspect_event_ids"][0]
    assert store["audit_artifacts"][suspect_id]["audit_status"] == "superseded"


def test_replacing_conflict_source_supersedes_open_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric check", "same anchor"))
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    services.save_case_report_grade(case_id, report3, _payload(10, "outlier", "same anchor"))

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][-1]
    suspect_id = conflict["suspect_event_ids"][0]

    services.save_case_report_grade(case_id, report3, _payload(7, "fixed", "same anchor"))
    store = alignment._store(case_id)
    assert alignment.alignment_summary(case_id, store)["open_conflicts"] == []
    assert store["conflicts"][-1]["status"] == "superseded"
    assert store["conflicts"][-1]["superseded_by"]
    assert store["audit_artifacts"][suspect_id]["audit_status"] == "superseded"


def test_criteria_file_update_enqueues_only_changed_criterion(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    alignment.append_teacher_grade_event(case_id, report, _payload(7), FakeEvaluator(), FakeModel())
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    alignment.append_teacher_grade_event(
        case_id,
        report2,
        _payload(3).model_copy(
            update={
                "criteria_id": "2",
                "criteria_score": 3,
                "scoring_point_scores": [{"point_id": "2.p1", "score": 3}],
            }
        ),
        FakeEvaluator(),
        FakeModel(),
    )
    store = alignment._store(case_id)
    store["audit_jobs"] = {}
    alignment._write_store(case_id, store)

    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    criteria["criteria"][0]["description"] = "changed rule one"
    services.save_criteria_file("criteria.json", criteria)
    jobs = alignment._store(case_id)["audit_jobs"].values()
    assert {job["criterion_id"] for job in jobs} == {"1"}


def test_alignment_audit_endpoint_drains_pending_job(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "", "teacher selected"))
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: FakeConsistencyAuditEvaluator())
    client = TestClient(main.app)
    response = client.post(f"/cases/{case_id}/alignment/audit", json={"criteria_file_name": "criteria.json"})
    assert response.status_code == 200, response.text
    jobs = alignment._store(case_id)["audit_jobs"].values()
    assert next(iter(jobs))["status"] == "done"
    assert alignment._store(case_id)["audit_artifacts"]


def test_alignment_audit_endpoint_refreshes_readiness_after_audit(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(8))
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: FakeFullAlignmentEvaluator())
    client = TestClient(main.app)

    response = client.post(
        f"/cases/{case_id}/alignment/audit",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )

    assert response.status_code == 200, response.text
    readiness = response.json()["data"]["alignment"]["readiness"]["criteria"]["1"]
    assert readiness["lifecycle"] == "active_selective"
    assert alignment.alignment_summary(case_id)["readiness"]["criteria"]["1"]["lifecycle"] == "active_selective"


def test_api_frontend_like_refinement_confirmation_flow(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    _append_and_accept_model(case_id, report)
    evaluator = RefinementThenCleanEvaluator()
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: evaluator)
    client = TestClient(main.app)

    save_response = client.post(
        f"/cases/{case_id}/reports/{report}/grade",
        json=_payload(7, "edge case", "teacher selected edge").model_dump(),
    )
    assert save_response.status_code == 200, save_response.text
    saved_grade = save_response.json()["data"]["result"]["criteria"][0]
    assert saved_grade["criteria_score"] == 7
    assert saved_grade["graded_by"] == "user"
    initial_store = alignment._store(case_id)
    assert initial_store["decisions"][-1]["decision_type"] == "pending"
    assert initial_store["conflicts"] == []

    audit_response = client.post(
        f"/cases/{case_id}/alignment/audit",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert audit_response.status_code == 200, audit_response.text
    audit_item = audit_response.json()["data"]["items"][0]
    assert audit_item["audit"]["diagnosis"] == "rubric_update_pending"
    assert audit_item["artifacts"][0]["audit_status"] == "deferred_pending_update"
    assert audit_item["artifacts"][0]["update_proposed"] is True
    assert audit_item["job"]["status"] == "done"

    proposals_response = client.get(
        f"/cases/{case_id}/criterion-proposals",
        params={"criteria_file_name": "criteria.json"},
    )
    assert proposals_response.status_code == 200, proposals_response.text
    proposals = proposals_response.json()["data"]["items"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["proposal_type"] == "update_criterion"
    assert proposal["target_criterion_id"] == "1"
    assert proposal["source_event_ids"]
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    assert evaluator.interpret_calls == 0

    confirm_response = client.post(
        f"/cases/{case_id}/criterion-proposals/{proposal['proposal_id']}/confirm",
        json={
            "proposal_version": proposal["proposal_version"],
            "criteria_file_name": "criteria.json",
            "confirmed_by": "frontend-smoke",
            "suggested_criterion": proposal["suggested_criterion"],
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    confirmed = confirm_response.json()["data"]["item"]
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_by"] == "frontend-smoke"
    assert set(proposal["source_event_ids"]).issubset(set(confirmed["confirmed_event_ids"]))

    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    assert criteria["criteria"][0]["description"] == "rule one refined with teacher-selected edge case"
    events_after_confirm = alignment._store(case_id)["events"]
    assert len(events_after_confirm) == 2
    assert next(
        event for event in events_after_confirm if event.get("grader_type") == "teacher"
    )["authored_grade"]["criteria_score"] == 7
    pending_job = next(iter(alignment._store(case_id)["audit_jobs"].values()))
    assert pending_job["status"] == "pending"
    assert pending_job["trigger"] == "criterion_proposal_confirmed"

    followup_response = client.post(
        f"/cases/{case_id}/alignment/audit",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert followup_response.status_code == 200, followup_response.text
    followup_item = followup_response.json()["data"]["items"][0]
    assert followup_item["audit"]["diagnosis"] == "consistent"
    assert followup_item["artifacts"][0]["audit_status"] == "trusted_clean"
    final_alignment = followup_response.json()["data"]["alignment"]
    assert final_alignment["open_conflicts"] == []
    assert next(iter(alignment._store(case_id)["audit_jobs"].values()))["status"] == "done"
    assert evaluator.interpret_calls == 1


def test_api_frontend_like_refinement_rejection_flow(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _append_and_accept_model(case_id, report)
    first_evaluator = RefinementProposalEvaluator()
    second_evaluator = RefinementProposalEvaluator()
    evaluators = [first_evaluator, second_evaluator, FakeFullAlignmentEvaluator()]
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: evaluators.pop(0))
    client = TestClient(main.app)

    save_response = client.post(
        f"/cases/{case_id}/reports/{report}/grade",
        json=_payload(7, "edge case", "teacher selected edge").model_dump(),
    )
    assert save_response.status_code == 200, save_response.text
    saved_grade = save_response.json()["data"]["result"]["criteria"][0]
    assert saved_grade["criteria_score"] == 7
    assert saved_grade["graded_by"] == "user"

    audit_response = client.post(
        f"/cases/{case_id}/alignment/audit",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert audit_response.status_code == 200, audit_response.text
    first_item = audit_response.json()["data"]["items"][0]
    assert first_item["audit"]["diagnosis"] == "rubric_update_pending"
    assert first_item["artifacts"][0]["audit_status"] == "deferred_pending_update"
    assert first_item["artifacts"][0]["update_proposed"] is True
    assert first_evaluator.interpret_calls == 0

    proposals_response = client.get(
        f"/cases/{case_id}/criterion-proposals",
        params={"criteria_file_name": "criteria.json"},
    )
    assert proposals_response.status_code == 200, proposals_response.text
    proposal = proposals_response.json()["data"]["items"][0]
    assert proposal["status"] == "pending"
    assert proposal["proposal_type"] == "update_criterion"
    assert proposal["source_event_ids"]
    assert audit_response.json()["data"]["alignment"]["open_conflicts"] == []

    reject_response = client.post(
        f"/cases/{case_id}/criterion-proposals/{proposal['proposal_id']}/reject",
        json={
            "proposal_version": proposal["proposal_version"],
            "reason": "Teacher only wants the original two-field rule for this criterion.",
        },
    )
    assert reject_response.status_code == 200, reject_response.text
    rejected = reject_response.json()["data"]["item"]
    assert rejected["status"] == "rejected"
    assert alignment._store(case_id)["refinement_rejections"]

    second_audit_response = client.post(
        f"/cases/{case_id}/alignment/audit",
        json={"criteria_id": "1", "criteria_file_name": "criteria.json"},
    )
    assert second_audit_response.status_code == 200, second_audit_response.text
    second_item = second_audit_response.json()["data"]["items"][0]
    assert second_item["audit"]["diagnosis"] == "consistent"
    assert second_item["artifacts"][0]["audit_status"] == "trusted_clean"
    assert "update_proposed" not in second_item["artifacts"][0]
    assert second_evaluator.context_events is None
    assert second_evaluator.interpret_calls == 1

    final_proposals = client.get(
        f"/cases/{case_id}/criterion-proposals",
        params={"criteria_file_name": "criteria.json"},
    ).json()["data"]["items"]
    assert [item["status"] for item in final_proposals] == ["rejected"]
    assert second_audit_response.json()["data"]["alignment"]["open_conflicts"] == []


def test_default_audit_detects_teacher_teacher_conflict_with_llm_adapter(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(10, "outlier", "same anchor"))
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: BoundaryAuditEvaluator())
    client = TestClient(main.app)
    response = client.post(f"/cases/{case_id}/alignment/audit", json={"criteria_id": "1", "criteria_file_name": "criteria.json"})
    assert response.status_code == 200, response.text
    conflict = alignment._store(case_id)["conflicts"][-1]
    assert conflict["diagnosis"] == "teacher_teacher_conflict"
    assert len(conflict["suspect_event_ids"]) == 1


def test_vertical_needs_review_does_not_block_teacher_learning(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric fit", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric fit", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator({7.0}))
    store = alignment._store(case_id)
    assert {artifact["audit_status"] for artifact in store["audit_artifacts"].values()} == {"trusted_clean"}
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"


def test_equal_teacher_outcomes_never_create_conflict(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric fit", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric fit", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator({7.0}))
    store = alignment._store(case_id)
    assert {artifact["audit_status"] for artifact in store["audit_artifacts"].values()} == {"trusted_clean"}
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert state["lifecycle"] == "active_selective"


def test_rubric_change_reaudits_history_blocks_then_reaccepts_clean_samples(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric check", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    active = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert active["lifecycle"] == "active_selective"

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "refined current rule with stricter interpretation"
    services.save_criteria_file("criteria.json", rubric)
    jobs = alignment._store(case_id)["audit_jobs"].values()
    assert {job["criterion_id"] for job in jobs} == {"1"}

    pending = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert pending["lifecycle"] == "collecting"
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator({7.0}))
    blocked = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert blocked["lifecycle"] == "collecting"
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    reaccepted = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert reaccepted["lifecycle"] == "active_selective"
    assert len(reaccepted["validation"]["trusted_event_ids"]) == 2


def test_false_positive_vertical_audit_does_not_rewrite_teacher_scores(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(7, "rubric check", "same anchor"))
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report2, _payload(7, "rubric check", "same anchor"))
    report3 = "report-c"
    _add_report(tmp_path, case_id, report3)
    services.save_case_report_grade(case_id, report3, _payload(7, "rubric check", "same anchor"))
    original_scores = {
        event["event_id"]: (event.get("authored_grade") or {}).get("criteria_score")
        for event in alignment._store(case_id)["events"]
        if event.get("grader_type") == "teacher"
    }

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FirstEventSuspectEvaluator())
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    state = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert len(state["validation"]["trusted_event_ids"]) == 3

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    corrected = alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())["readiness"]["criteria"]["1"]
    assert len(corrected["validation"]["trusted_event_ids"]) == 3
    current_scores = {
        event["event_id"]: (event.get("authored_grade") or {}).get("criteria_score")
        for event in alignment._store(case_id)["events"]
        if event.get("grader_type") == "teacher"
    }
    assert current_scores == original_scores


def test_teacher_teacher_conflict_does_not_offer_second_pass_reassessment(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    services.save_case_report_grade(case_id, report2, _payload(7, "alternative", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]

    with pytest.raises(ValueError, match="pattern selection"):
        alignment.reassess_conflict(case_id, conflict["conflict_id"])


def test_teacher_teacher_conflict_rejects_legacy_manual_exception(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    services.save_case_report_grade(case_id, report, _payload(8, "current", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    services.save_case_report_grade(case_id, report2, _payload(7, "alternative", "same anchor"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", BoundaryAuditEvaluator())
    conflict = alignment.alignment_summary(case_id)["open_conflicts"][0]
    with pytest.raises(ValueError, match="no reassessment"):
        alignment.resolve_conflict(case_id, conflict["conflict_id"], "manual_exception")


def test_rubric_update_pauses_batch_authorization_until_migration_repair(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8, "same", "validated pendulum evidence"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())
    authorized = alignment.authorize_criterion_batch(case_id, "criteria.json", "1")
    assert authorized["state"] == alignment.AUTH_BATCH_AUTHORIZED

    _, old_version, criteria_data = alignment._load_criteria("criteria.json")
    store = alignment._store(case_id)
    alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        old_version,
        criteria_data["criteria"][0],
        _model_payload(),
        "candidate-old",
    )
    alignment._write_store(case_id, store)

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "updated vertical rule"
    services.save_criteria_file("criteria.json", rubric)
    summary = alignment.alignment_summary(case_id)
    assert summary["criterion_authorizations"][0]["state"] == alignment.AUTH_REPAIR_REQUIRED
    assert summary["open_conflicts"] == []
    with pytest.raises(ValueError, match="migration audit"):
        alignment.confirm_criterion_repair(case_id, "criteria.json", "1")

    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    alignment.evaluate_alignment(case_id, "1", "criteria.json", FakeModel(), FakeEvaluator())
    repair = alignment.confirm_criterion_repair(case_id, "criteria.json", "1")
    assert repair["report_names"] == [report]
    projection = alignment.materialize_projection(case_id, report, alignment._store(case_id), "criteria.json")
    assert all(item.get("provenance", {}).get("rubric_version") != old_version for item in projection["criteria"] if item.get("graded_by") == "model")

    alignment.auto_grade_report(case_id, report, "criteria.json", FakeModel(), FakeEvaluator(), ["1"])
    final_authorization = alignment.alignment_summary(case_id)["criterion_authorizations"][0]
    assert final_authorization["state"] == alignment.AUTH_BATCH_AUTHORIZED


def test_unaccepted_model_grade_is_invalidated_and_queued_after_clean_rubric_migration(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8, "same", "validated pendulum evidence"))
    alignment.run_consistency_audit_for_criterion(case_id, "criteria.json", "1", FakeConsistencyAuditEvaluator())
    _, old_version, criteria_data = alignment._load_criteria("criteria.json")
    store = alignment._store(case_id)
    model_event = alignment._append_model_event(
        store,
        case_id,
        report,
        "1",
        "criteria.json",
        old_version,
        criteria_data["criteria"][0],
        _model_payload(),
        "candidate-old-unaccepted",
    )
    alignment._write_store(case_id, store)

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "updated rule for unaccepted migration"
    services.save_criteria_file("criteria.json", rubric)
    assert not any(
        item.get("conflict_type") == "model_rubric_migration"
        for item in alignment.alignment_summary(case_id)["open_conflicts"]
    )
    migrated = alignment.run_consistency_audit_for_criterion(
        case_id,
        "criteria.json",
        "1",
        FakeConsistencyAuditEvaluator(),
    )
    assert migrated["model_regrade_report_names"] == [report]
    store = alignment._store(case_id)
    assert alignment._event_status(model_event["event_id"], store["decisions"]) == "superseded"
    projection = alignment.materialize_projection(case_id, report, store, "criteria.json")
    assert all(item.get("graded_by") != "model" for item in projection["criteria"])


def test_global_summary_includes_single_teacher_vertical_disagreement(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    report3 = "report-c"
    _add_report(tmp_path, case_id, report2, "validated model evidence")
    _add_report(tmp_path, case_id, report3, "not yet graded")
    services.save_case_report_grade(case_id, report, _payload(7.5, "teacher reason", "same anchor"))
    alignment.run_consistency_audit_for_criterion(
        case_id,
        "criteria.json",
        "1",
        FakeConsistencyAuditEvaluator({7.5}),
    )
    _, rubric_version, criteria_data = alignment._load_criteria("criteria.json")
    store = alignment._store(case_id)
    model_event = alignment._append_model_event(
        store,
        case_id,
        report2,
        "1",
        "criteria.json",
        rubric_version,
        criteria_data["criteria"][0],
        _model_payload("model reason", "validated model evidence"),
        "global-summary-candidate",
    )
    alignment._update_auto_grade_job(
        store,
        case_id=case_id,
        report_name=report3,
        criterion_id="1",
        rubric_file="criteria.json",
        rubric_version=rubric_version,
        status="failed",
        error="temporary failure",
    )
    alignment._write_store(case_id, store)

    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    item = summary["criterion_global_summaries"][0]
    assert item["total_reports"] == 3
    assert item["scored_count"] == 2
    assert item["statistical_count"] == 2
    assert item["average_score"] == pytest.approx(7.75)
    assert item["average_loss"] == pytest.approx(2.25)
    assert item["teacher_count"] == 1
    assert item["model_count"] == 1
    assert item["conflict_count"] == 0
    assert item["eligible_report_names"] == []
    assert item["retryable_report_names"] == [report3]
    by_report = {point["report_name"]: point for point in item["points"]}
    assert by_report[report]["conflict"] is False
    assert by_report[report]["included_in_statistics"] is True
    assert by_report[report]["anchor_text"] == "same anchor"
    assert by_report[report]["target_section"] is None
    assert by_report[report]["anchor_occurrence"] == 0
    assert by_report[report2]["event_id"] == model_event["event_id"]
    assert by_report[report2]["author"] == "model"
    assert by_report[report2]["anchor_text"] == "validated model evidence"


def test_global_summary_keeps_point_when_current_grade_has_no_evidence(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(
        case_id,
        report,
        _payload(6, "teacher reason", "validated pendulum evidence"),
    )
    store = alignment._store(case_id)
    store["events"][0]["authored_grade"]["anchor_text"] = ""
    store["events"][0]["authored_grade"]["evidence_anchors"] = []
    alignment._write_store(case_id, store)

    item = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")["criterion_global_summaries"][0]

    assert item["scored_count"] == 1
    assert len(item["points"]) == 1
    assert item["points"][0]["report_name"] == report
    assert item["points"][0]["anchor_text"] == ""
    readiness = alignment.alignment_summary(
        case_id, criteria_file_name="criteria.json"
    )["global_readiness"]
    assert readiness["allowed"] is True
    assert readiness["blockers"] == []


def test_saved_teacher_grade_pending_automatic_audit_does_not_block_global(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    point = summary["criterion_global_summaries"][0]["points"][0]
    assert point["acceptance_status"] == "pending"
    assert summary["global_readiness"]["allowed"] is True


def test_alignment_endpoint_filters_global_summary_by_selected_rubric(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8))
    client = TestClient(main.app)
    response = client.get(f"/cases/{case_id}/alignment", params={"criteria_file_name": "criteria.json"})
    assert response.status_code == 200, response.text
    items = response.json()["data"]["criterion_global_summaries"]
    assert {item["criterion_id"] for item in items} == {"1", "2"}


def _auto_grade_and_accept(case_id: str, report_name: str, criterion_id: str = "1"):
    store = alignment._store(case_id)
    _, _, criteria_data = alignment._load_criteria("criteria.json")
    if not any(
        event.get("grader_type") == "teacher"
        and str(event.get("criterion_id")) == str(criterion_id)
        for event in store["events"]
    ):
        reference_name = f"teacher-reference-{criterion_id}"
        reference_dir = (
            Path(alignment.CASE_ROOT)
            / case_id
            / "markdown"
            / reference_name
        )
        reference_dir.mkdir(parents=True, exist_ok=True)
        reference_text = "validated pendulum evidence"
        (reference_dir / f"{reference_name}.md").write_text(
            reference_text, encoding="utf-8"
        )
        (reference_dir / f"{reference_name}_text_only.md").write_text(
            reference_text, encoding="utf-8"
        )
        criterion = next(
            item
            for item in criteria_data["criteria"]
            if str(item["criteria_id"]) == str(criterion_id)
        )
        reference_score = 8.0 if str(criterion_id) == "1" else 4.0
        point_id = str(criterion["scoring_points"][0]["point_id"])
        services.save_case_report_grade(
            case_id,
            reference_name,
            CaseReportGradeRequest(
                criteria_id=str(criterion_id),
                criteria_score=reference_score,
                score_reason="same",
                anchor_text=reference_text,
                scoring_point_scores=[
                    {"point_id": point_id, "score": reference_score}
                ],
                criteria_file_name="criteria.json",
            ),
        )
        _audit_clean(case_id, str(criterion_id))
    result = alignment.auto_grade_report(
        case_id,
        report_name,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        [criterion_id],
        scheduling_reason="pregrade",
    )
    assert result["grades"]
    return alignment.accept_model_grade_event(
        case_id,
        report_name,
        CaseReportModelGradeAcceptRequest(
            criteria_id=criterion_id,
            criteria_file_name="criteria.json",
        ),
    )


def test_three_distinct_model_acceptances_make_only_that_criterion_reliable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)

    expected_rates = [2 / 3, 3 / 4, 4 / 5]
    for report_name, expected in zip([report1, report2, report3], expected_rates):
        accepted = _auto_grade_and_accept(case_id, report_name, "1")
        assert accepted["consistency"]["smoothed_agreement"] == pytest.approx(expected)

    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    by_id = {item["criterion_id"]: item for item in summary["consistency_states"]}
    assert by_id["1"]["state"] == "reliable"
    assert by_id["1"]["agreement_count"] == 3
    assert by_id["1"]["full_batch_recommended"] is True
    assert by_id["2"]["state"] == "calibrating"
    assert by_id["2"]["agreement_count"] == 0
    assert by_id["2"]["full_batch_recommended"] is False


def test_teacher_correction_supersedes_prior_acceptance_and_downgrades(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        _auto_grade_and_accept(case_id, report_name)

    services.save_case_report_grade(case_id, report3, _payload(7, "teacher correction"))
    state = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(case_id, criteria_file_name="criteria.json")["consistency_states"]
    }["1"]
    assert state["agreement_count"] == 2
    assert state["correction_count"] == 1
    assert state["smoothed_agreement"] == pytest.approx(0.6)
    assert state["state"] == "unreliable"
    assert state["review_rate"] == 1.0


def test_teacher_can_change_correction_back_to_model_total(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _auto_grade_and_accept(case_id, report)

    services.save_case_report_grade(case_id, report, _payload(7, "teacher correction"))
    services.save_case_report_grade(case_id, report, _payload(8, "teacher accepts model total"))

    state = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }["1"]
    assert state["agreement_count"] == 1
    assert state["correction_count"] == 0
    store = alignment._store(case_id)
    record = next(
        item
        for item in store["consistency"]["criteria"].values()
        if item["record_key"] == state["record_key"]
    )
    assert len(record["outcome_history"]) >= 1
    assert record["outcome_history"][-1]["verdict"] == "corrected"
    assert len(
        next(
            item
            for item in alignment._store(case_id)["consistency"]["criteria"].values()
            if item["record_key"] == state["record_key"]
        )["outcome_history"]
    ) == len(record["outcome_history"])


def test_manual_batch_authorization_is_override_then_correction_suspends_it(tmp_path, monkeypatch):
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    _auto_grade_and_accept(case_id, report1)
    authorization = alignment.authorize_criterion_batch(case_id, "criteria.json", "1")
    assert authorization["state"] == "batch_authorized"
    overridden = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(case_id, criteria_file_name="criteria.json")["consistency_states"]
    }["1"]
    assert overridden["state"] == "reliable"
    assert overridden["state_source"] == "teacher_override"
    assert overridden["agreement_count"] == 1

    services.save_case_report_grade(case_id, report1, _payload(7, "override correction"))
    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    corrected = {item["criterion_id"]: item for item in summary["consistency_states"]}["1"]
    auth = {item["criterion_id"]: item for item in summary["criterion_authorizations"]}["1"]
    assert corrected["state"] == "unreliable"
    assert corrected["state_source"] == "evidence"
    assert auth["state"] == "teacher_validation"
    assert auth["suspended_reason"] == "teacher_corrected_model_score"


def test_deleting_teacher_verdict_removes_consistency_evidence(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="pregrade",
    )
    services.save_case_report_grade(case_id, report, _payload(7, "correction"))
    before = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")["consistency_states"][0]
    assert before["correction_count"] == 1

    services.delete_case_report_grade(
        case_id,
        report,
        CaseReportGradeDeleteRequest(criteria_id="1", criteria_file_name="criteria.json"),
    )
    after = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")["consistency_states"][0]
    assert after["agreement_count"] == 0
    assert after["correction_count"] == 0
    assert after["state"] == "calibrating"


def test_legacy_accepted_model_migration_is_idempotent(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _auto_grade_and_accept(case_id, report)
    consistency_path = tmp_path / "case" / case_id / "alignment" / "consistency.json"
    consistency_path.unlink()

    first = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    second = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    first_state = {item["criterion_id"]: item for item in first["consistency_states"]}["1"]
    second_state = {item["criterion_id"]: item for item in second["consistency_states"]}["1"]

    assert first_state["agreement_count"] == 1
    assert first_state["correction_count"] == 0
    assert second_state["agreement_count"] == 1
    assert second_state["correction_count"] == 0


@pytest.mark.parametrize(
    "invalid_store",
    [[], {"schema_version": "future-v2", "criteria": {}}, {"schema_version": "criterion-consistency-v1", "criteria": []}],
)
def test_invalid_consistency_store_fails_explicitly(tmp_path, monkeypatch, invalid_store):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    alignment_root = tmp_path / "case" / case_id / "alignment"
    alignment_root.mkdir(parents=True, exist_ok=True)
    (alignment_root / "consistency.json").write_text(json.dumps(invalid_store), encoding="utf-8")

    with pytest.raises(alignment.CorruptAlignmentStore):
        alignment._store(case_id)


def test_unique_legacy_teacher_correction_is_rebuilt(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="pregrade",
    )
    services.save_case_report_grade(case_id, report, _payload(7, "legacy correction"))
    consistency_path = tmp_path / "case" / case_id / "alignment" / "consistency.json"
    consistency_path.unlink()

    state = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }["1"]

    assert state["agreement_count"] == 0
    assert state["correction_count"] == 1
    assert state["state"] == "unreliable"


def test_criterion_content_change_starts_new_calibration_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, criteria_dir = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        _auto_grade_and_accept(case_id, report_name)
    previous = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }["1"]

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "refined rule one"
    services.save_criteria_file("criteria.json", rubric)
    state = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }["1"]

    assert state["state"] == "calibrating"
    assert state["agreement_count"] == 0
    assert state["correction_count"] == 0
    assert state["record_key"] != previous["record_key"]
    assert any(
        record["record_key"] == previous["record_key"]
        for record in alignment._store(case_id)["consistency"]["criteria"].values()
    )


def test_changing_one_structured_criterion_preserves_other_criterion_reliability(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, criteria_dir = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        graded = alignment.auto_grade_report(
            case_id,
            report_name,
            "criteria.json",
            CriterionAwareFakeModel(),
            FakeEvaluator(),
            ["1", "2"],
            scheduling_reason="pregrade",
        )
        assert len(graded["grades"]) == 2
        for criterion_id in ["1", "2"]:
            alignment.accept_model_grade_event(
                case_id,
                report_name,
                CaseReportModelGradeAcceptRequest(
                    criteria_id=criterion_id,
                    criteria_file_name="criteria.json",
                ),
            )

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][1]["scoring_points"][0]["deduction_rules"][0]["condition"] = (
        "refined rule two is not met"
    )
    services.save_criteria_file("criteria.json", rubric)
    states = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }

    assert states["1"]["state"] == "reliable"
    assert states["1"]["agreement_count"] == 3
    assert states["2"]["state"] == "calibrating"
    assert states["2"]["agreement_count"] == 0


def test_changed_card_revision_targets_every_report_and_leaves_other_card_results(
    tmp_path, monkeypatch
):
    case_id, report1, criteria_dir = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        graded = alignment.auto_grade_report(
            case_id,
            report_name,
            "criteria.json",
            CriterionAwareFakeModel(),
            FakeEvaluator(),
            ["1", "2"],
            scheduling_reason="pregrade",
        )
        assert len(graded["grades"]) == 2

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["scoring_points"][0]["deduction_rules"][0]["condition"] = (
        "updated nested rule requires complete Card re-evaluation"
    )
    services.save_criteria_file("criteria.json", rubric)

    store = alignment._store(case_id)
    revision = next(iter(store["criterion_revisions"].values()))
    assert revision["criterion_id"] == "1"
    assert revision["report_names"] == [report1, report2, report3]
    assert revision["status"] == "awaiting_audit"
    for report_name in [report1, report2, report3]:
        projection = alignment.materialize_projection(
            case_id, report_name, store, "criteria.json"
        )
        assert {item["criteria_id"] for item in projection["criteria"]} == {"2"}


def test_changed_card_supersedes_only_matching_targets_in_running_batch(
    tmp_path, monkeypatch
):
    case_id, _, criteria_dir = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    batch = alignment.create_auto_grade_batch(case_id, "criteria.json")
    alignment.confirm_auto_grade_batch(case_id, batch["batch_id"])
    with alignment._lock_for(case_id):
        store = alignment._store(case_id)
        store["auto_grade_batches"][batch["batch_id"]]["status"] = "running"
        alignment._write_store(case_id, store)

    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "updated complete Card"
    services.save_criteria_file("criteria.json", rubric)

    current = alignment._store(case_id)["auto_grade_batches"][batch["batch_id"]]
    by_criterion = {}
    for target in current["targets"]:
        by_criterion.setdefault(target["criterion_id"], set()).add(target["status"])
    assert by_criterion["1"] == {"superseded"}
    assert by_criterion["2"] == {"pending"}
    assert current["status"] == "running"


def test_scorer_revision_change_starts_new_calibration_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        _auto_grade_and_accept(case_id, report_name)
    previous = alignment.alignment_summary(
        case_id, criteria_file_name="criteria.json"
    )["consistency_states"][0]

    monkeypatch.setattr(alignment, "CONSISTENCY_SCORER_REVISION", "prompt-v2")
    current = alignment.alignment_summary(
        case_id, criteria_file_name="criteria.json"
    )["consistency_states"][0]

    assert previous["state"] == "reliable"
    assert current["state"] == "calibrating"
    assert current["record_key"] != previous["record_key"]
    assert current["agreement_count"] == 0


def test_pending_rubric_review_keeps_current_evidence_visible_but_pauses_full_batch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        alignment, "_confirmed_model_learning_events", lambda *args, **kwargs: []
    )
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)
    for report_name in [report1, report2, report3]:
        _auto_grade_and_accept(case_id, report_name)
    store = alignment._store(case_id)
    store["readiness"]["criteria"]["1"] = {
        "criterion_id": "1",
        "lifecycle": "evaluating",
        "reason": "pending_rubric_update",
    }
    alignment._write_store(case_id, store)

    state = {
        item["criterion_id"]: item
        for item in alignment.alignment_summary(
            case_id, criteria_file_name="criteria.json"
        )["consistency_states"]
    }["1"]

    assert state["state"] == "reliable"
    assert state["agreement_count"] == 3
    assert state["full_batch_recommended"] is False


def test_full_batch_is_blocked_while_calibrating_but_pregrade_and_repair_are_allowed(
    tmp_path, monkeypatch
):
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2, report3 = "report-b", "report-c"
    _add_report(tmp_path, case_id, report2)
    _add_report(tmp_path, case_id, report3)

    blocked = alignment.auto_grade_report(
        case_id,
        report1,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="backfill",
    )
    assert not blocked["grades"]
    assert blocked["abstentions"][0]["reason"] == "consistency_full_batch_not_authorized"

    pregrade = alignment.auto_grade_report(
        case_id,
        report2,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="pregrade",
    )
    repair = alignment.auto_grade_report(
        case_id,
        report3,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="repair",
    )
    assert len(pregrade["grades"]) == 1
    assert len(repair["grades"]) == 1


def test_open_conflict_blocks_new_full_batch_even_after_teacher_override(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _auto_grade_and_accept(case_id, report)
    alignment.authorize_criterion_batch(case_id, "criteria.json", "1")
    store = alignment._store(case_id)
    store["conflicts"].append(
        {
            "conflict_id": "conflict-consistency-test",
            "conflict_type": "teacher_teacher",
            "criterion_id": "1",
            "criteria_file_name": "criteria.json",
            "status": "open",
        }
    )
    alignment._write_store(case_id, store)
    _, rubric_version, rubric = alignment._load_criteria("criteria.json")

    assert not alignment._criterion_full_batch_allowed(
        alignment._store(case_id),
        case_id=case_id,
        criteria_file_name="criteria.json",
        rubric_version=rubric_version,
        criterion=rubric["criteria"][0],
    )


def test_legacy_mode_preserves_unconditional_existing_backfill_behavior(tmp_path, monkeypatch):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(alignment, "CONSISTENCY_AUTOMATION_MODE", "legacy")

    result = alignment.auto_grade_report(
        case_id,
        report,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="backfill",
    )
    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")

    assert len(result["grades"]) == 1
    assert summary["consistency_states"] == []
    assert summary["review_assignments"] == []


def test_shadow_mode_records_evidence_without_changing_backfill_behavior(tmp_path, monkeypatch):
    case_id, report1, _ = _case(tmp_path, monkeypatch)
    report2 = "report-b"
    _add_report(tmp_path, case_id, report2)
    monkeypatch.setattr(alignment, "CONSISTENCY_AUTOMATION_MODE", "shadow")
    _auto_grade_and_accept(case_id, report1)

    summary = alignment.alignment_summary(case_id, criteria_file_name="criteria.json")
    state = {item["criterion_id"]: item for item in summary["consistency_states"]}["1"]
    result = alignment.auto_grade_report(
        case_id,
        report2,
        "criteria.json",
        FakeModel(),
        FakeEvaluator(),
        ["1"],
        scheduling_reason="backfill",
    )

    assert state["agreement_count"] == 1
    assert state["full_batch_recommended"] is False
    assert len(result["grades"]) == 1
