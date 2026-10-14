"""Reset configured experiments to immutable rubric baselines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .alignment import case_alignment_lock, clear_case_grading_data
from .config import (
    CASE_BASELINE_DIR,
    CASE_ROOT,
    CRITERIA_BASELINE_DIR,
    CRITERIA_DIR,
    EXPERIMENT_RUBRIC_BASELINES,
)
from .criteria_lock import criteria_file_lock
from .grading.common import sha256_data, validate_safe_name
from .grading.persistence import atomic_write_json
from .schemas import CriteriaSchema


def _configured_baseline(case_id: str, criteria_file_name: str) -> str:
    expected_file = EXPERIMENT_RUBRIC_BASELINES.get(case_id)
    if expected_file is None:
        raise ValueError(f"experiment reset is not configured for case: {case_id}")
    if criteria_file_name != expected_file:
        raise ValueError("criteria_file_name does not match the configured experiment rubric")
    return expected_file


def _load_baseline(criteria_file_name: str) -> Dict[str, Any]:
    baseline_path = Path(CRITERIA_BASELINE_DIR) / criteria_file_name
    if not baseline_path.is_file():
        raise FileNotFoundError(str(baseline_path))
    try:
        raw_baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        return CriteriaSchema.model_validate(raw_baseline).model_dump(mode="json")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("experiment rubric baseline is invalid") from exc


def _restore_case_state(case_id: str) -> Dict[str, int]:
    baseline_dir = Path(CASE_BASELINE_DIR) / case_id
    if not baseline_dir.is_dir():
        return {"files": 0}

    case_dir = Path(CASE_ROOT) / case_id
    restored_files = 0
    for source in sorted(baseline_dir.rglob("*.json")):
        relative = source.relative_to(baseline_dir)
        if relative.parts[0] not in {
            "alignment",
            "grade_result",
            "implicit_grades.json",
            "criterion_proposals.json",
        }:
            raise ValueError(f"unsupported case baseline path: {relative}")
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid case baseline JSON: {relative}") from exc
        atomic_write_json(case_dir / relative, data)
        restored_files += 1
    return {"files": restored_files}


def reset_case_experiment(
    case_id: str,
    confirm_case_id: str,
    criteria_file_name: str,
) -> Dict[str, Any]:
    """Restore the rubric and grading-state snapshot configured for one case."""
    validate_safe_name(case_id)
    validate_safe_name(criteria_file_name)
    if confirm_case_id != case_id:
        raise ValueError("confirm_case_id must exactly match the case_id in the URL")

    baseline_file = _configured_baseline(case_id, criteria_file_name)
    baseline = _load_baseline(baseline_file)

    with criteria_file_lock(CRITERIA_DIR, baseline_file), case_alignment_lock(case_id):
        cleared = clear_case_grading_data(case_id, confirm_case_id)
        atomic_write_json(Path(CRITERIA_DIR) / baseline_file, baseline)
        restored = _restore_case_state(case_id)

    return {
        "case_id": case_id,
        "criteria_file_name": baseline_file,
        "rubric_version": sha256_data(baseline),
        "cleared": cleared,
        "restored": restored,
    }
