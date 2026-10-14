"""Build criterion-level Global View projections from current grading state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..grade_validation import (
    validate_evidence_anchors,
    validate_scoring_point_scores,
)
from ..schemas import structured_point_deduction_capacity

JOB_STATUSES = ("pending", "running", "done", "abstained", "failed", "stale")


def _conflict_metadata(
    open_conflicts: List[Dict[str, Any]],
) -> tuple[set[str], Dict[str, int]]:
    conflict_event_ids = {
        str(event_id)
        for conflict in open_conflicts
        if conflict.get("conflict_type") != "model_rubric_migration"
        for event_id in (
            conflict.get("suspect_event_ids")
            or conflict.get("affected_event_ids")
            or conflict.get("event_ids")
            or []
        )
    }
    migration_counts: Dict[str, int] = {}
    for conflict in open_conflicts:
        if conflict.get("conflict_type") != "model_rubric_migration":
            continue
        criterion_id = str(
            conflict.get("criterion_id") or conflict.get("criteria_id") or ""
        )
        migration_counts[criterion_id] = migration_counts.get(
            criterion_id, 0
        ) + len(conflict.get("affected_event_ids") or conflict.get("event_ids") or [])
    return conflict_event_ids, migration_counts


def _jobs_by_criterion_report(
    jobs: List[Dict[str, Any]],
    criteria_name: str,
) -> Dict[tuple[str, str], Dict[str, Any]]:
    return {
        (
            str(job.get("criterion_id") or job.get("criteria_id")),
            str(job.get("report_name")),
        ): job
        for job in jobs
        if job.get("criteria_file_name") == criteria_name
    }


def _author_from_projection(
    projection_item: Dict[str, Any],
    provenance: Dict[str, Any],
) -> str:
    grader = str(
        provenance.get("grader_type")
        or projection_item.get("grader_type")
        or projection_item.get("graded_by")
        or "unknown"
    )
    if grader in {"model", "llm"}:
        return "model"
    if grader in {"teacher", "user"}:
        return "teacher"
    return "unknown"


def _projection_point(
    *,
    report_name: str,
    report_text: str,
    criterion: Dict[str, Any],
    projection_item: Dict[str, Any],
    conflict_event_ids: set[str],
) -> Dict[str, Any]:
    criterion_id = str(criterion.get("criteria_id"))
    provenance = (
        projection_item.get("provenance")
        if isinstance(projection_item.get("provenance"), dict)
        else {}
    )
    event_id = str(provenance.get("event_id") or "") or None
    author = _author_from_projection(projection_item, provenance)
    legacy_anchor_text = str(
        projection_item.get("anchor_text")
        or projection_item.get("file_original_content")
        or ""
    )
    evidence_anchors = deepcopy(projection_item.get("evidence_anchors") or [])
    if not evidence_anchors and legacy_anchor_text:
        evidence_anchors = [
            {
                "anchor_type": "text",
                "text": legacy_anchor_text,
                "occurrence": 0,
            }
        ]

    try:
        validate_evidence_anchors(
            report_text,
            evidence_anchors,
            legacy_anchor_text,
        )
        evidence_valid = True
    except (TypeError, ValueError):
        evidence_valid = False

    expected_point_count = len(criterion.get("scoring_points") or [])
    try:
        allocation = validate_scoring_point_scores(
            criterion,
            list(projection_item.get("scoring_point_scores") or []),
            float(projection_item.get("criteria_score") or 0),
            require_complete=True,
        )
        allocation_valid = not expected_point_count or len(allocation) == expected_point_count
    except (TypeError, ValueError):
        allocation_valid = False

    validity_status = (
        "valid"
        if evidence_valid and allocation_valid
        else "model_regrade_required"
        if author == "model"
        else "teacher_repair_required"
    )
    is_conflict = bool(event_id and event_id in conflict_event_ids)
    return {
        "report_id": report_name,
        "report_name": report_name,
        "criterion_id": criterion_id,
        "criteria_id": criterion_id,
        "event_id": event_id,
        "rubric_version": provenance.get("rubric_version"),
        "score": projection_item.get("criteria_score"),
        "max_score": projection_item.get("max_score", criterion.get("max_score")),
        "author": author,
        "acceptance_status": provenance.get("decision_status")
        or projection_item.get("acceptance_status"),
        "conflict": is_conflict,
        "included_in_statistics": not is_conflict and validity_status == "valid",
        "validity_status": validity_status,
        "evidence_valid": evidence_valid,
        "allocation_valid": allocation_valid,
        "anchor_text": legacy_anchor_text,
        "target_section": projection_item.get("target_section"),
        "anchor_occurrence": 0,
        "evidence_anchors": evidence_anchors,
        "score_reason": projection_item.get("score_reason") or "",
        "scoring_point_scores": deepcopy(
            projection_item.get("scoring_point_scores") or []
        ),
    }


def _point_loss_summaries(
    criterion: Dict[str, Any],
    valid_points: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    criterion_id = str(criterion.get("criteria_id"))
    summaries: List[Dict[str, Any]] = []
    for scoring_point in criterion.get("scoring_points") or []:
        point_id = str(scoring_point.get("point_id") or "")
        losses: List[float] = []
        refs: List[Dict[str, Any]] = []
        for point in valid_points:
            allocation = next(
                (
                    item
                    for item in point.get("scoring_point_scores") or []
                    if str(item.get("point_id") or "") == point_id
                ),
                None,
            )
            if not allocation:
                continue
            max_score = float(
                allocation.get("max_score")
                if allocation.get("max_score") is not None
                else structured_point_deduction_capacity(scoring_point)
            )
            loss = max(max_score - float(allocation.get("score") or 0), 0.0)
            losses.append(loss)
            if loss > 1e-9:
                refs.append(
                    {
                        "report_id": point["report_id"],
                        "report_name": point["report_name"],
                        "event_id": point.get("event_id"),
                        "criterion_id": criterion_id,
                        "point_id": point_id,
                        "loss": loss,
                        "score_reason": point.get("score_reason") or "",
                        "evidence_anchors": deepcopy(
                            point.get("evidence_anchors") or []
                        ),
                    }
                )
        summaries.append(
            {
                "point_id": point_id,
                "summary": scoring_point.get("summary") or point_id,
                "average_loss": (sum(losses) / len(losses)) if losses else None,
                "total_loss": sum(losses),
                "covered_report_count": len(losses),
                "affected_report_count": len(refs),
                "affected_report_ids": [ref["report_id"] for ref in refs],
                "refs": refs,
            }
        )
    return summaries


def _criterion_summary(
    *,
    criterion: Dict[str, Any],
    criteria_name: str,
    rubric_version: str,
    report_names: List[str],
    projections: Dict[str, Dict[str, Any]],
    report_texts: Dict[str, str],
    jobs: Dict[tuple[str, str], Dict[str, Any]],
    conflict_event_ids: set[str],
    migration_conflict_count: int,
) -> Dict[str, Any]:
    criterion_id = str(criterion.get("criteria_id"))
    points: List[Dict[str, Any]] = []
    status_counts = {status: 0 for status in JOB_STATUSES}
    eligible_report_names: List[str] = []
    retryable_report_names: List[str] = []

    for report_name in report_names:
        projection_item = next(
            (
                item
                for item in projections[report_name].get("criteria", [])
                if str(item.get("criteria_id")) == criterion_id
            ),
            None,
        )
        job_status = str(
            (jobs.get((criterion_id, report_name)) or {}).get("status") or ""
        )
        if job_status in status_counts:
            status_counts[job_status] += 1
        if projection_item:
            points.append(
                _projection_point(
                    report_name=report_name,
                    report_text=report_texts.get(report_name, ""),
                    criterion=criterion,
                    projection_item=projection_item,
                    conflict_event_ids=conflict_event_ids,
                )
            )
        else:
            if job_status not in {"pending", "running", "failed", "abstained"}:
                eligible_report_names.append(report_name)
            if job_status == "failed":
                retryable_report_names.append(report_name)

    valid_points = [
        point
        for point in points
        if point.get("included_in_statistics")
        and isinstance(point.get("score"), (int, float))
    ]
    score_values = [float(point["score"]) for point in valid_points]
    max_score = float(criterion.get("max_score") or 0)
    average_score = (sum(score_values) / len(score_values)) if score_values else None
    expected_point_count = len(criterion.get("scoring_points") or [])
    return {
        "criteria_file_name": criteria_name,
        "criterion_id": criterion_id,
        "criteria_id": criterion_id,
        "rubric_version": rubric_version,
        "max_score": max_score,
        "total_reports": len(report_names),
        "scored_count": len(points),
        "statistical_count": len(valid_points),
        "ungraded_count": max(len(report_names) - len(points), 0),
        "average_score": average_score,
        "average_loss": (
            max_score - average_score if average_score is not None else None
        ),
        "total_loss": sum(max(max_score - score, 0.0) for score in score_values),
        "teacher_count": sum(1 for point in points if point["author"] == "teacher"),
        "model_count": sum(1 for point in points if point["author"] == "model"),
        "conflict_count": sum(1 for point in points if point["conflict"]),
        "migration_conflict_count": migration_conflict_count,
        "eligible_count": len(eligible_report_names),
        "eligible_report_names": eligible_report_names,
        "retryable_count": len(retryable_report_names),
        "retryable_report_names": retryable_report_names,
        "job_counts": status_counts,
        "point_loss_summaries": _point_loss_summaries(criterion, valid_points),
        "attribution_coverage_count": sum(
            1
            for point in valid_points
            if len(point.get("scoring_point_scores") or []) == expected_point_count
        ),
        "points": points,
    }


def build_criterion_summaries(
    *,
    criteria_name: str,
    rubric_version: str,
    criteria_data: Dict[str, Any],
    report_names: List[str],
    open_conflicts: List[Dict[str, Any]],
    auto_grade_jobs: List[Dict[str, Any]],
    projections: Dict[str, Dict[str, Any]],
    report_texts: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Build the authoritative criterion projections consumed by Global View."""
    conflict_event_ids, migration_counts = _conflict_metadata(open_conflicts)
    jobs = _jobs_by_criterion_report(auto_grade_jobs, criteria_name)
    return [
        _criterion_summary(
            criterion=criterion,
            criteria_name=criteria_name,
            rubric_version=rubric_version,
            report_names=report_names,
            projections=projections,
            report_texts=report_texts,
            jobs=jobs,
            conflict_event_ids=conflict_event_ids,
            migration_conflict_count=migration_counts.get(
                str(criterion.get("criteria_id")), 0
            ),
        )
        for criterion in criteria_data.get("criteria") or []
    ]
