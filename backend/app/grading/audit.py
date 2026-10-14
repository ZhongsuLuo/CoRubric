from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..schemas import CriteriaItem, CriterionProposalConfirmRequest
from .proposal_sources import proposal_source_event_digest


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _audit_job_key(criteria_file_name: Optional[str], criterion_id: str) -> str:
    return core._sha256_data({
        "criteria_file_name": criteria_file_name or "criteria.json",
        "criterion_id": str(criterion_id),
    })


def enqueue_consistency_audit(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
    trigger: str,
    store: Optional[Dict[str, Any]] = None,
    *,
    write: bool = False,
    mode: Optional[str] = None,
    focus_event_ids: Optional[List[str]] = None,
    source_rubric_version: Optional[str] = None,
    target_rubric_version: Optional[str] = None,
) -> Dict[str, Any]:
    owns_store = store is None
    if store is None:
        store = core._store(case_id)
    criteria_file = criteria_file_name or "criteria.json"
    key = _audit_job_key(criteria_file, criterion_id)
    previous = (store.setdefault("audit_jobs", {}) or {}).get(key, {})
    now = core._now()
    if mode is None:
        mode = "new_teacher_grade" if trigger == "teacher_grade_saved" else "rubric_migration"
    if mode not in {"new_teacher_grade", "rubric_migration", "conflict_reassessment"}:
        raise ValueError(f"invalid consistency audit mode: {mode}")
    previous_focus_ids = (
        previous.get("focus_event_ids") or []
        if previous.get("status") in {"pending", "running"}
        else []
    )
    merged_focus_ids = list(dict.fromkeys([
        *[str(item) for item in previous_focus_ids if item],
        *[str(item) for item in (focus_event_ids or []) if item],
    ]))
    job = {
        **previous,
        "job_id": key,
        "case_id": case_id,
        "criteria_file_name": criteria_file,
        "criterion_id": str(criterion_id),
        "criteria_id": str(criterion_id),
        "status": "pending",
        "trigger": trigger,
        "mode": mode,
        "focus_event_ids": merged_focus_ids,
        "source_rubric_version": source_rubric_version or previous.get("source_rubric_version"),
        "target_rubric_version": target_rubric_version or previous.get("target_rubric_version"),
        "queued_at": previous.get("queued_at") or now,
        "updated_at": now,
    }
    store.setdefault("audit_jobs", {})[key] = job
    if write or owns_store:
        core._write_store(case_id, store)
    return job


def _audit_job(case_id: str, criteria_file_name: Optional[str], criterion_id: str, store: Dict[str, Any]) -> Dict[str, Any]:
    criteria_file = criteria_file_name or "criteria.json"
    key = _audit_job_key(criteria_file, str(criterion_id))
    return (store.get("audit_jobs") or {}).get(key, {})


def _affected_case_ids_for_criteria_file(criteria_file_name: str, criteria_ids: set[str]) -> List[str]:
    root = Path(core.CASE_ROOT)
    if not root.exists():
        return []
    affected = []
    for case_dir in root.iterdir():
        if not case_dir.is_dir():
            continue
        try:
            events = core._read_json(case_dir / "alignment" / "events.json", [])
            batches = core._read_json(
                case_dir / "alignment" / "auto_grade_batches.json", {}
            )
        except core.CorruptAlignmentStore:
            continue
        has_event = any(
            event.get("rubric_file_name") == criteria_file_name
            and str(event.get("criterion_id")) in criteria_ids
            for event in events
        )
        has_batch_target = any(
            batch.get("criteria_file_name") == criteria_file_name
            and any(
                str(target.get("criterion_id")) in criteria_ids
                for target in (batch.get("targets") or [])
            )
            for batch in (batches.values() if isinstance(batches, dict) else [])
        )
        if has_event or has_batch_target:
            affected.append(case_dir.name)
    return affected


def enqueue_consistency_audits_for_criteria_file(criteria_file_name: str, criteria_ids: List[str], trigger: str) -> List[Dict[str, Any]]:
    core._safe_name(criteria_file_name)
    wanted = {str(cid) for cid in criteria_ids}
    if not wanted:
        return []
    jobs = []
    for case_id in _affected_case_ids_for_criteria_file(criteria_file_name, wanted):
        with core._lock_for(case_id):
            store = core._store(case_id)
            _, target_rubric_version, criteria_data = core._load_criteria(criteria_file_name)
            current_by_id = {
                str(item.get("criteria_id")): item
                for item in (criteria_data.get("criteria") or [])
            }
            for criterion_id in wanted:
                has_event = any(
                    event.get("rubric_file_name") == criteria_file_name
                    and str(event.get("criterion_id")) == criterion_id
                    for event in store.get("events", [])
                )
                has_batch_target = any(
                    batch.get("criteria_file_name") == criteria_file_name
                    and any(
                        str(target.get("criterion_id")) == criterion_id
                        for target in (batch.get("targets") or [])
                    )
                    for batch in (store.get("auto_grade_batches") or {}).values()
                )
                if has_event or has_batch_target:
                    criterion = current_by_id.get(criterion_id)
                    if criterion is None:
                        continue
                    authorization = core._criterion_authorization(store, criteria_file_name, criterion_id)
                    source_rubric_version = authorization.get("authorized_rubric_version")
                    if authorization.get("state") == core.AUTH_BATCH_AUTHORIZED:
                        core._set_criterion_authorization(
                            store,
                            criteria_file_name,
                            criterion_id,
                            core.AUTH_REPAIR_REQUIRED,
                            repair_from_rubric_version=source_rubric_version,
                            repair_to_rubric_version=target_rubric_version,
                            repair_started_at=core._now(),
                        )
                        core._ensure_model_migration_conflict(
                            store,
                            criteria_file_name,
                            criterion_id,
                            source_rubric_version,
                            target_rubric_version,
                        )
                    jobs.append(enqueue_consistency_audit(
                        case_id,
                        criteria_file_name,
                        criterion_id,
                        trigger,
                        store,
                        mode="rubric_migration",
                        source_rubric_version=source_rubric_version,
                        target_rubric_version=target_rubric_version,
                    ))
                    core._register_criterion_revision(
                        case_id,
                        store,
                        criteria_file_name=criteria_file_name,
                        criterion=criterion,
                        target_rubric_version=target_rubric_version,
                        trigger=trigger,
                    )
            core._write_store(case_id, store)
    return jobs


def _recommended_scoring_point_allocation(
    criterion: Dict[str, Any],
    raw: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], str]:
    if not raw.get("recommended_scoring_point_scores"):
        return [], "not_provided"
    try:
        return core._validated_scoring_point_scores(
            criterion,
            raw.get("recommended_scoring_point_scores"),
            float(raw.get("recommended_score")),
            require_complete=True,
            require_reasons=True,
            matched_scoring_points=list(
                raw.get("recommended_matched_scoring_points") or []
            ),
            matched_deduction_rules=list(
                raw.get("recommended_matched_deduction_rules") or []
            ),
        ), "valid"
    except (TypeError, ValueError):
        return [], "invalid"


