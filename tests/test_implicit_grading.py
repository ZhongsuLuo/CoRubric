from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.criteria_lock import criteria_file_lock
from backend.app import alignment, implicit_grading, main, services
from backend.app.schemas import (
    CriterionProposalConfirmRequest,
    CriterionProposalRejectRequest,
    CriteriaItem,
    EvidenceAnchor,
    ImplicitGradeCreateRequest,
)
from tests.test_alignment import FakeEvaluator, FakeModel, _case


def _scoring_point(point_id: str, max_score: float) -> dict:
    return {
        "point_id": point_id,
        "summary": "Structured scoring point",
        "deduction_rules": [{
            "rule_id": f"{point_id}.r1",
            "condition": "The required evidence is missing or insufficient.",
            "deduct": max_score,
        }],
        "max_deduction": max_score,
        "aggregation": "max_triggered",
    }


class FakeInductionModel:
    def __init__(self, proposal_type: str = "create_criterion"):
        self.proposal_type = proposal_type

    def propose(self, context):
        self.context = context
        target = None
        if self.proposal_type == "update_criterion":
            target = context["current_rubric"]["criteria"][0]["criteria_id"]
        implicit_ids = [item["implicit_grade_id"] for item in context["implicit_grades"]]
        return {
            "proposal_type": self.proposal_type,
            "target_criterion_id": target,
            "criteria_name": "Evidence interpretation",
            "description": "Award points for interpreting the selected evidence correctly.",
            "original_description": "Evidence interpretation",
            "max_score": 4,
            "section": "analysis",
            "scoring_points": [_scoring_point("evidence_interpretation.p1", 4)],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
            "source_grade_assignments": [
                {
                    "implicit_grade_id": implicit_id,
                    "generated_score_reason": "The selected evidence supports the saved teacher score.",
                    "scoring_point_scores": [{"point_id": "evidence_interpretation.p1", "score": 3}],
                    "matched_scoring_points": ["evidence_interpretation.p1"],
                    "matched_deduction_rules": [],
                }
                for implicit_id in implicit_ids
            ],
            "rationale": "The implicit grades all concern how evidence is interpreted.",
            "confidence": 0.8,
        }


class IncompleteStructureModel(FakeInductionModel):
    def propose(self, context):
        data = super().propose(context)
        data["scoring_points"] = None
        data["scoring_aggregation"] = None
        data["source_grade_assignments"] = None
        return data


class FailingInductionModel:
    def propose(self, context):
        raise RuntimeError("induction unavailable")


class TargetedUpdateModel(FakeInductionModel):
    def __init__(self, target_criterion_id: str):
        super().__init__("update_criterion")
        self.target_criterion_id = target_criterion_id

    def propose(self, context):
        data = super().propose(context)
        data["target_criterion_id"] = self.target_criterion_id
        data["criteria_name"] = f"Updated criterion {self.target_criterion_id}"
        data["description"] = f"Updated scoring logic for criterion {self.target_criterion_id}."
        data["original_description"] = f"Updated criterion {self.target_criterion_id}"
        return data


class CreateNamedLikeExistingModel(FakeInductionModel):
    def propose(self, context):
        data = super().propose(context)
        data["proposal_type"] = "create_criterion"
        data["target_criterion_id"] = None
        data["criteria_name"] = context["current_rubric"]["criteria"][0]["criteria_name"]
        return data


class CreatePartiallyNamedLikeExistingModel(FakeInductionModel):
    def propose(self, context):
        data = super().propose(context)
        data["proposal_type"] = "create_criterion"
        data["target_criterion_id"] = None
        data["criteria_name"] = f"{context['current_rubric']['criteria'][0]['criteria_name']} formula reasoning"
        data["section"] = "principle"
        return data


class CandidateWritingModel(FakeModel):
    def grade(self, report_text, criterion, boundary, examples):
        return {
            "criteria_score": 3,
            "score_reason": "Evidence is interpreted but not fully justified.",
            "anchor_text": "validated pendulum evidence",
            "target_section": criterion.get("section"),
        }


def _patch_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case_id, report, criteria_dir = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(implicit_grading, "CASE_ROOT", tmp_path / "case")
    monkeypatch.setattr(implicit_grading, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: FakeEvaluator())
    monkeypatch.setattr(alignment, "LiveGradingModel", lambda: FakeModel())
    return case_id, report, criteria_dir


