from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

AUTH_TEACHER_VALIDATION = "teacher_validation"
AUTH_BATCH_AUTHORIZED = "batch_authorized"
AUTH_REPAIR_REQUIRED = "repair_required"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_data(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def validate_safe_name(name: str) -> str:
    if os.path.basename(name) != name or name in {"", ".", ".."}:
        raise ValueError("unsafe file name")
    return name


def natural_name_key(value: str) -> tuple[tuple[int, object], ...]:
    """Sort names by their human-visible numeric order."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )
