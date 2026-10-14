from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
_WORKERS: dict[tuple[str, str], threading.Thread] = {}
_WORKERS_GUARD = threading.Lock()
def _criterion_revision_key(criteria_file_name: str, criterion_id: str) -> str:
    return core._sha256_data(
        {
            "criteria_file_name": criteria_file_name,
            "criterion_id": str(criterion_id),
        }
    )


def _supersede_batch_targets_for_criterion(
    store: Dict[str, Any],
    *,
    criteria_file_name: str,
    criterion_id: str,
    target_rubric_version: str,
    revision_id: str,
) -> None:
    from .batch import supersede_criterion_targets

    supersede_criterion_targets(
        store,
        criteria_file_name=criteria_file_name,
        criterion_id=criterion_id,
        target_rubric_version=target_rubric_version,
        revision_id=revision_id,
    )


def _register_criterion_revision(
    case_id: str,
    store: Dict[str, Any],
    *,
    criteria_file_name: str,
    criterion: Dict[str, Any],
    target_rubric_version: str,
    trigger: str,
) -> Dict[str, Any]:
    """Invalidate one changed Card across the case and persist its regrade intent."""
    criterion_id = str(criterion.get("criteria_id"))
    criterion_hash = core._sha256_data(criterion)
    key = _criterion_revision_key(criteria_file_name, criterion_id)
    revisions = store.setdefault("criterion_revisions", {})
    current = revisions.get(key)
    if (
        current
        and current.get("target_criterion_hash") == criterion_hash
        and current.get("status") not in {"failed", "superseded"}
    ):
        return current

    now = core._now()
    revision_id = f"rev_{uuid.uuid4().hex}"
    if current and current.get("status") != "completed":
        current["status"] = "superseded"
        current["superseded_at"] = now
        current["superseded_by_revision_id"] = revision_id

    stale_model_events = [
        event
        for event in store.get("events", [])
        if event.get("grader_type") == "model"
        and event.get("rubric_file_name") == criteria_file_name
        and str(event.get("criterion_id")) == criterion_id
        and str(event.get("criterion_hash") or core._sha256_data(event.get("criterion_snapshot") or {}))
        != criterion_hash
        and core._event_status(str(event.get("event_id")), store.get("decisions", []))
        != "superseded"
    ]
    report_names = core._case_report_names(case_id)
    revision = {
        "revision_id": revision_id,
        "case_id": case_id,
        "criteria_file_name": criteria_file_name,
        "criterion_id": criterion_id,
        "criterion_name": criterion.get("criteria_name"),
        "target_rubric_version": target_rubric_version,
        "target_criterion_hash": criterion_hash,
        "trigger": trigger,
        "status": "awaiting_audit",
        "report_names": report_names,
        "invalidated_report_names": sorted(
            {
                str(event.get("report_name"))
                for event in stale_model_events
                if event.get("report_name")
            },
            key=core._natural_name_key,
        ),
        "created_at": now,
        "updated_at": now,
    }
    revisions[key] = revision
    _supersede_batch_targets_for_criterion(
        store,
        criteria_file_name=criteria_file_name,
        criterion_id=criterion_id,
        target_rubric_version=target_rubric_version,
        revision_id=revision_id,
    )
    for report_name in report_names:
        core.materialize_projection(
            case_id,
            report_name,
            store,
            criteria_file_name,
            persist=True,
        )
    return revision


def _pending_rubric_update_for_criterion(
    case_id: str, criteria_file_name: str, criterion_id: str
) -> bool:
    return any(
        proposal.get("status") == "pending"
        and proposal.get("proposal_type") == "update_criterion"
        and proposal.get("criteria_file_name") == criteria_file_name
        and str(proposal.get("target_criterion_id")) == str(criterion_id)
        for proposal in core._read_proposals(case_id)
    )


def _active_revision(
    store: Dict[str, Any], criteria_file_name: str, criterion_id: str
) -> Optional[Dict[str, Any]]:
    return (store.get("criterion_revisions") or {}).get(
        _criterion_revision_key(criteria_file_name, criterion_id)
    )