def _implicit_request(report: str) -> ImplicitGradeCreateRequest:
    return ImplicitGradeCreateRequest(
        report_name=report,
        anchor=EvidenceAnchor(text="validated pendulum evidence"),
        score=3,
        max_score=4,
        notes="Evidence is interpreted but not fully justified.",
        temporary_label="evidence interpretation",
        criteria_file_name="criteria.json",
        request_id="req-1",
    )


def test_implicit_grade_persists_without_alignment_event(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    assert item["status"] == "queued"
    assert item["implicit_grade_id"].startswith("ig_")
    assert item["source_digest"]
    assert (tmp_path / "case" / case_id / "implicit_grades.json").exists()
    assert not (tmp_path / "case" / case_id / "alignment" / "events.json").exists()


def test_live_induction_repairs_an_incomplete_model_response(monkeypatch):
    from backend.libs import config as model_config

    complete = {
        "proposal_type": "create_criterion",
        "target_criterion_id": None,
        "criteria_name": "报告基本信息",
        "description": "报告应完整呈现姓名、学号、课程和实验名称。",
        "original_description": "报告基本信息完整",
        "max_score": 4,
        "section": "基本信息",
        "scoring_points": [_scoring_point("report_identity.p1", 4)],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        "source_grade_assignments": [],
        "rationale": "教师选择并评分了报告基本信息。",
        "confidence": 0.8,
    }
    responses = [
        json.dumps({"proposal_type": "create_criterion", "criteria_name": None}),
        json.dumps(complete, ensure_ascii=False),
    ]

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=responses.pop(0)))]
            )

    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    config = SimpleNamespace(provider="fake", model="fake", request_options=lambda: {})
    monkeypatch.setattr(model_config, "GRADING_MODEL_CONFIG", config)
    monkeypatch.setattr(model_config, "get_chat_client", lambda provider: client)

    proposal = implicit_grading.LiveInductionModel().propose({
        "current_rubric": {"language": "zh", "criteria": []},
        "implicit_grades": [{"implicit_grade_id": "ig-1", "score": 4}],
    })

    assert proposal == complete
    assert len(completions.calls) == 2
    repair_request = json.loads(completions.calls[1]["messages"][1]["content"])
    assert "criteria_name" in repair_request["missing_or_invalid_fields"]
    assert "scoring_points" in repair_request["missing_or_invalid_fields"]
    assert "section" in repair_request["missing_or_invalid_fields"]


def test_implicit_grade_api_returns_before_induction_finishes(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def blocking_induction(case_id_arg, criteria_file_name):
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(main, "run_induction_for_case", blocking_induction)
    client = TestClient(main.app)

    start = time.monotonic()
    response = client.post(
        f"/cases/{case_id}/implicit-grades",
        json=_implicit_request(report).model_dump(mode="json"),
    )
    elapsed = time.monotonic() - start
    release.set()

    assert response.status_code == 200
    assert response.json()["data"]["item"]["status"] == "queued"
    assert elapsed < 0.5
    assert started.wait(timeout=1)


def test_archived_implicit_grade_does_not_block_readding_same_source(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    first = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    archived = implicit_grading.archive_implicit_grade(case_id, first["implicit_grade_id"])
    second = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    items = implicit_grading.list_implicit_grades(case_id)

    assert archived["status"] == "archived"
    assert second["implicit_grade_id"] != first["implicit_grade_id"]
    assert second["status"] == "queued"
    assert [item["status"] for item in items].count("archived") == 1
    assert [item["status"] for item in items].count("queued") == 1


def test_confirmed_implicit_grade_does_not_block_readding_same_source(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    first = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
        confirmed_by="teacher-a",
    )

    implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req, FakeEvaluator(), FakeModel())
    second = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    items = implicit_grading.list_implicit_grades(case_id)

    assert second["implicit_grade_id"] != first["implicit_grade_id"]
    assert second["status"] == "queued"
    assert [item["status"] for item in items].count("confirmed") == 1
    assert [item["status"] for item in items].count("queued") == 1


def test_induction_creates_pending_proposal_and_marks_sources_ready(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    assert proposal
    assert proposal["status"] == "pending"
    assert proposal["source_implicit_grade_ids"] == [item["implicit_grade_id"]]
    assert proposal["source_grade_assignments"][0]["generated_score_reason"] == "The selected evidence supports the saved teacher score."
    assert proposal["suggested_criterion"]["scoring_points"]
    assert proposal["suggested_criterion"]["scoring_points"][0]["deduction_rules"]
    reloaded = implicit_grading.list_implicit_grades(case_id)[0]
    assert reloaded["status"] == "proposal_ready"
    assert reloaded["proposal_id"] == proposal["proposal_id"]
    assert not (tmp_path / "case" / case_id / "alignment" / "events.json").exists()


def test_confirm_create_promotes_implicit_grade_to_alignment_event_idempotently(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    suggested = CriteriaItem(**proposal["suggested_criterion"])
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=suggested,
        criteria_file_name="criteria.json",
        confirmed_by="teacher-a",
    )
    confirmed = implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req, FakeEvaluator(), FakeModel())
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_event_ids"]
    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    saved = next(item for item in criteria["criteria"] if item["criteria_id"] == suggested.criteria_id)
    assert saved["scoring_points"]
    events = json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())
    assert len(events) == 1
    assert events[0]["criterion_id"] == suggested.criteria_id
    assert events[0]["authored_grade"]["score_reason"] == "Evidence is interpreted but not fully justified."
    point_scores = events[0]["authored_grade"]["scoring_point_scores"]
    assert point_scores
    assert sum(item["score"] for item in point_scores) == events[0]["authored_grade"]["criteria_score"]
    implicit_item = implicit_grading.list_implicit_grades(case_id)[0]
    assert implicit_item["status"] == "confirmed"
    assert implicit_item["promoted_criterion_id"] == suggested.criteria_id
    assert implicit_item["confirmed_event_id"] == events[0]["event_id"]
    again = implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req, FakeEvaluator(), FakeModel())
    assert again["confirmed_event_ids"] == confirmed["confirmed_event_ids"]
    assert len(json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())) == 1


