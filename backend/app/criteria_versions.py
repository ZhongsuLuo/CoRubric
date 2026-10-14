from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .grading.common import sha256_data


_INDEX_LOCK = threading.RLock()
_INDEX_DIRECTORY = ".grading_versions"
_INDEX_FILE = "index.json"


def grading_criterion_payload(criterion: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the score-affecting part of a criterion.

    ``section`` is presentation metadata: regrouping a rubric must not make
    teacher grades, model grades, or learned readiness stale.
    """
    return {key: value for key, value in criterion.items() if key != "section"}


def grading_rubric_payload(criteria_data: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(criteria_data)
    payload["criteria"] = [
        grading_criterion_payload(item)
        for item in criteria_data.get("criteria", [])
        if isinstance(item, Mapping)
    ]
    return payload


def criteria_content_hash(criteria_data: Mapping[str, Any]) -> str:
    return sha256_data(criteria_data)


def _index_path(criteria_dir: str | Path) -> Path:
    return Path(criteria_dir) / _INDEX_DIRECTORY / _INDEX_FILE


def _read_index(criteria_dir: str | Path) -> Dict[str, Dict[str, Any]]:
    path = _index_path(criteria_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_index(criteria_dir: str | Path, index: Dict[str, Dict[str, Any]]) -> None:
    path = _index_path(criteria_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _record_for(index: Mapping[str, Any], criteria_data: Mapping[str, Any]) -> Mapping[str, Any]:
    record = index.get(criteria_content_hash(criteria_data), {})
    return record if isinstance(record, Mapping) else {}


def _rubric_version_from_index(index: Mapping[str, Any], criteria_data: Mapping[str, Any]) -> str:
    content_hash = criteria_content_hash(criteria_data)
    return str(_record_for(index, criteria_data).get("grading_version") or content_hash)


def _criterion_version_from_index(
    index: Mapping[str, Any],
    criteria_data: Mapping[str, Any],
    criterion_id: str,
) -> Optional[str]:
    criterion = next(
        (
            item
            for item in criteria_data.get("criteria", [])
            if isinstance(item, Mapping) and str(item.get("criteria_id")) == criterion_id
        ),
        None,
    )
    if criterion is None:
        return None
    versions = _record_for(index, criteria_data).get("criterion_versions", {})
    stored_version = versions.get(criterion_id) if isinstance(versions, Mapping) else None
    return str(stored_version or sha256_data(criterion))


def rubric_version(criteria_dir: str | Path, criteria_data: Mapping[str, Any]) -> str:
    with _INDEX_LOCK:
        return _rubric_version_from_index(_read_index(criteria_dir), criteria_data)


def criterion_version(
    criteria_dir: str | Path,
    criteria_data: Mapping[str, Any],
    criterion_id: Optional[str],
) -> Optional[str]:
    if not criterion_id:
        return None
    target = str(criterion_id)
    with _INDEX_LOCK:
        return _criterion_version_from_index(_read_index(criteria_dir), criteria_data, target)


def record_saved_versions(
    criteria_dir: str | Path,
    before: Optional[Mapping[str, Any]],
    after: Mapping[str, Any],
) -> None:
    """Record versions while preserving them across category-only changes."""
    after_content_hash = criteria_content_hash(after)
    after_items = {
        str(item.get("criteria_id")): item
        for item in after.get("criteria", [])
        if isinstance(item, Mapping) and item.get("criteria_id") is not None
    }

    with _INDEX_LOCK:
        index = _read_index(criteria_dir)
        preserve_versions = before is not None and grading_rubric_payload(before) == grading_rubric_payload(after)
        if preserve_versions:
            grading_version = _rubric_version_from_index(index, before)
            criterion_versions = {
                criterion_id: _criterion_version_from_index(index, before, criterion_id)
                for criterion_id in after_items
            }
        else:
            grading_version = after_content_hash
            criterion_versions = {
                criterion_id: sha256_data(item)
                for criterion_id, item in after_items.items()
            }

        index[after_content_hash] = {
            "grading_version": grading_version,
            "criterion_versions": criterion_versions,
        }
        _write_index(criteria_dir, index)
