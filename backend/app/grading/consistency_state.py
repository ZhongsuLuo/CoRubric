from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Optional

from .. import consistency
from ..config import (
    CONSISTENCY_CALIBRATING_REVIEW_RATE,
    CONSISTENCY_PRIOR_ALPHA,
    CONSISTENCY_PRIOR_BETA,
    CONSISTENCY_RELIABLE_REVIEW_RATE,
    CONSISTENCY_RELIABLE_THRESHOLD,
    CONSISTENCY_REVIEW_POLICY_VERSION,
    CONSISTENCY_UNRELIABLE_REVIEW_RATE,
    GRADING_MODEL_ID,
)
from ..grade_validation import (
    validate_evidence_anchors as _validated_evidence_anchors,
    validate_scoring_point_scores as _validated_scoring_point_scores,
)
from .common import (
    AUTH_BATCH_AUTHORIZED,
    AUTH_REPAIR_REQUIRED,
    AUTH_TEACHER_VALIDATION,
    now_iso,
    sha256_data,
)

class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _authorization_key(criteria_file_name: Optional[str], criterion_id: str) -> str:
    return f"{criteria_file_name or 'criteria.json'}::{criterion_id}"


def _criterion_authorization(
    store: Dict[str, Any], criteria_file_name: Optional[str], criterion_id: str
) -> Dict[str, Any]:
    key = _authorization_key(criteria_file_name, str(criterion_id))
    item = (store.get("criterion_authorizations") or {}).get(key)
    if isinstance(item, dict):
        return item
    return {
        "criteria_file_name": criteria_file_name or "criteria.json",
        "criterion_id": str(criterion_id),
        "state": AUTH_TEACHER_VALIDATION,
        "authorized_rubric_version": None,
    }


def _set_criterion_authorization(
    store: Dict[str, Any],
    criteria_file_name: Optional[str],
    criterion_id: str,
    state: str,
    **extra: Any,
) -> Dict[str, Any]:
    if state not in {AUTH_TEACHER_VALIDATION, AUTH_BATCH_AUTHORIZED, AUTH_REPAIR_REQUIRED}:
        raise ValueError(f"invalid criterion authorization state: {state}")
    criteria_file = criteria_file_name or "criteria.json"
    key = _authorization_key(criteria_file, str(criterion_id))
    previous = _criterion_authorization(store, criteria_file, str(criterion_id))
    item = {
        **previous,
        **extra,
        "criteria_file_name": criteria_file,
        "criterion_id": str(criterion_id),
        "state": state,
        "updated_at": now_iso(),
    }
    store.setdefault("criterion_authorizations", {})[key] = item
    return item