def test_confirmed_proposal_starts_registered_revision_workers(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    started_jobs = []
    monkeypatch.setattr(
        implicit_grading,
        "ensure_criterion_revision_workers_for_jobs",
        lambda jobs: started_jobs.extend(jobs) or len(jobs),
    )
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(
        case_id, "criteria.json", FakeInductionModel()
    )

    implicit_grading.confirm_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )

    assert started_jobs
    assert started_jobs[0]["case_id"] == case_id
    assert started_jobs[0]["criteria_file_name"] == "criteria.json"


def test_implicit_review_uses_model_reason_when_teacher_left_reason_blank(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(
        case_id,
        _implicit_request(report).model_copy(update={"notes": None, "temporary_label": None}),
    )
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    assignment = proposal["source_grade_assignments"][0]
    assert assignment["generated_score_reason"] == "The selected evidence supports the saved teacher score."

    implicit_grading.confirm_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )
    events = json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())
    assert events[0]["authored_grade"]["score_reason"] == assignment["generated_score_reason"]
    assert "Uninduced grading" not in events[0]["authored_grade"]["score_reason"]


def test_induction_converts_create_to_update_when_existing_criterion_matches(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", CreateNamedLikeExistingModel())
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
    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    updated = next(item for item in criteria["criteria"] if item["criteria_id"] == "1")
    events = json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())

    assert proposal["proposal_type"] == "update_criterion"
    assert proposal["target_criterion_id"] == "1"
    assert confirmed["status"] == "confirmed"
    assert events[0]["criterion_id"] == "1"
    assert len(updated["few_shot_examples"]) == 1


def test_teacher_can_retarget_proposal_to_a_different_existing_criterion_during_review(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())

    confirmed = implicit_grading.confirm_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
            target_criterion_id="2",
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )

    criteria = json.loads((criteria_dir / "criteria.json").read_text())
    events = json.loads((tmp_path / "case" / case_id / "alignment" / "events.json").read_text())
    assert confirmed["proposal_type"] == "update_criterion"
    assert confirmed["target_criterion_id"] == "2"
    assert len(criteria["criteria"]) == 2
    assert events[0]["criterion_id"] == "2"


