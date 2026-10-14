from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.app.config import CASE_ROOT, CRITERIA_DIR
from backend.app.schemas import CriteriaSchema


QA_CASE_ID = "QA Global Conflict"
QA_READY_CASE_ID = "QA Global Ready"
QA_LARGE_CASE_ID = "QA 51 Reports"
QA_SCALE_CASES = {
    0: "QA 00 Reports",
    1: "QA 01 Report",
    5: "QA 05 Reports",
    15: "QA 15 Reports",
    20: "QA 20 Reports",
    50: "QA 50 Reports",
}
QA_CRITERIA_FILE = "qa_global_conflict.json"
QA_REPORTS = [
    "QA-01-teacher-conflict",
    "QA-02-teacher-clean",
    "QA-03-model-accepted",
    "QA-04-model-batch",
    "QA-05-manual-exception",
    "QA-06-failed",
    "QA-07-abstained",
    "QA-08-ungraded",
]
CREATED_AT = "2026-07-15T00:00:00+00:00"
TINY_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _canonical_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _criterion(criteria_id: str, name: str, max_score: float, description: str, section: str) -> dict[str, Any]:
    point_id = f"{criteria_id}.p1"
    return {
        "criteria_id": criteria_id,
        "criteria_name": name,
        "description": description,
        "original_description": description,
        "max_score": max_score,
        "section": section,
        "few_shot_examples": [],
        "scoring_points": [
            {
                "point_id": point_id,
                "summary": description,
                "deduction_rules": [
                    {
                        "rule_id": f"{point_id}.r1",
                        "condition": f"未满足：{description}",
                        "deduct": max_score,
                    }
                ],
                "max_deduction": max_score,
                "aggregation": "max_triggered",
            }
        ],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": max_score},
    }


def _rubric() -> dict[str, Any]:
    raw = {
        "schema_version": "structured-rubric-v1",
        "title": "QA Global & Conflict Rubric",
        "background": "Synthetic business fixture for Global distribution and Conflict workflow verification.",
        "language": "zh",
        "base_score": 100,
        "criteria": [
            _criterion("1", "证据质量", 10, "证据应直接支持结论，且来源清晰。", "analysis"),
            _criterion("2", "分析准确性", 5, "计算、单位和推理应准确。", "analysis"),
            _criterion("3", "结论完整性", 4, "结论应回应实验目标并说明限制。", "conclusion"),
            _criterion("4", "格式规范", 2, "报告结构与标注应完整。", "format"),
        ],
    }
    return CriteriaSchema.model_validate(raw).model_dump(mode="json")


def _event(
    report: str,
    criterion: dict[str, Any],
    score: float,
    grader_type: str,
    rubric_version: str,
    *,
    suffix: str,
    reason: str,
    anchor: str,
) -> dict[str, Any]:
    event_id = f"evt_qa_{suffix}"
    grader_id = "qa-model" if grader_type == "model" else "qa-teacher"
    authored_grade = {
        "criteria_score": score,
        "score_reason": reason,
        "anchor_text": anchor,
        "target_section": criterion["section"],
        "criteria_name": criterion["criteria_name"],
        "description": criterion["description"],
        "original_description": criterion["original_description"],
        "max_score": criterion["max_score"],
        "section": criterion["section"],
        "scoring_points": criterion["scoring_points"],
        "scoring_aggregation": criterion["scoring_aggregation"],
        "matched_scoring_points": [criterion["scoring_points"][0]["point_id"]],
        "matched_deduction_rules": [],
        "scoring_point_scores": [
            {
                "point_id": criterion["scoring_points"][0]["point_id"],
                "score": score,
                "max_score": criterion["max_score"],
            }
        ],
    }
    return {
        "event_id": event_id,
        "event_type": "grade_authored",
        "idempotency_key": f"qa::{suffix}",
        "case_id": QA_CASE_ID,
        "report_name": report,
        "criterion_id": criterion["criteria_id"],
        "grader_type": grader_type,
        "grader_id": grader_id,
        "model_version": "qa-model-v1" if grader_type == "model" else None,
        "rubric_file_name": QA_CRITERIA_FILE,
        "rubric_version": rubric_version,
        "criterion_hash": _canonical_hash(criterion),
        "criterion_snapshot": criterion,
        "authored_grade": authored_grade,
        "rubric_metadata": {
            "title": "QA Global & Conflict Rubric",
            "base_score": 100,
            "language": "zh",
        },
        "created_at": CREATED_AT,
    }


