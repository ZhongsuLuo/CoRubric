from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _payload_digest(payload: Dict[str, Any]) -> str:
    allowed = {k: payload.get(k) for k in sorted(payload.keys()) if k not in {"graded_by", "teacher_id", "rubric_hash", "model_id"}}
    return core._sha256_data(allowed)


def _event_key(case_id: str, report_name: str, criterion_id: str, rubric_version: str, grader_type: str, grader_id: str, payload: Dict[str, Any]) -> str:
    return core._sha256_data({
        "case_id": case_id,
        "report_name": report_name,
        "criterion_id": str(criterion_id),
        "rubric_version": rubric_version,
        "grader_type": grader_type,
        "grader_id": grader_id,
        "request_id": payload.get("request_id"),
        "payload_digest": _payload_digest(payload),
    })


def _append_decision(store: Dict[str, Any], decision_type: str, event_ids: List[str], reason: str, **extra: Any) -> Dict[str, Any]:
    decision = {
        "decision_id": f"dec_{uuid.uuid4().hex}",
        "decision_type": decision_type,
        "event_ids": event_ids,
        "reason": reason,
        "created_at": core._now(),
        **extra,
    }
    store["decisions"].append(decision)
    return decision


def _item_from_event(ev: Dict[str, Any], status: Optional[str] = None) -> Dict[str, Any]:
    snap = ev["criterion_snapshot"]
    authored = ev.get("authored_grade") or {}
    accepted_model = ev.get("grader_type") == "model" and status == "accepted_model"
    return {
        "criteria_id": str(ev.get("criterion_id")),
        "criteria_name": snap["criteria_name"],
        "description": snap["description"],
        "original_description": snap["original_description"],
        "max_score": float(snap["max_score"]),
        "criteria_score": authored.get("criteria_score"),
        "file_original_content": authored.get("anchor_text", ""),
        "section": snap.get("section") or "",
        "target_section": authored.get("target_section"),
        # A missing teacher rationale may be supplemented after audit. Keep it
        # outside authored_grade so model text is never mistaken for a direct
        # teacher input, but show it in the normal score view for usability.
        "score_reason": authored.get("score_reason", "") or ev.get("model_score_reason", ""),
        "anchor_text": authored.get("anchor_text", ""),
        "evidence_anchors": deepcopy(authored.get("evidence_anchors") or []),
        "scoring_points": deepcopy(snap["scoring_points"]),
        "scoring_aggregation": deepcopy(snap["scoring_aggregation"]),
        "matched_scoring_points": list(authored.get("matched_scoring_points") or []),
        "matched_deduction_rules": list(authored.get("matched_deduction_rules") or []),
        "scoring_point_scores": _scoring_point_scores_with_model_reasons(
            authored.get("scoring_point_scores"),
            ev.get("model_scoring_point_scores"),
        ),
        "graded_by": "user" if accepted_model else ("model" if ev.get("grader_type") == "model" else "user"),
        "acceptance_status": "accepted_model" if accepted_model else status,
        "rubric_file_name": ev.get("rubric_file_name"),
        "provenance": {
            "event_id": ev.get("event_id"),
            "grader_type": ev.get("grader_type"),
            "decision_status": status,
            "grader_id": ev.get("grader_id"),
            "rubric_file_name": ev.get("rubric_file_name"),
            "rubric_version": ev.get("rubric_version"),
            "model_version": ev.get("model_version"),
        },
    }