def test_confirm_rejects_a_new_max_score_below_saved_teacher_score(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    edited = proposal["suggested_criterion"]
    edited["max_score"] = 2
    edited["scoring_points"] = [_scoring_point("evidence_interpretation.p1", 2)]
    edited["scoring_aggregation"] = {"mode": "sum_deductions_capped", "cap": 2}

    with pytest.raises(ValueError, match="cannot be lower than the saved teacher score"):
        implicit_grading.confirm_proposal(
            case_id,
            proposal["proposal_id"],
            CriterionProposalConfirmRequest(
                proposal_version=proposal["proposal_version"],
                suggested_criterion=CriteriaItem(**edited),
                criteria_file_name="criteria.json",
            ),
            FakeEvaluator(),
            FakeModel(),
        )


def test_induction_does_not_merge_partial_name_match_into_existing_criterion(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", CreatePartiallyNamedLikeExistingModel())

    assert proposal["proposal_type"] == "create_criterion"
    assert proposal["target_criterion_id"] is None
    assert proposal["suggested_criterion"]["criteria_id"] != "1"


def test_empty_rubric_induction_repairs_missing_model_structure(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    empty_rubric = {
        "schema_version": "structured-rubric-v1",
        "title": "Empty rubric",
        "background": "",
        "language": "zh",
        "base_score": 100,
        "criteria": [],
    }
    (criteria_dir / "criteria.json").write_text(
        json.dumps(empty_rubric, ensure_ascii=False),
        encoding="utf-8",
    )
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))

    proposal = implicit_grading.run_induction_for_case(
        case_id,
        "criteria.json",
        IncompleteStructureModel(),
    )

    assert proposal["proposal_type"] == "create_criterion"
    assert proposal["target_criterion_id"] is None
    suggested = proposal["suggested_criterion"]
    assert suggested["scoring_points"][0]["deduction_rules"][0]["deduct"] == 4
    assert suggested["scoring_aggregation"] == {
        "mode": "sum_deductions_capped",
        "cap": 4,
        "score_formula": None,
    }
    reloaded = implicit_grading.list_implicit_grades(case_id)[0]
    assert reloaded["implicit_grade_id"] == item["implicit_grade_id"]
    assert reloaded["status"] == "proposal_ready"
    assert reloaded["failure_reason"] is None


def test_existing_criterion_induction_keeps_structure_when_model_omits_it(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    existing = json.loads((criteria_dir / "criteria.json").read_text(encoding="utf-8"))["criteria"][0]
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))

    proposal = implicit_grading.run_induction_for_case(
        case_id,
        "criteria.json",
        IncompleteStructureModel("update_criterion"),
    )

    assert proposal["proposal_type"] == "update_criterion"
    assert proposal["target_criterion_id"] == existing["criteria_id"]
    assert proposal["suggested_criterion"]["scoring_points"] == existing["scoring_points"]
    assert proposal["suggested_criterion"]["scoring_aggregation"] == existing["scoring_aggregation"]


