"""Persisted report-major automatic-grading batch state machine.

This module owns batch lifecycle and worker coordination. It imports the legacy
alignment facade lazily because storage and the single-report grading use case
are still exposed there; keeping that dependency explicit prevents another set
of shadow stores and preserves existing test seams during the migration.
"""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..config import GRADING_MODEL_ID

TARGET_STATUSES = (
    "pending",
    "running",
    "done",
    "abstained",
    "failed",
    "stale",
    "superseded",
    "cancelled",
)
TERMINAL_TARGET_STATUSES = set(TARGET_STATUSES) - {"pending", "running"}
TERMINAL_BATCH_STATUSES = {
    "completed",
    "completed_with_issues",
    "failed",
    "superseded",
    "cancelled",
}

_WORKERS: dict[tuple[str, str], threading.Thread] = {}
_WORKERS_GUARD = threading.Lock()


def _core():
    from .. import alignment

    return alignment


def active_worker_ids(case_id: str) -> List[str]:
    with _WORKERS_GUARD:
        return [
            batch_id
            for (worker_case_id, batch_id), worker in _WORKERS.items()
            if worker_case_id == case_id and worker.is_alive()
        ]


def progress(batch: Dict[str, Any]) -> Dict[str, Any]:
    targets = list(batch.get("targets") or [])
    counts = {
        status: sum(1 for target in targets if target.get("status") == status)
        for status in TARGET_STATUSES
    }
    completed = sum(counts[status] for status in TERMINAL_TARGET_STATUSES)
    total = len(targets)
    return {
        "total": total,
        "completed": completed,
        "percent": round((completed / total) * 100, 1) if total else 100.0,
        **{
            key: value
            for key, value in counts.items()
            if value or key not in {"superseded", "cancelled"}
        },
    }


def view(batch: Dict[str, Any]) -> Dict[str, Any]:
    item = deepcopy(batch)
    item["progress"] = progress(item)
    return item


def supersede_criterion_targets(
    store: Dict[str, Any],
    *,
    criteria_file_name: str,
    criterion_id: str,
    target_rubric_version: str,
    revision_id: str,
) -> None:
    core = _core()
    now = core._now()
    for batch in (store.get("auto_grade_batches") or {}).values():
        if batch.get("criteria_file_name") != criteria_file_name:
            continue
        if batch.get("rubric_version") == target_rubric_version:
            continue
        changed = False
        for target in batch.get("targets") or []:
            if str(target.get("criterion_id")) != str(criterion_id):
                continue
            if target.get("status") == "superseded":
                continue
            target.update(
                {
                    "status": "superseded",
                    "reason": "criterion_updated",
                    "superseded_by_revision_id": revision_id,
                    "updated_at": now,
                }
            )
            changed = True
        if not changed:
            continue
        remaining = [
            target
            for target in (batch.get("targets") or [])
            if target.get("status") not in TERMINAL_TARGET_STATUSES
        ]
        if not remaining:
            batch["status"] = "superseded"
            batch["completed_at"] = now
            batch["superseded_by_revision_id"] = revision_id
        batch["updated_at"] = now


def _calibration_confirmed(
    store: Dict[str, Any], criteria_file_name: str, rubric_version: str
) -> bool:
    return any(
        item.get("criteria_file_name") == criteria_file_name
        and item.get("rubric_version") == rubric_version
        and (item.get("calibration") or {}).get("status") == "confirmed"
        for item in (store.get("auto_grade_batches") or {}).values()
    )


def _teacher_graded_criterion_ids(
    case_id: str,
    report_name: str,
    store: Dict[str, Any],
    criteria_file_name: str,
) -> set[str]:
    """Return effective teacher grades for the report's current rubric."""
    projection = _core().materialize_projection(
        case_id,
        report_name,
        store,
        criteria_file_name,
    )
    return {
        str(item.get("criteria_id"))
        for item in (projection.get("criteria") or [])
        if (item.get("provenance") or {}).get("grader_type") == "teacher"
    }


