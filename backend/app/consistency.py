from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


CONSISTENCY_SCHEMA_VERSION = "criterion-consistency-v1"
STATE_CALIBRATING = "calibrating"
STATE_RELIABLE = "reliable"
STATE_UNRELIABLE = "unreliable"


@dataclass(frozen=True)
class RecordIdentity:
    """Fields that make consistency evidence reusable for one scorer."""

    case_id: str
    criteria_file_name: str
    criterion_id: str
    criterion_hash: str
    scorer_revision: str
    scorer_component_hash: str
    model_version: str
    prompt_revision: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def empty_store(policy_version: str = "v1") -> Dict[str, Any]:
    return {
        "schema_version": CONSISTENCY_SCHEMA_VERSION,
        "policy_version": policy_version,
        "criteria": {},
    }


def validate_policy(
    *,
    threshold: float,
    alpha: float,
    beta: float,
    calibrating_review_rate: float,
    reliable_review_rate: float,
    unreliable_review_rate: float,
) -> None:
    if not 0 <= threshold <= 1:
        raise ValueError("consistency threshold must be between 0 and 1")
    if alpha <= 0 or beta <= 0:
        raise ValueError("consistency prior alpha and beta must be positive")
    for name, value in {
        "calibrating": calibrating_review_rate,
        "reliable": reliable_review_rate,
        "unreliable": unreliable_review_rate,
    }.items():
        if not 0 <= value <= 1:
            raise ValueError(f"{name} review rate must be between 0 and 1")


def build_scorer_revision(
    model_version: Optional[str],
    candidate_hash: Optional[str],
    prompt_revision: str,
) -> str:
    return _sha256(
        {
            "model_version": model_version or "unknown-model",
            "candidate_hash": candidate_hash or "rubric-candidate",
            "prompt_revision": prompt_revision,
        }
    )


def record_key(
    case_id: str,
    criteria_file_name: str,
    criterion_id: str,
    criterion_hash: str,
    scorer_revision: str,
) -> str:
    return _sha256(
        {
            "case_id": case_id,
            "criteria_file_name": criteria_file_name,
            "criterion_id": str(criterion_id),
            "criterion_hash": criterion_hash,
            "scorer_revision": scorer_revision,
        }
    )


def record_matches_identity(
    record: Dict[str, Any],
    identity: RecordIdentity,
    *,
    allow_component_compatibility: bool = True,
) -> bool:
    """Return whether a record belongs to the same effective scorer.

    Current records match by the composite scorer revision. Component
    compatibility is limited to records created during the migration from a
    whole-rubric candidate hash to a criterion-level component hash. That
    compatibility still requires explicit model and prompt matches, so a real
    scorer change always starts a new calibration record.
    """

    if (
        str(record.get("case_id") or "") != identity.case_id
        or record.get("criteria_file_name") != identity.criteria_file_name
        or str(record.get("criterion_id")) != identity.criterion_id
        or record.get("criterion_hash") != identity.criterion_hash
    ):
        return False
    stored_model = record.get("model_version")
    stored_prompt = record.get("prompt_revision")
    if stored_model and str(stored_model) != identity.model_version:
        return False
    if stored_prompt and str(stored_prompt) != identity.prompt_revision:
        return False
    if record.get("scorer_revision") == identity.scorer_revision:
        return True
    if not allow_component_compatibility:
        return False
    return (
        record.get("scorer_component_hash") == identity.scorer_component_hash
        and str(record.get("model_version") or "") == identity.model_version
        and str(record.get("prompt_revision") or "") == identity.prompt_revision
    )


def latest_matching_record(
    records: Iterable[Dict[str, Any]],
    identity: RecordIdentity,
    *,
    allow_component_compatibility: bool = True,
) -> Optional[Dict[str, Any]]:
    matching = [
        record
        for record in records
        if record_matches_identity(
            record,
            identity,
            allow_component_compatibility=allow_component_compatibility,
        )
    ]
    if not matching:
        return None
    return max(matching, key=lambda item: str(item.get("updated_at") or ""))


def _base_state(agreement_count: int, correction_count: int, rate: float, threshold: float) -> str:
    if rate >= threshold:
        return STATE_RELIABLE
    if correction_count > 0:
        return STATE_UNRELIABLE
    return STATE_CALIBRATING


def _review_rate(
    state: str,
    *,
    calibrating_review_rate: float,
    reliable_review_rate: float,
    unreliable_review_rate: float,
) -> float:
    if state == STATE_RELIABLE:
        return reliable_review_rate
    if state == STATE_UNRELIABLE:
        return unreliable_review_rate
    return calibrating_review_rate


