from __future__ import annotations

import threading
from pathlib import Path

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def canonical_criteria_file_name(file_name: str) -> str:
    base = Path(file_name).name
    if not base.endswith(".json"):
        base = f"{base}.json"
    return base


def criteria_file_lock(criteria_dir: str | Path, file_name: str) -> threading.RLock:
    key = str((Path(criteria_dir) / canonical_criteria_file_name(file_name)).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())
