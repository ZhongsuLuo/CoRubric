"""Filesystem persistence and case-scoped locking for grading state."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from .. import consistency
from ..config import CONSISTENCY_REVIEW_POLICY_VERSION


class AlignmentStoreError(RuntimeError):
    pass


class CorruptAlignmentStore(AlignmentStoreError):
    pass


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def case_dir(case_id: str) -> Path:
    core._safe_name(case_id)
    return Path(core.CASE_ROOT) / case_id


def report_dir(case_id: str, report_name: str) -> Path:
    core._safe_name(report_name)
    return case_dir(case_id) / "markdown" / report_name


def alignment_dir(case_id: str) -> Path:
    return case_dir(case_id) / "alignment"


def lock_for(case_id: str) -> threading.RLock:
    key = str(alignment_dir(case_id))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise CorruptAlignmentStore(f"Corrupted alignment JSON: {path}") from exc


def _read_consistency_store(root: Path) -> Dict[str, Any]:
    path = root / "consistency.json"
    data = read_json(
        path,
        consistency.empty_store(CONSISTENCY_REVIEW_POLICY_VERSION),
    )
    if not isinstance(data, dict):
        raise CorruptAlignmentStore(f"Invalid consistency store object: {path}")
    if data.get("schema_version") != consistency.CONSISTENCY_SCHEMA_VERSION:
        raise CorruptAlignmentStore(f"Unsupported consistency store schema: {path}")
    if not isinstance(data.get("criteria"), dict):
        raise CorruptAlignmentStore(f"Invalid consistency criteria map: {path}")
    data.setdefault("policy_version", CONSISTENCY_REVIEW_POLICY_VERSION)
    return data


def load_store(case_id: str) -> Dict[str, Any]:
    root = alignment_dir(case_id)
    store = {
        "events": read_json(root / "events.json", []),
        "decisions": read_json(root / "decisions.json", []),
        "conflicts": read_json(root / "conflicts.json", []),
        "readiness": read_json(root / "readiness.json", {"criteria": {}}),
        "auto_grade_jobs": read_json(root / "auto_grade_jobs.json", {}),
        "auto_grade_batches": read_json(root / "auto_grade_batches.json", {}),
        "model_grade_diagnostics": read_json(
            root / "model_grade_diagnostics.json", []
        ),
        "criterion_revisions": read_json(root / "criterion_revisions.json", {}),
        "audit_artifacts": read_json(root / "audit_artifacts.json", {}),
        "consistency_audits": read_json(root / "consistency_audits.json", []),
        "audit_jobs": read_json(root / "audit_jobs.json", {}),
        "refinement_rejections": read_json(root / "refinement_rejections.json", []),
        "criterion_authorizations": read_json(root / "criterion_authorizations.json", {}),
        "conflict_reassessments": read_json(root / "conflict_reassessments.json", {}),
        "consistency": _read_consistency_store(root),
    }
    if core._consistency_enabled():
        core._hydrate_consistency_from_legacy_events(store)
    return store


def write_store(case_id: str, store: Dict[str, Any]) -> None:
    root = alignment_dir(case_id)
    atomic_write_json(root / "events.json", store["events"])
    atomic_write_json(root / "decisions.json", store["decisions"])
    atomic_write_json(root / "conflicts.json", store["conflicts"])
    atomic_write_json(root / "readiness.json", store["readiness"])
    atomic_write_json(root / "auto_grade_jobs.json", store.get("auto_grade_jobs", {}))
    atomic_write_json(root / "auto_grade_batches.json", store.get("auto_grade_batches", {}))
    atomic_write_json(
        root / "model_grade_diagnostics.json",
        store.get("model_grade_diagnostics", []),
    )
    atomic_write_json(root / "criterion_revisions.json", store.get("criterion_revisions", {}))
    atomic_write_json(root / "audit_artifacts.json", store.get("audit_artifacts", {}))
    atomic_write_json(root / "consistency_audits.json", store.get("consistency_audits", []))
    atomic_write_json(root / "audit_jobs.json", store.get("audit_jobs", {}))
    atomic_write_json(root / "refinement_rejections.json", store.get("refinement_rejections", []))
    atomic_write_json(root / "criterion_authorizations.json", store.get("criterion_authorizations", {}))
    atomic_write_json(root / "conflict_reassessments.json", store.get("conflict_reassessments", {}))
    atomic_write_json(
        root / "consistency.json",
        store.get(
            "consistency",
            consistency.empty_store(CONSISTENCY_REVIEW_POLICY_VERSION),
        ),
    )
