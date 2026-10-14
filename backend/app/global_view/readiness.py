"""Entry gate for the read-only cohort Global View."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def _identity(value: Dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workflow_blockers(
    *,
    criteria_name: str,
    proposals: List[Dict[str, Any]],
    authorizations: List[Dict[str, Any]],
    open_conflicts: List[Dict[str, Any]],
    repair_required_state: str,
) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    for proposal in proposals:
        if (
            str(proposal.get("criteria_file_name") or "criteria.json")
            == criteria_name
            and proposal.get("status") == "pending"
        ):
            blockers.append(
                {
                    "kind": "rubric_proposal",
                    "label": "Review scoring standard update",
                    "criterion_id": proposal.get("target_criterion_id"),
                    "proposal_id": proposal.get("proposal_id"),
                }
            )
    for conflict in open_conflicts:
        if str(conflict.get("criteria_file_name") or criteria_name) != criteria_name:
            continue
        refs = (
            conflict.get("suspect_events")
            or conflict.get("affected_events")
            or []
        )
        first_ref = refs[0] if refs else {}
        blockers.append(
            {
                "kind": "conflict",
                "label": "Resolve grading conflict",
                "report_name": first_ref.get("report_name"),
                "criterion_id": str(conflict.get("criteria_id") or ""),
                "event_id": first_ref.get("event_id"),
            }
        )
    for authorization in authorizations:
        if (
            str(authorization.get("criteria_file_name") or "criteria.json")
            == criteria_name
            and authorization.get("state") == repair_required_state
        ):
            blockers.append(
                {
                    "kind": "repair_required",
                    "label": "Confirm scoring repair",
                    "criterion_id": str(authorization.get("criterion_id") or ""),
                }
            )
    return blockers


def build_global_readiness(
    *,
    criteria_name: str,
    proposals: List[Dict[str, Any]],
    authorizations: List[Dict[str, Any]],
    open_conflicts: List[Dict[str, Any]],
    repair_required_state: str,
) -> Dict[str, Any]:
    """Return only workflow states that make Global View unsafe to enter."""
    blockers = _workflow_blockers(
        criteria_name=criteria_name,
        proposals=proposals,
        authorizations=authorizations,
        open_conflicts=open_conflicts,
        repair_required_state=repair_required_state,
    )
    deduplicated = {_identity(blocker): blocker for blocker in blockers}
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            str(item.get("report_name") or ""),
            str(item.get("criterion_id") or ""),
            str(item.get("kind") or ""),
        ),
    )
    return {
        "allowed": not ordered,
        "blockers": ordered,
        "blocker_count": len(ordered),
    }
