from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict, List, Optional


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _case_report_names(case_id: str) -> List[str]:
    markdown_root = core._case_dir(case_id) / "markdown"
    if not markdown_root.exists():
        return []
    return sorted(
        (path.name for path in markdown_root.iterdir() if path.is_dir()),
        key=core._natural_name_key,
    )


def _criterion_global_summaries(
    case_id: str,
    store: Dict[str, Any],
    criteria_file_name: Optional[str],
    open_conflicts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
    if rubric_version == "missing":
        return []
    report_names = _case_report_names(case_id)
    projections = {
        report_name: core.materialize_projection(case_id, report_name, store, criteria_name)
        for report_name in report_names
    }
    return core.build_criterion_summaries(
        criteria_name=criteria_name,
        rubric_version=rubric_version,
        criteria_data=criteria_data,
        report_names=report_names,
        open_conflicts=open_conflicts,
        auto_grade_jobs=list((store.get("auto_grade_jobs") or {}).values()),
        projections=projections,
        report_texts={
            report_name: core._report_text(case_id, report_name)
            for report_name in report_names
        },
    )


def _global_report_summaries(
    case_id: str,
    criteria_file_name: Optional[str],
    criteria_data: Dict[str, Any],
    criterion_summaries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    del criteria_file_name  # Retained in the private signature for compatibility.
    return core.build_report_summaries(
        _case_report_names(case_id),
        criteria_data,
        criterion_summaries,
    )


def _global_readiness(
    case_id: str,
    criteria_file_name: Optional[str],
    authorizations: List[Dict[str, Any]],
    open_conflicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    criteria_name = criteria_file_name or "criteria.json"
    return core.build_global_readiness(
        criteria_name=criteria_name,
        proposals=core._read_proposals(case_id),
        authorizations=authorizations,
        open_conflicts=open_conflicts,
        repair_required_state=core.AUTH_REPAIR_REQUIRED,
    )


def _valid_open_teacher_conflict(
    store: Dict[str, Any], conflict: Dict[str, Any]
) -> bool:
    if conflict.get("conflict_type") != "teacher_teacher":
        return False
    by_id = {
        str(event.get("event_id")): event for event in store.get("events", [])
    }
    expected_ids = {str(item) for item in conflict.get("teacher_event_ids") or []}
    pattern_ids: set[str] = set()
    for kind in ("current", "alternative"):
        pattern = conflict.get(f"{kind}_pattern") or {}
        event_ids = {str(item) for item in pattern.get("event_ids") or []}
        representative_id = str(pattern.get("representative_event_id") or "")
        if not pattern.get("pattern_id") or not event_ids or representative_id not in event_ids:
            return False
        pattern_ids.update(event_ids)
    if len(expected_ids) < 2 or pattern_ids != expected_ids:
        return False
    return all(
        (event := by_id.get(event_id)) is not None
        and event.get("grader_type") == "teacher"
        and event.get("event_type", "grade_authored") == "grade_authored"
        and str(event.get("rubric_file_name") or "")
        == str(conflict.get("criteria_file_name") or "")
        and str(event.get("criterion_id") or "")
        == str(conflict.get("criterion_id") or "")
        and str(event.get("rubric_version") or "")
        == str(conflict.get("audited_rubric_version") or "")
        and core._event_status(event_id, store.get("decisions", [])) != "superseded"
        for event_id in expected_ids
    )


def cohort_synthesis(
    case_id: str,
    criteria_file_name: Optional[str] = None,
    criterion_id: Optional[str] = None,
    model: Optional[core.CohortSynthesisModel] = None,
) -> Dict[str, Any]:
    summary = alignment_summary(case_id, criteria_file_name=criteria_file_name)
    if not summary.get("global_readiness", {}).get("allowed", True):
        raise ValueError("cohort synthesis requires all teacher confirmations to be complete")
    criteria_name, _, criteria_data = core._load_criteria(criteria_file_name)
    return core.generate_cohort_synthesis(
        case_id=case_id,
        criteria_name=criteria_name,
        criterion_id=criterion_id,
        criteria_data=criteria_data,
        criterion_summaries=summary.get("criterion_global_summaries") or [],
        projection_hash=summary.get("global_projection_hash"),
        model_id=core.GRADING_MODEL_ID,
        qa_mode=os.getenv("GLOBAL_SYNTHESIS_QA", "").strip().lower()
        in {"1", "true", "yes"},
        model=model,
    )


def _consistency_summary_for_criteria(
    case_id: str,
    store: Dict[str, Any],
    criteria_file_name: Optional[str],
    rubric_version: str,
    criteria_data: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not core._consistency_enabled():
        return [], []
    criteria_name = criteria_file_name or "criteria.json"
    records = (store.get("consistency") or {}).get("criteria", {})
    states: List[Dict[str, Any]] = []
    assignments: List[Dict[str, Any]] = []
    readiness_by_id = ((store.get("readiness") or {}).get("criteria") or {})
    for criterion in criteria_data.get("criteria", []):
        criterion_id = str(criterion.get("criteria_id"))
        readiness = readiness_by_id.get(criterion_id) or {}
        candidate_hash = str((readiness.get("candidate") or {}).get("candidate_hash") or "")
        identity = core._current_consistency_identity(
            case_id, criteria_name, criterion, readiness
        )
        record = core.consistency.latest_matching_record(
            records.values(),
            identity,
        )
        if record is None:
            record = core._ensure_consistency_record(
                store,
                case_id=case_id,
                criteria_file_name=criteria_name,
                criterion_id=criterion_id,
                criterion_hash=identity.criterion_hash,
                scorer_revision=identity.scorer_revision,
            )
            core._apply_consistency_identity(
                record,
                identity,
                candidate_hash=candidate_hash,
            )
        authorization = core._criterion_authorization(store, criteria_name, criterion_id)
        authorized_version = authorization.get("authorized_rubric_version")
        authorization_matches = not authorized_version or authorized_version == rubric_version
        if (
            authorization.get("state") == core.AUTH_BATCH_AUTHORIZED
            and authorization_matches
            and not (record.get("override") or {}).get("active")
        ):
            core.consistency.activate_override(
                record,
                authorized_by=str(authorization.get("authorized_by") or core.GRADING_TEACHER_ID),
                rubric_version=str(authorization.get("authorized_rubric_version") or ""),
            )
            core.consistency.recompute_record(
                record,
                **core._consistency_policy(),
                trigger="legacy_batch_authorization_loaded",
            )
        has_conflict = core._has_open_conflict(store, criterion_id)
        item = core.consistency.summary(record, has_open_conflict=has_conflict)
        item["automation_mode"] = core.CONSISTENCY_AUTOMATION_MODE
        item["full_batch_recommended"] = core._consistency_adaptive() and core._criterion_full_batch_allowed(
            store,
            case_id=case_id,
            criteria_file_name=criteria_name,
            rubric_version=rubric_version,
            criterion=criterion,
        )
        states.append(item)
        for assignment in record.get("assignments", {}).values():
            visible_assignment = deepcopy(assignment)
            if has_conflict and not visible_assignment.get("reviewed_at"):
                visible_assignment["review_rate"] = 1.0
                visible_assignment["review_required"] = True
                visible_assignment["conflict_override"] = True
            assignments.append(
                {
                    **visible_assignment,
                    "criteria_file_name": criteria_name,
                    "criterion_hash": identity.criterion_hash,
                    "scorer_revision": record.get("scorer_revision"),
                }
            )
    return states, assignments


def alignment_summary(
    case_id: str,
    store: Optional[Dict[str, Any]] = None,
    criteria_file_name: Optional[str] = None,
) -> Dict[str, Any]:
    store = store or core._store(case_id)
    open_conflicts = []
    for conflict in store.get("conflicts", []):
        if not _valid_open_teacher_conflict(store, conflict):
            continue
        if not core._conflict_is_currently_open(store, conflict):
            continue
        # A conflict is only meaningful against a confirmed rubric. Older
        # persisted conflicts are hidden immediately once a matching update is
        # pending, even before the follow-up audit has had a chance to retire
        # their stored record.
        if core._conflict_is_deferred_by_pending_update(case_id, store, conflict):
            continue
        # Older persisted conflicts may predate event-reference enrichment. Rebuild
        # these refs at read time so clients always receive report names, scores,
        # and structured-rule references for navigation and explanation.
        item = deepcopy(conflict)
        item["suspect_events"] = core._conflict_event_refs(store, item.get("suspect_event_ids") or [])
        item["supporting_events"] = core._conflict_event_refs(store, item.get("supporting_event_ids") or [])
        item["affected_events"] = core._conflict_event_refs(store, item.get("affected_event_ids") or item.get("event_ids") or [])
        if not item.get("criteria_file_name"):
            refs = item["suspect_events"] or item["affected_events"]
            if refs:
                item["criteria_file_name"] = refs[0].get("rubric_file_name")
        open_conflicts.append(item)
    readiness = deepcopy(store.get("readiness", {"criteria": {}}))
    readiness_criteria = readiness.setdefault("criteria", {})
    criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
    if rubric_version != "missing":
        rubric_metadata = {
            "title": criteria_data.get("title"),
            "background": criteria_data.get("background"),
            "language": criteria_data.get("language"),
        }
        for criterion in criteria_data.get("criteria", []):
            criterion_id = str(criterion.get("criteria_id"))
            current_state = readiness_criteria.get(criterion_id)
            if current_state and not core._readiness_state_reusable(
                store, criterion_id, rubric_version, current_state
            ):
                current_state = core._build_readiness_state(
                    "evaluating",
                    criterion_id,
                    rubric_version,
                    "readiness_basis_changed_requires_revalidation",
                    previous_candidate_hash=(
                        (current_state.get("candidate") or {}).get("candidate_hash")
                    ),
                )
                current_state["readiness_basis_hash"] = _readiness_basis_hash(
                    store, rubric_version
                )
                readiness_criteria[criterion_id] = current_state
            if (
                current_state
                or core.AUTO_GRADE_MIN_TEACHER_SAMPLES != 0
                or core._has_teacher_history(store, criterion_id)
                or core._confirmed_model_learning_events(
                    store, criterion_id, rubric_version
                )
            ):
                continue
            readiness_criteria[criterion_id] = {
                "lifecycle": "active_selective",
                "criterion_id": criterion_id,
                "rubric_version": rubric_version,
                "reason": "rubric_only_bootstrap_enabled",
                "boundary": core._rubric_only_boundary(criterion, rubric_version, rubric_metadata),
                "validation": {
                    "strategy": "rubric_only_bootstrap",
                    "checks": [],
                    "trusted_event_ids": [],
                },
                "min_teacher_samples": core.AUTO_GRADE_MIN_TEACHER_SAMPLES,
                "readiness_policy_version": core.READINESS_POLICY_VERSION,
                "readiness_basis_hash": _readiness_basis_hash(
                    store, rubric_version
                ),
                "updated_at": core._now(),
            }
    for proposal in core._read_proposals(case_id):
        if (
            proposal.get("status") == "pending"
            and proposal.get("proposal_type") == "update_criterion"
            and proposal.get("target_criterion_id") is not None
        ):
            criterion_id = str(proposal["target_criterion_id"])
            readiness_criteria[criterion_id] = {
                "lifecycle": "evaluating",
                "criterion_id": criterion_id,
                "reason": "pending_rubric_update",
                "rubric_version": proposal.get("criteria_file_version_at_proposal"),
                "min_teacher_samples": core.AUTO_GRADE_MIN_TEACHER_SAMPLES,
                "readiness_policy_version": core.READINESS_POLICY_VERSION,
                "updated_at": proposal.get("updated_at") or core._now(),
            }
    consistency_states, review_assignments = _consistency_summary_for_criteria(
        case_id,
        store,
        criteria_name,
        rubric_version,
        criteria_data,
    )
    authorizations = list((store.get("criterion_authorizations") or {}).values())
    audit_jobs = list((store.get("audit_jobs") or {}).values())
    criterion_revisions = list((store.get("criterion_revisions") or {}).values())
    criterion_summaries = _criterion_global_summaries(
        case_id,
        store,
        criteria_file_name,
        open_conflicts,
    )
    report_summaries = _global_report_summaries(
        case_id,
        criteria_file_name,
        criteria_data,
        criterion_summaries,
    )
    global_readiness = _global_readiness(
        case_id,
        criteria_file_name,
        authorizations,
        open_conflicts,
    )
    projection_hash = core._sha256_data({
        "rubric_version": rubric_version,
        "criterion_summaries": criterion_summaries,
        "report_summaries": report_summaries,
        "global_readiness": global_readiness,
    })
    return {
        "case_id": case_id,
        "readiness": readiness,
        "open_conflicts": open_conflicts,
        "criterion_authorizations": authorizations,
        "auto_grade_jobs": list((store.get("auto_grade_jobs") or {}).values()),
        "auto_grade_batches": [
            core._batch_view(item)
            for item in sorted(
                (store.get("auto_grade_batches") or {}).values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
        ],
        "criterion_revisions": sorted(
            criterion_revisions,
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        ),
        "audit_jobs": audit_jobs,
        "consistency_states": consistency_states,
        "review_assignments": review_assignments,
        "consistency_automation_mode": core.CONSISTENCY_AUTOMATION_MODE,
        "criterion_global_summaries": criterion_summaries,
        "global_report_summaries": report_summaries,
        "global_readiness": global_readiness,
        "global_projection_hash": projection_hash,
    }


def _readiness_basis_hash(store: Dict[str, Any], rubric_version: str) -> str:
    return core._sha256_data({
        "rubric_version": rubric_version,
        "min_teacher_samples": core.AUTO_GRADE_MIN_TEACHER_SAMPLES,
        "events": store.get("events", []),
        "decisions": store.get("decisions", []),
        "conflicts": store.get("conflicts", []),
        "audit_artifacts": store.get("audit_artifacts", {}),
        "consistency_audits": store.get("consistency_audits", []),
        "audit_jobs": store.get("audit_jobs", {}),
    })


def _grading_evidence_hash(store: Dict[str, Any]) -> str:
    """Concurrent evidence guard independent of unrelated Card file changes."""
    return core._sha256_data({
        "min_teacher_samples": core.AUTO_GRADE_MIN_TEACHER_SAMPLES,
        "events": store.get("events", []),
        "decisions": store.get("decisions", []),
        "conflicts": store.get("conflicts", []),
        "audit_artifacts": store.get("audit_artifacts", {}),
        "consistency_audits": store.get("consistency_audits", []),
    })


def _auto_grade_job_key(report_name: str, criterion_id: str, rubric_file: Optional[str]) -> str:
    return core._sha256_data({
        "report_name": report_name,
        "criterion_id": str(criterion_id),
        "rubric_file_name": rubric_file or "criteria.json",
    })


def _update_auto_grade_job(
    store: Dict[str, Any],
    *,
    case_id: str,
    report_name: str,
    criterion_id: str,
    rubric_file: Optional[str],
    rubric_version: str,
    status: str,
    reason: Optional[str] = None,
    decision: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    jobs = store.setdefault("auto_grade_jobs", {})
    key = _auto_grade_job_key(report_name, criterion_id, rubric_file)
    previous = jobs.get(key, {})
    now = core._now()
    job = {
        **previous,
        "job_id": key,
        "case_id": case_id,
        "report_name": report_name,
        "criteria_id": str(criterion_id),
        "criterion_id": str(criterion_id),
        "criteria_file_name": rubric_file or "criteria.json",
        "rubric_version": rubric_version,
        "status": status,
        "reason": reason,
        "decision": decision,
        "error": error,
        "updated_at": now,
    }
    if status == "pending":
        job["queued_at"] = now
    if status == "running":
        job["started_at"] = now
    if status in {"done", "abstained", "failed", "stale"}:
        job["completed_at"] = now
    jobs[key] = job
    return job