def _conflict_event_refs(store: Dict[str, Any], event_ids: List[str]) -> List[Dict[str, Any]]:
    by_id = {str(event.get("event_id")): event for event in store.get("events", [])}
    audit_artifacts = store.get("audit_artifacts") or {}
    refs: List[Dict[str, Any]] = []
    for raw_id in event_ids:
        event_id = str(raw_id)
        event = by_id.get(event_id)
        if not event:
            continue
        authored = event.get("authored_grade") or {}
        artifact = audit_artifacts.get(event_id) or {}
        refs.append({
            "event_id": event_id,
            "report_name": event.get("report_name"),
            "criteria_id": str(event.get("criterion_id")),
            "criterion_id": str(event.get("criterion_id")),
            "rubric_file_name": event.get("rubric_file_name"),
            "rubric_version": event.get("rubric_version"),
            "grader_type": event.get("grader_type"),
            "score": authored.get("criteria_score"),
            "anchor_text": authored.get("anchor_text") or "",
            "score_reason": authored.get("score_reason") or "",
            "matched_scoring_points": list(authored.get("matched_scoring_points") or []),
            "matched_deduction_rules": list(authored.get("matched_deduction_rules") or []),
            "scoring_point_scores": deepcopy(authored.get("scoring_point_scores") or []),
            "audited_rule_ids": list(artifact.get("matched_rules") or []),
            "model_scoring_point_scores": deepcopy(artifact.get("model_scoring_point_scores") or []),
            "model_allocation_status": artifact.get("model_allocation_status"),
            "model_matched_scoring_points": list(artifact.get("model_matched_scoring_points") or []),
            "model_matched_deduction_rules": list(artifact.get("model_matched_deduction_rules") or []),
            "audit_fit": artifact.get("fit"),
            "audit_issue_type": artifact.get("issue_type"),
            "audit_reason": artifact.get("reason"),
            "expected_score_range": artifact.get("expected_score_range"),
            "recommended_score": artifact.get("recommended_score"),
            "recommended_scoring_point_scores": deepcopy(
                artifact.get("recommended_scoring_point_scores") or []
            ),
            "recommended_matched_scoring_points": list(
                artifact.get("recommended_matched_scoring_points") or []
            ),
            "recommended_matched_deduction_rules": list(
                artifact.get("recommended_matched_deduction_rules") or []
            ),
            "created_at": event.get("created_at"),
        })
    return refs