def ensure_record(
    consistency_store: Dict[str, Any],
    *,
    case_id: str,
    criteria_file_name: str,
    criterion_id: str,
    criterion_hash: str,
    scorer_revision: str,
    threshold: float,
    alpha: float,
    beta: float,
    calibrating_review_rate: float,
    reliable_review_rate: float,
    unreliable_review_rate: float,
) -> Dict[str, Any]:
    key = record_key(case_id, criteria_file_name, criterion_id, criterion_hash, scorer_revision)
    criteria = consistency_store.setdefault("criteria", {})
    record = criteria.get(key)
    if record is None:
        rate = alpha / (alpha + beta)
        state = _base_state(0, 0, rate, threshold)
        record = {
            "record_key": key,
            "case_id": case_id,
            "criteria_file_name": criteria_file_name,
            "criterion_id": str(criterion_id),
            "criteria_id": str(criterion_id),
            "criterion_hash": criterion_hash,
            "scorer_revision": scorer_revision,
            "state": state,
            "state_source": "evidence",
            "agreement_count": 0,
            "correction_count": 0,
            "smoothed_agreement": rate,
            "threshold": threshold,
            "review_rate": _review_rate(
                state,
                calibrating_review_rate=calibrating_review_rate,
                reliable_review_rate=reliable_review_rate,
                unreliable_review_rate=unreliable_review_rate,
            ),
            "override": None,
            "outcomes": {},
            "outcome_history": [],
            "assignments": {},
            "transitions": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        criteria[key] = record
    return record


def scores_equal(model_score: float, teacher_score: float, abs_tol: float = 1e-6) -> bool:
    return abs(float(model_score) - float(teacher_score)) <= abs_tol


def recompute_record(
    record: Dict[str, Any],
    *,
    threshold: float,
    alpha: float,
    beta: float,
    calibrating_review_rate: float,
    reliable_review_rate: float,
    unreliable_review_rate: float,
    trigger: str,
    trigger_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    outcomes = [item for item in record.get("outcomes", {}).values() if item.get("effective", True)]
    agreement_count = sum(1 for item in outcomes if item.get("verdict") == "accepted")
    correction_count = sum(1 for item in outcomes if item.get("verdict") == "corrected")
    rate = (agreement_count + alpha) / (agreement_count + correction_count + alpha + beta)
    evidence_state = _base_state(agreement_count, correction_count, rate, threshold)
    active_override = (record.get("override") or {}).get("active") is True
    state = STATE_RELIABLE if active_override else evidence_state
    source = "teacher_override" if active_override else "evidence"
    previous_state = record.get("state")
    previous_source = record.get("state_source")
    record.update(
        {
            "state": state,
            "state_source": source,
            "evidence_state": evidence_state,
            "agreement_count": agreement_count,
            "correction_count": correction_count,
            "smoothed_agreement": rate,
            "threshold": threshold,
            "review_rate": _review_rate(
                state,
                calibrating_review_rate=calibrating_review_rate,
                reliable_review_rate=reliable_review_rate,
                unreliable_review_rate=unreliable_review_rate,
            ),
            "updated_at": _now(),
        }
    )
    for assignment in record.get("assignments", {}).values():
        if assignment.get("reviewed_at"):
            continue
        effective_rate = 1.0 if assignment.get("conflict_override") else record["review_rate"]
        assignment["current_state"] = state
        assignment["review_rate"] = effective_rate
        assignment["review_required"] = float(assignment.get("sample", 1.0)) < effective_rate
        assignment["updated_at"] = _now()
    transitioned = previous_state != state or previous_source != source
    if transitioned:
        transition = {
            "transition_id": _sha256(
                {
                    "record_key": record.get("record_key"),
                    "from": previous_state,
                    "to": state,
                    "from_source": previous_source,
                    "to_source": source,
                    "agreement_count": agreement_count,
                    "correction_count": correction_count,
                    "trigger": trigger,
                    "trigger_event_id": trigger_event_id,
                }
            ),
            "from": previous_state,
            "to": state,
            "from_source": previous_source,
            "to_source": source,
            "agreement_count": agreement_count,
            "correction_count": correction_count,
            "smoothed_agreement": rate,
            "trigger": trigger,
            "trigger_event_id": trigger_event_id,
            "created_at": _now(),
        }
        if not any(
            item.get("transition_id") == transition["transition_id"]
            for item in record.setdefault("transitions", [])
        ):
            record["transitions"].append(transition)
        record["last_transition"] = transition
    return record


def upsert_outcome(
    record: Dict[str, Any],
    *,
    model_event_id: str,
    report_name: str,
    verdict: str,
    model_score: float,
    teacher_score: float,
    trigger: str,
    teacher_event_id: Optional[str] = None,
) -> bool:
    if verdict not in {"accepted", "corrected"}:
        raise ValueError("invalid consistency verdict")
    outcomes = record.setdefault("outcomes", {})
    current = outcomes.get(model_event_id)
    candidate = {
        "model_event_id": model_event_id,
        "report_name": report_name,
        "verdict": verdict,
        "model_score": float(model_score),
        "teacher_score": float(teacher_score),
        "trigger": trigger,
        "teacher_event_id": teacher_event_id,
        "effective": True,
    }
    comparable = {key: value for key, value in candidate.items() if key not in {"teacher_event_id"}}
    if current and all(current.get(key) == value for key, value in comparable.items()):
        return False
    if current:
        historical = deepcopy(current)
        historical["effective"] = False
        historical["superseded_at"] = _now()
        historical["superseded_by_teacher_event_id"] = teacher_event_id
        record.setdefault("outcome_history", []).append(historical)
    candidate["created_at"] = current.get("created_at") if current else _now()
    candidate["updated_at"] = _now()
    outcomes[model_event_id] = candidate
    return True


def remove_outcomes(record: Dict[str, Any], model_event_ids: list[str], *, trigger_event_id: str) -> bool:
    changed = False
    outcomes = record.setdefault("outcomes", {})
    for model_event_id in model_event_ids:
        current = outcomes.pop(model_event_id, None)
        if not current:
            continue
        historical = deepcopy(current)
        historical["effective"] = False
        historical["superseded_at"] = _now()
        historical["superseded_by_teacher_event_id"] = trigger_event_id
        record.setdefault("outcome_history", []).append(historical)
        changed = True
    return changed


def activate_override(record: Dict[str, Any], *, authorized_by: str, rubric_version: str) -> None:
    current = record.get("override") or {}
    if current.get("active"):
        return
    record["override"] = {
        "active": True,
        "source": "teacher_override",
        "authorized_by": authorized_by,
        "rubric_version": rubric_version,
        "activated_at": _now(),
    }


def release_override(record: Dict[str, Any], *, reason: str, trigger_event_id: Optional[str] = None) -> bool:
    current = record.get("override") or {}
    if not current.get("active"):
        return False
    current.update(
        {
            "active": False,
            "released_at": _now(),
            "release_reason": reason,
            "release_event_id": trigger_event_id,
        }
    )
    record["override"] = current
    return True


def assign_review(
    record: Dict[str, Any],
    *,
    model_event_id: str,
    report_name: str,
    policy_version: str,
    conflict_override: bool = False,
) -> Dict[str, Any]:
    effective_rate = 1.0 if conflict_override else float(record.get("review_rate", 0.5))
    sample = int(
        _sha256(
            {
                "record_key": record.get("record_key"),
                "model_event_id": model_event_id,
                "policy_version": policy_version,
            }
        )[:13],
        16,
    ) / float(16**13)
    assignment = {
        "model_event_id": model_event_id,
        "report_name": report_name,
        "criterion_id": record.get("criterion_id"),
        "criteria_id": record.get("criterion_id"),
        "state_at_assignment": record.get("state"),
        "review_rate": effective_rate,
        "review_required": sample < effective_rate,
        "sample": sample,
        "conflict_override": conflict_override,
        "policy_version": policy_version,
        "assigned_at": _now(),
    }
    record.setdefault("assignments", {})[model_event_id] = assignment
    return assignment


def summary(record: Dict[str, Any], *, has_open_conflict: bool = False) -> Dict[str, Any]:
    effective_review_rate = 1.0 if has_open_conflict else float(record.get("review_rate", 0.5))
    return {
        "record_key": record.get("record_key"),
        "criteria_file_name": record.get("criteria_file_name"),
        "criterion_id": record.get("criterion_id"),
        "criteria_id": record.get("criterion_id"),
        "criterion_hash": record.get("criterion_hash"),
        "scorer_revision": record.get("scorer_revision"),
        "state": record.get("state", STATE_CALIBRATING),
        "state_source": record.get("state_source", "evidence"),
        "evidence_state": record.get("evidence_state", record.get("state", STATE_CALIBRATING)),
        "agreement_count": int(record.get("agreement_count", 0)),
        "correction_count": int(record.get("correction_count", 0)),
        "smoothed_agreement": float(record.get("smoothed_agreement", 0.5)),
        "threshold": float(record.get("threshold", 0.8)),
        "review_rate": effective_review_rate,
        "effective_review_rate": effective_review_rate,
        "base_review_rate": float(record.get("review_rate", 0.5)),
        "review_required_count": sum(
            1
            for item in record.get("assignments", {}).values()
            if item.get("review_required") and not item.get("reviewed_at")
        ),
        "reviewed_count": len(record.get("outcomes", {})),
        "has_open_conflict": has_open_conflict,
        "conflict_override": has_open_conflict,
        "override": deepcopy(record.get("override")),
        "last_transition": deepcopy(record.get("last_transition")),
        "updated_at": record.get("updated_at"),
    }