def _decision(event_id: str, status: str, suffix: str) -> dict[str, Any]:
    return {
        "decision_id": f"dec_qa_{suffix}",
        "decision_type": status,
        "event_ids": [event_id],
        "reason": f"QA fixture status: {status}",
        "created_at": CREATED_AT,
    }


def _job(
    report: str,
    criterion_id: str,
    rubric_version: str,
    status: str,
    *,
    error: str | None = None,
) -> tuple[str, dict[str, Any]]:
    key = _canonical_hash({
        "report_name": report,
        "criterion_id": criterion_id,
        "rubric_file_name": QA_CRITERIA_FILE,
    })
    return key, {
        "job_id": key,
        "case_id": QA_CASE_ID,
        "report_name": report,
        "criteria_id": criterion_id,
        "criterion_id": criterion_id,
        "criteria_file_name": QA_CRITERIA_FILE,
        "rubric_version": rubric_version,
        "status": status,
        "reason": "qa_fixture",
        "decision": status if status in {"done", "abstained"} else None,
        "error": error,
        "updated_at": CREATED_AT,
        "completed_at": CREATED_AT,
    }


def seed_qa_case(case_root: Path = Path(CASE_ROOT), criteria_dir: Path = Path(CRITERIA_DIR)) -> dict[str, Any]:
    rubric = _rubric()
    rubric_version = _canonical_hash(rubric)
    old_rubric_version = "qa-old-rubric-v1"
    criteria_by_id = {item["criteria_id"]: item for item in rubric["criteria"]}

    case_dir = case_root / QA_CASE_ID
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    criteria_dir.mkdir(parents=True, exist_ok=True)
    _write_json(criteria_dir / QA_CRITERIA_FILE, rubric)

    report_notes = {
        "QA-01-teacher-conflict": "教师评分 7.5；与另一份可比较报告的教师评分形成模式冲突。",
        "QA-02-teacher-clean": "教师评分 10；审核通过。",
        "QA-03-model-accepted": "模型评分 8.5，教师直接接受，作者仍应显示为模型。",
        "QA-04-model-batch": "批量授权后的模型评分 9。",
        "QA-05-manual-exception": "教师保留 6 分并标记 manual exception。",
        "QA-06-failed": "自动评分失败，可重试。",
        "QA-07-abstained": "模型 abstain，不应显示为 0 分。",
        "QA-08-ungraded": "尚未评分，应进入 eligible，但不应显示为 0 分。",
    }
    for index, report in enumerate(QA_REPORTS, start=1):
        report_dir = case_dir / "markdown" / report
        report_dir.mkdir(parents=True, exist_ok=True)
        content = (
            f"# QA 实验报告 {index}\n\n"
            f"报告：{report}\n\n"
            f"## 业务状态\n\n{report_notes[report]}\n\n"
            "## 实验分析\n\n该报告包含可定位的证据、计算过程和结论，用于验证评分界面。\n"
            "\n重复证据首先出现。重复证据随后再次出现，用于验证原有 occurrence 规则。\n"
            "\n| 指标 | 数值 |\n| --- | ---: |\n| 平均值 | 10.0 |\n"
            f"\n![校准图]({TINY_PNG_DATA_URI} \"QA calibration image\")\n"
        )
        (report_dir / f"{report}.md").write_text(content, encoding="utf-8")
        (report_dir / f"{report}_text_only.md").write_text(content, encoding="utf-8")

    events = [
        _event(QA_REPORTS[0], criteria_by_id["1"], 7.5, "teacher", rubric_version, suffix="c1_r1_teacher_conflict", reason="证据基本相关，但论证链不完整。", anchor="可定位的证据"),
        _event(QA_REPORTS[1], criteria_by_id["1"], 10, "teacher", rubric_version, suffix="c1_r2_teacher_clean", reason="证据完整且直接支持结论。", anchor="证据、计算过程和结论"),
        _event(QA_REPORTS[2], criteria_by_id["1"], 8.5, "model", rubric_version, suffix="c1_r3_model_accepted", reason="证据清晰，存在少量论证缺口。", anchor="可定位的证据"),
        _event(QA_REPORTS[3], criteria_by_id["1"], 9, "model", rubric_version, suffix="c1_r4_model_batch", reason="证据和结论匹配。", anchor="证据、计算过程和结论"),
        _event(QA_REPORTS[4], criteria_by_id["1"], 6, "teacher", rubric_version, suffix="c1_r5_manual_exception", reason="教师保留业务判断。", anchor="教师保留 6 分"),
        _event(QA_REPORTS[0], criteria_by_id["2"], 4.5, "teacher", rubric_version, suffix="c2_r1_teacher", reason="计算正确，单位说明略少。", anchor="计算过程"),
        _event(QA_REPORTS[1], criteria_by_id["2"], 5, "teacher", rubric_version, suffix="c2_r2_teacher", reason="计算、单位和推理完整。", anchor="平均值"),
        _event(QA_REPORTS[2], criteria_by_id["2"], 4, "model", rubric_version, suffix="c2_r3_model_accepted", reason="主要计算正确。", anchor="校准图"),
        _event(QA_REPORTS[0], criteria_by_id["3"], 4, "teacher", rubric_version, suffix="c3_r1_teacher_current", reason="结论完整回应目标。", anchor="结论"),
        _event(QA_REPORTS[2], criteria_by_id["3"], 3, "model", old_rubric_version, suffix="c3_r3_model_stale", reason="旧 rubric 模型评分。", anchor="结论"),
        _event(QA_REPORTS[3], criteria_by_id["3"], 3.5, "model", old_rubric_version, suffix="c3_r4_model_stale", reason="旧 rubric 模型评分。", anchor="结论"),
    ]
    # Migration fixtures need a genuinely different Card snapshot/hash. A file
    # version alone no longer invalidates unrelated Cards.
    for event in events:
        if (
            event["criterion_id"] == "3"
            and event["rubric_version"] == old_rubric_version
        ):
            stale_snapshot = json.loads(json.dumps(event["criterion_snapshot"]))
            stale_snapshot["description"] = "旧版结论规则"
            event["criterion_snapshot"] = stale_snapshot
            event["criterion_hash"] = _canonical_hash(stale_snapshot)
    by_suffix = {event["event_id"].removeprefix("evt_qa_"): event for event in events}
    decisions = [
        _decision(by_suffix["c1_r1_teacher_conflict"]["event_id"], "quarantined", "c1_r1"),
        _decision(by_suffix["c1_r2_teacher_clean"]["event_id"], "trusted", "c1_r2"),
        _decision(by_suffix["c1_r3_model_accepted"]["event_id"], "accepted_model", "c1_r3"),
        _decision(by_suffix["c1_r4_model_batch"]["event_id"], "model_only", "c1_r4"),
        _decision(by_suffix["c1_r5_manual_exception"]["event_id"], "manual_exception", "c1_r5"),
        _decision(by_suffix["c2_r1_teacher"]["event_id"], "trusted", "c2_r1"),
        _decision(by_suffix["c2_r2_teacher"]["event_id"], "trusted", "c2_r2"),
        _decision(by_suffix["c2_r3_model_accepted"]["event_id"], "model_only", "c2_r3"),
        _decision(by_suffix["c3_r1_teacher_current"]["event_id"], "trusted", "c3_r1"),
        _decision(by_suffix["c3_r3_model_stale"]["event_id"], "model_only", "c3_r3"),
        _decision(by_suffix["c3_r4_model_stale"]["event_id"], "model_only", "c3_r4"),
    ]

    conflict_event_id = by_suffix["c1_r1_teacher_conflict"]["event_id"]
    supporting_event_id = by_suffix["c1_r2_teacher_clean"]["event_id"]

    def teacher_pattern(event: dict[str, Any]) -> dict[str, Any]:
        grade = event["authored_grade"]
        event_id = event["event_id"]
        return {
            "pattern_id": f"pattern_{event_id}",
            "event_ids": [event_id],
            "representative_event_id": event_id,
            "event_id": event_id,
            "report_name": event["report_name"],
            "grader_type": "teacher",
            "criteria_score": grade["criteria_score"],
            "score_reason": grade["score_reason"],
            "anchor_text": grade["anchor_text"],
            "scoring_point_scores": grade["scoring_point_scores"],
            "matched_scoring_points": grade["matched_scoring_points"],
            "matched_deduction_rules": grade["matched_deduction_rules"],
        }
    stale_event_ids = [
        by_suffix["c3_r3_model_stale"]["event_id"],
        by_suffix["c3_r4_model_stale"]["event_id"],
    ]
    conflicts = [
        {
            "conflict_id": "conflict_qa_teacher_teacher",
            "conflict_type": "teacher_teacher",
            "criteria_file_name": QA_CRITERIA_FILE,
            "criterion_id": "1",
            "criteria_id": "1",
            "status": "open",
            "review_status": "open",
            "suspect_event_ids": [conflict_event_id],
            "supporting_event_ids": [supporting_event_id],
            "affected_event_ids": [supporting_event_id, conflict_event_id],
            "event_ids": [supporting_event_id, conflict_event_id],
            "teacher_event_ids": [supporting_event_id, conflict_event_id],
            "current_event_id": supporting_event_id,
            "alternative_event_id": conflict_event_id,
            "current_pattern": teacher_pattern(
                by_suffix["c1_r2_teacher_clean"]
            ),
            "alternative_pattern": teacher_pattern(
                by_suffix["c1_r1_teacher_conflict"]
            ),
            "audited_rubric_version": rubric_version,
            "reason": "两个可比较的教师评分模式对同类证据给出了不同分数。",
            "created_at": CREATED_AT,
        },
        {
            "conflict_id": "conflict_qa_model_migration",
            "conflict_type": "model_rubric_migration",
            "criteria_file_name": QA_CRITERIA_FILE,
            "criterion_id": "3",
            "criteria_id": "3",
            "status": "open",
            "review_status": "open",
            "suspect_event_ids": [],
            "supporting_event_ids": [],
            "affected_event_ids": stale_event_ids,
            "event_ids": stale_event_ids,
            "source_rubric_version": old_rubric_version,
            "target_rubric_version": rubric_version,
            "reason": "2 个模型评分来自旧 rubric，需要教师确认后重评。",
            "created_at": CREATED_AT,
        },
    ]

    job_items = [
        _job(QA_REPORTS[2], "1", rubric_version, "done"),
        _job(QA_REPORTS[3], "1", rubric_version, "done"),
        _job(QA_REPORTS[5], "1", rubric_version, "failed", error="QA simulated provider timeout"),
        _job(QA_REPORTS[6], "1", rubric_version, "abstained"),
        _job(QA_REPORTS[7], "1", rubric_version, "pending"),
    ]
    auto_grade_jobs = dict(job_items)

    readiness = {
        "criteria": {
            "1": {"criterion_id": "1", "criteria_id": "1", "lifecycle": "active_selective", "rubric_version": rubric_version, "reason": "qa_batch_authorized", "updated_at": CREATED_AT},
            "2": {"criterion_id": "2", "criteria_id": "2", "lifecycle": "active_selective", "rubric_version": rubric_version, "reason": "qa_ready_for_authorization", "updated_at": CREATED_AT},
            "3": {"criterion_id": "3", "criteria_id": "3", "lifecycle": "active_selective", "rubric_version": rubric_version, "reason": "qa_repair_required", "updated_at": CREATED_AT},
            "4": {"criterion_id": "4", "criteria_id": "4", "lifecycle": "collecting", "rubric_version": rubric_version, "reason": "qa_not_ready", "updated_at": CREATED_AT},
        }
    }
    authorizations = {
        f"{QA_CRITERIA_FILE}::1": {"criteria_file_name": QA_CRITERIA_FILE, "criterion_id": "1", "state": "batch_authorized", "authorized_rubric_version": rubric_version, "authorized_at": CREATED_AT, "authorized_by": "qa-teacher", "updated_at": CREATED_AT},
        f"{QA_CRITERIA_FILE}::2": {"criteria_file_name": QA_CRITERIA_FILE, "criterion_id": "2", "state": "teacher_validation", "authorized_rubric_version": None, "updated_at": CREATED_AT},
        f"{QA_CRITERIA_FILE}::3": {"criteria_file_name": QA_CRITERIA_FILE, "criterion_id": "3", "state": "repair_required", "authorized_rubric_version": old_rubric_version, "repair_from_rubric_version": old_rubric_version, "repair_to_rubric_version": rubric_version, "updated_at": CREATED_AT},
        f"{QA_CRITERIA_FILE}::4": {"criteria_file_name": QA_CRITERIA_FILE, "criterion_id": "4", "state": "teacher_validation", "authorized_rubric_version": None, "updated_at": CREATED_AT},
    }
    artifacts = {
        conflict_event_id: {
            "event_id": conflict_event_id,
            "criterion_id": "1",
            "audited_rubric_version": rubric_version,
            "teacher_score": 7.5,
            "teacher_anchor": "可定位的证据",
            "teacher_reason": "证据基本相关，但论证链不完整。",
            "fit": "needs_review",
            "audit_status": "needs_review",
            "issue_type": "rubric_score_gap",
            "reason": "7.5 分尚不能由当前单一扣分规则精确解释。",
            "expected_score_range": [6, 7],
            "recommended_score": 7,
            "confidence": 0.91,
            "matched_rules": ["1.p1.r1"],
            "created_at": CREATED_AT,
        }
    }
    audit_job_key = _canonical_hash({"criteria_file_name": QA_CRITERIA_FILE, "criterion_id": "3"})
    audit_jobs = {
        audit_job_key: {
            "job_id": audit_job_key,
            "case_id": QA_CASE_ID,
            "criteria_file_name": QA_CRITERIA_FILE,
            "criterion_id": "3",
            "criteria_id": "3",
            "status": "done",
            "trigger": "qa_fixture",
            "mode": "rubric_migration",
            "source_rubric_version": old_rubric_version,
            "target_rubric_version": rubric_version,
            "queued_at": CREATED_AT,
            "updated_at": CREATED_AT,
            "completed_at": CREATED_AT,
        }
    }

    alignment_dir = case_dir / "alignment"
    stores = {
        "events.json": events,
        "decisions.json": decisions,
        "conflicts.json": conflicts,
        "readiness.json": readiness,
        "auto_grade_jobs.json": auto_grade_jobs,
        "audit_artifacts.json": artifacts,
        "consistency_audits.json": [],
        "audit_jobs.json": audit_jobs,
        "refinement_rejections.json": [],
        "criterion_authorizations.json": authorizations,
        "conflict_reassessments.json": {},
    }
    for file_name, data in stores.items():
        _write_json(alignment_dir / file_name, data)

    ready_case_dir = case_root / QA_READY_CASE_ID
    if ready_case_dir.exists():
        shutil.rmtree(ready_case_dir)
    shutil.copytree(case_dir, ready_case_dir)
    ready_stores = deepcopy(stores)
    for event in ready_stores["events.json"]:
        event["case_id"] = QA_READY_CASE_ID
    for job in ready_stores["auto_grade_jobs.json"].values():
        job["case_id"] = QA_READY_CASE_ID
    ready_stores["audit_jobs.json"] = {}
    ready_stores["conflicts.json"] = []
    ready_stores["criterion_authorizations.json"][f"{QA_CRITERIA_FILE}::3"] = {
        "criteria_file_name": QA_CRITERIA_FILE,
        "criterion_id": "3",
        "state": "teacher_validation",
        "authorized_rubric_version": None,
        "updated_at": CREATED_AT,
    }
    for file_name, data in ready_stores.items():
        _write_json(ready_case_dir / "alignment" / file_name, data)

    large_case_dir = case_root / QA_LARGE_CASE_ID
    if large_case_dir.exists():
        shutil.rmtree(large_case_dir)
    for index in range(1, 52):
        report = f"QA-LARGE-{index:02d}"
        report_dir = large_case_dir / "markdown" / report
        report_dir.mkdir(parents=True, exist_ok=True)
        content = f"# QA large-case report {index}\n\n用于验证超过 50 份报告时的非阻塞提醒。\n"
        (report_dir / f"{report}.md").write_text(content, encoding="utf-8")
        (report_dir / f"{report}_text_only.md").write_text(content, encoding="utf-8")
    empty_stores = {
        "events.json": [],
        "decisions.json": [],
        "conflicts.json": [],
        "readiness.json": {"criteria": {}},
        "auto_grade_jobs.json": {},
        "audit_artifacts.json": {},
        "consistency_audits.json": [],
        "audit_jobs.json": {},
        "refinement_rejections.json": [],
        "criterion_authorizations.json": {},
        "conflict_reassessments.json": {},
    }
    for file_name, data in empty_stores.items():
        _write_json(large_case_dir / "alignment" / file_name, data)

    for report_count, scale_case_id in QA_SCALE_CASES.items():
        scale_case_dir = case_root / scale_case_id
        if scale_case_dir.exists():
            shutil.rmtree(scale_case_dir)
        (scale_case_dir / "markdown").mkdir(parents=True, exist_ok=True)
        for index in range(1, report_count + 1):
            report = f"QA-SCALE-{report_count:02d}-{index:02d}"
            report_dir = scale_case_dir / "markdown" / report
            report_dir.mkdir(parents=True, exist_ok=True)
            content = (
                f"# QA scale report {index}\n\n"
                f"用于验证 {report_count} 份报告的自适应缩略图、缩放和连续滚动。\n"
            )
            (report_dir / f"{report}.md").write_text(content, encoding="utf-8")
            (report_dir / f"{report}_text_only.md").write_text(content, encoding="utf-8")
        for file_name, data in empty_stores.items():
            _write_json(scale_case_dir / "alignment" / file_name, data)

    manifest = {
        "case_id": QA_CASE_ID,
        "ready_case_id": QA_READY_CASE_ID,
        "large_case_id": QA_LARGE_CASE_ID,
        "scale_cases": {str(count): case_id for count, case_id in QA_SCALE_CASES.items()},
        "criteria_file_name": QA_CRITERIA_FILE,
        "rubric_version": rubric_version,
        "reports": QA_REPORTS,
        "large_case_reports": 51,
        "expected": {
            "1": {"scored": 5, "statistical": 4, "average": 8.375, "teacher": 3, "model": 2, "conflicts": 1, "eligible": 0, "retryable": 1},
            "2": {"scored": 3, "average": 4.5, "teacher": 2, "model": 1, "conflicts": 0, "eligible": 5, "retryable": 0},
            "3": {"scored": 1, "average": 4.0, "teacher": 1, "model": 0, "conflicts": 0, "migration_conflicts": 0},
            "4": {"scored": 0, "average": None, "teacher": 0, "model": 0, "conflicts": 0, "eligible": 8},
        },
    }
    _write_json(case_dir / "qa_fixture_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an isolated Global/Conflict QA business case.")
    parser.add_argument("--case-root", type=Path, default=Path(CASE_ROOT))
    parser.add_argument("--criteria-dir", type=Path, default=Path(CRITERIA_DIR))
    args = parser.parse_args()
    manifest = seed_qa_case(args.case_root, args.criteria_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
