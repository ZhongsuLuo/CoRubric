from __future__ import annotations

"""Compatibility facade for the modular grading domains.

New workflow implementation belongs in ``backend.app.grading``. Some imported
configuration and helper names intentionally remain public here because domain
modules resolve them lazily and existing tests/consumers replace them at runtime.
"""

import json
import math
import os
import shutil
import threading
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import (
    AUTO_GRADE_MAX_WORKERS,
    AUTO_GRADE_MIN_TEACHER_SAMPLES,
    CASE_ROOT,
    CONSISTENCY_AUTOMATION_MODE,
    CONSISTENCY_CALIBRATING_REVIEW_RATE,
    CONSISTENCY_PRIOR_ALPHA,
    CONSISTENCY_PRIOR_BETA,
    CONSISTENCY_RELIABLE_REVIEW_RATE,
    CONSISTENCY_RELIABLE_THRESHOLD,
    CONSISTENCY_REVIEW_POLICY_VERSION,
    CONSISTENCY_SCORER_REVISION,
    CONSISTENCY_UNRELIABLE_REVIEW_RATE,
    CRITERIA_DIR,
    GRADING_MODEL_ID,
    GRADING_TEACHER_ID,
    READINESS_POLICY_VERSION,
)
from . import consistency
from .global_view import (
    CohortSynthesisModel,
    build_criterion_summaries,
    build_global_readiness,
    build_report_summaries,
    generate_cohort_synthesis,
)
from .grade_validation import (
    validate_evidence_anchors as _validated_evidence_anchors,
    validate_scoring_point_scores as _validated_scoring_point_scores,
)
from .grading.common import (
    AUTH_BATCH_AUTHORIZED,
    AUTH_REPAIR_REQUIRED,
    AUTH_TEACHER_VALIDATION,
    canonical_json as _canonical_json,
    natural_name_key as _natural_name_key,
    now_iso as _now,
    sha256_data as _sha256_data,
    validate_safe_name as _safe_name,
)
from .schemas import (
    CaseReportGradeDeleteRequest,
    CaseReportGradeRequest,
    CriteriaSchema,
    ScoringPointScore,
)
from .criteria_versions import criterion_version as grading_criterion_version
from .criteria_versions import rubric_version as grading_rubric_version

MAX_TEACHER_SUPPORTING_CONTEXT_CHARS = 600
from .grading.persistence import (
    AlignmentStoreError,
    CorruptAlignmentStore,
    alignment_dir as _alignment_dir,
    atomic_write_json as _atomic_write_json,
    case_dir as _case_dir,
    load_store as _store,
    lock_for as _lock_for,
    read_json as _read_json,
    report_dir as _report_dir,
    write_store as _write_store,
)


class CaseGradingResetConflict(AlignmentStoreError):
    """Raised when debug grading reset would race with active grading work."""


class ModelEvidenceUnavailable(ValueError):
    """The model returned evidence that cannot be grounded in the report."""


def case_alignment_lock(case_id: str) -> threading.RLock:
    return _lock_for(case_id)


