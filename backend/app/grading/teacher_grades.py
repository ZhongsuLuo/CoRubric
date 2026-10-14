from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Dict, Optional

from ..grade_validation import locate_rendered_evidence_text


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()


def _report_text(case_id: str, report_name: str) -> str:
    report_dir = core._report_dir(case_id, report_name)
    preferred = report_dir / f"{report_name}_text_only.md"
    path = preferred if preferred.exists() else next(iter(report_dir.glob("*_text_only.md")), None)
    return path.read_text(encoding="utf-8") if path and path.exists() else ""


def _teacher_supporting_context(report_text: str, event: Dict[str, Any]) -> str:
    """Bound report context so a teacher-selected span remains the audit authority."""
    authored = event.get("authored_grade") or {}
    anchor = str(authored.get("anchor_text") or "").strip()
    if not anchor or not report_text:
        return report_text
    start = report_text.find(anchor)
    if start < 0:
        text_anchor = next(
            (
                item
                for item in authored.get("evidence_anchors") or []
                if item.get("anchor_type", "text") == "text"
                and str(item.get("text") or "").strip() == anchor
            ),
            {},
        )
        located = locate_rendered_evidence_text(
            report_text,
            anchor,
            int(text_anchor.get("occurrence") or 0),
        )
        if located is None:
            return ""
        start, end = located
    else:
        end = start + len(anchor)
    if end - start >= core.MAX_TEACHER_SUPPORTING_CONTEXT_CHARS:
        return anchor[:core.MAX_TEACHER_SUPPORTING_CONTEXT_CHARS]
    padding = max((core.MAX_TEACHER_SUPPORTING_CONTEXT_CHARS - (end - start)) // 2, 0)
    return report_text[max(0, start - padding):min(len(report_text), end + padding)]


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())

def _active_event_by_key(store: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            event for event in store["events"]
            if event.get("idempotency_key") == key
            and event.get("event_type", "grade_authored") == "grade_authored"
            and core._event_status(event["event_id"], store["decisions"]) != "superseded"
        ),
        None,
    )


def _append_teacher_learning_decision(
    store: Dict[str, Any],
    *,
    case_id: str,
    report_name: str,
    criterion_id: str,
    rubric_version: str,
    criterion: Dict[str, Any],
    event: Dict[str, Any],
    authored: Dict[str, Any],
    evaluator: Optional[core.AlignmentEvaluator],
    validation_model: Optional[core.GradingModel],
) -> None:
    if evaluator is not None or validation_model is not None:
        evidence_complete = bool(_normalized(authored.get("score_reason")) and _normalized(authored.get("anchor_text")))
        if not evidence_complete:
            core._append_decision(store, "pending", [event["event_id"]], "teacher reason/evidence incomplete; excluded from learning", evaluator_id=core.GRADING_MODEL_ID)
        else:
            core._append_decision(store, "trusted", [event["event_id"]], "teacher grade accepted; vertical audit handles conflicts", evaluator_id=core.GRADING_MODEL_ID)

        core.evaluate_readiness_in_store(
            store,
            criterion_id,
            rubric_version,
            case_id,
            validation_model,
            evaluator,
            criterion_snapshot=criterion,
            rubric_metadata=event.get("rubric_metadata") or {},
        )
        return

    core._append_decision(
        store,
        "pending",
        [event["event_id"]],
        "teacher grade saved; consistency audit pending",
        evaluator_id=core.GRADING_MODEL_ID,
    )
    core.enqueue_consistency_audit(
        case_id,
        event.get("rubric_file_name"),
        criterion_id,
        "teacher_grade_saved",
        store,
        mode="new_teacher_grade",
        focus_event_ids=[str(event["event_id"])],
        target_rubric_version=rubric_version,
    )