def _trusted_teacher_events(store: Dict[str, Any], criterion_id: Optional[str] = None, rubric_version: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for ev in store["events"]:
        if ev.get("grader_type") != "teacher":
            continue
        if criterion_id is not None and str(ev.get("criterion_id")) != str(criterion_id):
            continue
        if rubric_version is not None and ev.get("rubric_version") != rubric_version:
            continue
        if core._event_status(ev["event_id"], store["decisions"]) == "trusted":
            out.append(ev)
    return out


def _audit_artifact_for(store: Dict[str, Any], event_id: str, rubric_version: Optional[str] = None) -> Optional[Dict[str, Any]]:
    artifact = (store.get("audit_artifacts") or {}).get(event_id)
    if not isinstance(artifact, dict):
        return None
    if rubric_version is not None and artifact.get("audited_rubric_version") != rubric_version:
        return None
    return artifact


def _event_audit_status(store: Dict[str, Any], event: Dict[str, Any], rubric_version: Optional[str] = None) -> str:
    event_id = str(event.get("event_id"))
    if core._event_status(event_id, store["decisions"]) == "superseded":
        return "superseded"
    artifact = _audit_artifact_for(store, event_id, rubric_version)
    if not artifact:
        return "pending"
    explicit = artifact.get("audit_status")
    if explicit:
        return str(explicit)
    if artifact.get("fit") == "consistent" and float(artifact.get("confidence") or 0) >= 0.5:
        return "trusted_clean"
    if artifact.get("fit") in {"suspect", "needs_review"}:
        return str(artifact.get("fit"))
    return "uncertain"


def _has_audit_state(store: Dict[str, Any]) -> bool:
    return bool(store.get("audit_artifacts") or store.get("consistency_audits") or store.get("audit_jobs"))


def _learning_teacher_events(store: Dict[str, Any], criterion_id: Optional[str] = None, rubric_version: Optional[str] = None) -> List[Dict[str, Any]]:
    if not _has_audit_state(store):
        return _trusted_teacher_events(store, criterion_id, rubric_version)
    out = []
    for ev in store["events"]:
        if ev.get("grader_type") != "teacher" or ev.get("event_type", "grade_authored") != "grade_authored":
            continue
        if criterion_id is not None and str(ev.get("criterion_id")) != str(criterion_id):
            continue
        audit_status = _event_audit_status(store, ev, rubric_version)
        if audit_status in {"trusted_clean", "teacher_confirmed_clean"}:
            out.append(ev)
    return out


def _confirmed_model_learning_events(
    store: Dict[str, Any],
    criterion_id: Optional[str] = None,
    rubric_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return provenance-preserving model positives eligible for construction only."""
    open_conflict_event_ids = {
        event_id
        for conflict in store.get("conflicts", [])
        if core._conflict_is_currently_open(store, conflict)
        for event_id in core._conflict_event_ids(conflict)
    }
    active_teacher_keys = {
        (
            str(event.get("rubric_file_name") or "criteria.json"),
            str(event.get("report_name") or ""),
            str(event.get("criterion_id") or ""),
        )
        for event in store.get("events", [])
        if event.get("grader_type") == "teacher"
        and event.get("event_type", "grade_authored") == "grade_authored"
        and core._event_status(str(event.get("event_id")), store.get("decisions", []))
        != "superseded"
    }
    current_by_report: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for event in store.get("events", []):
        event_id = str(event.get("event_id") or "")
        if (
            event.get("grader_type") != "model"
            or event.get("event_type", "grade_authored") != "grade_authored"
            or core._event_status(event_id, store.get("decisions", []))
            != "accepted_model"
        ):
            continue
        event_criterion_id = str(event.get("criterion_id") or "")
        event_rubric_version = str(event.get("rubric_version") or "")
        rubric_file = str(event.get("rubric_file_name") or "criteria.json")
        report_name = str(event.get("report_name") or "")
        if criterion_id is not None and event_criterion_id != str(criterion_id):
            continue
        if rubric_version is not None and event_rubric_version != str(rubric_version):
            continue
        if (rubric_file, report_name, event_criterion_id) in active_teacher_keys:
            continue
        key = (rubric_file, report_name, event_criterion_id, event_rubric_version)
        current_by_report[key] = event

    out: List[Dict[str, Any]] = []
    for event in current_by_report.values():
        event_id = str(event.get("event_id") or "")
        report_name = str(event.get("report_name") or "")
        if event_id in open_conflict_event_ids:
            continue
        artifact = (store.get("audit_artifacts") or {}).get(event_id)
        if isinstance(artifact, dict) and (
            artifact.get("audit_status")
            in {
                "quarantined",
                "suspect",
                "needs_review",
                "uncertain",
                "deferred_pending_update",
                "superseded",
            }
            or artifact.get("fit") in {"suspect", "needs_review", "uncertain"}
        ):
            continue
        authored = event.get("authored_grade") or {}
        criterion = event.get("criterion_snapshot") or {}
        try:
            score = float(authored.get("criteria_score"))
            if (
                not math.isfinite(score)
                or score < 0
                or score > float(criterion.get("max_score", 0) or 0)
                or not core._normalized(authored.get("score_reason"))
            ):
                continue
            _validated_scoring_point_scores(
                criterion,
                list(authored.get("scoring_point_scores") or []),
                score,
                require_complete=True,
            )
            _validated_evidence_anchors(
                core._report_text(str(event.get("case_id") or ""), report_name),
                list(authored.get("evidence_anchors") or []),
                str(authored.get("anchor_text") or ""),
            )
        except (TypeError, ValueError):
            continue
        out.append(event)
    return out


def _has_open_conflict(store: Dict[str, Any], criterion_id: str) -> bool:
    return any(
        c.get("conflict_type") == "teacher_teacher"
        and core._conflict_is_currently_open(store, c)
        and str(c.get("criterion_id")) == str(criterion_id)
        for c in store["conflicts"]
    )


def _consistency_enabled() -> bool:
    return core.CONSISTENCY_AUTOMATION_MODE in {"shadow", "adaptive"}


def _consistency_adaptive() -> bool:
    return core.CONSISTENCY_AUTOMATION_MODE == "adaptive"


def _consistency_policy() -> Dict[str, float]:
    policy = {
        "threshold": CONSISTENCY_RELIABLE_THRESHOLD,
        "alpha": CONSISTENCY_PRIOR_ALPHA,
        "beta": CONSISTENCY_PRIOR_BETA,
        "calibrating_review_rate": CONSISTENCY_CALIBRATING_REVIEW_RATE,
        "reliable_review_rate": CONSISTENCY_RELIABLE_REVIEW_RATE,
        "unreliable_review_rate": CONSISTENCY_UNRELIABLE_REVIEW_RATE,
    }
    consistency.validate_policy(**policy)
    return policy


def _event_scorer_revision(event: Dict[str, Any]) -> str:
    return str(
        event.get("scorer_revision")
        or consistency.build_scorer_revision(
            str(
                event.get("model_version")
                or event.get("grader_id")
                or GRADING_MODEL_ID
            ),
            str(
                event.get("scorer_component_hash")
                or sha256_data(event.get("criterion_snapshot") or {})
            ),
            core.CONSISTENCY_SCORER_REVISION,
        )
    )


def _event_scorer_component(event: Dict[str, Any]) -> str:
    return str(
        event.get("scorer_component_hash")
        or sha256_data(event.get("criterion_snapshot") or {})
    )


def _current_scorer_component(
    case_id: str,
    criterion_id: str,
    readiness: Dict[str, Any],
    criterion: Dict[str, Any],
) -> str:
    if (readiness.get("candidate") or {}).get("candidate_hash"):
        try:
            return sha256_data(
                core._load_candidate_snapshot(case_id, criterion_id, readiness)
            )
        except (FileNotFoundError, core.CorruptAlignmentStore, ValueError):
            pass
    return sha256_data(criterion)


def _current_consistency_identity(
    case_id: str,
    criteria_file_name: str,
    criterion: Dict[str, Any],
    readiness: Dict[str, Any],
) -> consistency.RecordIdentity:
    criterion_id = str(criterion.get("criteria_id"))
    scorer_component = _current_scorer_component(
        case_id, criterion_id, readiness, criterion
    )
    return consistency.RecordIdentity(
        case_id=case_id,
        criteria_file_name=criteria_file_name,
        criterion_id=criterion_id,
        criterion_hash=sha256_data(criterion),
        scorer_revision=consistency.build_scorer_revision(
            GRADING_MODEL_ID,
            scorer_component,
            core.CONSISTENCY_SCORER_REVISION,
        ),
        scorer_component_hash=scorer_component,
        model_version=GRADING_MODEL_ID,
        prompt_revision=core.CONSISTENCY_SCORER_REVISION,
    )


def _model_event_consistency_identity(
    event: Dict[str, Any],
) -> consistency.RecordIdentity:
    return consistency.RecordIdentity(
        case_id=str(event.get("case_id") or ""),
        criteria_file_name=str(event.get("rubric_file_name") or "criteria.json"),
        criterion_id=str(event.get("criterion_id") or ""),
        criterion_hash=str(
            event.get("criterion_hash")
            or sha256_data(event.get("criterion_snapshot") or {})
        ),
        scorer_revision=_event_scorer_revision(event),
        scorer_component_hash=_event_scorer_component(event),
        model_version=str(
            event.get("model_version")
            or event.get("grader_id")
            or GRADING_MODEL_ID
        ),
        prompt_revision=str(event.get("prompt_revision") or "legacy-unknown"),
    )


def _apply_consistency_identity(
    record: Dict[str, Any],
    identity: consistency.RecordIdentity,
    *,
    candidate_hash: str = "",
) -> None:
    record["model_version"] = identity.model_version
    record["candidate_hash"] = candidate_hash
    record["scorer_component_hash"] = identity.scorer_component_hash
    record["prompt_revision"] = identity.prompt_revision


def _ensure_current_consistency_record(
    case_id: str,
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion: Dict[str, Any],
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    identity = _current_consistency_identity(
        case_id, criteria_file_name, criterion, readiness
    )
    record = consistency.latest_matching_record(
        (store.get("consistency") or {}).get("criteria", {}).values(),
        identity,
    )
    if record is None:
        record = _ensure_consistency_record(
            store,
            case_id=case_id,
            criteria_file_name=criteria_file_name,
            criterion_id=identity.criterion_id,
            criterion_hash=identity.criterion_hash,
            scorer_revision=identity.scorer_revision,
        )
    _apply_consistency_identity(
        record,
        identity,
        candidate_hash=str(
            (readiness.get("candidate") or {}).get("candidate_hash") or ""
        ),
    )
    return record


def _ensure_consistency_record(
    store: Dict[str, Any],
    *,
    case_id: str,
    criteria_file_name: str,
    criterion_id: str,
    criterion_hash: str,
    scorer_revision: str,
) -> Dict[str, Any]:
    consistency_store = store.setdefault(
        "consistency", consistency.empty_store(CONSISTENCY_REVIEW_POLICY_VERSION)
    )
    return consistency.ensure_record(
        consistency_store,
        case_id=case_id,
        criteria_file_name=criteria_file_name,
        criterion_id=criterion_id,
        criterion_hash=criterion_hash,
        scorer_revision=scorer_revision,
        **_consistency_policy(),
    )


def _record_for_model_event(
    store: Dict[str, Any], event: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if not _consistency_enabled():
        return None
    identity = _model_event_consistency_identity(event)
    record = consistency.latest_matching_record(
        (store.get("consistency") or {}).get("criteria", {}).values(),
        identity,
        allow_component_compatibility=False,
    )
    if record is None:
        record = _ensure_consistency_record(
            store,
            case_id=identity.case_id,
            criteria_file_name=identity.criteria_file_name,
            criterion_id=identity.criterion_id,
            criterion_hash=identity.criterion_hash,
            scorer_revision=identity.scorer_revision,
        )
    event_identity = replace(
        identity,
        prompt_revision=str(
            event.get("prompt_revision")
            or record.get("prompt_revision")
            or "legacy-unknown"
        ),
    )
    _apply_consistency_identity(
        record,
        event_identity,
        candidate_hash=str(event.get("candidate_hash") or ""),
    )
    return record


def _record_model_review_assignment(store: Dict[str, Any], event: Dict[str, Any]) -> None:
    record = _record_for_model_event(store, event)
    if not record:
        return
    consistency.assign_review(
        record,
        model_event_id=str(event["event_id"]),
        report_name=str(event.get("report_name") or ""),
        policy_version=CONSISTENCY_REVIEW_POLICY_VERSION,
        conflict_override=_has_open_conflict(store, str(event.get("criterion_id") or "")),
    )


def _criterion_full_batch_allowed(
    store: Dict[str, Any],
    *,
    case_id: str,
    criteria_file_name: str,
    rubric_version: str,
    criterion: Dict[str, Any],
) -> bool:
    criterion_id = str(criterion.get("criteria_id"))
    if _has_open_conflict(store, criterion_id):
        return False
    authorization = _criterion_authorization(store, criteria_file_name, criterion_id)
    readiness = ((store.get("readiness") or {}).get("criteria") or {}).get(criterion_id) or {}
    if readiness and readiness.get("lifecycle") != "active_selective":
        return False
    authorized_version = authorization.get("authorized_rubric_version")
    if (
        authorization.get("state") == AUTH_BATCH_AUTHORIZED
        and (not authorized_version or authorized_version == rubric_version)
    ):
        return True
    identity = _current_consistency_identity(
        case_id, criteria_file_name, criterion, readiness
    )
    return any(
        consistency.record_matches_identity(record, identity)
        and record.get("state") == consistency.STATE_RELIABLE
        for record in (store.get("consistency") or {}).get("criteria", {}).values()
    )


def _hydrate_consistency_from_legacy_events(store: Dict[str, Any]) -> None:
    """Idempotently rebuild safe consistency evidence from pre-feature events.

    Accepted model events are unambiguous positive evidence. A teacher event is
    rebuilt only when exactly one older model event has the same report,
    criterion, rubric file, and criterion snapshot hash. Ambiguous history stays
    audit-only rather than inflating agreement counts.
    """
    events = [
        event
        for event in store.get("events", [])
        if event.get("event_type", "grade_authored") == "grade_authored"
    ]
    model_events = [event for event in events if event.get("grader_type") == "model"]
    active_teacher_events = [
        event
        for event in events
        if event.get("grader_type") == "teacher"
        and core._event_status(str(event.get("event_id") or ""), store.get("decisions", []))
        != "superseded"
    ]

    def apply_outcome(
        model_event: Dict[str, Any],
        *,
        teacher_score: float,
        trigger: str,
        teacher_event_id: Optional[str] = None,
    ) -> None:
        record = _record_for_model_event(store, model_event)
        if not record:
            return
        model_score = float(
            (model_event.get("authored_grade") or {}).get("criteria_score", 0) or 0
        )
        verdict = (
            "accepted"
            if consistency.scores_equal(model_score, teacher_score)
            else "corrected"
        )
        current = record.get("outcomes", {}).get(str(model_event.get("event_id") or ""))
        if (
            current
            and current.get("verdict") == verdict
            and consistency.scores_equal(float(current.get("model_score", 0)), model_score)
            and consistency.scores_equal(float(current.get("teacher_score", 0)), teacher_score)
        ):
            return
        changed = consistency.upsert_outcome(
            record,
            model_event_id=str(model_event.get("event_id") or ""),
            report_name=str(model_event.get("report_name") or ""),
            verdict=verdict,
            model_score=model_score,
            teacher_score=teacher_score,
            trigger=trigger,
            teacher_event_id=teacher_event_id,
        )
        if changed:
            consistency.recompute_record(
                record,
                **_consistency_policy(),
                trigger=trigger,
                trigger_event_id=teacher_event_id or str(model_event.get("event_id") or ""),
            )

    for model_event in model_events:
        event_id = str(model_event.get("event_id") or "")
        has_active_teacher = any(
            teacher.get("report_name") == model_event.get("report_name")
            and teacher.get("rubric_file_name") == model_event.get("rubric_file_name")
            and str(teacher.get("criterion_id")) == str(model_event.get("criterion_id"))
            for teacher in active_teacher_events
        )
        if (
            event_id
            and not has_active_teacher
            and core._event_status(event_id, store.get("decisions", [])) == "accepted_model"
        ):
            apply_outcome(
                model_event,
                teacher_score=float(
                    (model_event.get("authored_grade") or {}).get("criteria_score", 0) or 0
                ),
                trigger="legacy_accepted_model_rebuild",
            )

    for teacher_event in active_teacher_events:
        teacher_event_id = str(teacher_event.get("event_id") or "")
        if not teacher_event_id:
            continue
        teacher_hash = str(
            teacher_event.get("criterion_hash")
            or sha256_data(teacher_event.get("criterion_snapshot") or {})
        )
        compatible = [
            event
            for event in model_events
            if event.get("report_name") == teacher_event.get("report_name")
            and event.get("rubric_file_name") == teacher_event.get("rubric_file_name")
            and str(event.get("criterion_id")) == str(teacher_event.get("criterion_id"))
            and str(
                event.get("criterion_hash")
                or sha256_data(event.get("criterion_snapshot") or {})
            )
            == teacher_hash
            and str(event.get("created_at") or "")
            <= str(teacher_event.get("created_at") or "")
        ]
        if len(compatible) != 1:
            continue
        apply_outcome(
            compatible[0],
            teacher_score=float(
                (teacher_event.get("authored_grade") or {}).get("criteria_score", 0) or 0
            ),
            trigger="legacy_teacher_event_rebuild",
            teacher_event_id=teacher_event_id,
        )


def _latest_comparable_model_event(
    store: Dict[str, Any],
    *,
    report_name: str,
    criteria_file_name: str,
    criterion_id: str,
    criterion_hash: str,
) -> Optional[Dict[str, Any]]:
    candidates = [
        event
        for event in store.get("events", [])
        if event.get("event_type", "grade_authored") == "grade_authored"
        and event.get("grader_type") == "model"
        and event.get("report_name") == report_name
        and event.get("rubric_file_name") == criteria_file_name
        and str(event.get("criterion_id")) == str(criterion_id)
        and str(event.get("criterion_hash") or sha256_data(event.get("criterion_snapshot") or {}))
        == criterion_hash
    ]
    return candidates[-1] if candidates else None


def _suspend_batch_authorization_after_correction(
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion_id: str,
    trigger_event_id: Optional[str],
) -> None:
    authorization = _criterion_authorization(store, criteria_file_name, criterion_id)
    if authorization.get("state") != AUTH_BATCH_AUTHORIZED:
        return
    _set_criterion_authorization(
        store,
        criteria_file_name,
        criterion_id,
        AUTH_TEACHER_VALIDATION,
        previous_authorized_rubric_version=authorization.get("authorized_rubric_version"),
        authorized_rubric_version=None,
        suspended_at=now_iso(),
        suspended_reason="teacher_corrected_model_score",
        suspended_by_event_id=trigger_event_id,
    )


def _record_consistency_feedback(
    store: Dict[str, Any],
    *,
    model_event: Dict[str, Any],
    teacher_score: float,
    trigger: str,
    teacher_event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    record = _record_for_model_event(store, model_event)
    if not record:
        return None
    model_score = float((model_event.get("authored_grade") or {}).get("criteria_score", 0) or 0)
    verdict = "accepted" if consistency.scores_equal(model_score, teacher_score) else "corrected"
    consistency.upsert_outcome(
        record,
        model_event_id=str(model_event["event_id"]),
        report_name=str(model_event.get("report_name") or ""),
        verdict=verdict,
        model_score=model_score,
        teacher_score=float(teacher_score),
        trigger=trigger,
        teacher_event_id=teacher_event_id,
    )
    assignment = record.setdefault("assignments", {}).get(str(model_event["event_id"]))
    if assignment:
        assignment["reviewed_at"] = now_iso()
        assignment["verdict"] = verdict
    if verdict == "corrected":
        consistency.release_override(
            record,
            reason="teacher_corrected_model_score",
            trigger_event_id=teacher_event_id,
        )
        _suspend_batch_authorization_after_correction(
            store,
            str(model_event.get("rubric_file_name") or "criteria.json"),
            str(model_event.get("criterion_id") or ""),
            teacher_event_id,
        )
    consistency.recompute_record(
        record,
        **_consistency_policy(),
        trigger=trigger,
        trigger_event_id=teacher_event_id or str(model_event.get("event_id") or ""),
    )
    return record


def _remove_consistency_feedback_for_model_events(
    store: Dict[str, Any], model_event_ids: List[str], trigger_event_id: str
) -> None:
    if not _consistency_enabled() or not model_event_ids:
        return
    for record in (store.get("consistency") or {}).get("criteria", {}).values():
        if consistency.remove_outcomes(record, model_event_ids, trigger_event_id=trigger_event_id):
            consistency.recompute_record(
                record,
                **_consistency_policy(),
                trigger="teacher_grade_deleted",
                trigger_event_id=trigger_event_id,
            )


def _remove_consistency_feedback_for_report(
    store: Dict[str, Any],
    *,
    report_name: str,
    criteria_file_name: str,
    criterion_id: str,
    trigger_event_id: str,
) -> None:
    if not _consistency_enabled():
        return
    for record in (store.get("consistency") or {}).get("criteria", {}).values():
        if (
            record.get("criteria_file_name") != criteria_file_name
            or str(record.get("criterion_id")) != str(criterion_id)
        ):
            continue
        model_event_ids = [
            str(model_event_id)
            for model_event_id, outcome in record.get("outcomes", {}).items()
            if outcome.get("report_name") == report_name
        ]
        if consistency.remove_outcomes(record, model_event_ids, trigger_event_id=trigger_event_id):
            consistency.recompute_record(
                record,
                **_consistency_policy(),
                trigger="teacher_grade_deleted",
                trigger_event_id=trigger_event_id,
            )