def clear_case_grading_data(case_id: str, confirm_case_id: str) -> Dict[str, int]:
    """Debug-only reset of derived grading state for exactly one case.

    Source reports, PDFs, converted markdown, and rubric files are deliberately
    outside this operation. The exact case id must be echoed by the caller so a
    stale UI selection cannot clear another case.
    """
    _safe_name(case_id)
    if confirm_case_id != case_id:
        raise ValueError("confirm_case_id must exactly match the case_id in the URL")

    case_dir = _case_dir(case_id)
    if not case_dir.is_dir():
        raise FileNotFoundError(case_id)

    with _lock_for(case_id):
        from .grading.batch import active_worker_ids

        active_workers = active_worker_ids(case_id)
        if active_workers:
            raise CaseGradingResetConflict(
                "Auto-grading is still running for this case. Wait for it to finish before clearing grades."
            )

        try:
            batches = _read_json(_alignment_dir(case_id) / "auto_grade_batches.json", {})
        except CorruptAlignmentStore:
            batches = {}
        if isinstance(batches, dict) and any(
            isinstance(batch, dict) and batch.get("status") in {"queued", "running"}
            for batch in batches.values()
        ):
            raise CaseGradingResetConflict(
                "Auto-grading is queued or running for this case. Wait for it to finish before clearing grades."
            )

        implicit_path = case_dir / "implicit_grades.json"
        try:
            implicit_grades = _read_json(implicit_path, [])
        except CorruptAlignmentStore:
            implicit_grades = []
        if isinstance(implicit_grades, list) and any(
            isinstance(item, dict) and item.get("status") in {"queued", "inducing"}
            for item in implicit_grades
        ):
            raise CaseGradingResetConflict(
                "Rubric induction is still running for this case. Wait for it to finish before clearing grades."
            )

        root = _alignment_dir(case_id)

        def collection_size(file_name: str) -> int:
            try:
                value = _read_json(root / file_name, [])
            except CorruptAlignmentStore:
                return 0
            return len(value) if isinstance(value, (dict, list)) else 0

        proposal_path = case_dir / "criterion_proposals.json"
        try:
            proposals = _read_json(proposal_path, [])
        except CorruptAlignmentStore:
            proposals = []
        grade_files = list((case_dir / "grade_result").glob("*_grade.json"))
        candidate_dir = root / "candidates"
        candidate_files = (
            sum(1 for path in candidate_dir.rglob("*") if path.is_file())
            if candidate_dir.is_dir()
            else 0
        )
        cleared = {
            "events": collection_size("events.json"),
            "decisions": collection_size("decisions.json"),
            "conflicts": collection_size("conflicts.json"),
            "auto_grade_jobs": collection_size("auto_grade_jobs.json"),
            "auto_grade_batches": collection_size("auto_grade_batches.json"),
            "consistency_audits": collection_size("consistency_audits.json"),
            "implicit_grades": len(implicit_grades) if isinstance(implicit_grades, list) else 0,
            "criterion_proposals": len(proposals) if isinstance(proposals, list) else 0,
            "grade_files": len(grade_files),
            "candidate_files": candidate_files,
        }

        # Debug-only: replace every derived scoring store with its canonical
        # empty form while leaving source documents and rubrics untouched.
        _write_store(
            case_id,
            {
                "events": [],
                "decisions": [],
                "conflicts": [],
                "readiness": {"criteria": {}},
                "auto_grade_jobs": {},
                "auto_grade_batches": {},
                "model_grade_diagnostics": [],
                "criterion_revisions": {},
                "audit_artifacts": {},
                "consistency_audits": [],
                "audit_jobs": {},
                "refinement_rejections": [],
                "criterion_authorizations": {},
                "conflict_reassessments": {},
                "consistency": consistency.empty_store(CONSISTENCY_REVIEW_POLICY_VERSION),
            },
        )
        _atomic_write_json(implicit_path, [])
        _atomic_write_json(proposal_path, [])
        for path in grade_files:
            path.unlink()
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        return cleared


def _load_criteria(criteria_file_name: Optional[str]) -> tuple[Optional[str], str, Dict[str, Any]]:
    name = criteria_file_name or "criteria.json"
    _safe_name(name)
    # Alignment always reads the selected server-owned structured rubric.
    path = Path(CRITERIA_DIR) / name
    if not path.exists():
        return name, "missing", {"criteria": []}
    try:
        data = CriteriaSchema.model_validate(json.loads(path.read_text(encoding="utf-8"))).model_dump(mode="json")
    except (json.JSONDecodeError, ValueError) as exc:
        raise CorruptAlignmentStore(f"Corrupted criteria JSON: {path}") from exc
    return name, grading_rubric_version(CRITERIA_DIR, data), data


def _proposal_path(case_id: str) -> Path:
    return _case_dir(case_id) / "criterion_proposals.json"


def _read_proposals(case_id: str) -> List[Dict[str, Any]]:
    return _read_json(_proposal_path(case_id), [])


def _write_proposals(case_id: str, proposals: List[Dict[str, Any]]) -> None:
    _atomic_write_json(_proposal_path(case_id), proposals)


def _pending_update_proposals_for_criterion(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
) -> List[Dict[str, Any]]:
    """Find unresolved rubric updates that must precede conflict evaluation."""
    criteria_name = criteria_file_name or "criteria.json"
    return [
        proposal
        for proposal in _read_proposals(case_id)
        if proposal.get("status") == "pending"
        and proposal.get("proposal_type") == "update_criterion"
        and str(proposal.get("target_criterion_id")) == str(criterion_id)
        and str(proposal.get("criteria_file_name") or "criteria.json") == criteria_name
    ]


def _has_pending_update_for_criterion(case_id: str, criterion_id: str) -> bool:
    """Conservative cross-file gate used before any downstream grading action."""
    return any(
        proposal.get("status") == "pending"
        and proposal.get("proposal_type") == "update_criterion"
        and str(proposal.get("target_criterion_id")) == str(criterion_id)
        for proposal in _read_proposals(case_id)
    )


def _criterion_version(criteria_data: Dict[str, Any], criterion_id: Optional[str]) -> Optional[str]:
    return grading_criterion_version(CRITERIA_DIR, criteria_data, criterion_id)


def _require_criterion(criteria_data: Dict[str, Any], criterion_id: str) -> Dict[str, Any]:
    for item in criteria_data.get("criteria", []):
        if str(item.get("criteria_id")) == str(criterion_id):
            return deepcopy(item)
    raise ValueError(f"unknown criterion: {criterion_id}")