def append_teacher_grade_event(
    case_id: str,
    report_name: str,
    payload: core.CaseReportGradeRequest,
    evaluator: Optional[core.AlignmentEvaluator] = None,
    validation_model: Optional[core.GradingModel] = None,
) -> Dict[str, Any]:
    if not core._report_dir(case_id, report_name).exists():
        raise FileNotFoundError(str(core._report_dir(case_id, report_name)))
    with core._lock_for(case_id):
        store = core._store(case_id)
        criteria_name, rubric_version, criteria_data = core._load_criteria(payload.criteria_file_name)
        criterion_id = str(payload.criteria_id)
        if rubric_version == "missing":
            raise ValueError(f"criteria file not found: {criteria_name}")
        snap = core._require_criterion(criteria_data, criterion_id)
        criterion_hash = core._sha256_data(snap)
        comparable_model = core._latest_comparable_model_event(
            store,
            report_name=report_name,
            criteria_file_name=criteria_name or "criteria.json",
            criterion_id=criterion_id,
            criterion_hash=criterion_hash,
        )
        max_score = float(snap["max_score"])
        score = float(payload.criteria_score or 0)
        score = min(max(score, 0.0), max_score) if max_score else max(score, 0.0)
        scoring_point_scores = core._validated_scoring_point_scores(
            snap,
            list(payload.scoring_point_scores or []),
            score,
            require_complete=True,
        )
        evidence_anchors = core._validated_evidence_anchors(
            _report_text(case_id, report_name),
            list(payload.evidence_anchors or []),
            payload.anchor_text or "",
        )
        primary_anchor = next(
            (
                str(anchor.get("text") or anchor.get("image_alt") or anchor.get("description") or "")
                for anchor in evidence_anchors
                if anchor.get("anchor_type") != "absence"
            ),
            payload.anchor_text or "",
        )
        authored = {
            "criteria_score": score,
            "score_reason": payload.score_reason or "",
            "anchor_text": primary_anchor,
            "evidence_anchors": evidence_anchors,
            "target_section": payload.target_section,
            "criteria_name": snap["criteria_name"],
            "scoring_points": deepcopy(snap["scoring_points"]),
            "scoring_aggregation": deepcopy(snap["scoring_aggregation"]),
            "matched_scoring_points": list(payload.matched_scoring_points or []),
            "matched_deduction_rules": list(payload.matched_deduction_rules or []),
            "scoring_point_scores": scoring_point_scores,
            "description": snap["description"],
            "original_description": snap["original_description"],
            "max_score": max_score,
            "section": snap.get("section") or "",
        }
        key = core._event_key(case_id, report_name, criterion_id, rubric_version, "teacher", core.GRADING_TEACHER_ID, authored)
        replaces_existing_grade = evaluator is None and validation_model is None
        existing = _active_event_by_key(store, key)
        if existing:
            prior_ids = [
                e["event_id"] for e in store["events"]
                if replaces_existing_grade
                and e["event_id"] != existing["event_id"]
                and e.get("report_name") == report_name
                and str(e.get("criterion_id")) == criterion_id
                and e.get("rubric_file_name") == criteria_name
                and e.get("event_type", "grade_authored") == "grade_authored"
                and core._event_status(e["event_id"], store["decisions"]) != "superseded"
            ]
            if prior_ids:
                core._append_decision(
                    store,
                    "superseded",
                    prior_ids,
                    "teacher replaced effective grade",
                    superseded_by=existing["event_id"],
                )
                core._supersede_conflicts_for_events(
                    store,
                    prior_ids,
                    reason="teacher replaced conflict source grade",
                    superseded_by=existing["event_id"],
                )
            job = core._audit_job(case_id, criteria_name, criterion_id, store)
            if replaces_existing_grade and job.get("status") in {"stale", "failed"}:
                core.enqueue_consistency_audit(case_id, criteria_name, criterion_id, "teacher_grade_saved", store)
            if comparable_model:
                core._record_consistency_feedback(
                    store,
                    model_event=comparable_model,
                    teacher_score=score,
                    trigger="teacher_grade_saved",
                    teacher_event_id=str(existing["event_id"]),
                )
            if prior_ids or comparable_model or job.get("status") in {"stale", "failed"}:
                core._write_store(case_id, store)
            result = core.materialize_projection(case_id, report_name, store, criteria_name, persist=True)
            return {"event": existing, "result": result, "duplicate": True}
        prior_ids = [
            e["event_id"] for e in store["events"]
            if replaces_existing_grade
            and e.get("report_name") == report_name
            and str(e.get("criterion_id")) == criterion_id
            and e.get("rubric_file_name") == criteria_name
            and e.get("event_type", "grade_authored") == "grade_authored"
            and core._event_status(e["event_id"], store["decisions"]) != "superseded"
        ]
        ev = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "event_type": "grade_authored",
            "idempotency_key": key,
            "case_id": case_id,
            "report_name": report_name,
            "criterion_id": criterion_id,
            "grader_type": "teacher",
            "grader_id": core.GRADING_TEACHER_ID,
            "rubric_file_name": criteria_name,
            "rubric_version": rubric_version,
            "candidate_parent_hash": (
                ((store.get("readiness") or {}).get("criteria") or {}).get(criterion_id, {}).get("candidate", {}).get("candidate_hash")
            ),
            "criterion_hash": criterion_hash,
            # Keep the exact model attempt that this teacher grade reviewed.
            # Audits may run after newer model events exist, so resolving this
            # relationship later by "latest event" alone is ambiguous.
            "compared_model_event_id": (
                str(comparable_model["event_id"]) if comparable_model else None
            ),
            "criterion_snapshot": snap,
            "rubric_metadata": {
                "title": criteria_data.get("title"),
                "language": criteria_data.get("language", "zh"),
                "base_score": criteria_data.get("base_score", 100),
            },
            "authored_grade": authored,
            "created_at": core._now(),
        }
        store["events"].append(ev)
        if prior_ids:
            core._append_decision(
                store,
                "superseded",
                prior_ids,
                "teacher replaced effective grade",
                superseded_by=ev["event_id"],
            )
            core._supersede_conflicts_for_events(
                store,
                prior_ids,
                reason="teacher replaced conflict source grade",
                superseded_by=ev["event_id"],
            )
        _append_teacher_learning_decision(
            store,
            case_id=case_id,
            report_name=report_name,
            criterion_id=criterion_id,
            rubric_version=rubric_version,
            criterion=snap,
            event=ev,
            authored=authored,
            evaluator=evaluator,
            validation_model=validation_model,
        )
        if comparable_model:
            core._record_consistency_feedback(
                store,
                model_event=comparable_model,
                teacher_score=score,
                trigger="teacher_grade_saved",
                teacher_event_id=str(ev["event_id"]),
            )
        core._write_store(case_id, store)
        result = core.materialize_projection(case_id, report_name, store, criteria_name, persist=True)
        return {"event": ev, "result": result, "duplicate": False}