def test_failed_implicit_grade_can_be_retried_without_losing_teacher_input(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    original = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    assert implicit_grading.run_induction_for_case(case_id, "criteria.json", FailingInductionModel()) is None

    failed = implicit_grading.list_implicit_grades(case_id)[0]
    assert failed["status"] == "failed"
    assert "induction unavailable" in failed["failure_reason"]

    retried = implicit_grading.retry_implicit_grade(case_id, original["implicit_grade_id"])

    assert retried["status"] == "queued"
    assert retried["failure_reason"] is None
    assert retried["anchor"] == original["anchor"]
    assert retried["score"] == original["score"]


def test_rejected_implicit_grade_can_be_induced_again(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    original = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    implicit_grading.reject_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalRejectRequest(proposal_version=proposal["proposal_version"], reason="Retry test"),
    )

    retried = implicit_grading.retry_implicit_grade(case_id, original["implicit_grade_id"])

    assert retried["status"] == "queued"
    assert retried["proposal_id"] is None


def test_retry_endpoint_queues_induction_for_the_original_rubric(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    implicit_grading.run_induction_for_case(case_id, "criteria.json", FailingInductionModel())
    calls = []
    monkeypatch.setattr(main, "_run_induction_async", lambda *args: calls.append(args))
    client = TestClient(main.app)

    response = client.post(f"/cases/{case_id}/implicit-grades/{item['implicit_grade_id']}/retry")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["item"]["status"] == "queued"
    assert calls == [(case_id, "criteria.json")]


def test_induction_merges_same_pending_proposal_across_reports(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    other_report = "report-b"
    other_dir = tmp_path / "case" / case_id / "markdown" / other_report
    other_dir.mkdir(parents=True)
    (other_dir / f"{other_report}.md").write_text("# report b\nvalidated pendulum evidence\n", encoding="utf-8")
    (other_dir / f"{other_report}_text_only.md").write_text("validated pendulum evidence\n", encoding="utf-8")

    first = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    first_proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    second_req = _implicit_request(other_report).model_copy(update={"request_id": "req-2"})
    second = implicit_grading.create_implicit_grade(case_id, second_req)
    merged_proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    proposals = implicit_grading.list_criterion_proposals(case_id, criteria_file_name="criteria.json")
    items = {
        item["implicit_grade_id"]: item
        for item in implicit_grading.list_implicit_grades(case_id)
    }

    assert first_proposal["proposal_id"] == merged_proposal["proposal_id"]
    assert len(proposals) == 1
    assert proposals[0]["source_implicit_grade_ids"] == [
        first["implicit_grade_id"],
        second["implicit_grade_id"],
    ]
    assert items[first["implicit_grade_id"]]["proposal_id"] == proposals[0]["proposal_id"]
    assert items[second["implicit_grade_id"]]["proposal_id"] == proposals[0]["proposal_id"]


def test_confirm_rejects_stale_proposal_version(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    req = CriterionProposalConfirmRequest(
        proposal_version=999,
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )
    with pytest.raises(implicit_grading.StaleProposalError):
        implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)


def test_confirm_rejects_when_rubric_changed_since_proposal(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel("update_criterion"))
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "changed after proposal"
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )
    with pytest.raises(implicit_grading.StaleProposalError, match="criteria file changed"):
        implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)


def test_confirm_create_allows_unrelated_rubric_growth_since_proposal(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"].append(
        {
            "criteria_id": "later-added",
            "criteria_name": "Later added standard",
            "description": "A standard confirmed after this proposal was created.",
            "original_description": "Later added standard",
            "max_score": 4,
            "section": "analysis",
            "few_shot_examples": [],
            "scoring_points": [_scoring_point("later-added.p1", 4)],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        }
    )
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )

    confirmed = implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)
    updated = json.loads((criteria_dir / "criteria.json").read_text())

    assert confirmed["status"] == "confirmed"
    assert any(item["criteria_id"] == "later-added" for item in updated["criteria"])
    assert any(item["criteria_name"] == "Evidence interpretation" for item in updated["criteria"])


def test_confirm_update_allows_unrelated_criterion_change_since_proposal(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    rubric_path = criteria_dir / "criteria.json"
    rubric = json.loads(rubric_path.read_text())
    rubric["criteria"].append(
        {
            "criteria_id": "2",
            "criteria_name": "Formula reasoning",
            "description": "Check formula reasoning.",
            "original_description": "Formula reasoning",
            "max_score": 4,
            "section": "analysis",
            "few_shot_examples": [],
            "scoring_points": [_scoring_point("2.p1", 4)],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        }
    )
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")

    implicit_grading.create_implicit_grade(
        case_id,
        _implicit_request(report).model_copy(update={
            "request_id": "title",
            "temporary_label": "title check",
            "notes": "Title is present.",
        }),
    )
    first_proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", TargetedUpdateModel("1"))
    implicit_grading.create_implicit_grade(
        case_id,
        _implicit_request(report).model_copy(update={
            "request_id": "formula",
            "temporary_label": "formula check",
            "notes": "Formula is justified.",
            "score": 4,
        }),
    )
    second_proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", TargetedUpdateModel("2"))

    implicit_grading.confirm_proposal(
        case_id,
        first_proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=first_proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**first_proposal["suggested_criterion"]),
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )
    confirmed_second = implicit_grading.confirm_proposal(
        case_id,
        second_proposal["proposal_id"],
        CriterionProposalConfirmRequest(
            proposal_version=second_proposal["proposal_version"],
            suggested_criterion=CriteriaItem(**second_proposal["suggested_criterion"]),
            target_criterion_id="2",
            criteria_file_name="criteria.json",
        ),
        FakeEvaluator(),
        FakeModel(),
    )
    updated = json.loads(rubric_path.read_text())

    assert confirmed_second["status"] == "confirmed"
    assert next(item for item in updated["criteria"] if item["criteria_id"] == "1")["criteria_name"] == "Updated criterion 1"
    assert next(item for item in updated["criteria"] if item["criteria_id"] == "2")["criteria_name"] == "Updated criterion 2"


def test_confirm_rejects_mismatched_criteria_file(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="other.json",
    )
    with pytest.raises(implicit_grading.StaleProposalError, match="different criteria file"):
        implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)