def _revision_card_is_current(
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion_id: str,
    criterion_hash: str,
) -> bool:
    revision = _active_revision(store, criteria_file_name, criterion_id)
    return bool(
        revision
        and revision.get("target_criterion_hash") == criterion_hash
        and revision.get("status") not in {"superseded"}
    )


def advance_criterion_revision(
    case_id: str, criteria_file_name: str, criterion_id: str
) -> Optional[Dict[str, Any]]:
    """Move one changed Card from audited to a full-case criterion regrade."""
    with core._lock_for(case_id):
        store = core._store(case_id)
        revision = _active_revision(store, criteria_file_name, criterion_id)
        if not revision or revision.get("status") in {
            "completed",
            "regrading",
            "superseded",
        }:
            return deepcopy(revision) if revision else None
        criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
        criterion = core._require_criterion(criteria_data, str(criterion_id))
        current_hash = core._sha256_data(criterion)
        if (
            revision.get("target_rubric_version") != rubric_version
            or revision.get("target_criterion_hash") != current_hash
        ):
            revision["status"] = "superseded"
            revision["superseded_at"] = core._now()
            revision["updated_at"] = revision["superseded_at"]
            core._write_store(case_id, store)
            return deepcopy(revision)

        audit_job = core._audit_job(case_id, criteria_name, str(criterion_id), store)
        if (
            audit_job.get("status") != "done"
            or audit_job.get("target_rubric_version") != rubric_version
        ):
            revision["status"] = "awaiting_audit"
            revision["updated_at"] = core._now()
            core._write_store(case_id, store)
            return deepcopy(revision)
        if _pending_rubric_update_for_criterion(
            case_id, criteria_name, str(criterion_id)
        ):
            revision["status"] = "blocked_by_rubric_update"
            revision["updated_at"] = core._now()
            core._write_store(case_id, store)
            return deepcopy(revision)

        blocking_conflicts = [
            conflict
            for conflict in store.get("conflicts", [])
            if core._conflict_is_currently_open(store, conflict)
            and str(conflict.get("criterion_id")) == str(criterion_id)
            and conflict.get("conflict_type") != "model_rubric_migration"
        ]
        if blocking_conflicts:
            revision["status"] = "blocked_by_conflict"
            revision["blocking_conflict_ids"] = [
                str(conflict.get("conflict_id")) for conflict in blocking_conflicts
            ]
            revision["updated_at"] = core._now()
            core._write_store(case_id, store)
            return deepcopy(revision)

        # Migration conflicts describe exactly the stale-results condition this
        # full-card regrade repairs; close them before scheduling the new batch.
        for conflict in store.get("conflicts", []):
            if (
                core._conflict_is_currently_open(store, conflict)
                and conflict.get("conflict_type") == "model_rubric_migration"
                and str(conflict.get("criterion_id")) == str(criterion_id)
            ):
                conflict["status"] = "resolved"
                conflict["resolution_type"] = "full_card_regrade_scheduled"
                conflict["resolved_at"] = core._now()

        # Teacher grades made against the old Card remain in immutable history,
        # but cannot override the newly evaluated Card in the current projection.
        stale_events = [
            event
            for event in store.get("events", [])
            if event.get("rubric_file_name") == criteria_name
            and str(event.get("criterion_id")) == str(criterion_id)
            and str(
                event.get("criterion_hash")
                or core._sha256_data(event.get("criterion_snapshot") or {})
            )
            != current_hash
            and core._event_status(
                str(event.get("event_id")), store.get("decisions", [])
            )
            != "superseded"
        ]
        stale_ids = [str(event["event_id"]) for event in stale_events]
        if stale_ids:
            core._append_decision(
                store,
                "superseded",
                stale_ids,
                "historical grade invalidated before complete Card re-evaluation",
                revision_id=revision.get("revision_id"),
                target_rubric_version=rubric_version,
            )
        authorization = core._criterion_authorization(
            store, criteria_name, str(criterion_id)
        )
        if authorization.get("state") == core.AUTH_REPAIR_REQUIRED:
            core._set_criterion_authorization(
                store,
                criteria_name,
                str(criterion_id),
                core.AUTH_REPAIR_REQUIRED,
                repair_confirmed_at=core._now(),
                repair_confirmed_by="criterion_revision_workflow",
                repair_pending_report_names=list(
                    revision.get("report_names") or []
                ),
                repair_to_rubric_version=rubric_version,
            )
        for report_name in revision.get("report_names") or []:
            core.materialize_projection(
                case_id, report_name, store, criteria_name, persist=True
            )
        revision["status"] = "scheduling_regrade"
        revision["updated_at"] = core._now()
        core._write_store(case_id, store)

    batch = core.create_auto_grade_batch(
        case_id,
        criteria_file_name,
        created_by="rubric_revision",
        report_names=list(revision.get("report_names") or []),
        criteria_ids=[str(criterion_id)],
        purpose="criterion_revision",
        revision_ids=[str(revision.get("revision_id"))],
        calibration_required=False,
    )
    with core._lock_for(case_id):
        store = core._store(case_id)
        current = _active_revision(store, criteria_file_name, criterion_id)
        if current and current.get("revision_id") == revision.get("revision_id"):
            current["status"] = "regrading"
            current["batch_id"] = batch["batch_id"]
            current["regrade_started_at"] = core._now()
            current["updated_at"] = current["regrade_started_at"]
            core._write_store(case_id, store)
            revision = deepcopy(current)
    core.ensure_auto_grade_batch_worker(case_id, str(batch["batch_id"]))
    return revision


