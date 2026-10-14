from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


_TRANSIENT_AUDIT_FIELDS = {"model_comparison"}


def proposal_source_event_digest(event: Dict[str, Any]) -> str:
    """Hash only the persisted teacher event, never audit-time enrichment."""
    stable_event = {
        key: value
        for key, value in event.items()
        if key not in _TRANSIENT_AUDIT_FIELDS
    }
    canonical = json.dumps(
        stable_event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
