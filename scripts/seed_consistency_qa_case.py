from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from backend.app import alignment, implicit_grading, main, services
from backend.app.schemas import CriteriaSchema


CASE_ID = "Consistency QA"
CRITERIA_FILE = "consistency_qa.json"
REPORT_NAMES = ["qa-report-1", "qa-report-2", "qa-report-3", "qa-report-4"]


class SeedModel:
    def grade(self, report_text, criterion, boundary, examples):
        criterion_id = str(criterion.get("criteria_id"))
        score = 8 if criterion_id == "1" else 4
        return {
            "criteria_score": score,
            "score_reason": f"Seeded model score for criterion {criterion_id}",
            "anchor_text": "validated QA evidence",
            "target_section": criterion.get("section"),
        }


class SeedEvaluator:
    def compare_teacher_cases(self, criterion, prior_text, prior_grade, new_text, new_grade):
        return {"comparable": True, "reason_evidence_consistent": True, "reason": "QA seed"}

    def reason_and_evidence_agree(self, criterion, expected, candidate):
        return {"agree": True, "reason": "QA seed"}

    def admit(self, report_text, criterion, boundary):
        return {
            "decision": "inside",
            "matched_rule_ids": list(boundary.get("covered_rule_ids") or []),
            "reason": "QA seed",
        }

    def propose_criterion_update(
        self, report_text, current_criterion, event, context_events=None
    ):
        return {
            "decision": "no_update",
            "suggested_criterion": None,
            "rationale": "The isolated QA evaluator keeps the rubric unchanged.",
            "confidence": 1.0,
        }

    def interpret_grade(self, report_text, current_criterion, event):
        return {
            "evidence_source": "teacher_selected",
            "inferred_evidence": "",
            "fit": "consistent",
            "confidence": 1.0,
            "issue_type": None,
            "severity": None,
            "matched_rules": [str(current_criterion.get("criteria_id"))],
            "reason": "The isolated QA evaluator accepts the teacher review.",
        }


def rubric() -> dict:
    raw = {
        "schema_version": "structured-rubric-v1",
        "title": "Consistency QA Rubric",
        "background": "Isolated fixture for adaptive consistency browser acceptance testing.",
        "language": "en",
        "base_score": 100,
        "criteria": [
            {
                "criteria_id": "1",
                "criteria_name": "Evidence quality",
                "description": "Award points when the report contains validated QA evidence.",
                "original_description": "Evidence quality",
                "max_score": 10,
                "section": "analysis",
                "few_shot_examples": [],
                "scoring_points": [
                    {
                        "point_id": "1.p1",
                        "summary": "Validated evidence",
                        "deduction_rules": [
                            {
                                "rule_id": "1.p1.r1",
                                "condition": "Validated evidence is absent",
                                "deduct": 10,
                            }
                        ],
                        "max_deduction": 10,
                        "aggregation": "max_triggered",
                    }
                ],
                "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
            },
            {
                "criteria_id": "2",
                "criteria_name": "Discussion clarity",
                "description": "Award points for a clear discussion.",
                "original_description": "Discussion clarity",
                "max_score": 5,
                "section": "discussion",
                "few_shot_examples": [],
                "scoring_points": [
                    {
                        "point_id": "2.p1",
                        "summary": "Clear discussion",
                        "deduction_rules": [
                            {
                                "rule_id": "2.p1.r1",
                                "condition": "The discussion is unclear",
                                "deduct": 5,
                            }
                        ],
                        "max_deduction": 5,
                        "aggregation": "max_triggered",
                    }
                ],
                "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 5},
            },
        ],
    }
    return CriteriaSchema.model_validate(raw).model_dump(mode="json")


def configure_roots(case_root: Path, criteria_dir: Path) -> None:
    for module in (alignment, services, main, implicit_grading):
        module.CASE_ROOT = case_root
        module.CRITERIA_DIR = criteria_dir


def seed(case_root: Path, criteria_dir: Path, reset: bool) -> None:
    case_dir = case_root / CASE_ID
    if reset and case_dir.exists():
        shutil.rmtree(case_dir)
    criteria_dir.mkdir(parents=True, exist_ok=True)
    (criteria_dir / CRITERIA_FILE).write_text(
        json.dumps(rubric(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for report_name in REPORT_NAMES:
        report_dir = case_dir / "markdown" / report_name
        report_dir.mkdir(parents=True, exist_ok=True)
        text = f"# {report_name}\n\nvalidated QA evidence\n\nThe discussion is clear.\n"
        (report_dir / f"{report_name}.md").write_text(text, encoding="utf-8")
        (report_dir / f"{report_name}_text_only.md").write_text(text, encoding="utf-8")
        (case_dir / f"{report_name}.pdf").write_bytes(b"%PDF-1.4\n% QA fixture\n")

    configure_roots(case_root, criteria_dir)
    model = SeedModel()
    evaluator = SeedEvaluator()
    # Three reports model the existing current+2 pre-grade window. The fourth is
    # deliberately left eligible so a reliable transition can exercise real UI
    # backfill without calling an external model.
    for report_name in REPORT_NAMES[:3]:
        result = alignment.auto_grade_report(
            CASE_ID,
            report_name,
            CRITERIA_FILE,
            model,
            evaluator,
            ["1", "2"],
            scheduling_reason="pregrade",
        )
        if len(result.get("grades") or []) != 2:
            raise RuntimeError(f"failed to seed both model grades for {report_name}: {result}")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Seed the isolated adaptive-consistency QA case.")
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--criteria-dir", type=Path, required=True)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    seed(args.case_root.resolve(), args.criteria_dir.resolve(), args.reset)
    print(
        json.dumps(
            {
                "case_id": CASE_ID,
                "criteria_file": CRITERIA_FILE,
                "reports": REPORT_NAMES,
                "case_root": str(args.case_root.resolve()),
                "criteria_dir": str(args.criteria_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main_cli()