def run_criterion_revision_workflow(
    case_id: str, criteria_file_name: str, criterion_id: str
) -> Optional[Dict[str, Any]]:
    with core._lock_for(case_id):
        store = core._store(case_id)
        revision = _active_revision(store, criteria_file_name, criterion_id)
        if not revision or revision.get("status") in {
            "completed",
            "regrading",
            "superseded",
        }:
            return deepcopy(revision) if revision else None
        audit_job = core._audit_job(case_id, criteria_file_name, criterion_id, store)
    if audit_job.get("status") in {"pending", "failed"}:
        core.run_consistency_audit_for_criterion(
            case_id, criteria_file_name, criterion_id
        )
    core.evaluate_alignment(case_id, criterion_id, criteria_file_name)
    return advance_criterion_revision(case_id, criteria_file_name, criterion_id)


def ensure_criterion_revision_worker(
    case_id: str, criteria_file_name: str, criterion_id: str
) -> bool:
    key = (case_id, _criterion_revision_key(criteria_file_name, criterion_id))
    with _WORKERS_GUARD:
        current = _WORKERS.get(key)
        if current and current.is_alive():
            return False

        def worker() -> None:
            try:
                run_criterion_revision_workflow(
                    case_id, criteria_file_name, criterion_id
                )
            finally:
                with _WORKERS_GUARD:
                    _WORKERS.pop(key, None)

        thread = threading.Thread(
            target=worker,
            name=f"criterion-revision:{case_id}:{criterion_id}",
            daemon=True,
        )
        _WORKERS[key] = thread
        thread.start()
        return True


def ensure_criterion_revision_workers_for_jobs(
    jobs: List[Dict[str, Any]],
) -> int:
    started = 0
    for job in jobs:
        case_id = str(job.get("case_id") or "")
        criteria_file_name = str(job.get("criteria_file_name") or "")
        criterion_id = str(job.get("criterion_id") or "")
        if case_id and criteria_file_name and criterion_id:
            started += int(
                ensure_criterion_revision_worker(
                    case_id, criteria_file_name, criterion_id
                )
            )
    return started


def resume_criterion_revision_workers(
    case_id: str, criteria_file_name: Optional[str] = None
) -> int:
    with core._lock_for(case_id):
        revisions = [
            deepcopy(item)
            for item in (core._store(case_id).get("criterion_revisions") or {}).values()
            if item.get("status")
            in {"awaiting_audit", "blocked_by_rubric_update", "blocked_by_conflict"}
            and (
                not criteria_file_name
                or item.get("criteria_file_name") == criteria_file_name
            )
        ]
    started = 0
    for revision in revisions:
        # Blocked revisions are cheap to re-check and remain blocked until their
        # persisted conflict/proposal state changes.
        started += int(
            ensure_criterion_revision_worker(
                case_id,
                str(revision.get("criteria_file_name") or "criteria.json"),
                str(revision.get("criterion_id") or ""),
            )
        )
    return started