def _prepare_report_targets(
    batch: Dict[str, Any],
    report_name: str,
    teacher_graded_ids: set[str],
    now: str,
) -> List[str]:
    """Preserve late teacher grades and start only the remaining report targets."""
    pending_ids: List[str] = []
    for target in batch.get("targets") or []:
        if (
            target.get("report_name") != report_name
            or target.get("status") in TERMINAL_TARGET_STATUSES
        ):
            continue

        criterion_id = str(target.get("criterion_id"))
        if criterion_id in teacher_graded_ids:
            target.update(
                {
                    "status": "done",
                    "reason": "teacher_grade_preserved",
                    "updated_at": now,
                }
            )
            continue

        target.update({"status": "running", "updated_at": now})
        pending_ids.append(criterion_id)

    batch["updated_at"] = now
    return pending_ids


def create(
    case_id: str,
    criteria_file_name: str,
    created_by: str = "teacher",
    *,
    report_names: Optional[List[str]] = None,
    start_report_name: Optional[str] = None,
    criteria_ids: Optional[List[str]] = None,
    purpose: str = "manual_batch",
    revision_ids: Optional[List[str]] = None,
    calibration_required: Optional[bool] = None,
) -> Dict[str, Any]:
    """Persist ungraded-by-teacher targets in the teacher-visible order."""
    core = _core()
    core._safe_name(criteria_file_name)
    with core._lock_for(case_id):
        store = core._store(case_id)
        rubric_file, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
        if rubric_version == "missing":
            raise ValueError(f"criteria file not found: {rubric_file}")
        report_root = core._case_dir(case_id) / "markdown"
        if not report_root.exists():
            raise FileNotFoundError(str(report_root))
        case_report_names = {path.name for path in report_root.iterdir() if path.is_dir()}
        if report_names is None:
            requested_report_names = sorted(case_report_names, key=core._natural_name_key)
        else:
            requested_report_names = [str(name) for name in report_names]
            for report_name in requested_report_names:
                core._safe_name(report_name)
            if len(requested_report_names) != len(set(requested_report_names)):
                raise ValueError("report_names must not contain duplicates")
            if set(requested_report_names) != case_report_names:
                missing = sorted(
                    case_report_names - set(requested_report_names),
                    key=core._natural_name_key,
                )
                unexpected = sorted(
                    set(requested_report_names) - case_report_names,
                    key=core._natural_name_key,
                )
                raise ValueError(
                    "report_names must exactly match the case reports; "
                    f"missing={missing}, unexpected={unexpected}"
                )

        if not requested_report_names:
            raise ValueError("case has no converted reports")
        requested_start = start_report_name or requested_report_names[0]
        core._safe_name(requested_start)
        if requested_start not in requested_report_names:
            raise ValueError("start_report_name must be present in report_names")
        start_index = requested_report_names.index(requested_start)
        ordered_report_names = requested_report_names[start_index:] + requested_report_names[:start_index]
        selected_ids = {str(item) for item in criteria_ids} if criteria_ids is not None else None
        criteria = [
            item
            for item in (criteria_data.get("criteria") or [])
            if selected_ids is None or str(item.get("criteria_id")) in selected_ids
        ]
        if not criteria:
            raise ValueError("criteria file has no criteria")
        if selected_ids is not None and {
            str(item.get("criteria_id")) for item in criteria
        } != selected_ids:
            raise ValueError("one or more criteria_ids are not present in the criteria file")

        batches = store.setdefault("auto_grade_batches", {})
        requested_criterion_ids = {
            str(criterion.get("criteria_id")) for criterion in criteria
        }
        active = next(
            (
                item
                for item in reversed(list(batches.values()))
                if item.get("criteria_file_name") == rubric_file
                and item.get("rubric_version") == rubric_version
                and {
                    str(criterion.get("criterion_id"))
                    for criterion in (item.get("criteria") or [])
                }
                == requested_criterion_ids
                and item.get("status") not in TERMINAL_BATCH_STATUSES
            ),
            None,
        )
        if active:
            return view(active)

        calibration_confirmed = (
            _calibration_confirmed(store, rubric_file or "criteria.json", rubric_version)
            if calibration_required is None
            else not calibration_required
        )
        now = core._now()
        batch_id = f"batch_{uuid.uuid4().hex}"
        teacher_graded_by_report = {
            report_name: _teacher_graded_criterion_ids(
                case_id,
                report_name,
                store,
                rubric_file or "criteria.json",
            )
            for report_name in ordered_report_names
        }
        targets = [
            {
                "report_name": report_name,
                "criterion_id": str(criterion.get("criteria_id")),
                "criterion_name": criterion.get("criteria_name"),
                "status": "pending",
                "updated_at": now,
            }
            for report_name in ordered_report_names
            for criterion in criteria
            if str(criterion.get("criteria_id"))
            not in teacher_graded_by_report[report_name]
        ]
        batch = {
            "batch_id": batch_id,
            "case_id": case_id,
            "criteria_file_name": rubric_file or "criteria.json",
            "rubric_version": rubric_version,
            "model_id": GRADING_MODEL_ID,
            "purpose": purpose,
            "revision_ids": list(revision_ids or []),
            "status": "queued" if calibration_confirmed else "awaiting_calibration",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "report_names": ordered_report_names,
            "start_report_name": requested_start,
            "criteria": [
                {
                    "criterion_id": str(item.get("criteria_id")),
                    "criterion_name": item.get("criteria_name"),
                    "max_score": item.get("max_score"),
                }
                for item in criteria
            ],
            "targets": targets,
            "calibration": {
                "required": not calibration_confirmed,
                "status": "confirmed" if calibration_confirmed else "pending",
                "rubric_version": rubric_version,
                "confirmed_at": now if calibration_confirmed else None,
                "confirmed_by": (
                    "prior_rubric_version_confirmation" if calibration_confirmed else None
                ),
            },
        }
        batches[batch_id] = batch
        core._write_store(case_id, store)
        return view(batch)


