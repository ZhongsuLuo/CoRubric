import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app import alignment, services
from backend.app.schemas import ConflictResolveRequest
from tests.test_alignment import (
    BoundaryAuditEvaluator,
    NoUpdateNeedsReviewEvaluator,
    RefinementProposalEvaluator,
    _add_report,
    _append_and_accept_model,
    _case,
    _payload,
)


class InvalidConcreteUpdateEvaluator(NoUpdateNeedsReviewEvaluator):
    def __init__(self, invalid_budget: bool):
        super().__init__()
        self.invalid_budget = invalid_budget

    def propose_criterion_update(
        self, report_text, current_criterion, event, context_events=None
    ):
        suggested = deepcopy(current_criterion)
        if self.invalid_budget:
            suggested["description"] = "invalid changed criterion"
            suggested["max_score"] = float(suggested["max_score"]) - 1
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": "invalid or unchanged patch",
            "confidence": 0.9,
        }


class LowConfidenceTeacherComparison(BoundaryAuditEvaluator):
    def compare_teacher_cases(
        self, criterion, prior_text, prior_grade, new_text, new_grade
    ):
        result = super().compare_teacher_cases(
            criterion, prior_text, prior_grade, new_text, new_grade
        )
        result["confidence"] = 0.69
        return result


class SpoofedTeacherComparison(BoundaryAuditEvaluator):
    def compare_teacher_cases(
        self, criterion, prior_text, prior_grade, new_text, new_grade
    ):
        result = super().compare_teacher_cases(
            criterion, prior_text, prior_grade, new_text, new_grade
        )
        result["teacher_event_ids"] = ["model-or-unknown-event"]
        return result


