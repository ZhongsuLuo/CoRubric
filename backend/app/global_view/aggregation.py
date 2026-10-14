"""Deterministic report-level aggregation for the cohort Global View."""

from __future__ import annotations

from typing import Any, Dict, List


def build_report_summaries(
    report_names: List[str],
    criteria_data: Dict[str, Any],
    criterion_summaries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Aggregate criterion projections into one score/status record per report."""
    criteria = list(criteria_data.get("criteria") or [])
    base_score = float(criteria_data.get("base_score") or 100)
    point_by_report_criterion = {
        (str(point.get("report_id")), str(summary.get("criterion_id"))): point
        for summary in criterion_summaries
        for point in summary.get("points") or []
    }
    summaries: List[Dict[str, Any]] = []
    for report_name in report_names:
        valid_count = 0
        conflict_count = 0
        loss = 0.0
        teacher_count = 0
        model_count = 0
        analysis_reasons: List[str] = []
        criteria_scores: Dict[str, Any] = {}
        for criterion in criteria:
            criterion_id = str(criterion.get("criteria_id"))
            point = point_by_report_criterion.get((report_name, criterion_id))
            if not point:
                criteria_scores[criterion_id] = {"status": "not_graded", "score": None}
                continue
            if point.get("conflict"):
                conflict_count += 1
                criteria_scores[criterion_id] = {
                    "status": "conflict",
                    "score": None,
                    "event_id": point.get("event_id"),
                }
                continue
            if not point.get("included_in_statistics"):
                criteria_scores[criterion_id] = {
                    "status": point.get("validity_status") or "invalid",
                    "score": None,
                    "event_id": point.get("event_id"),
                    "author": point.get("author"),
                }
                continue
            score = float(point.get("score") or 0)
            max_score = float(
                point.get("max_score") or criterion.get("max_score") or 0
            )
            valid_count += 1
            loss += max(max_score - score, 0.0)
            teacher_count += int(point.get("author") == "teacher")
            model_count += int(point.get("author") == "model")
            score_reason = str(point.get("score_reason") or "").strip()
            if score_reason and score_reason not in analysis_reasons:
                analysis_reasons.append(score_reason)
            criteria_scores[criterion_id] = {
                "status": "graded",
                "score": score,
                "max_score": max_score,
                "event_id": point.get("event_id"),
                "author": point.get("author"),
            }
        total_criteria = len(criteria)
        summaries.append(
            {
                "report_id": report_name,
                "report_name": report_name,
                "analysis_summary": " ".join(analysis_reasons),
                "score": max(base_score - loss, 0.0) if valid_count else None,
                "base_score": base_score,
                "graded_criteria_count": valid_count,
                "total_criteria_count": total_criteria,
                "conflict_count": conflict_count,
                "partial": valid_count < total_criteria,
                "provisional": valid_count < total_criteria,
                "teacher_count": teacher_count,
                "model_count": model_count,
                "criteria_scores": criteria_scores,
            }
        )
    return summaries