def confirm(case_id: str, batch_id: str, confirmed_by: str = "teacher") -> Dict[str, Any]:
    core = _core()
    with core._lock_for(case_id):
        store = core._store(case_id)
        batch = (store.get("auto_grade_batches") or {}).get(batch_id)
        if not batch:
            raise FileNotFoundError(batch_id)
        if batch.get("status") in TERMINAL_BATCH_STATUSES:
            return view(batch)
        now = core._now()
        batch["calibration"] = {
            **(batch.get("calibration") or {}),
            "required": False,
            "status": "confirmed",
            "confirmed_at": now,
            "confirmed_by": confirmed_by,
        }
        if batch.get("status") == "awaiting_calibration":
            batch["status"] = "queued"
        batch["updated_at"] = now
        core._write_store(case_id, store)
        return view(batch)


def _finish_cancellation(batch: Dict[str, Any]) -> None:
    core = _core()
    now = core._now()
    for target in batch.get("targets") or []:
        if target.get("status") not in TERMINAL_TARGET_STATUSES:
            target.update(
                {
                    "status": "cancelled",
                    "reason": "batch_cancelled",
                    "updated_at": now,
                }
            )
    batch.update(
        {
            "status": "cancelled",
            "cancelled_at": now,
            "completed_at": now,
            "updated_at": now,
        }
    )


def cancel(case_id: str, batch_id: str, cancelled_by: str = "teacher") -> Dict[str, Any]:
    """Stop future report work while allowing the current model request to finish."""
    core = _core()
    with core._lock_for(case_id):
        store = core._store(case_id)
        batch = (store.get("auto_grade_batches") or {}).get(batch_id)
        if not batch:
            raise FileNotFoundError(batch_id)
        if batch.get("status") in TERMINAL_BATCH_STATUSES:
            return view(batch)

        now = core._now()
        batch["cancelled_by"] = cancelled_by
        batch["cancel_requested_at"] = now
        has_running_target = any(
            target.get("status") == "running" for target in batch.get("targets") or []
        )
        for target in batch.get("targets") or []:
            if target.get("status") == "pending":
                target.update(
                    {
                        "status": "cancelled",
                        "reason": "batch_cancelled",
                        "updated_at": now,
                    }
                )
        if has_running_target:
            batch.update({"status": "cancelling", "updated_at": now})
        else:
            _finish_cancellation(batch)
        core._write_store(case_id, store)
        return view(batch)


