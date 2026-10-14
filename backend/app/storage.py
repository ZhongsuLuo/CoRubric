import json
import os
from typing import Any, Dict, List

from .config import CASE_CRITERIA_FILES, CRITERIA_DIR, RESULTS_DIR
from .criteria_lock import canonical_criteria_file_name
from .case_catalog import metadata


def ensure_dirs() -> None:
    os.makedirs(CRITERIA_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)


def _safe_name(file_name: str) -> str:
    return canonical_criteria_file_name(file_name)


def list_json_files(directory: str) -> List[str]:
    ensure_dirs()
    if not os.path.exists(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".json"))


def read_json(directory: str, file_name: str) -> Dict[str, Any]:
    ensure_dirs()
    safe = _safe_name(file_name)
    path = os.path.join(directory, safe)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(directory: str, file_name: str, data: Dict[str, Any]) -> str:
    ensure_dirs()
    safe = _safe_name(file_name)
    path = os.path.join(directory, safe)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def delete_json(directory: str, file_name: str) -> None:
    ensure_dirs()
    safe = _safe_name(file_name)
    path = os.path.join(directory, safe)
    if os.path.exists(path):
        os.remove(path)


def criteria_list(case_id: str | None = None) -> List[str]:
    items = list_json_files(CRITERIA_DIR)
    if case_id is None:
        return items
    uploaded = metadata(case_id)
    if uploaded:
        names = [uploaded.get('default_criteria_file'), *uploaded.get('criteria_files', [])]
        return list(dict.fromkeys(name for name in names if name in items))
    configured_file = CASE_CRITERIA_FILES.get(case_id)
    return [configured_file] if configured_file in items else []


def results_list() -> List[str]:
    return list_json_files(RESULTS_DIR)
