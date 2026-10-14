"""Isolated deterministic app used only by Playwright boundary tests."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path


E2E_ROOT = Path(os.environ.get("GRADING_E2E_ROOT", "/tmp/grading-conflict-update-e2e"))
if os.environ.get("GRADING_E2E_RESET") == "1":
    if E2E_ROOT.name != "grading-conflict-update-e2e":
        raise RuntimeError("refusing to reset an unexpected E2E directory")
    shutil.rmtree(E2E_ROOT, ignore_errors=True)

CRITERIA_DIR = E2E_ROOT / "criteria"
CASE_ROOT = E2E_ROOT / "case"
API_DATA_DIR = E2E_ROOT / "api"
BASELINE_DIR = E2E_ROOT / "baselines"
for path in (CRITERIA_DIR, CASE_ROOT, API_DATA_DIR, BASELINE_DIR):
    path.mkdir(parents=True, exist_ok=True)

os.environ["GRADING_CRITERIA_DIR"] = str(CRITERIA_DIR)
os.environ["GRADING_CASE_ROOT"] = str(CASE_ROOT)
os.environ["GRADING_DATA_DIR"] = str(API_DATA_DIR)
os.environ["GRADING_CRITERIA_BASELINE_DIR"] = str(BASELINE_DIR)
os.environ["AUTO_GRADE_MIN_TEACHER_SAMPLES"] = "0"

from backend.app import alignment, main, services  # noqa: E402
from backend.app.schemas import (  # noqa: E402
    CaseReportGradeRequest,
    CaseReportModelGradeAcceptRequest,
    CriteriaSchema,
)


class DeterministicBoundaryAdapter:
    def compare_teacher_cases(
        self, criterion, prior_text, prior_grade, new_text, new_grade
    ):
        return {
            "comparable": True,
            "same_grading_pattern": True,
            "confidence": 0.98,
            "reason": "Both teacher events apply the same deterministic evidence pattern.",
        }

    def interpret_grade(self, report_text, current_criterion, event):
        return {
            "evidence_source": "teacher_selected",
            "inferred_evidence": "",
            "fit": "consistent",
            "confidence": 0.98,
            "issue_type": "none",
            "severity": "none",
            "matched_rules": [str(current_criterion["criteria_id"])],
            "reason": "The deterministic fixture fits the criterion.",
        }

    def propose_criterion_update(
        self, report_text, current_criterion, event, context_events=None
    ):
        suggested = deepcopy(current_criterion)
        selected_alternative = bool((event.get("conflict_resolution") or {}).get("selected_pattern"))
        marker = (
            "alternative teacher pattern enabled"
            if selected_alternative
            else "teacher model correction enabled"
        )
        suggested["description"] = marker
        suggested["original_description"] = marker
        suggested["scoring_points"][0]["deduction_rules"][0]["deduct"] = 1
        suggested["scoring_points"][0]["deduction_rules"].append(
            {
                "rule_id": "1.p1.r2",
                "condition": "The pattern is absent",
                "deduct": 2,
            }
        )
        return {
            "decision": "update_criterion",
            "suggested_criterion": suggested,
            "rationale": marker,
            "confidence": 0.98,
        }

    def reason_and_evidence_agree(self, criterion, expected, candidate):
        return {"agree": True, "reason": "deterministic agreement"}

    def admit(self, report_text, criterion, boundary):
        return {
            "decision": "inside",
            "matched_rule_ids": list(boundary.get("covered_rule_ids") or []),
            "reason": "deterministic admission",
        }


class DeterministicBoundaryModel:
    def grade(self, report_text, criterion, boundary, examples):
        max_score = float(criterion.get("max_score") or 0)
        score = (
            1.0
            if "enabled" in str(criterion.get("description") or "")
            else max_score
        )
        point = criterion["scoring_points"][0]
        point_id = str(point["point_id"])
        rule_id = str(point["deduction_rules"][0]["rule_id"])
        deducted = score < max_score
        return {
            "criteria_score": score,
            "score_reason": "Deterministic browser-test model grade.",
            "anchor_text": "same evidence pattern",
            "matched_scoring_points": [point_id] if deducted else [],
            "matched_deduction_rules": [rule_id] if deducted else [],
            "scoring_point_scores": [
                {
                    "point_id": point_id,
                    "score": score,
                    "reason": (
                        f"Evidence triggers {rule_id}."
                        if deducted
                        else f"Evidence satisfies {point_id}."
                    ),
                }
            ],
        }


alignment.LLMAlignmentEvaluator = DeterministicBoundaryAdapter
alignment.LiveGradingModel = DeterministicBoundaryModel


def _rubric(title: str) -> dict:
    raw = {
        "schema_version": "structured-rubric-v1",
        "title": title,
        "background": "Conflict/update browser fixture",
        "language": "en",
        "base_score": 3,
        "criteria": [
            {
                "criteria_id": "1",
                "criteria_name": "Boundary criterion",
                "description": "Original two-point standard",
                "original_description": "Original two-point standard",
                "max_score": 2,
                "section": "analysis",
                "few_shot_examples": [],
                "scoring_points": [
                    {
                        "point_id": "1.p1",
                        "summary": "Apply the evidence pattern",
                        "deduction_rules": [
                            {
                                "rule_id": "1.p1.r1",
                                "condition": "The pattern is incomplete",
                                "deduct": 2,
                            }
                        ],
                        "max_deduction": 2,
                        "aggregation": "max_triggered",
                    }
                ],
                "scoring_aggregation": {
                    "mode": "sum_deductions_capped",
                    "cap": 2,
                },
            },
            {
                "criteria_id": "2",
                "criteria_name": "Unaffected criterion",
                "description": "Must remain untouched",
                "original_description": "Must remain untouched",
                "max_score": 1,
                "section": "analysis",
                "few_shot_examples": [],
                "scoring_points": [
                    {
                        "point_id": "2.p1",
                        "summary": "Unaffected point",
                        "deduction_rules": [
                            {
                                "rule_id": "2.p1.r1",
                                "condition": "Missing",
                                "deduct": 1,
                            }
                        ],
                        "max_deduction": 1,
                        "aggregation": "max_triggered",
                    }
                ],
                "scoring_aggregation": {
                    "mode": "sum_deductions_capped",
                    "cap": 1,
                },
            },
        ],
    }
    return CriteriaSchema.model_validate(raw).model_dump(mode="json")


def _write_report(case_id: str, report_name: str) -> None:
    report_dir = CASE_ROOT / case_id / "markdown" / report_name
    report_dir.mkdir(parents=True, exist_ok=True)
    text = "# Report\nsame evidence pattern\n"
    (report_dir / f"{report_name}.md").write_text(text, encoding="utf-8")
    (report_dir / f"{report_name}_text_only.md").write_text(text, encoding="utf-8")
    (CASE_ROOT / case_id / f"{report_name}.pdf").write_bytes(b"pdf")


def _teacher_payload(criteria_file_name: str, score: float) -> CaseReportGradeRequest:
    return CaseReportGradeRequest(
        criteria_id="1",
        criteria_score=score,
        score_reason=f"Teacher selected {score:g} points.",
        anchor_text="same evidence pattern",
        scoring_point_scores=[{"point_id": "1.p1", "score": score}],
        criteria_file_name=criteria_file_name,
    )


def _seed_teacher_conflict(case_id: str, criteria_file_name: str) -> None:
    for report_name in ("report-a", "report-b"):
        _write_report(case_id, report_name)
    services.save_case_report_grade(
        case_id, "report-a", _teacher_payload(criteria_file_name, 2)
    )
    alignment.run_consistency_audit_for_criterion(
        case_id, criteria_file_name, "1", DeterministicBoundaryAdapter()
    )
    services.save_case_report_grade(
        case_id, "report-b", _teacher_payload(criteria_file_name, 1)
    )
    alignment.run_consistency_audit_for_criterion(
        case_id, criteria_file_name, "1", DeterministicBoundaryAdapter()
    )


def _seed_teacher_model_update(case_id: str, criteria_file_name: str) -> None:
    for report_name in ("report-a", "report-b"):
        _write_report(case_id, report_name)
    store = alignment._store(case_id)
    _, rubric_version, criteria = alignment._load_criteria(criteria_file_name)
    criterion = criteria["criteria"][0]
    model_event = alignment._append_model_event(
        store,
        case_id,
        "report-a",
        "1",
        criteria_file_name,
        rubric_version,
        criterion,
        {
            "criteria_score": 2,
            "score_reason": "Initial deterministic model score.",
            "anchor_text": "same evidence pattern",
            "matched_scoring_points": [],
            "matched_deduction_rules": [],
            "scoring_point_scores": [
                {"point_id": "1.p1", "score": 2, "reason": "Full credit."}
            ],
        },
        "e2e-model-candidate",
    )
    alignment._write_store(case_id, store)
    alignment.accept_model_grade_event(
        case_id,
        "report-a",
        CaseReportModelGradeAcceptRequest(
            criteria_id="1", criteria_file_name=criteria_file_name
        ),
    )
    services.save_case_report_grade(
        case_id, "report-a", _teacher_payload(criteria_file_name, 1)
    )
    alignment.run_consistency_audit_for_criterion(
        case_id, criteria_file_name, "1", DeterministicBoundaryAdapter()
    )
    assert model_event["event_id"]


def _seed_negative_case(case_id: str, criteria_file_name: str) -> None:
    _write_report(case_id, "report-a")
    _write_report(case_id, "report-b")
    services.save_case_report_grade(
        case_id, "report-a", _teacher_payload(criteria_file_name, 2)
    )
    services.save_case_report_grade(
        case_id, "report-a", _teacher_payload(criteria_file_name, 1)
    )
    alignment.run_consistency_audit_for_criterion(
        case_id, criteria_file_name, "1", DeterministicBoundaryAdapter()
    )
    store = alignment._store(case_id)
    store["conflicts"].append(
        {
            "conflict_id": "legacy-migration-state",
            "conflict_type": "model_rubric_migration",
            "criterion_id": "1",
            "criteria_file_name": criteria_file_name,
            "status": "open",
        }
    )
    alignment._write_store(case_id, store)


def _seed() -> None:
    fixtures = {
        "case 1": "01-current.json",
        "case 2": "02-alternative.json",
        "case 3": "03-update-confirm.json",
        "case 4": "04-update-reject.json",
        "case 5": "05-negative.json",
    }
    for case_id, criteria_file_name in fixtures.items():
        case_dir = CASE_ROOT / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        # Use the same persisted catalog contract as uploaded cases. Production
        # no longer enumerates arbitrary folders or exposes every global rubric.
        (case_dir / "case.json").write_text(json.dumps({
            "kind": "uploaded_pdf_folder", "id": case_id,
            "name": case_id, "criteria_files": [criteria_file_name],
            "default_criteria_file": criteria_file_name,
        }), encoding="utf-8")
        (CRITERIA_DIR / criteria_file_name).write_text(
            json.dumps(_rubric(case_id), ensure_ascii=False), encoding="utf-8"
        )
    (CRITERIA_DIR / "criteria.json").write_text(
        json.dumps(_rubric("Default startup rubric"), ensure_ascii=False),
        encoding="utf-8",
    )
    (CRITERIA_DIR / "“原子吸收光谱法测定饮用水中的镁含量”实验评分标准_criterion_categories_v2.json").write_text(
        json.dumps(_rubric("Case 1 startup rubric"), ensure_ascii=False),
        encoding="utf-8",
    )
    _seed_teacher_conflict("case 1", fixtures["case 1"])
    _seed_teacher_conflict("case 2", fixtures["case 2"])
    _seed_teacher_model_update("case 3", fixtures["case 3"])
    _seed_teacher_model_update("case 4", fixtures["case 4"])
    _seed_negative_case("case 5", fixtures["case 5"])


if os.environ.get("GRADING_E2E_RESET") == "1":
    _seed()

app = main.app