def get(case_id: str, batch_id: str) -> Dict[str, Any]:
    core = _core()
    with core._lock_for(case_id):
        store = core._store(case_id)
        batch = (store.get("auto_grade_batches") or {}).get(batch_id)
        if not batch:
            raise FileNotFoundError(batch_id)
        if batch.get("status") == "cancelling" and batch_id not in active_worker_ids(case_id):
            _finish_cancellation(batch)
            core._write_store(case_id, store)
        return view(batch)


def list_batches(
    case_id: str, criteria_file_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    core = _core()
    with core._lock_for(case_id):
        items = list((core._store(case_id).get("auto_grade_batches") or {}).values())
        if criteria_file_name:
            items = [
                item
                for item in items
                if item.get("criteria_file_name") == criteria_file_name
            ]
        return [
            view(item)
            for item in sorted(
                items,
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
        ]


def _update_report_targets(
    batch: Dict[str, Any],
    report_name: str,
    statuses: Dict[str, tuple[str, Optional[str]]],
) -> None:
    core = _core()
    now = core._now()
    for target in batch.get("targets") or []:
        if (
            target.get("report_name") != report_name
            or target.get("status") in TERMINAL_TARGET_STATUSES
        ):
            continue
        criterion_id = str(target.get("criterion_id"))
        status, detail = statuses.get(
            criterion_id,
            ("failed", "batch_worker_received_no_result"),
        )
        target.update({"status": status, "reason": detail, "updated_at": now})


def _update_completed_target(
    batch: Dict[str, Any],
    report_name: str,
    completion: Dict[str, Any],
    now: str,
) -> None:
    """Expose one target only after its report projection has been persisted."""
    criterion_id = str(completion.get("criterion_id"))
    for target in batch.get("targets") or []:
        if (
            target.get("report_name") != report_name
            or str(target.get("criterion_id")) != criterion_id
            or target.get("status") in {"superseded", "cancelled"}
        ):
            continue
        target.update(
            {
                "status": completion.get("status", "failed"),
                "reason": completion.get("reason") or completion.get("error"),
                "updated_at": now,
            }
        )
        batch["updated_at"] = now
        return


def run(case_id: str, batch_id: str) -> Dict[str, Any]:
    """Run or resume a persisted batch in report-major order."""
    core = _core()
    with core._lock_for(case_id):
        store = core._store(case_id)
        batch = (store.get("auto_grade_batches") or {}).get(batch_id)
        if not batch:
            raise FileNotFoundError(batch_id)
        if batch.get("status") == "awaiting_calibration":
            raise ValueError("rubric calibration must be confirmed before grading")
        if batch.get("status") in TERMINAL_BATCH_STATUSES:
            return view(batch)
        if batch.get("status") == "cancelling":
            _finish_cancellation(batch)
            core._write_store(case_id, store)
            return view(batch)
        for target in batch.get("targets") or []:
            if target.get("status") == "running":
                target["status"] = "pending"
        batch["status"] = "running"
        batch.setdefault("started_at", core._now())
        batch["updated_at"] = core._now()
        core._write_store(case_id, store)

    criteria_ids = [str(item.get("criterion_id")) for item in (batch.get("criteria") or [])]
    for report_name in batch.get("report_names") or []:
        with core._lock_for(case_id):
            store = core._store(case_id)
            current = (store.get("auto_grade_batches") or {}).get(batch_id)
            if not current:
                raise FileNotFoundError(batch_id)
            if current.get("status") in TERMINAL_BATCH_STATUSES:
                return view(current)
            if current.get("status") == "cancelling":
                _finish_cancellation(current)
                core._write_store(case_id, store)
                return view(current)
            teacher_graded_ids = _teacher_graded_criterion_ids(
                case_id,
                report_name,
                store,
                str(current.get("criteria_file_name") or "criteria.json"),
            )
            now = core._now()
            pending_ids = _prepare_report_targets(
                current,
                report_name,
                teacher_graded_ids,
                now,
            )
            core._write_store(case_id, store)
            if not pending_ids:
                continue

        try:
            def persist_target_completion(
                store: Dict[str, Any], completion: Dict[str, Any]
            ) -> None:
                persisted_batch = (store.get("auto_grade_batches") or {}).get(batch_id)
                if persisted_batch:
                    _update_completed_target(
                        persisted_batch, report_name, completion, core._now()
                    )

            result = core.auto_grade_report(
                case_id,
                report_name,
                batch.get("criteria_file_name"),
                criteria_ids=pending_ids or criteria_ids,
                scheduling_reason=(
                    "criterion_revision"
                    if batch.get("purpose") == "criterion_revision"
                    else "batch"
                ),
                on_item_persisted=persist_target_completion,
            )
            statuses: Dict[str, tuple[str, Optional[str]]] = {}
            for item in result.get("grades") or []:
                statuses[str(item.get("criteria_id"))] = ("done", "model_grade_recorded")
            for item in result.get("abstentions") or []:
                statuses[str(item.get("criteria_id"))] = (
                    "abstained",
                    str(item.get("reason") or "abstained"),
                )
            for item in result.get("failures") or []:
                statuses[str(item.get("criteria_id"))] = (
                    "failed",
                    str(item.get("error") or "failed"),
                )
        except Exception as exc:
            statuses = {
                criterion_id: ("failed", f"{type(exc).__name__}: {exc}")
                for criterion_id in pending_ids
            }

        with core._lock_for(case_id):
            store = core._store(case_id)
            current = (store.get("auto_grade_batches") or {}).get(batch_id)
            if not current:
                raise FileNotFoundError(batch_id)
            _update_report_targets(current, report_name, statuses)
            if current.get("status") == "cancelling":
                _finish_cancellation(current)
            current["updated_at"] = core._now()
            core._write_store(case_id, store)
            if current.get("status") == "cancelled":
                return view(current)

    with core._lock_for(case_id):
        store = core._store(case_id)
        batch = (store.get("auto_grade_batches") or {}).get(batch_id)
        if not batch:
            raise FileNotFoundError(batch_id)
        if batch.get("status") == "superseded":
            return view(batch)
        batch_progress = progress(batch)
        has_issues = bool(
            batch_progress["failed"]
            or batch_progress["stale"]
            or batch_progress["abstained"]
            or batch_progress.get("superseded", 0)
        )
        batch["status"] = "completed_with_issues" if has_issues else "completed"
        batch["completed_at"] = core._now()
        batch["updated_at"] = batch["completed_at"]
        for revision_id in batch.get("revision_ids") or []:
            for revision in (store.get("criterion_revisions") or {}).values():
                if revision.get("revision_id") == revision_id:
                    revision["status"] = (
                        "completed"
                        if batch["status"] == "completed"
                        else "completed_with_issues"
                    )
                    revision["completed_at"] = batch["completed_at"]
                    revision["updated_at"] = batch["completed_at"]
        core._write_store(case_id, store)
        return view(batch)


def ensure_worker(case_id: str, batch_id: str) -> bool:
    batch = get(case_id, batch_id)
    if batch.get("status") not in {"queued", "running"}:
        return False
    key = (case_id, batch_id)
    with _WORKERS_GUARD:
        current = _WORKERS.get(key)
        if current and current.is_alive():
            return False

        def worker() -> None:
            try:
                run(case_id, batch_id)
            except FileNotFoundError:
                pass
            finally:
                with _WORKERS_GUARD:
                    _WORKERS.pop(key, None)

        thread = threading.Thread(
            target=worker,
            name=f"auto-grade-batch:{case_id}:{batch_id}",
            daemon=True,
        )
        _WORKERS[key] = thread
        thread.start()
        return True