def _event_status(event_id: str, decisions: List[Dict[str, Any]]) -> str:
    status = "pending"
    for d in decisions:
        if event_id in d.get("event_ids", []):
            if d.get("decision_type") == "trusted":
                status = "trusted"
            elif d.get("decision_type") == "quarantined":
                status = "quarantined"
            elif d.get("decision_type") == "model_only":
                status = "model_only"
            elif d.get("decision_type") == "accepted_model":
                status = "accepted_model"
            elif d.get("decision_type") == "superseded":
                status = "superseded"
            elif d.get("decision_type") == "manual_exception":
                status = "manual_exception"
    return status


from .grading.consistency_state import (
    _authorization_key,
    _criterion_authorization,
    _set_criterion_authorization,
    _trusted_teacher_events,
    _audit_artifact_for,
    _event_audit_status,
    _has_audit_state,
    _learning_teacher_events,
    _confirmed_model_learning_events,
    _has_open_conflict,
    _consistency_enabled,
    _consistency_adaptive,
    _consistency_policy,
    _event_scorer_revision,
    _event_scorer_component,
    _current_scorer_component,
    _current_consistency_identity,
    _model_event_consistency_identity,
    _apply_consistency_identity,
    _ensure_current_consistency_record,
    _ensure_consistency_record,
    _record_for_model_event,
    _record_model_review_assignment,
    _criterion_full_batch_allowed,
    _hydrate_consistency_from_legacy_events,
    _latest_comparable_model_event,
    _suspend_batch_authorization_after_correction,
    _record_consistency_feedback,
    _remove_consistency_feedback_for_model_events,
    _remove_consistency_feedback_for_report,

)


from .grading.events import (
    _payload_digest,
    _event_key,
    _append_decision,
    _item_from_event,
    _conflict_event_refs,
    _conflict_event_ids,
    _conflict_is_currently_open,
    _conflict_rubric_files,
    _conflict_is_deferred_by_pending_update,
    defer_conflicts_for_pending_update,
    _ensure_model_migration_conflict,
    _supersede_conflicts_for_events,
    _calculate_file_score,
    _model_scoring_point_explanation,
    materialize_projection,

)


from .grading.adapters import (
    AlignmentEvaluator,
    ConsistencyAuditEvaluator,
    GradingModel,
    LLMAlignmentEvaluator,
    LiveGradingModel,
)
from .grading.teacher_grades import (
    _report_text,
    _teacher_supporting_context,
    _normalized,
    _active_event_by_key,
    _append_teacher_learning_decision,
    append_teacher_grade_event,
    append_teacher_delete_event,

)

from .grading.audit import (
    _audit_job,
    _audit_job_key,
    _events_for_audit,
    _events_for_rubric_migration,
    _store_event_update_proposal,
    authorize_criterion_batch,
    confirm_criterion_repair,
    enqueue_consistency_audit,
    enqueue_consistency_audits_for_criteria_file,
    reassess_conflict,
    record_refinement_rejection,
    resolve_conflict,
    run_consistency_audit_for_criterion,
)


from .grading.readiness import (
    _rubric_has_independent_grounding,
    _boundary_for,
    _rubric_only_boundary,
    _examples_with_text,
    _persist_candidate,
    _persist_rubric_only_candidate,
    _readiness_policy_matches,
    _has_teacher_history,
    _readiness_state_reusable,
    _build_readiness_state,
    _load_candidate_snapshot,
    evaluate_readiness_in_store,
    evaluate_alignment,

)


from .grading.summary import (
    _case_report_names,
    _criterion_global_summaries,
    _global_report_summaries,
    _global_readiness,
    cohort_synthesis,
    _consistency_summary_for_criteria,
    alignment_summary,
    _readiness_basis_hash,
    _grading_evidence_hash,
    _auto_grade_job_key,
    _update_auto_grade_job,

)


from .grading.revisions import (
    _active_revision,
    _criterion_revision_key,
    _register_criterion_revision,
    _revision_card_is_current,
    advance_criterion_revision,
    ensure_criterion_revision_worker,
    ensure_criterion_revision_workers_for_jobs,
    resume_criterion_revision_workers,
    run_criterion_revision_workflow,
)


from .grading.batch import (
    cancel as cancel_auto_grade_batch,
    confirm as confirm_auto_grade_batch,
    create as create_auto_grade_batch,
    ensure_worker as ensure_auto_grade_batch_worker,
    get as get_auto_grade_batch,
    list_batches as list_auto_grade_batches,
    progress as _batch_progress,
    run as run_auto_grade_batch,
    view as _batch_view,
)


from .grading.auto_grade import (
    _append_model_event,
    _boundary_admission,
    _ground_model_evidence_anchors,
    accept_model_grade_event,
    auto_grade_report,
)