def test_induction_caps_batch_and_anchor_context_and_does_not_retry_failed(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    long_text = "validated pendulum evidence " * 1000
    report_dir = tmp_path / "case" / case_id / "markdown" / report
    (report_dir / f"{report}_text_only.md").write_text(long_text, encoding="utf-8")
    for idx in range(implicit_grading.MAX_INDUCTION_BATCH + 2):
        req = _implicit_request(report).model_copy(update={"request_id": f"req-{idx}", "score": idx + 1})
        item = implicit_grading.create_implicit_grade(case_id, req)
        if idx == implicit_grading.MAX_INDUCTION_BATCH + 1:
            implicit_grading.update_implicit_grade(
                case_id,
                item["implicit_grade_id"],
                implicit_grading.ImplicitGradeUpdateRequest(status="failed"),
                allow_lifecycle=True,
            )
    model = FakeInductionModel()
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", model)
    assert proposal
    assert len(model.context["implicit_grades"]) == implicit_grading.MAX_INDUCTION_BATCH
    assert all(len(item["anchor_context"]) <= implicit_grading.MAX_ANCHOR_CONTEXT_CHARS for item in model.context["implicit_grades"])
    failed = [item for item in implicit_grading.list_implicit_grades(case_id) if item["status"] == "failed"]
    assert failed
    assert failed[0]["proposal_id"] is None


def test_induction_anchor_context_is_centered_on_selected_text(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    report_dir = tmp_path / "case" / case_id / "markdown" / report
    report_text = "HEADER STUDENT METADATA\n" + ("filler " * 500) + "A = epsilon b c\nnearby principle text"
    (report_dir / f"{report}_text_only.md").write_text(report_text, encoding="utf-8")
    req = _implicit_request(report).model_copy(update={
        "anchor": EvidenceAnchor(text="A = epsilon b c"),
        "notes": "Lambert-Beer law formula is correct.",
    })
    implicit_grading.create_implicit_grade(case_id, req)
    model = FakeInductionModel()
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", model)

    assert proposal
    context = model.context["implicit_grades"][0]["anchor_context"]
    assert "A = epsilon b c" in context
    assert "nearby principle text" in context
    assert "HEADER STUDENT METADATA" not in context


def test_induction_context_bounds_anchor_notes_labels_and_rubric(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    rubric = json.loads((criteria_dir / "criteria.json").read_text())
    rubric["criteria"][0]["description"] = "rubric detail " * 1000
    rubric["criteria"][0]["original_description"] = "original detail " * 1000
    (criteria_dir / "criteria.json").write_text(json.dumps(rubric), encoding="utf-8")
    req = _implicit_request(report).model_copy(update={
        "anchor": EvidenceAnchor(text="anchor text " * 600),
        "notes": "teacher note " * 300,
        "temporary_label": "label " * 80,
    })
    implicit_grading.create_implicit_grade(case_id, req)
    model = FakeInductionModel()
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", model)
    assert proposal
    item = model.context["implicit_grades"][0]
    assert len(item["anchor"]["text"]) <= implicit_grading.MAX_ANCHOR_TEXT_CHARS
    assert len(item["notes"]) <= implicit_grading.MAX_NOTES_CHARS
    assert len(item["temporary_label"]) <= implicit_grading.MAX_LABEL_CHARS
    compact = model.context["current_rubric"]["criteria"][0]
    assert len(compact["description"]) <= implicit_grading.MAX_RUBRIC_FIELD_CHARS
    assert len(compact["original_description"]) <= implicit_grading.MAX_RUBRIC_FIELD_CHARS


def test_implicit_grade_create_request_only_accepts_teacher_source(report="r1"):
    with pytest.raises(ValidationError):
        ImplicitGradeCreateRequest(
            report_name=report,
            anchor=EvidenceAnchor(text="evidence"),
            score=1,
            source="model",
        )


def test_confirm_rolls_back_criteria_when_promotion_fails(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    before = json.loads((criteria_dir / "criteria.json").read_text())
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    monkeypatch.setattr(implicit_grading, "append_teacher_grade_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )
    with pytest.raises(RuntimeError, match="boom"):
        implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)
    assert json.loads((criteria_dir / "criteria.json").read_text()) == before
    failed = implicit_grading.list_criterion_proposals(case_id)[0]
    assert failed["status"] == "failed"
    assert "promotion_failed" in failed["failure_reason"]


def test_confirm_restores_alignment_when_later_source_promotion_fails(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    before_criteria = json.loads((criteria_dir / "criteria.json").read_text())
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report).model_copy(update={"request_id": "one"}))
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report).model_copy(update={
        "request_id": "two",
        "anchor": EvidenceAnchor(text="second evidence"),
        "notes": "second note",
    }))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    alignment_root = tmp_path / "case" / case_id / "alignment"
    projection_path = tmp_path / "case" / case_id / "grade_result" / f"{report}_grade.json"
    real_append = implicit_grading.append_teacher_grade_event
    calls = 0

    def fake_append(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            result = real_append(args[0], args[1], args[2], FakeEvaluator(), CandidateWritingModel())
            assert any((alignment_root / "candidates").rglob("*.manifest.json"))
            return result
        raise RuntimeError("second promotion failed")

    monkeypatch.setattr(implicit_grading, "append_teacher_grade_event", fake_append)
    req = CriterionProposalConfirmRequest(
        proposal_version=proposal["proposal_version"],
        suggested_criterion=CriteriaItem(**proposal["suggested_criterion"]),
        criteria_file_name="criteria.json",
    )
    with pytest.raises(RuntimeError, match="second promotion failed"):
        implicit_grading.confirm_proposal(case_id, proposal["proposal_id"], req)
    assert json.loads((criteria_dir / "criteria.json").read_text()) == before_criteria
    assert not (alignment_root / "events.json").exists()
    assert not projection_path.exists()
    assert not any((alignment_root / "candidates").rglob("*")) if (alignment_root / "candidates").exists() else True
    failed = implicit_grading.list_criterion_proposals(case_id)[0]
    assert failed["status"] == "failed"


def test_cannot_mutate_implicit_source_after_proposal_ready(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    with pytest.raises(implicit_grading.StaleProposalError, match="already linked"):
        implicit_grading.update_implicit_grade(
            case_id,
            item["implicit_grade_id"],
            implicit_grading.ImplicitGradeUpdateRequest(notes="changed after proposal"),
        )


def test_public_implicit_patch_cannot_mutate_lifecycle_fields(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    item = implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    with pytest.raises(ValueError, match="lifecycle"):
        implicit_grading.update_implicit_grade(
            case_id,
            item["implicit_grade_id"],
            implicit_grading.ImplicitGradeUpdateRequest(status="confirmed", proposal_id="prop_fake"),
        )


def test_reject_proposal_leaves_implicit_grade_uninduced_and_does_not_mutate_criteria(tmp_path, monkeypatch):
    case_id, report, criteria_dir = _patch_roots(tmp_path, monkeypatch)
    before = (criteria_dir / "criteria.json").read_text()
    implicit_grading.create_implicit_grade(case_id, _implicit_request(report))
    proposal = implicit_grading.run_induction_for_case(case_id, "criteria.json", FakeInductionModel())
    rejected = implicit_grading.reject_proposal(
        case_id,
        proposal["proposal_id"],
        CriterionProposalRejectRequest(proposal_version=proposal["proposal_version"], reason="bad cluster"),
    )
    assert rejected["status"] == "rejected"
    assert implicit_grading.list_implicit_grades(case_id)[0]["status"] == "uninduced"
    assert (criteria_dir / "criteria.json").read_text() == before
    assert not (tmp_path / "case" / case_id / "alignment" / "events.json").exists()


def test_criteria_lock_canonicalizes_json_suffix(tmp_path):
    assert criteria_file_lock(tmp_path, "criteria") is criteria_file_lock(tmp_path, "criteria.json")


def test_criteria_lock_allows_reentrant_confirmation_paths(tmp_path):
    lock = criteria_file_lock(tmp_path, "criteria.json")
    with lock:
        with lock:
            assert True


def test_api_create_implicit_grade_does_not_call_grade_endpoint_or_live_induction(tmp_path, monkeypatch):
    case_id, report, _ = _patch_roots(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(main, "run_induction_for_case", lambda *args, **kwargs: calls.append(args))
    client = TestClient(main.app)
    response = client.post(
        f"/cases/{case_id}/implicit-grades",
        json=_implicit_request(report).model_dump(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["item"]["status"] == "queued"
    assert calls
    assert not (tmp_path / "case" / case_id / "alignment" / "events.json").exists()