def _normalize_audit_artifact(
    event: Dict[str, Any],
    criterion: Dict[str, Any],
    rubric_version: str,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    authored = event.get("authored_grade") or {}
    raw = raw or {}
    teacher_anchor = authored.get("anchor_text") or ""
    evidence_source = "teacher_selected" if core._normalized(teacher_anchor) else "inferred"
    confidence = float(raw.get("confidence") if raw.get("confidence") is not None else (0.75 if evidence_source == "teacher_selected" else 0.4))
    fit = str(raw.get("fit") or ("consistent" if evidence_source == "teacher_selected" else "uncertain"))
    model_point_scores, allocation_status = core._model_scoring_point_explanation(
        criterion,
        raw.get("scoring_point_scores"),
        authored.get("criteria_score"),
        authored.get("scoring_point_scores"),
    )
    recommended_point_scores, recommended_allocation_status = (
        _recommended_scoring_point_allocation(criterion, raw)
    )
    model_score_reason = ""
    if not core._normalized(authored.get("score_reason")):
        model_score_reason = str(raw.get("suggested_score_reason") or "").strip()
    artifact = {
        "event_id": event["event_id"],
        "criterion_id": str(event.get("criterion_id")),
        "audited_rubric_version": rubric_version,
        "teacher_score": authored.get("criteria_score"),
        "teacher_anchor": teacher_anchor,
        "teacher_reason": authored.get("score_reason") or "",
        "model_score_reason": model_score_reason,
        "evidence_source": raw.get("evidence_source") or evidence_source,
        "inferred_evidence": raw.get("inferred_evidence") or ("" if teacher_anchor else raw.get("evidence") or ""),
        "issue_type": raw.get("issue_type"),
        "severity": raw.get("severity"),
        "matched_rules": raw.get("matched_rules") or [str(criterion.get("criteria_id"))],
        # These fields are model interpretations of the saved teacher total.
        # They deliberately stay separate from authored_grade.scoring_point_scores,
        # which represents explicit teacher-entered point scores in legacy flows.
        "model_scoring_point_scores": model_point_scores,
        "model_allocation_status": allocation_status,
        "model_matched_scoring_points": list(raw.get("matched_scoring_points") or []),
        "model_matched_deduction_rules": list(raw.get("matched_deduction_rules") or []),
        "expected_score_range": raw.get("expected_score_range"),
        "recommended_score": raw.get("recommended_score"),
        "recommended_scoring_point_scores": recommended_point_scores,
        "recommended_allocation_status": recommended_allocation_status,
        "recommended_matched_scoring_points": list(
            raw.get("recommended_matched_scoring_points") or []
        ),
        "recommended_matched_deduction_rules": list(
            raw.get("recommended_matched_deduction_rules") or []
        ),
        "reason": raw.get("reason") or "",
        "fit": fit,
        "confidence": confidence,
        "created_at": raw.get("created_at") or core._now(),
    }
    if artifact["evidence_source"] == "teacher_selected":
        artifact["audit_status"] = "trusted_clean" if fit == "consistent" and confidence >= 0.5 else "needs_review"
    else:
        artifact["audit_status"] = "trusted_clean" if fit == "consistent" and confidence >= 0.75 else "uncertain"
    return artifact


def _artifact_completes_missing_reasons(
    event: Dict[str, Any],
    criterion: Dict[str, Any],
    artifact: Dict[str, Any],
) -> bool:
    authored = event.get("authored_grade") or {}
    if (
        not core._normalized(authored.get("score_reason"))
        and not core._normalized(artifact.get("model_score_reason"))
    ):
        return False
    authored_by_id = {
        str(item.get("point_id")): item
        for item in authored.get("scoring_point_scores") or []
        if isinstance(item, dict)
    }
    missing_point_reason = any(
        not core._normalized((authored_by_id.get(str(point.get("point_id"))) or {}).get("reason"))
        for point in criterion.get("scoring_points") or []
    )
    return not missing_point_reason or (
        artifact.get("model_allocation_status") == "valid"
        and bool(artifact.get("model_scoring_point_scores"))
    )


from .rubric_refinement import (
    _pending_update_proposal_for_event,
    _proposal_is_refinement_placeholder,
    _proposal_contradicts_source_event,
    _supersede_update_proposal,
    _event_update_proposal_allowed,
    _model_comparison_for_teacher_event,
    _binary_partial_score_gap,
    _refinement_rejection_applies,
    _propose_refinement,
    _coerce_suggested_criterion,
    _store_event_update_proposal,

)


def _validated_refinement_criterion(
    refinement: Dict[str, Any], current_criterion: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if (
        refinement.get("decision") != "update_criterion"
        or not isinstance(refinement.get("suggested_criterion"), dict)
        or float(refinement.get("confidence") or 0) < 0.5
    ):
        return None
    try:
        suggested = CriteriaItem.model_validate(
            _coerce_suggested_criterion(refinement, current_criterion)
        ).model_dump(mode="json")
    except ValueError:
        return None
    if core._sha256_data(suggested) == core._sha256_data(current_criterion):
        return None
    return suggested


# Public compatibility exports retained for alignment.py and existing tests.
from .rubric_refinement import (
    _anchor_feature_flags,
    _criterion_refinement_signature,
    _event_has_pending_update_proposal,
    _proposal_match_key,
    _proposal_refinement_signature,
    record_refinement_rejection,
)


def _events_for_audit(
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion_id: str,
    rubric_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [
        event for event in store.get("events", [])
        if event.get("grader_type") == "teacher"
        and event.get("event_type", "grade_authored") == "grade_authored"
        and event.get("rubric_file_name") == criteria_file_name
        and str(event.get("criterion_id")) == str(criterion_id)
        and (rubric_version is None or event.get("rubric_version") == rubric_version)
        and core._event_status(event["event_id"], store["decisions"]) != "superseded"
    ]


def _teacher_outcome(event: Dict[str, Any]) -> Dict[str, Any]:
    grade = event.get("authored_grade") or {}
    return {
        "criteria_score": grade.get("criteria_score"),
        "scoring_point_scores": {
            str(item.get("point_id")): item.get("score")
            for item in grade.get("scoring_point_scores") or []
            if isinstance(item, dict) and item.get("point_id") is not None
        },
        "matched_scoring_points": sorted(
            str(item) for item in grade.get("matched_scoring_points") or []
        ),
        "matched_deduction_rules": sorted(
            str(item) for item in grade.get("matched_deduction_rules") or []
        ),
    }


def _teacher_outcomes_differ(prior: Dict[str, Any], new: Dict[str, Any]) -> bool:
    return _teacher_outcome(prior) != _teacher_outcome(new)


def _teacher_pattern(event: Dict[str, Any]) -> Dict[str, Any]:
    grade = event.get("authored_grade") or {}
    event_id = str(event.get("event_id") or "")
    return {
        "pattern_id": f"pattern_{event_id}",
        "event_ids": [event_id],
        "representative_event_id": event_id,
        "event_id": event_id,
        "report_name": event.get("report_name"),
        "grader_type": event.get("grader_type"),
        "criteria_score": grade.get("criteria_score"),
        "score_reason": grade.get("score_reason") or "",
        "anchor_text": grade.get("anchor_text") or "",
        "scoring_point_scores": deepcopy(grade.get("scoring_point_scores") or []),
        "matched_scoring_points": list(grade.get("matched_scoring_points") or []),
        "matched_deduction_rules": list(grade.get("matched_deduction_rules") or []),
    }


def _teacher_teacher_conflict_candidate(
    *,
    case_id: str,
    criterion: Dict[str, Any],
    focus_event: Dict[str, Any],
    teacher_events: List[Dict[str, Any]],
    evaluator: core.ConsistencyAuditEvaluator,
) -> Optional[Dict[str, Any]]:
    if _event_update_proposal_allowed(focus_event):
        return None
    candidates = [
        event
        for event in teacher_events
        if str(event.get("event_id")) != str(focus_event.get("event_id"))
        and event.get("report_name") != focus_event.get("report_name")
        and event.get("rubric_version") == focus_event.get("rubric_version")
        and str(event.get("created_at") or "") < str(focus_event.get("created_at") or "")
        and _teacher_outcomes_differ(event, focus_event)
    ]
    candidates.sort(key=lambda event: str(event.get("created_at") or ""), reverse=True)
    new_text = core._teacher_supporting_context(
        core._report_text(case_id, str(focus_event.get("report_name"))), focus_event
    )
    for prior in candidates:
        prior_text = core._teacher_supporting_context(
            core._report_text(case_id, str(prior.get("report_name"))), prior
        )
        try:
            result = evaluator.compare_teacher_cases(
                criterion,
                prior_text,
                prior.get("authored_grade") or {},
                new_text,
                focus_event.get("authored_grade") or {},
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
        confidence = float(result.get("confidence") or 0.0)
        returned_event_ids = result.get("teacher_event_ids") or result.get("event_ids")
        if returned_event_ids and {
            str(item) for item in returned_event_ids
        } != {str(prior["event_id"]), str(focus_event["event_id"])}:
            continue
        if not (
            result.get("comparable") is True
            and result.get("same_grading_pattern") is True
            and confidence >= 0.7
        ):
            continue
        return {
            "current_event_id": str(prior["event_id"]),
            "alternative_event_id": str(focus_event["event_id"]),
            "teacher_event_ids": [str(prior["event_id"]), str(focus_event["event_id"])],
            "current_pattern": _teacher_pattern(prior),
            "alternative_pattern": _teacher_pattern(focus_event),
            "reason": result.get("reason") or "Comparable teacher grading patterns have different outcomes.",
            "confidence": confidence,
        }
    return None


def _events_for_rubric_migration(
    store: Dict[str, Any], criteria_file_name: str, criterion_id: str
) -> List[Dict[str, Any]]:
    """Return current effective scores that require vertical review under a new rubric.

    Before batch authorization only teacher-authored and individually accepted model
    scores are effective. After authorization model-only scores are handled by the
    migration repair/regrade flow rather than trusted as teacher learning samples.
    """
    out: List[Dict[str, Any]] = []
    for event in store.get("events", []):
        if event.get("event_type", "grade_authored") != "grade_authored":
            continue
        if event.get("rubric_file_name") != criteria_file_name:
            continue
        if str(event.get("criterion_id")) != str(criterion_id):
            continue
        status = core._event_status(str(event.get("event_id")), store.get("decisions", []))
        if status == "superseded":
            continue
        grader_type = str(event.get("grader_type") or "")
        if grader_type == "teacher":
            out.append(event)
        elif grader_type == "model" and status == "accepted_model":
            out.append(event)
    return out


def _normalize_audit_result(
    criterion_id: str,
    artifacts: List[Dict[str, Any]],
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = raw or {}
    diagnosis = str(raw.get("diagnosis") or ("consistent" if all(a.get("audit_status") == "trusted_clean" for a in artifacts) else "inconclusive"))
    affected = [str(a["event_id"]) for a in artifacts]
    suspects = [str(x) for x in (raw.get("suspect_event_ids") or [])]
    supporting = [str(x) for x in (raw.get("supporting_event_ids") or [])]
    if diagnosis == "needs_review" and not suspects:
        suspects = affected
    elif diagnosis in {"isolated_outlier", "grading_drift"} and not suspects:
        suspects = [str(a["event_id"]) for a in artifacts if a.get("audit_status") != "trusted_clean"]
    if diagnosis in {"needs_review", "grading_drift"} and not suspects:
        suspects = [event_id for event_id in affected if event_id not in supporting]
    if not supporting and suspects:
        supporting = [event_id for event_id in affected if event_id not in suspects]
    normalized = {
        "audit_id": raw.get("audit_id") or f"audit_{uuid.uuid4().hex}",
        "criterion_id": str(criterion_id),
        "status": raw.get("status") or "complete",
        "diagnosis": diagnosis,
        "suspect_event_ids": suspects,
        "supporting_event_ids": supporting,
        "affected_event_ids": [str(x) for x in (raw.get("affected_event_ids") or affected)],
        "reason": raw.get("reason") or diagnosis,
        "confidence": float(raw.get("confidence") if raw.get("confidence") is not None else 0.0),
        "created_at": raw.get("created_at") or core._now(),
    }
    if isinstance(raw.get("conflict_candidate"), dict):
        normalized["conflict_candidate"] = deepcopy(raw["conflict_candidate"])
    if raw.get("mode"):
        normalized["mode"] = raw["mode"]
    return normalized


def _apply_consistency_audit_result(store: Dict[str, Any], audit: Dict[str, Any]) -> None:
    artifacts = store.setdefault("audit_artifacts", {})
    suspect_ids = set(audit.get("suspect_event_ids") or [])
    affected_ids = set(audit.get("affected_event_ids") or [])
    diagnosis = str(audit.get("diagnosis") or "")
    criterion_id = str(audit.get("criterion_id"))
    for conflict in store.get("conflicts", []):
        if conflict.get("status") != "open" or str(conflict.get("criterion_id")) != criterion_id:
            continue
        conflict_ids = core._conflict_event_ids(conflict)
        if diagnosis == "rubric_update_pending":
            conflict["status"] = "deferred"
            conflict["deferred_at"] = core._now()
            conflict["deferred_reason"] = "pending_rubric_update"
        elif not conflict_ids or conflict_ids.intersection(affected_ids):
            conflict["status"] = "superseded"
            conflict["superseded_at"] = core._now()
    for event_id in affected_ids:
        artifact = artifacts.get(event_id)
        if not isinstance(artifact, dict):
            continue
        if event_id in suspect_ids:
            artifact["audit_status"] = "suspect" if diagnosis == "isolated_outlier" else "needs_review"
        elif diagnosis == "rubric_under_specified":
            artifact["audit_status"] = "needs_review"
        elif artifact.get("fit") == "consistent" and float(artifact.get("confidence") or 0) >= 0.5:
            artifact["audit_status"] = "trusted_clean"
    store.setdefault("consistency_audits", []).append(audit)
    candidate = audit.get("conflict_candidate")
    if diagnosis == "teacher_teacher_conflict" and isinstance(candidate, dict):
        event_ids = [str(item) for item in candidate.get("teacher_event_ids") or []]
        if len(event_ids) != 2:
            return
        duplicate = any(
            conflict.get("status") == "open"
            and conflict.get("conflict_type") == "teacher_teacher"
            and set(str(item) for item in conflict.get("teacher_event_ids") or []) == set(event_ids)
            for conflict in store.get("conflicts", [])
        )
        if duplicate:
            return
        refs = core._conflict_event_refs(store, event_ids)
        store.setdefault("conflicts", []).append({
            "conflict_id": f"conf_{uuid.uuid4().hex}",
            "conflict_type": "teacher_teacher",
            "criterion_id": criterion_id,
            "criteria_file_name": refs[0].get("rubric_file_name") if refs else None,
            "status": "open",
            "diagnosis": "teacher_teacher_conflict",
            "event_ids": event_ids,
            "teacher_event_ids": event_ids,
            "current_event_id": candidate.get("current_event_id"),
            "alternative_event_id": candidate.get("alternative_event_id"),
            "current_pattern": deepcopy(candidate.get("current_pattern") or {}),
            "alternative_pattern": deepcopy(candidate.get("alternative_pattern") or {}),
            "suspect_event_ids": [str(candidate.get("alternative_event_id"))],
            "supporting_event_ids": [str(candidate.get("current_event_id"))],
            "affected_event_ids": event_ids,
            "suspect_events": core._conflict_event_refs(
                store, [str(candidate.get("alternative_event_id"))]
            ),
            "supporting_events": core._conflict_event_refs(
                store, [str(candidate.get("current_event_id"))]
            ),
            "affected_events": refs,
            "reason": candidate.get("reason") or audit.get("reason"),
            "confidence": candidate.get("confidence", audit.get("confidence")),
            "audited_rubric_version": refs[0].get("rubric_version") if refs else None,
            "created_at": core._now(),
        })


def run_consistency_audit_for_criterion(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
    evaluator: Optional[core.ConsistencyAuditEvaluator] = None,
) -> Dict[str, Any]:
    criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
    if rubric_version == "missing":
        raise ValueError(f"criteria file not found: {criteria_name}")
    criterion = core._require_criterion(criteria_data, str(criterion_id))
    with core._lock_for(case_id):
        store = core._store(case_id)
        key = _audit_job_key(criteria_name, str(criterion_id))
        existing_job = _audit_job(case_id, criteria_name, str(criterion_id), store)
        mode = str(existing_job.get("mode") or "rubric_migration")
        all_teacher_events = _events_for_audit(
            store,
            criteria_name or "criteria.json",
            str(criterion_id),
            rubric_version,
        )
        if mode == "new_teacher_grade":
            focus_ids = {str(item) for item in (existing_job.get("focus_event_ids") or [])}
            events = [event for event in all_teacher_events if str(event.get("event_id")) in focus_ids]
            if not events and all_teacher_events:
                events = [all_teacher_events[-1]]
        else:
            events = _events_for_rubric_migration(store, criteria_name or "criteria.json", str(criterion_id))
        events = deepcopy(events)
        for event in events:
            comparison = _model_comparison_for_teacher_event(store, event)
            if comparison:
                event["model_comparison"] = comparison
        event_ids = [str(event["event_id"]) for event in events]
        job = enqueue_consistency_audit(
            case_id,
            criteria_name,
            str(criterion_id),
            str(existing_job.get("trigger") or "manual"),
            store,
            mode=mode,
            focus_event_ids=event_ids if mode == "new_teacher_grade" else None,
            source_rubric_version=existing_job.get("source_rubric_version"),
            target_rubric_version=rubric_version,
        )
        if mode == "new_teacher_grade":
            # Starting a run consumes only the currently active focus set.
            # Retired/deleted focus IDs from an older pending job must not keep
            # this run permanently stale; genuinely new concurrent IDs will be
            # merged by enqueue_consistency_audit after this write.
            job["focus_event_ids"] = event_ids
        job["status"] = "running"
        job["started_at"] = core._now()
        job["updated_at"] = core._now()
        store.setdefault("audit_jobs", {})[key] = job
        existing_artifacts = deepcopy(store.get("audit_artifacts") or {})
        refinement_rejections = deepcopy(store.get("refinement_rejections") or [])
        core._write_store(case_id, store)

    evaluator = evaluator or core.LLMAlignmentEvaluator()
    criterion_version = core._criterion_version(criteria_data, str(criterion_id))
    artifacts = []
    try:
        # Stage 1: decide whether the rubric itself needs an update.  Conflict
        # evaluation is deliberately a later stage, because a conflict against
        # a rubric that may change is not yet meaningful.
        review_records: List[Dict[str, Any]] = []
        for event in events:
            existing_artifact = core._audit_artifact_for({"audit_artifacts": existing_artifacts, "decisions": []}, str(event["event_id"]), rubric_version)
            if existing_artifact and existing_artifact.get("audit_status") == "trusted_clean":
                review_records.append({"event": event, "existing_artifact": existing_artifact, "proposal": None, "binary_gap": None})
                continue
            report_text = core._teacher_supporting_context(
                core._report_text(case_id, str(event.get("report_name"))),
                event,
            )
            pending_update = _pending_update_proposal_for_event(case_id, str(event["event_id"]))
            invalid_pending_update = (
                _proposal_is_refinement_placeholder(pending_update)
                or _proposal_contradicts_source_event(pending_update, event)
            )
            proposal = pending_update if pending_update and not invalid_pending_update else None
            if pending_update and invalid_pending_update:
                _supersede_update_proposal(
                    case_id,
                    str(pending_update["proposal_id"]),
                    "superseded by case-based rubric refinement proposal",
                )
            binary_gap = _binary_partial_score_gap(criterion, event)
            rejected_refinement = _refinement_rejection_applies(
                {"refinement_rejections": refinement_rejections},
                criteria_name or "criteria.json",
                criterion_version,
                str(criterion_id),
                event,
                binary_gap,
            )
            update_decision = None
            if (
                mode == "new_teacher_grade"
                and proposal is None
                # If an existing proposal was just superseded because it
                # contradicts this source event, regenerate the update in the
                # same first-stage pass. Otherwise do not create a competing
                # update while one is still pending.
                and not core._pending_update_proposals_for_criterion(case_id, criteria_name, str(criterion_id))
                and not rejected_refinement
                and hasattr(evaluator, "propose_criterion_update")
                and _event_update_proposal_allowed(event)
            ):
                refinement = _propose_refinement(evaluator, report_text, criterion, event)
                update_decision = "model_execution_error_no_rubric_change"
                suggested = _validated_refinement_criterion(refinement, criterion)
                if suggested:
                    refinement["suggested_criterion"] = suggested
                    if binary_gap:
                        refinement["rubric_patch"] = {
                            **(refinement.get("rubric_patch") or {}),
                            **binary_gap,
                        }
                    candidate = {
                        "suggested_criterion": _coerce_suggested_criterion(refinement, criterion),
                        "rationale": refinement.get("rationale"),
                        "rubric_patch": refinement.get("rubric_patch"),
                    }
                    if not _proposal_is_refinement_placeholder(candidate) and not _proposal_contradicts_source_event(candidate, event):
                        proposal = _store_event_update_proposal(
                            case_id,
                            criteria_name or "criteria.json",
                            rubric_version,
                            criteria_data,
                            criterion,
                            event,
                            refinement,
                        )
                        update_decision = "rubric_update_proposed"
            review_records.append({
                "event": event,
                "existing_artifact": existing_artifact,
                "report_text": report_text,
                "proposal": proposal,
                "binary_gap": binary_gap,
                "update_decision": update_decision,
            })
        pending_updates = core._pending_update_proposals_for_criterion(case_id, criteria_name, str(criterion_id))
        has_pending_rubric_update = bool(pending_updates) or any(
            record.get("proposal") for record in review_records
        )
        conflict_candidate = None
        if mode == "new_teacher_grade" and not has_pending_rubric_update:
            for event in events:
                conflict_candidate = _teacher_teacher_conflict_candidate(
                    case_id=case_id,
                    criterion=criterion,
                    focus_event=event,
                    teacher_events=all_teacher_events,
                    evaluator=evaluator,
                )
                if conflict_candidate:
                    break
        # Stage 2: only after the update decision is settled may we evaluate
        # teacher grades against the rubric.  Pending updates generate an
        # explicit deferred audit record instead of a provisional conflict.
        for record in review_records:
            event = record["event"]
            proposal = record.get("proposal")
            binary_gap = record.get("binary_gap")
            existing_artifact = record.get("existing_artifact")
            if has_pending_rubric_update:
                artifact = deepcopy(existing_artifact) if existing_artifact else _normalize_audit_artifact(
                    event,
                    criterion,
                    rubric_version,
                    {
                        "fit": "uncertain",
                        "confidence": 0.0,
                        "issue_type": "rubric_update_pending",
                        "severity": "none",
                        "reason": "conflict evaluation deferred until the pending rubric update is confirmed or rejected",
                    },
                )
                artifact["audit_status"] = "deferred_pending_update"
                artifact["reason"] = "conflict evaluation deferred until the pending rubric update is confirmed or rejected"
            elif (
                existing_artifact
                and existing_artifact.get("audit_status") == "trusted_clean"
                and _artifact_completes_missing_reasons(event, criterion, existing_artifact)
            ):
                artifact = existing_artifact
            else:
                raw = evaluator.interpret_grade(record.get("report_text") or "", criterion, event)
                artifact = _normalize_audit_artifact(event, criterion, rubric_version, raw)
            if mode == "new_teacher_grade" and not has_pending_rubric_update:
                artifact["audit_status"] = (
                    "needs_review"
                    if conflict_candidate
                    and str(artifact.get("event_id"))
                    == str(conflict_candidate.get("alternative_event_id"))
                    else "trusted_clean"
                )
            if proposal:
                artifact["proposal_id"] = proposal.get("proposal_id")
                artifact["update_proposed"] = True
                artifact["update_issue_type"] = "rubric_under_specified_partial_score" if binary_gap else "rubric_update_proposed"
            elif record.get("update_decision"):
                artifact["update_decision"] = record["update_decision"]
            artifacts.append(artifact)
        suspect_ids = []
        if conflict_candidate:
            suspect_ids = [str(conflict_candidate["alternative_event_id"])]
        affected_ids = [str(artifact["event_id"]) for artifact in artifacts]
        # A pending rubric update is the first-class resolution path. Do not
        # surface a teacher-vs-rubric conflict until the teacher accepts or
        # rejects that update and the same historical events are re-audited.
        if has_pending_rubric_update:
            diagnosis = "rubric_update_pending"
            reason = "rubric update proposal pending teacher confirmation; conflict check deferred until the updated rubric is audited"
            suspect_ids = []
        elif conflict_candidate:
            diagnosis = "teacher_teacher_conflict"
            reason = str(conflict_candidate.get("reason") or "teacher grading patterns conflict")
            affected_ids = list(conflict_candidate["teacher_event_ids"])
        elif any(artifact.get("fit") == "uncertain" for artifact in artifacts):
            diagnosis = "inconclusive"
            reason = "vertical rubric check uncertain"
        else:
            diagnosis = "consistent"
            reason = "all teacher grades fit current rubric"
        raw_audit = {
            "mode": mode,
            "diagnosis": diagnosis,
            "suspect_event_ids": suspect_ids,
            "supporting_event_ids": [],
            "affected_event_ids": affected_ids,
            "reason": reason,
            "confidence": min([float(a.get("confidence") or 0) for a in artifacts], default=0.0),
            "conflict_candidate": conflict_candidate,
        }
        audit = _normalize_audit_result(str(criterion_id), artifacts, raw_audit)
    except Exception as exc:
        with core._lock_for(case_id):
            store = core._store(case_id)
            store.setdefault("audit_jobs", {})[key] = {
                **job,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "completed_at": core._now(),
                "updated_at": core._now(),
            }
            core._write_store(case_id, store)
        raise

    with core._lock_for(case_id):
        store = core._store(case_id)
        latest_job = (store.get("audit_jobs") or {}).get(key, {})
        current_candidates = (
            _events_for_audit(
                store,
                criteria_name or "criteria.json",
                str(criterion_id),
                rubric_version,
            )
            if mode == "new_teacher_grade"
            else _events_for_rubric_migration(store, criteria_name or "criteria.json", str(criterion_id))
        )
        current_event_ids = [
            str(event["event_id"])
            for event in current_candidates
            if mode != "new_teacher_grade" or str(event.get("event_id")) in set(event_ids)
        ]
        focus_changed = (
            mode == "new_teacher_grade"
            and set(str(item) for item in (latest_job.get("focus_event_ids") or [])) != set(event_ids)
        )
        if current_event_ids != event_ids or focus_changed:
            now = core._now()
            store.setdefault("audit_jobs", {})[key] = {
                **job,
                "status": "pending",
                "reason": "teacher_events_changed_during_audit",
                "previous_status": "stale",
                "queued_at": now,
                "updated_at": now,
            }
            core._write_store(case_id, store)
            return {"audit": audit, "artifacts": artifacts, "job": store["audit_jobs"][key]}
        for artifact in artifacts:
            store.setdefault("audit_artifacts", {})[artifact["event_id"]] = artifact
        by_event_id = {str(event.get("event_id")): event for event in store.get("events", [])}
        reports_with_generated_reasons: set[str] = set()
        for artifact in artifacts:
            generated_reason = str(artifact.get("model_score_reason") or "").strip()
            generated_point_scores = artifact.get("model_scoring_point_scores") or []
            event = by_event_id.get(str(artifact["event_id"]))
            if not event:
                continue
            authored = event.get("authored_grade") or {}
            generated = False
            if generated_reason and not core._normalized(authored.get("score_reason")):
                event["model_score_reason"] = generated_reason
                generated = True
            authored_point_scores = authored.get("scoring_point_scores") or []
            if generated_point_scores and (
                not authored_point_scores
                or any(
                    not core._normalized(item.get("reason"))
                    for item in authored_point_scores
                    if isinstance(item, dict)
                )
            ):
                event["model_scoring_point_scores"] = deepcopy(generated_point_scores)
                generated = True
            if generated:
                reports_with_generated_reasons.add(str(event.get("report_name") or ""))
        _apply_consistency_audit_result(store, audit)
        model_regrade_report_names: List[str] = []
        authorization = core._criterion_authorization(store, criteria_name, str(criterion_id))
        if (
            mode == "rubric_migration"
            and audit.get("diagnosis") == "consistent"
            and authorization.get("state") == core.AUTH_TEACHER_VALIDATION
        ):
            stale_model_events = [
                event for event in store.get("events", [])
                if event.get("grader_type") == "model"
                and event.get("rubric_file_name") == criteria_name
                and str(event.get("criterion_id")) == str(criterion_id)
                and event.get("rubric_version") != rubric_version
                and core._event_status(str(event.get("event_id")), store.get("decisions", [])) == "model_only"
            ]
            stale_model_ids = [str(event["event_id"]) for event in stale_model_events]
            model_regrade_report_names = sorted({
                str(event.get("report_name")) for event in stale_model_events if event.get("report_name")
            })
            if stale_model_ids:
                core._append_decision(
                    store,
                    "superseded",
                    stale_model_ids,
                    "unaccepted model grades invalidated by rubric migration",
                    target_rubric_version=rubric_version,
                )
        store.setdefault("audit_jobs", {})[key] = {
            **job,
            "status": "done",
            "diagnosis": audit.get("diagnosis"),
            "reason": audit.get("reason"),
            "completed_at": core._now(),
            "updated_at": core._now(),
        }
        for report_name in reports_with_generated_reasons | set(model_regrade_report_names):
            if report_name:
                core.materialize_projection(case_id, report_name, store, criteria_name, persist=True)
        core._write_store(case_id, store)
        return {
            "audit": audit,
            "artifacts": artifacts,
            "job": store["audit_jobs"][key],
            "model_regrade_report_names": model_regrade_report_names,
        }


def reassess_conflict(
    case_id: str,
    conflict_id: str,
    teacher_feedback: str = "",
    evaluator: Optional[core.ConsistencyAuditEvaluator] = None,
) -> Dict[str, Any]:
    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = next((item for item in store.get("conflicts", []) if item.get("conflict_id") == conflict_id), None)
        if not conflict:
            raise FileNotFoundError(conflict_id)
        if conflict.get("status") != "open":
            raise ValueError("conflict is not open")
        if conflict.get("conflict_type") == "teacher_teacher":
            raise ValueError(
                "teacher-teacher conflicts require direct pattern selection"
            )
        existing_reassessment = (store.get("conflict_reassessments") or {}).get(conflict.get("reassessment_id"))
        if (
            isinstance(existing_reassessment, dict)
            and str(existing_reassessment.get("teacher_feedback") or "") == teacher_feedback
        ):
            return {"conflict": deepcopy(conflict), "reassessment": deepcopy(existing_reassessment)}
        if conflict.get("conflict_type") == "model_rubric_migration":
            raise ValueError("model rubric migration conflicts cannot be disputed")
        event_ids = list(conflict.get("suspect_event_ids") or conflict.get("event_ids") or [])
        if len(event_ids) != 1:
            raise ValueError("conflict reassessment requires a single event")
        event_id = str(event_ids[0])
        event = next((item for item in store.get("events", []) if str(item.get("event_id")) == event_id), None)
        if not event:
            raise FileNotFoundError(event_id)
        criteria_name, rubric_version, criteria_data = core._load_criteria(conflict.get("criteria_file_name") or event.get("rubric_file_name"))
        criterion = core._require_criterion(criteria_data, str(event.get("criterion_id")))
        artifact = deepcopy((store.get("audit_artifacts") or {}).get(event_id) or {})
        event_snapshot = deepcopy(event)

    evaluator = evaluator or core.LLMAlignmentEvaluator()
    if not hasattr(evaluator, "reassess_conflict"):
        raise ValueError("conflict evaluator does not support reassessment")
    report_text = core._report_text(case_id, str(event_snapshot.get("report_name")))
    result = evaluator.reassess_conflict(  # type: ignore[attr-defined]
        report_text,
        criterion,
        event_snapshot,
        artifact,
        teacher_feedback,
    )
    diagnosis = str(result.get("diagnosis"))
    reassessment = {
        "reassessment_id": f"reassess_{uuid.uuid4().hex}",
        "conflict_id": conflict_id,
        "event_id": event_id,
        "criterion_id": str(event_snapshot.get("criterion_id")),
        "rubric_version": rubric_version,
        "diagnosis": diagnosis,
        "reason": result.get("reason") or diagnosis,
        "confidence": float(result.get("confidence") or 0.0),
        "teacher_feedback": teacher_feedback,
        "suggested_criterion": result.get("suggested_criterion"),
        "created_at": core._now(),
    }

    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = next((item for item in store.get("conflicts", []) if item.get("conflict_id") == conflict_id), None)
        if not conflict or conflict.get("status") != "open":
            raise ValueError("conflict changed during reassessment")
        current_event = next((item for item in store.get("events", []) if str(item.get("event_id")) == event_id), None)
        if not current_event or core._event_status(event_id, store.get("decisions", [])) == "superseded":
            raise ValueError("conflict event changed during reassessment")
        store.setdefault("conflict_reassessments", {})[reassessment["reassessment_id"]] = reassessment
        conflict["reassessment_id"] = reassessment["reassessment_id"]
        conflict["reassessment"] = deepcopy(reassessment)
        conflict["criteria_file_name"] = criteria_name
        conflict["review_status"] = "awaiting_teacher_confirmation"
        conflict["updated_at"] = core._now()

        if diagnosis == "rubric_under_specified" and isinstance(result.get("suggested_criterion"), dict):
            criteria_name, current_rubric_version, criteria_data = core._load_criteria(
                conflict.get("criteria_file_name") or current_event.get("rubric_file_name")
            )
            current_criterion = core._require_criterion(criteria_data, str(current_event.get("criterion_id")))
            proposal = _store_event_update_proposal(
                case_id,
                criteria_name or "criteria.json",
                current_rubric_version,
                criteria_data,
                current_criterion,
                current_event,
                {
                    "suggested_criterion": result["suggested_criterion"],
                    "rationale": result.get("reason") or "Conflict reassessment found the rubric under-specified.",
                    "confidence": result.get("confidence"),
                },
            )
            conflict["status"] = "deferred"
            conflict["deferred_reason"] = "pending_rubric_update"
            conflict["deferred_by_proposal_id"] = proposal.get("proposal_id")
            reassessment["proposal_id"] = proposal.get("proposal_id")
            store["conflict_reassessments"][reassessment["reassessment_id"]] = reassessment
        core._write_store(case_id, store)
        return {"conflict": deepcopy(conflict), "reassessment": reassessment}


def _selected_teacher_pattern_kind(
    conflict: Dict[str, Any], selected_pattern_id: str
) -> str:
    for kind in ("current", "alternative"):
        pattern = conflict.get(f"{kind}_pattern") or {}
        if str(pattern.get("pattern_id") or "") == selected_pattern_id:
            return kind
    raise ValueError("selected pattern does not belong to this conflict")


def _conflict_by_id(store: Dict[str, Any], conflict_id: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            item
            for item in store.get("conflicts", [])
            if item.get("conflict_id") == conflict_id
        ),
        None,
    )


def _validate_teacher_conflict_events(
    store: Dict[str, Any], conflict: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    by_id = {
        str(event.get("event_id")): event for event in store.get("events", [])
    }
    expected_file = str(conflict.get("criteria_file_name") or "")
    expected_criterion = str(conflict.get("criterion_id") or "")
    expected_version = str(conflict.get("audited_rubric_version") or "")
    pattern_ids: set[str] = set()
    for kind in ("current", "alternative"):
        pattern = conflict.get(f"{kind}_pattern") or {}
        event_ids = [str(item) for item in pattern.get("event_ids") or []]
        representative_id = str(pattern.get("representative_event_id") or "")
        if not event_ids or representative_id not in event_ids:
            raise ValueError("conflict teacher pattern is incomplete")
        pattern_ids.update(event_ids)
        for event_id in event_ids:
            event = by_id.get(event_id)
            if (
                not event
                or event.get("grader_type") != "teacher"
                or event.get("event_type", "grade_authored") != "grade_authored"
                or str(event.get("rubric_file_name") or "") != expected_file
                or str(event.get("criterion_id") or "") != expected_criterion
                or str(event.get("rubric_version") or "") != expected_version
                or core._event_status(event_id, store.get("decisions", []))
                == "superseded"
            ):
                raise ValueError("conflict pattern contains an invalid teacher event")
    if pattern_ids != {
        str(item) for item in conflict.get("teacher_event_ids") or []
    }:
        raise ValueError("conflict teacher event lineage is inconsistent")
    return by_id


def _resolve_alternative_teacher_pattern(
    case_id: str,
    conflict_id: str,
    selected_pattern_id: str,
    teacher_reason: str,
    evaluator: Optional[core.ConsistencyAuditEvaluator],
) -> Dict[str, Any]:
    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = _conflict_by_id(store, conflict_id)
        if not conflict:
            raise FileNotFoundError(conflict_id)
        if conflict.get("status") != "open":
            if (
                conflict.get("status") == "resolved"
                and conflict.get("resolution_type") == "adopt_alternative_pattern"
                and conflict.get("selected_pattern_id") == selected_pattern_id
            ):
                return {"conflict": deepcopy(conflict)}
            raise ValueError("conflict is not open")
        if conflict.get("conflict_type") != "teacher_teacher":
            raise ValueError("pattern selection requires a teacher-teacher conflict")
        if _selected_teacher_pattern_kind(conflict, selected_pattern_id) != "alternative":
            raise ValueError("selected pattern is not the alternative pattern")
        by_id = _validate_teacher_conflict_events(store, conflict)
        current_event = by_id.get(str(conflict.get("current_event_id") or ""))
        alternative_event = by_id.get(
            str(conflict.get("alternative_event_id") or "")
        )
        if not current_event or not alternative_event:
            raise ValueError("conflict teacher events are missing")
        criteria_name, rubric_version, criteria_data = core._load_criteria(
            conflict.get("criteria_file_name")
            or alternative_event.get("rubric_file_name")
        )
        if alternative_event.get("rubric_version") != rubric_version:
            raise ValueError("conflict rubric version is stale")
        criterion = core._require_criterion(
            criteria_data, str(conflict.get("criterion_id"))
        )
        conflict_snapshot = deepcopy(conflict)
        current_snapshot = deepcopy(current_event)
        alternative_snapshot = deepcopy(alternative_event)

    evaluator = evaluator or core.LLMAlignmentEvaluator()
    report_text = core._teacher_supporting_context(
        core._report_text(case_id, str(alternative_snapshot.get("report_name"))),
        alternative_snapshot,
    )
    proposal_input = deepcopy(alternative_snapshot)
    proposal_input["conflict_resolution"] = {
        "selected_pattern": "alternative",
        "current_pattern": conflict_snapshot.get("current_pattern"),
        "alternative_pattern": conflict_snapshot.get("alternative_pattern"),
        "teacher_reason": teacher_reason,
    }
    refinement = _propose_refinement(
        evaluator, report_text, criterion, proposal_input
    )
    suggested_payload = _validated_refinement_criterion(refinement, criterion)
    if not suggested_payload:
        raise ValueError(
            "selected alternative pattern did not produce a valid rubric update"
        )
    refinement["suggested_criterion"] = suggested_payload
    source_events = [current_snapshot, alternative_snapshot]
    refinement["source_event_ids"] = [
        str(event["event_id"]) for event in source_events
    ]
    refinement["source_event_digests"] = [
        proposal_source_event_digest(event) for event in source_events
    ]
    refinement["rationale"] = (
        teacher_reason
        or refinement.get("rationale")
        or "Teacher selected the alternative grading pattern."
    )
    proposal = _store_event_update_proposal(
        case_id,
        criteria_name or "criteria.json",
        rubric_version,
        criteria_data,
        criterion,
        proposal_input,
        refinement,
    )
    suggested = CriteriaItem.model_validate(proposal["suggested_criterion"])
    from .. import implicit_grading

    try:
        confirmed = implicit_grading.confirm_proposal(
            case_id,
            str(proposal["proposal_id"]),
            CriterionProposalConfirmRequest(
                proposal_version=int(proposal["proposal_version"]),
                suggested_criterion=suggested,
                target_criterion_id=str(conflict_snapshot["criterion_id"]),
                criteria_file_name=criteria_name,
                confirmed_by=core.GRADING_TEACHER_ID,
            ),
            start_revision_workers=False,
        )
    except Exception:
        _supersede_update_proposal(
            case_id,
            str(proposal["proposal_id"]),
            "alternative conflict resolution failed before commit",
        )
        raise

    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = _conflict_by_id(store, conflict_id)
        if not conflict or conflict.get("status") != "open":
            raise ValueError("conflict changed during rubric update")
        revision = core._active_revision(
            store,
            criteria_name or "criteria.json",
            str(conflict_snapshot["criterion_id"]),
        )
        if not revision:
            raise ValueError("rubric update did not register a criterion revision")
        selected_event_ids = [
            str(item)
            for item in (
                conflict.get("alternative_pattern") or {}
            ).get("event_ids")
            or []
        ]
        conflict["status"] = "resolved"
        conflict["resolution_type"] = "adopt_alternative_pattern"
        conflict["selected_pattern_id"] = selected_pattern_id
        conflict["selected_event_ids"] = selected_event_ids
        conflict["superseded_event_ids"] = [
            str(item) for item in conflict.get("teacher_event_ids") or []
        ]
        conflict["source_rubric_version"] = conflict.get("audited_rubric_version")
        conflict["target_rubric_version"] = revision.get("target_rubric_version")
        conflict["revision_id"] = revision.get("revision_id")
        conflict["resolved_by"] = "teacher"
        conflict["resolution_reason"] = teacher_reason
        conflict["resolution_proposal_id"] = proposal["proposal_id"]
        conflict["resolved_at"] = core._now()
        conflict["review_status"] = "resolved"
        core._write_store(case_id, store)
        implicit_grading.ensure_criterion_revision_workers_for_jobs([revision])
        return {
            "conflict": deepcopy(conflict),
            "proposal": deepcopy(confirmed),
        }


def _resolve_current_teacher_pattern(
    case_id: str,
    conflict_id: str,
    selected_pattern_id: str,
    teacher_reason: str,
) -> Dict[str, Any]:
    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = _conflict_by_id(store, conflict_id)
        if not conflict:
            raise FileNotFoundError(conflict_id)
        if conflict.get("status") != "open":
            if (
                conflict.get("status") == "resolved"
                and conflict.get("resolution_type") == "keep_current_pattern"
                and conflict.get("selected_pattern_id") == selected_pattern_id
            ):
                return {"conflict": deepcopy(conflict)}
            raise ValueError("conflict is not open")
        if conflict.get("conflict_type") != "teacher_teacher":
            raise ValueError("pattern selection requires a teacher-teacher conflict")
        by_id = _validate_teacher_conflict_events(store, conflict)
        current_event = by_id.get(str(conflict.get("current_event_id") or ""))
        alternative_event = by_id.get(
            str(conflict.get("alternative_event_id") or "")
        )
        if not current_event or not alternative_event:
            raise ValueError("conflict teacher events are missing")

        selected_grade = current_event.get("authored_grade") or {}
        corrected_grade = deepcopy(alternative_event.get("authored_grade") or {})
        for key in (
            "criteria_score",
            "scoring_point_scores",
            "matched_scoring_points",
            "matched_deduction_rules",
        ):
            corrected_grade[key] = deepcopy(selected_grade.get(key))
        corrected_grade["score_reason"] = (
            teacher_reason
            or selected_grade.get("score_reason")
            or "Teacher selected the established scoring pattern."
        )
        event_id = f"evt_{uuid.uuid4().hex}"
        replacement = {
            **deepcopy(alternative_event),
            "event_id": event_id,
            "idempotency_key": core._sha256_data(
                {
                    "case_id": case_id,
                    "conflict_id": conflict_id,
                    "selected_pattern_id": selected_pattern_id,
                }
            ),
            "grader_type": "teacher",
            "grader_id": core.GRADING_TEACHER_ID,
            "authorship": "teacher_conflict_adjudication",
            "source_conflict_id": conflict_id,
            "selected_pattern_event_id": str(current_event["event_id"]),
            "replaced_event_id": str(alternative_event["event_id"]),
            "authored_grade": corrected_grade,
            "created_at": core._now(),
        }
        store["events"].append(replacement)
        core._append_decision(
            store,
            "trusted",
            [event_id],
            teacher_reason or "teacher conflict adjudication",
            conflict_id=conflict_id,
            selected_pattern="current",
        )
        core._append_decision(
            store,
            "superseded",
            [str(alternative_event["event_id"])],
            "teacher conflict adjudication replaced the alternative score",
            conflict_id=conflict_id,
            superseded_by=event_id,
        )
        conflict.update(
            {
                "status": "resolved",
                "resolution_type": "keep_current_pattern",
                "selected_pattern_id": selected_pattern_id,
                "selected_event_ids": list(
                    (conflict.get("current_pattern") or {}).get("event_ids") or []
                ),
                "superseded_event_ids": list(
                    (conflict.get("alternative_pattern") or {}).get("event_ids")
                    or []
                ),
                "source_rubric_version": conflict.get("audited_rubric_version"),
                "target_rubric_version": None,
                "revision_id": None,
                "resolved_by": "teacher",
                "resolution_reason": teacher_reason,
                "resolution_event_id": event_id,
                "resolved_at": core._now(),
                "review_status": "resolved",
            }
        )
        core._write_store(case_id, store)
        projection = core.materialize_projection(
            case_id,
            str(alternative_event.get("report_name")),
            store,
            str(alternative_event.get("rubric_file_name") or "criteria.json"),
            persist=True,
        )
        return {
            "conflict": deepcopy(conflict),
            "replacement_event": deepcopy(replacement),
            "projection": projection,
        }


def resolve_conflict(
    case_id: str,
    conflict_id: str,
    selected_pattern_id: str,
    teacher_reason: str = "",
    evaluator: Optional[core.ConsistencyAuditEvaluator] = None,
) -> Dict[str, Any]:
    # Compatibility only for persisted conflicts created before pattern-based
    # teacher adjudication. New clients submit a real teacher pattern ID.
    legacy_actions = {"confirm_audit_error", "manual_exception"}
    action = selected_pattern_id if selected_pattern_id in legacy_actions else ""
    if not action:
        with core._lock_for(case_id):
            store = core._store(case_id)
            conflict = _conflict_by_id(store, conflict_id)
            if not conflict:
                raise FileNotFoundError(conflict_id)
            kind = _selected_teacher_pattern_kind(conflict, selected_pattern_id)
            action = f"select_{kind}_pattern"
    if action == "select_alternative_pattern":
        return _resolve_alternative_teacher_pattern(
            case_id,
            conflict_id,
            selected_pattern_id,
            teacher_reason,
            evaluator,
        )
    if action == "select_current_pattern":
        return _resolve_current_teacher_pattern(
            case_id, conflict_id, selected_pattern_id, teacher_reason
        )
    with core._lock_for(case_id):
        store = core._store(case_id)
        conflict = _conflict_by_id(store, conflict_id)
        if not conflict:
            raise FileNotFoundError(conflict_id)
        if conflict.get("status") != "open":
            expected_resolution = "audit_error" if action == "confirm_audit_error" else "manual_exception"
            if conflict.get("status") == "resolved" and conflict.get("resolution_type") == expected_resolution:
                reassessment = (store.get("conflict_reassessments") or {}).get(conflict.get("reassessment_id")) or {}
                artifact = (store.get("audit_artifacts") or {}).get(str(reassessment.get("event_id"))) or {}
                return {"conflict": deepcopy(conflict), "artifact": deepcopy(artifact)}
            raise ValueError("conflict is not open")
        reassessment = (store.get("conflict_reassessments") or {}).get(conflict.get("reassessment_id"))
        if not isinstance(reassessment, dict):
            raise ValueError("conflict has no reassessment")
        diagnosis = str(reassessment.get("diagnosis"))
        event_id = str(reassessment.get("event_id"))
        if action == "confirm_audit_error" and diagnosis != "audit_error":
            raise ValueError("audit-error confirmation requires an audit_error reassessment")
        if action == "manual_exception" and diagnosis != "inconclusive":
            raise ValueError("manual exception requires an inconclusive reassessment")

        artifact = (store.get("audit_artifacts") or {}).get(event_id)
        if not isinstance(artifact, dict):
            raise ValueError("conflict audit artifact is missing")
        if action == "confirm_audit_error":
            artifact["audit_status"] = "teacher_confirmed_clean"
            resolution_type = "audit_error"
        else:
            artifact["audit_status"] = "manual_exception"
            core._append_decision(
                store,
                "manual_exception",
                [event_id],
                teacher_reason or "teacher retained score after inconclusive conflict reassessment",
                conflict_id=conflict_id,
            )
            resolution_type = "manual_exception"
        artifact["resolved_conflict_id"] = conflict_id
        artifact["resolution_type"] = resolution_type
        artifact["resolution_reason"] = teacher_reason
        artifact["resolved_at"] = core._now()
        conflict["status"] = "resolved"
        conflict["resolution_type"] = resolution_type
        conflict["resolution_reason"] = teacher_reason
        conflict["resolved_at"] = core._now()
        conflict["review_status"] = "resolved"
        conflict["rejection_signature"] = core._sha256_data({
            "event_id": event_id,
            "rubric_version": reassessment.get("rubric_version"),
            "diagnosis": diagnosis,
        })
        core._write_store(case_id, store)
        return {"conflict": deepcopy(conflict), "artifact": deepcopy(artifact)}


def _activate_consistency_override_for_criterion(
    *,
    case_id: str,
    store: Dict[str, Any],
    criteria_name: str,
    rubric_version: str,
    criteria_data: Dict[str, Any],
    criterion_id: str,
    authorized_by: str,
) -> None:
    if not core._consistency_enabled():
        return
    criterion = core._require_criterion(criteria_data, criterion_id)
    readiness = (
        ((store.get("readiness") or {}).get("criteria") or {}).get(criterion_id)
        or {}
    )
    record = core._ensure_current_consistency_record(
        case_id,
        store,
        criteria_name or "criteria.json",
        criterion,
        readiness,
    )
    core.consistency.activate_override(
        record,
        authorized_by=authorized_by,
        rubric_version=rubric_version,
    )
    core.consistency.recompute_record(
        record,
        **core._consistency_policy(),
        trigger="teacher_batch_authorized",
    )


def authorize_criterion_batch(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
    authorized_by: str = "teacher",
) -> Dict[str, Any]:
    criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
    with core._lock_for(case_id):
        store = core._store(case_id)
        current = core._criterion_authorization(store, criteria_name, str(criterion_id))
        if (
            current.get("state") == core.AUTH_BATCH_AUTHORIZED
            and current.get("authorized_rubric_version") == rubric_version
        ):
            if core._consistency_enabled():
                _activate_consistency_override_for_criterion(
                    case_id=case_id,
                    store=store,
                    criteria_name=criteria_name,
                    rubric_version=rubric_version,
                    criteria_data=criteria_data,
                    criterion_id=str(criterion_id),
                    authorized_by=authorized_by,
                )
                core._write_store(case_id, store)
            return deepcopy(current)
        readiness = (
            ((store.get("readiness") or {}).get("criteria") or {}).get(
                str(criterion_id)
            )
            or {}
        )
        if readiness.get("lifecycle") != "active_selective":
            raise ValueError("criterion is not ready for batch auto-grading")
        if core._has_open_conflict(store, str(criterion_id)):
            raise ValueError("criterion has unresolved conflicts")
        item = core._set_criterion_authorization(
            store,
            criteria_name,
            str(criterion_id),
            core.AUTH_BATCH_AUTHORIZED,
            authorized_rubric_version=rubric_version,
            authorized_at=core._now(),
            authorized_by=authorized_by,
            repair_from_rubric_version=None,
            repair_to_rubric_version=None,
        )
        _activate_consistency_override_for_criterion(
            case_id=case_id,
            store=store,
            criteria_name=criteria_name,
            rubric_version=rubric_version,
            criteria_data=criteria_data,
            criterion_id=str(criterion_id),
            authorized_by=authorized_by,
        )
        core._write_store(case_id, store)
        return deepcopy(item)


def confirm_criterion_repair(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
    confirmed_by: str = "teacher",
) -> Dict[str, Any]:
    criteria_name, rubric_version, _ = core._load_criteria(criteria_file_name)
    with core._lock_for(case_id):
        store = core._store(case_id)
        authorization = core._criterion_authorization(store, criteria_name, str(criterion_id))
        if authorization.get("state") != core.AUTH_REPAIR_REQUIRED:
            if (
                authorization.get("state") == core.AUTH_BATCH_AUTHORIZED
                and authorization.get("authorized_rubric_version") == rubric_version
                and authorization.get("repair_completed_at")
            ):
                return {"authorization": deepcopy(authorization), "report_names": []}
            raise ValueError("criterion does not require repair")
        audit_job = _audit_job(case_id, criteria_name, str(criterion_id), store)
        if audit_job.get("status") != "done" or audit_job.get("target_rubric_version") != rubric_version:
            raise ValueError("rubric migration audit must complete before model repair")
        readiness = ((store.get("readiness") or {}).get("criteria") or {}).get(str(criterion_id)) or {}
        if readiness.get("lifecycle") != "active_selective" or readiness.get("rubric_version") != rubric_version:
            raise ValueError("criterion must pass readiness evaluation under the updated rubric")
        score_conflicts = [
            item for item in store.get("conflicts", [])
            if item.get("status") == "open"
            and item.get("conflict_type") != "model_rubric_migration"
            and str(item.get("criterion_id")) == str(criterion_id)
        ]
        if score_conflicts:
            raise ValueError("teacher score conflicts must be resolved before model repair")
        migration_conflicts = [
            item for item in store.get("conflicts", [])
            if item.get("status") == "open"
            and item.get("conflict_type") == "model_rubric_migration"
            and str(item.get("criterion_id")) == str(criterion_id)
        ]
        stale_model_events = [
            event for event in store.get("events", [])
            if event.get("grader_type") == "model"
            and event.get("rubric_file_name") == criteria_name
            and str(event.get("criterion_id")) == str(criterion_id)
            and event.get("rubric_version") != rubric_version
            and core._event_status(str(event.get("event_id")), store.get("decisions", [])) == "model_only"
        ]
        stale_ids = [str(event["event_id"]) for event in stale_model_events]
        if stale_ids:
            core._append_decision(
                store,
                "superseded",
                stale_ids,
                "model grades superseded for confirmed rubric migration repair",
                confirmed_by=confirmed_by,
                target_rubric_version=rubric_version,
            )
        for conflict in migration_conflicts:
            conflict["status"] = "resolved"
            conflict["resolution_type"] = "model_regrade_confirmed"
            conflict["resolved_at"] = core._now()
        report_names = sorted({str(event.get("report_name")) for event in stale_model_events if event.get("report_name")})
        next_state = core.AUTH_REPAIR_REQUIRED if report_names else core.AUTH_BATCH_AUTHORIZED
        item = core._set_criterion_authorization(
            store,
            criteria_name,
            str(criterion_id),
            next_state,
            repair_confirmed_at=core._now(),
            repair_confirmed_by=confirmed_by,
            repair_pending_report_names=report_names,
            repair_to_rubric_version=rubric_version,
            authorized_rubric_version=rubric_version if not report_names else authorization.get("authorized_rubric_version"),
            repair_completed_at=core._now() if not report_names else None,
        )
        core._write_store(case_id, store)
        for report_name in report_names:
            core.materialize_projection(case_id, report_name, store, criteria_name, persist=True)
        return {"authorization": deepcopy(item), "report_names": report_names}