def _conflict_event_ids(conflict: Dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("event_ids", "suspect_event_ids", "supporting_event_ids", "affected_event_ids"):
        ids.update(str(event_id) for event_id in (conflict.get(key) or []) if event_id)
    for key in ("suspect_events", "supporting_events", "affected_events"):
        for ref in conflict.get(key) or []:
            if isinstance(ref, dict) and ref.get("event_id"):
                ids.add(str(ref["event_id"]))
    return ids


def _conflict_is_currently_open(store: Dict[str, Any], conflict: Dict[str, Any]) -> bool:
    if conflict.get("status") != "open":
        return False
    event_ids = _conflict_event_ids(conflict)
    if not event_ids:
        return True
    return not all(
        core._event_status(event_id, store.get("decisions", [])) == "superseded"
        for event_id in event_ids
    )


def _conflict_rubric_files(store: Dict[str, Any], conflict: Dict[str, Any]) -> set[str]:
    by_event_id = {str(event.get("event_id")): event for event in store.get("events", [])}
    return {
        str(by_event_id[event_id].get("rubric_file_name") or "criteria.json")
        for event_id in _conflict_event_ids(conflict)
        if event_id in by_event_id
    }


def _conflict_is_deferred_by_pending_update(case_id: str, store: Dict[str, Any], conflict: Dict[str, Any]) -> bool:
    criterion_id = str(conflict.get("criterion_id") or "")
    if not criterion_id:
        return False
    return any(
        core._pending_update_proposals_for_criterion(case_id, rubric_file_name, criterion_id)
        for rubric_file_name in (_conflict_rubric_files(store, conflict) or {"criteria.json"})
    )


def defer_conflicts_for_pending_update(
    case_id: str,
    criteria_file_name: Optional[str],
    criterion_id: str,
    proposal_id: str,
) -> None:
    """Retire conflict results made against a rubric that is awaiting an update decision."""
    criteria_name = criteria_file_name or "criteria.json"
    with core._lock_for(case_id):
        store = core._store(case_id)
        changed = False
        for conflict in store.get("conflicts", []):
            if (
                _conflict_is_currently_open(store, conflict)
                and str(conflict.get("criterion_id")) == str(criterion_id)
                and criteria_name in (_conflict_rubric_files(store, conflict) or {"criteria.json"})
            ):
                conflict["status"] = "deferred"
                conflict["deferred_at"] = core._now()
                conflict["deferred_by_proposal_id"] = proposal_id
                conflict["deferred_reason"] = "pending_rubric_update"
                changed = True
        if changed:
            _, rubric_version, _ = core._load_criteria(criteria_name)
            readiness = store.setdefault("readiness", {}).setdefault("criteria", {})
            state = core._build_readiness_state(
                "evaluating",
                str(criterion_id),
                rubric_version,
                "pending_rubric_update",
            )
            state["readiness_basis_hash"] = core._readiness_basis_hash(
                store, rubric_version
            )
            readiness[str(criterion_id)] = state
            core._write_store(case_id, store)


def _ensure_model_migration_conflict(
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion_id: str,
    source_rubric_version: Optional[str],
    target_rubric_version: str,
) -> Optional[Dict[str, Any]]:
    # Kept as a compatibility hook for callers. Rubric migration is revision
    # state, not a teacher-facing conflict task.
    return None


def _supersede_conflicts_for_events(
    store: Dict[str, Any],
    event_ids: List[str],
    *,
    reason: str,
    superseded_by: Optional[str] = None,
) -> None:
    event_id_set = {str(event_id) for event_id in event_ids if event_id}
    if not event_id_set:
        return
    now = core._now()
    for conflict in store.get("conflicts", []):
        if conflict.get("status") != "open":
            continue
        if not (_conflict_event_ids(conflict) & event_id_set):
            continue
        conflict["status"] = "superseded"
        conflict["superseded_at"] = now
        conflict["superseded_reason"] = reason
        if superseded_by:
            conflict["superseded_by"] = superseded_by
    artifacts = store.setdefault("audit_artifacts", {})
    for event_id in event_id_set:
        artifact = artifacts.get(event_id)
        if isinstance(artifact, dict):
            artifact["audit_status"] = "superseded"
            artifact["superseded_at"] = now
            artifact["superseded_reason"] = reason


def _calculate_file_score(criteria_items: List[Dict[str, Any]], base_score: float) -> float:
    total_deduction = 0.0
    for item in criteria_items:
        if item.get("criteria_score") is None:
            continue
        total_deduction += float(item.get("max_score", 0) or 0) - float(item.get("criteria_score", 0) or 0)
    return max(base_score - total_deduction, 0.0)


def _scoring_point_scores_with_model_reasons(
    authored_scores: Any,
    model_scores: Any,
) -> List[Dict[str, Any]]:
    """Fill only missing teacher point reasons from a validated model explanation."""
    authored = deepcopy(authored_scores or [])
    generated = deepcopy(model_scores or [])
    if not authored:
        return generated
    generated_by_id = {
        str(item.get("point_id")): item
        for item in generated
        if isinstance(item, dict) and item.get("point_id")
    }
    for item in authored:
        if not isinstance(item, dict) or str(item.get("reason") or "").strip():
            continue
        generated_item = generated_by_id.get(str(item.get("point_id")))
        if not generated_item:
            continue
        reason = str(generated_item.get("reason") or "").strip()
        if reason:
            item["reason"] = reason
    return authored


def _model_scoring_point_explanation(
    criterion: Dict[str, Any],
    raw_scores: Any,
    teacher_score: Any,
    authored_scores: Any = None,
) -> tuple[List[Dict[str, Any]], str]:
    """Validate a model's explanation without ever changing the teacher total."""
    if not raw_scores:
        return [], "not_provided"
    if not isinstance(raw_scores, list):
        return [], "invalid"
    try:
        total = float(teacher_score)
        scores = [core.ScoringPointScore.model_validate(item) for item in raw_scores]
        normalized = core._validated_scoring_point_scores(
            criterion,
            scores,
            total,
            require_complete=True,
        )
        if any(not str(item.get("reason") or "").strip() for item in normalized):
            return [], "invalid"
        if authored_scores:
            authored = core._validated_scoring_point_scores(
                criterion,
                authored_scores,
                total,
                require_complete=True,
            )
            authored_by_id = {item["point_id"]: item["score"] for item in authored}
            if any(
                abs(item["score"] - authored_by_id.get(item["point_id"], float("inf"))) > 1e-6
                for item in normalized
            ):
                return [], "invalid"
        return normalized, "valid"
    except (TypeError, ValueError):
        return [], "invalid"


def materialize_projection(
    case_id: str,
    report_name: str,
    store: Optional[Dict[str, Any]] = None,
    criteria_file_name: Optional[str] = None,
    *,
    persist: bool = False,
) -> Dict[str, Any]:
    store = store or core._store(case_id)
    grade_dir = core._case_dir(case_id) / "grade_result"
    projection_path = grade_dir / f"{report_name}_grade.json"
    result = {"file_name": f"{report_name}.md", "language": "zh", "base_score": 100, "file_score": 100, "criteria": []}
    by_criterion: Dict[str, Dict[str, Any]] = {}
    relevant = [
        e for e in store["events"]
        if e.get("report_name") == report_name
        and (not criteria_file_name or e.get("rubric_file_name") == criteria_file_name)
    ]
    current_versions: Dict[str, str] = {}
    current_criterion_hashes: Dict[tuple[str, str], str] = {}
    rubric_files = {
        str(event.get("rubric_file_name") or "criteria.json")
        for event in relevant
        if event.get("event_type", "grade_authored") == "grade_authored"
    }
    for rubric_file in rubric_files:
        _, version, rubric_data = core._load_criteria(rubric_file)
        current_versions[rubric_file] = version
        for criterion in rubric_data.get("criteria") or []:
            current_criterion_hashes[
                (rubric_file, str(criterion.get("criteria_id")))
            ] = core._sha256_data(criterion)

    teacher_events: Dict[str, tuple[Dict[str, Any], str]] = {}
    accepted_model_events: Dict[str, tuple[Dict[str, Any], str]] = {}
    current_model_events: Dict[str, tuple[Dict[str, Any], str]] = {}
    for ev in relevant:
        if ev.get("event_type") != "grade_authored":
            continue
        criterion_id = str(ev.get("criterion_id"))
        status = core._event_status(str(ev["event_id"]), store["decisions"])
        rubric_file = str(ev.get("rubric_file_name") or "criteria.json")
        current_criterion_hash = current_criterion_hashes.get(
            (rubric_file, criterion_id)
        )
        event_criterion_hash = str(
            ev.get("criterion_hash")
            or core._sha256_data(ev.get("criterion_snapshot") or {})
        )
        revision = core._active_revision(store, rubric_file, criterion_id)
        old_card_invalidated = bool(
            revision
            and revision.get("target_criterion_hash") == current_criterion_hash
            and revision.get("status") not in {"completed", "completed_with_issues"}
            and event_criterion_hash != current_criterion_hash
        )
        if (
            ev.get("grader_type") == "teacher"
            and status in {"trusted", "pending", "quarantined", "manual_exception"}
            and not old_card_invalidated
        ):
            teacher_events[criterion_id] = (ev, status)
            continue
        if ev.get("grader_type") != "model":
            continue
        if status == "accepted_model" and event_criterion_hash == current_criterion_hash:
            accepted_model_events[criterion_id] = (ev, status)
            continue
        if status == "model_only" and event_criterion_hash == current_criterion_hash:
            current_model_events[criterion_id] = (ev, status)

    for criterion_id in set(teacher_events) | set(accepted_model_events) | set(current_model_events):
        selected = teacher_events.get(criterion_id) or accepted_model_events.get(criterion_id) or current_model_events.get(criterion_id)
        if selected:
            by_criterion[criterion_id] = _item_from_event(*selected)
    result["criteria"] = list(by_criterion.values())
    if relevant:
        metadata = relevant[-1].get("rubric_metadata") or {}
        result["base_score"] = float(metadata.get("base_score") or result.get("base_score") or 100)
        result["language"] = metadata.get("language") or result.get("language") or "zh"
    base = float(result.get("base_score") or 100)
    result["file_score"] = _calculate_file_score(result["criteria"], base)
    if persist:
        grade_dir.mkdir(parents=True, exist_ok=True)
        core._atomic_write_json(projection_path, result)
    return result