def _teacher_conflict(tmp_path, monkeypatch):
    case_id, report_a, _ = _case(tmp_path, monkeypatch)
    report_b = "report-b"
    _add_report(tmp_path, case_id, report_b)
    services.save_case_report_grade(case_id, report_a, _payload(8, "current"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report_b, _payload(4, "alternative"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    return case_id, alignment.alignment_summary(case_id)["open_conflicts"][0]


def test_tt_03_04_and_api_02_conflict_has_two_selectable_teacher_patterns(
    tmp_path, monkeypatch
):
    case_id, conflict = _teacher_conflict(tmp_path, monkeypatch)

    assert conflict["conflict_type"] == "teacher_teacher"
    assert set(conflict["teacher_event_ids"]) == {
        *conflict["current_pattern"]["event_ids"],
        *conflict["alternative_pattern"]["event_ids"],
    }
    assert {
        conflict["current_pattern"]["grader_type"],
        conflict["alternative_pattern"]["grader_type"],
    } == {"teacher"}
    with pytest.raises(ValueError, match="does not belong"):
        alignment.resolve_conflict(case_id, conflict["conflict_id"], "pattern_unknown")


def test_cr_01_through_06_current_pattern_is_direct_and_idempotent(
    tmp_path, monkeypatch
):
    case_id, conflict = _teacher_conflict(tmp_path, monkeypatch)
    selected_id = conflict["current_pattern"]["pattern_id"]
    before_version = conflict["audited_rubric_version"]

    first = alignment.resolve_conflict(case_id, conflict["conflict_id"], selected_id)
    second = alignment.resolve_conflict(case_id, conflict["conflict_id"], selected_id)
    store = alignment._store(case_id)

    assert first["conflict"]["resolution_type"] == "keep_current_pattern"
    assert second["conflict"]["resolution_event_id"] == first["conflict"]["resolution_event_id"]
    assert first["conflict"]["source_rubric_version"] == before_version
    assert first["conflict"]["target_rubric_version"] is None
    assert first["conflict"]["revision_id"] is None
    assert not store.get("criterion_revisions")
    assert not store.get("auto_grade_batches")
    replacement = next(
        event
        for event in store["events"]
        if event.get("event_id") == first["conflict"]["resolution_event_id"]
    )
    assert replacement["grader_type"] == "teacher"
    assert replacement["authorship"] == "teacher_conflict_adjudication"
    with pytest.raises(ValueError, match="conflict is not open"):
        alignment.resolve_conflict(
            case_id,
            conflict["conflict_id"],
            conflict["alternative_pattern"]["pattern_id"],
        )


@pytest.mark.parametrize(
    "evaluator",
    [LowConfidenceTeacherComparison(), SpoofedTeacherComparison()],
)
def test_tt_07_08_invalid_ids_or_low_confidence_fail_closed(
    tmp_path, monkeypatch, evaluator
):
    case_id, report_a, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    services.save_case_report_grade(case_id, report_a, _payload(8, "first"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, "report-b", _payload(4, "second"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", evaluator
    )

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_tt_09_same_total_with_different_structured_outcome_can_conflict(
    tmp_path, monkeypatch
):
    case_id, report_a, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    services.save_case_report_grade(case_id, report_a, _payload(8, "first"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    alternative = _payload(8, "second").model_copy(
        update={"matched_scoring_points": ["1.p1"]}
    )
    services.save_case_report_grade(case_id, "report-b", alternative)

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )

    assert len(alignment.alignment_summary(case_id)["open_conflicts"]) == 1


def test_tt_10_reason_only_change_does_not_create_conflict(tmp_path, monkeypatch):
    case_id, report_a, _ = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    services.save_case_report_grade(case_id, report_a, _payload(8, "wording A"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, "report-b", _payload(8, "wording B"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_tt_05_teacher_replacement_does_not_conflict_with_superseded_score(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    services.save_case_report_grade(case_id, report, _payload(8, "before edit"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    services.save_case_report_grade(case_id, report, _payload(4, "after edit"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_tt_06_teacher_events_from_different_rubric_versions_do_not_conflict(
    tmp_path, monkeypatch
):
    case_id, report_a, criteria_dir = _case(tmp_path, monkeypatch)
    _add_report(tmp_path, case_id, "report-b")
    services.save_case_report_grade(case_id, report_a, _payload(8, "old version"))
    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )
    rubric_path = criteria_dir / "criteria.json"
    rubric = json.loads(rubric_path.read_text())
    rubric["criteria"][0]["description"] = "new rubric wording"
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
    services.save_case_report_grade(case_id, "report-b", _payload(4, "new version"))

    alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", BoundaryAuditEvaluator()
    )

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []


def test_ca_08_09_invalid_patch_or_stale_rubric_leaves_conflict_open(
    tmp_path, monkeypatch
):
    case_id, conflict = _teacher_conflict(tmp_path, monkeypatch)
    criteria_path = tmp_path / "criteria" / "criteria.json"
    before = criteria_path.read_text()

    with pytest.raises(ValueError, match="valid rubric update"):
        alignment.resolve_conflict(
            case_id,
            conflict["conflict_id"],
            conflict["alternative_pattern"]["pattern_id"],
            evaluator=InvalidConcreteUpdateEvaluator(invalid_budget=True),
        )

    store = alignment._store(case_id)
    assert criteria_path.read_text() == before
    assert alignment.alignment_summary(case_id)["open_conflicts"][0][
        "conflict_id"
    ] == conflict["conflict_id"]
    assert not store.get("criterion_revisions")
    assert not store.get("auto_grade_batches")
    assert not (tmp_path / "case" / case_id / "criterion_proposals.json").exists()

    rubric = json.loads(before)
    rubric["criteria"][0]["description"] = "concurrent rubric edit"
    criteria_path.write_text(json.dumps(rubric), encoding="utf-8")
    concurrent = criteria_path.read_text()
    with pytest.raises(ValueError, match="stale"):
        alignment.resolve_conflict(
            case_id,
            conflict["conflict_id"],
            conflict["alternative_pattern"]["pattern_id"],
            evaluator=RefinementProposalEvaluator(),
        )
    assert criteria_path.read_text() == concurrent


def test_tm_01_04_update_proposal_records_both_sources_and_material_diff(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    model_event, _ = _append_and_accept_model(case_id, report)
    services.save_case_report_grade(case_id, report, _payload(7, "teacher correction"))
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
    assert len(proposals) == 1
    assert proposals[0]["source_teacher_event_ids"] == [teacher_event["event_id"]]
    assert proposals[0]["source_model_event_ids"] == [model_event["event_id"]]
    assert proposals[0]["scoring_diff"]["total_score_changed"] is True


def test_tm_07_no_update_is_internal_and_does_not_create_task_or_revision(
    tmp_path, monkeypatch
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    model_event, rubric_version = _append_and_accept_model(case_id, report)
    services.save_case_report_grade(case_id, report, _payload(7, "teacher correction"))

    result = alignment.run_consistency_audit_for_criterion(
        case_id, "criteria.json", "1", NoUpdateNeedsReviewEvaluator()
    )
    store = alignment._store(case_id)

    assert result["artifacts"][0]["update_decision"] == (
        "model_execution_error_no_rubric_change"
    )
    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    assert not (tmp_path / "case" / case_id / "criterion_proposals.json").exists()
    assert not store.get("criterion_revisions")
    assert alignment._event_status(model_event["event_id"], store["decisions"]) == "superseded"
    assert alignment._load_criteria("criteria.json")[1] == rubric_version


@pytest.mark.parametrize("invalid_budget", [False, True])
def test_tm_08_unchanged_or_invalid_patch_is_treated_as_no_update(
    tmp_path, monkeypatch, invalid_budget
):
    case_id, report, _ = _case(tmp_path, monkeypatch)
    _append_and_accept_model(case_id, report)
    services.save_case_report_grade(case_id, report, _payload(7, "teacher correction"))

    result = alignment.run_consistency_audit_for_criterion(
        case_id,
        "criteria.json",
        "1",
        InvalidConcreteUpdateEvaluator(invalid_budget),
    )

    assert result["artifacts"][0]["update_decision"] == (
        "model_execution_error_no_rubric_change"
    )
    assert not (tmp_path / "case" / case_id / "criterion_proposals.json").exists()


def test_tt_11_and_api_01_legacy_conflicts_are_hidden_and_action_payload_is_rejected(
    tmp_path, monkeypatch
):
    case_id, _, _ = _case(tmp_path, monkeypatch)
    store = alignment._store(case_id)
    store["conflicts"] = [
        {
            "conflict_id": "legacy-score",
            "conflict_type": "score_rubric",
            "criteria_file_name": "criteria.json",
            "criterion_id": "1",
            "status": "open",
        },
        {
            "conflict_id": "legacy-migration",
            "conflict_type": "model_rubric_migration",
            "criteria_file_name": "criteria.json",
            "criterion_id": "1",
            "status": "open",
        },
    ]
    alignment._write_store(case_id, store)

    assert alignment.alignment_summary(case_id)["open_conflicts"] == []
    assert ConflictResolveRequest(selected_pattern_id="pattern-a").selected_pattern_id == (
        "pattern-a"
    )
    with pytest.raises(ValidationError):
        ConflictResolveRequest(action="select_current_pattern")