def append_teacher_delete_event(
    case_id: str, report_name: str, payload: core.CaseReportGradeDeleteRequest
) -> Dict[str, Any]:
    """Append an immutable teacher deletion event and supersede current grade events."""
    if not core._report_dir(case_id, report_name).exists():
        raise FileNotFoundError(str(core._report_dir(case_id, report_name)))
    criterion_id = str(payload.criteria_id)
    with core._lock_for(case_id):
        store = core._store(case_id)
        criteria_name, _, _ = core._load_criteria(payload.criteria_file_name)
        prior_ids = [
            e["event_id"] for e in store["events"]
            if e.get("report_name") == report_name
            and str(e.get("criterion_id")) == criterion_id
            and e.get("rubric_file_name") == criteria_name
            and e.get("event_type", "grade_authored") != "grade_deleted"
            and core._event_status(e["event_id"], store["decisions"]) != "superseded"
        ]
        key = core._sha256_data({
            "case_id": case_id,
            "report_name": report_name,
            "criterion_id": criterion_id,
            "rubric_file_name": criteria_name,
            "event_type": "grade_deleted",
            "grader_id": core.GRADING_TEACHER_ID,
            "effective_event_ids": sorted(prior_ids),
        })
        existing = next((e for e in store["events"] if e.get("idempotency_key") == key), None)
        if existing:
            return {
                "event": existing,
                "result": core.materialize_projection(case_id, report_name, store, criteria_name, persist=True),
                "duplicate": True,
            }
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "event_type": "grade_deleted",
            "idempotency_key": key,
            "case_id": case_id,
            "report_name": report_name,
            "criterion_id": criterion_id,
            "rubric_file_name": criteria_name,
            "grader_type": "teacher",
            "grader_id": core.GRADING_TEACHER_ID,
            "effective_event_ids": sorted(prior_ids),
            "created_at": core._now(),
        }
        store["events"].append(event)
        if prior_ids:
            core._append_decision(store, "superseded", prior_ids, "teacher deleted effective grade", superseded_by=event["event_id"])
            core._supersede_conflicts_for_events(
                store,
                prior_ids,
                reason="teacher deleted conflict source grade",
                superseded_by=event["event_id"],
            )
        core._remove_consistency_feedback_for_report(
            store,
            report_name=report_name,
            criteria_file_name=criteria_name or "criteria.json",
            criterion_id=criterion_id,
            trigger_event_id=str(event["event_id"]),
        )
        core._write_store(case_id, store)
        return {
            "event": event,
            "result": core.materialize_projection(case_id, report_name, store, criteria_name, persist=True),
            "duplicate": False,
        }
