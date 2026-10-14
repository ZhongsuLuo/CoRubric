from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Dict, Optional

from .proposal_sources import proposal_source_event_digest


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _proposal_match_key(proposal: Dict[str, Any]) -> tuple[str, str, str, str]:
    criteria_file = str(proposal.get("criteria_file_name") or "criteria.json")
    proposal_type = str(proposal.get("proposal_type") or "")
    if proposal_type == "update_criterion":
        return (criteria_file, proposal_type, str(proposal.get("target_criterion_id") or ""), "")
    suggested = proposal.get("suggested_criterion") or {}
    name = " ".join(str(suggested.get("criteria_name") or "").lower().split())
    section = " ".join(str(suggested.get("section") or "").lower().split())
    return (criteria_file, proposal_type, name, section)


def _event_has_pending_update_proposal(case_id: str, event_id: str) -> bool:
    return _pending_update_proposal_for_event(case_id, event_id) is not None


def _pending_update_proposal_for_event(case_id: str, event_id: str) -> Optional[Dict[str, Any]]:
    for proposal in core._read_proposals(case_id):
        if proposal.get("status") != "pending":
            continue
        if proposal.get("proposal_type") != "update_criterion":
            continue
        if str(event_id) in {str(item) for item in proposal.get("source_event_ids", [])}:
            return proposal
    return None


def _proposal_is_refinement_placeholder(proposal: Optional[Dict[str, Any]]) -> bool:
    if not proposal:
        return False
    patch = proposal.get("rubric_patch") or {}
    suggested = proposal.get("suggested_criterion") or {}
    description = str(suggested.get("description") or "")
    return (
        patch.get("reason") == "unexplained_partial_score_for_binary_rule"
        or "待细化" in description
        or "需要补充部分得分档位" in description
    )


def _proposal_contradicts_source_event(proposal: Optional[Dict[str, Any]], event: Dict[str, Any]) -> bool:
    if not proposal:
        return False
    authored = event.get("authored_grade") or {}
    anchor_text = str(authored.get("anchor_text") or "")
    suggested = proposal.get("suggested_criterion") or {}
    proposal_text = "\n".join([
        str(suggested.get("description") or ""),
        core._canonical_json(suggested.get("scoring_points") or []),
        str(proposal.get("rationale") or ""),
    ])
    claims_missing_name = any(
        phrase in proposal_text
        for phrase in ("缺姓名", "缺失姓名", "缺少姓名", "仅出现标题", "只出现标题")
    )
    if claims_missing_name and ("姓名" in anchor_text or "name" in anchor_text.lower()):
        return True
    claims_missing_title = any(
        phrase in proposal_text
        for phrase in ("缺标题", "缺失标题", "缺少标题", "仅出现姓名", "只出现姓名")
    )
    if claims_missing_title and ("实验报告" in anchor_text or "report" in anchor_text.lower()):
        return True
    return False


def _supersede_update_proposal(case_id: str, proposal_id: str, reason: str) -> None:
    proposals = core._read_proposals(case_id)
    changed = False
    now = core._now()
    for proposal in proposals:
        if proposal.get("proposal_id") != proposal_id or proposal.get("status") != "pending":
            continue
        proposal["status"] = "superseded"
        proposal["failure_reason"] = reason
        proposal["updated_at"] = now
        changed = True
    if changed:
        core._write_proposals(case_id, proposals)


def _event_update_proposal_allowed(event: Dict[str, Any]) -> bool:
    comparison = event.get("model_comparison") or {}
    # Ordinary update review is strictly teacher-versus-model. Teacher-written
    # prose alone is not a rubric change signal.
    return bool(
        comparison.get("total_score_changed")
        or comparison.get("scoring_point_scores_changed")
        or comparison.get("matched_scoring_points_changed")
        or comparison.get("matched_deduction_rules_changed")
    )


def _scoring_point_score_map(authored_grade: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for item in authored_grade.get("scoring_point_scores") or []:
        point_id = str(item.get("point_id") or "")
        if not point_id:
            continue
        try:
            scores[point_id] = float(item.get("score") or 0)
        except (TypeError, ValueError):
            continue
    return scores


def _model_comparison_for_teacher_event(
    store: Dict[str, Any],
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    model_event_id = str(event.get("compared_model_event_id") or "")
    model_event = next(
        (
            candidate
            for candidate in store.get("events", [])
            if model_event_id and str(candidate.get("event_id")) == model_event_id
        ),
        None,
    )
    if model_event is None:
        criterion_hash = str(
            event.get("criterion_hash")
            or core._sha256_data(event.get("criterion_snapshot") or {})
        )
        candidates = [
            candidate
            for candidate in store.get("events", [])
            if candidate.get("event_type", "grade_authored") == "grade_authored"
            and candidate.get("grader_type") == "model"
            and candidate.get("report_name") == event.get("report_name")
            and candidate.get("rubric_file_name") == event.get("rubric_file_name")
            and str(candidate.get("criterion_id")) == str(event.get("criterion_id"))
            and str(
                candidate.get("criterion_hash")
                or core._sha256_data(candidate.get("criterion_snapshot") or {})
            )
            == criterion_hash
            and str(candidate.get("created_at") or "") <= str(event.get("created_at") or "")
        ]
        model_event = candidates[-1] if candidates else None
    if model_event is None:
        return None

    model_grade = model_event.get("authored_grade") or {}
    teacher_grade = event.get("authored_grade") or {}
    model_points = _scoring_point_score_map(model_grade)
    teacher_points = _scoring_point_score_map(teacher_grade)
    all_point_ids = set(model_points) | set(teacher_points)
    point_scores_changed = any(
        abs(model_points.get(point_id, 0) - teacher_points.get(point_id, 0)) > 1e-6
        for point_id in all_point_ids
    )
    try:
        model_score = float(model_grade.get("criteria_score") or 0)
        teacher_score = float(teacher_grade.get("criteria_score") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "model_event_id": str(model_event.get("event_id") or ""),
        "model_event_digest": proposal_source_event_digest(model_event),
        "model_score": model_score,
        "teacher_score": teacher_score,
        "model_scoring_point_scores": model_points,
        "teacher_scoring_point_scores": teacher_points,
        "total_score_changed": abs(model_score - teacher_score) > 1e-6,
        "scoring_point_scores_changed": point_scores_changed,
        "matched_scoring_points_changed": set(
            str(item) for item in model_grade.get("matched_scoring_points") or []
        )
        != set(str(item) for item in teacher_grade.get("matched_scoring_points") or []),
        "matched_deduction_rules_changed": set(
            str(item) for item in model_grade.get("matched_deduction_rules") or []
        )
        != set(str(item) for item in teacher_grade.get("matched_deduction_rules") or []),
    }


def _binary_partial_score_gap(criterion: Dict[str, Any], event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    authored = event.get("authored_grade") or {}
    try:
        score = float(authored.get("criteria_score") or 0)
        max_score = float(authored.get("max_score") or criterion.get("max_score") or 0)
    except (TypeError, ValueError):
        return None
    if max_score <= 0 or score <= 0 or score >= max_score:
        return None

    text = "\n".join(
        str(criterion.get(key) or "")
        for key in ("description", "original_description", "criteria_name")
    )
    max_label = str(int(max_score)) if max_score.is_integer() else str(max_score)
    binary_missing_rule = (
        "缺失" in text
        and (f"扣{max_label}" in text or f"扣 {max_label}" in text or f"deduct {max_label}" in text.lower())
    )
    if not binary_missing_rule:
        return None
    return {
        "reason": "partial_score_for_binary_rule",
        "teacher_score": score,
        "current_supported_scores": [0, max_score],
    }


def _anchor_feature_flags(anchor_text: str) -> Dict[str, bool]:
    lower = anchor_text.lower()
    return {
        "contains_name_marker": "姓名" in anchor_text or "name" in lower,
        "contains_report_title_marker": "实验报告" in anchor_text or "report" in lower,
        "contains_experiment_name_marker": "实验名称" in anchor_text or "experiment" in lower,
    }


def _criterion_refinement_signature(
    criteria_file_name: str,
    criterion_version: str,
    criterion_id: str,
    event: Dict[str, Any],
    binary_gap: Optional[Dict[str, Any]] = None,
) -> str:
    authored = event.get("authored_grade") or {}
    anchor_text = str(authored.get("anchor_text") or "")
    max_score = authored.get("max_score")
    score = authored.get("criteria_score")
    try:
        score_value = float(score)
        max_value = float(max_score or 0)
        score_fraction = round(score_value / max_value, 4) if max_value else None
    except (TypeError, ValueError, ZeroDivisionError):
        score_value = score
        max_value = max_score
        score_fraction = None
    return core._sha256_data({
        "criteria_file_name": criteria_file_name,
        "criterion_id": str(criterion_id),
        "criterion_version": criterion_version,
        "score": score_value,
        "max_score": max_value,
        "score_fraction": score_fraction,
        "has_anchor": bool(core._normalized(anchor_text)),
        "has_reason": bool(core._normalized(authored.get("score_reason"))),
        "scoring_point_scores": sorted(_scoring_point_score_map(authored).items()),
        "target_section": authored.get("target_section"),
        "anchor_flags": _anchor_feature_flags(anchor_text),
        "gap_reason": (binary_gap or {}).get("reason") or "teacher_grade_refinement",
    })


def _proposal_refinement_signature(proposal: Dict[str, Any]) -> str:
    suggested = proposal.get("suggested_criterion") or {}
    return core._sha256_data({
        "proposal_type": proposal.get("proposal_type"),
        "target_criterion_id": proposal.get("target_criterion_id"),
        "description": suggested.get("description"),
        "original_description": suggested.get("original_description"),
        "max_score": suggested.get("max_score"),
        "scoring_points": suggested.get("scoring_points") or [],
        "scoring_aggregation": suggested.get("scoring_aggregation"),
        "rubric_patch": proposal.get("rubric_patch"),
    })


def _refinement_rejection_applies(
    store: Dict[str, Any],
    criteria_file_name: str,
    criterion_version: str,
    criterion_id: str,
    event: Dict[str, Any],
    binary_gap: Optional[Dict[str, Any]],
) -> bool:
    signature = _criterion_refinement_signature(criteria_file_name, criterion_version, criterion_id, event, binary_gap)
    return any(
        item.get("event_signature") == signature
        and item.get("status") == "rejected"
        for item in store.get("refinement_rejections", [])
    )


def record_refinement_rejection(case_id: str, proposal: Dict[str, Any], reason: Optional[str] = None) -> None:
    if proposal.get("proposal_type") != "update_criterion":
        return
    criteria_file_name = str(proposal.get("criteria_file_name") or "criteria.json")
    criterion_id = str(proposal.get("target_criterion_id") or "")
    criterion_version = str(proposal.get("target_criterion_version_at_proposal") or "")
    source_ids = {str(item) for item in proposal.get("source_event_ids", [])}
    if not criterion_id or not criterion_version or not source_ids:
        return
    with core._lock_for(case_id):
        store = core._store(case_id)
        by_id = {str(event.get("event_id")): event for event in store.get("events", [])}
        existing = {
            (str(item.get("event_signature")), str(item.get("proposal_signature")))
            for item in store.get("refinement_rejections", [])
        }
        proposal_signature = _proposal_refinement_signature(proposal)
        now = core._now()
        for event_id in source_ids:
            event = by_id.get(event_id)
            if not event:
                continue
            patch = proposal.get("rubric_patch") if isinstance(proposal.get("rubric_patch"), dict) else None
            event_signature = _criterion_refinement_signature(criteria_file_name, criterion_version, criterion_id, event, patch)
            key = (event_signature, proposal_signature)
            if key in existing:
                continue
            store.setdefault("refinement_rejections", []).append({
                "rejection_id": f"rej_{uuid.uuid4().hex}",
                "status": "rejected",
                "proposal_id": proposal.get("proposal_id"),
                "criteria_file_name": criteria_file_name,
                "criterion_id": criterion_id,
                "criterion_version": criterion_version,
                "source_event_id": event_id,
                "event_signature": event_signature,
                "proposal_signature": proposal_signature,
                "reason": reason or proposal.get("failure_reason"),
                "created_at": now,
                "updated_at": now,
            })
            existing.add(key)
        core._write_store(case_id, store)


def _propose_refinement(
    evaluator: core.ConsistencyAuditEvaluator,
    report_text: str,
    criterion: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        return evaluator.propose_criterion_update(report_text, criterion, event, [])  # type: ignore[misc]
    except TypeError:
        return evaluator.propose_criterion_update(report_text, criterion, event)


def _coerce_suggested_criterion(raw: Dict[str, Any], current_criterion: Dict[str, Any]) -> Dict[str, Any]:
    suggested = raw.get("suggested_criterion")
    if not isinstance(suggested, dict):
        suggested = {}
    merged = deepcopy(current_criterion)
    for key in ("criteria_name", "description", "original_description", "max_score", "section", "few_shot_examples", "scoring_points", "scoring_aggregation"):
        if key in suggested and suggested[key] is not None:
            merged[key] = suggested[key]
        elif key in raw and raw[key] is not None:
            merged[key] = raw[key]
    merged["criteria_id"] = str(current_criterion.get("criteria_id"))
    if "few_shot_examples" not in merged or merged["few_shot_examples"] is None:
        merged["few_shot_examples"] = []
    return merged


def _store_event_update_proposal(
    case_id: str,
    criteria_file_name: str,
    rubric_version: str,
    criteria_data: Dict[str, Any],
    criterion: Dict[str, Any],
    event: Dict[str, Any],
    raw: Dict[str, Any],
) -> Dict[str, Any]:
    now = core._now()
    criterion_id = str(criterion.get("criteria_id"))
    event_id = str(event["event_id"])
    event_digest = proposal_source_event_digest(event)
    comparison = event.get("model_comparison") or {}
    model_event_id = str(comparison.get("model_event_id") or "")
    explicit_source_ids = [str(item) for item in raw.get("source_event_ids") or []]
    explicit_source_digests = [
        str(item) for item in raw.get("source_event_digests") or []
    ]
    source_event_ids = explicit_source_ids or [event_id]
    source_event_digests = explicit_source_digests or [event_digest]
    if model_event_id and model_event_id not in set(source_event_ids):
        source_event_ids.append(model_event_id)
        source_event_digests.append(str(comparison.get("model_event_digest") or ""))
    source_teacher_event_ids = (
        [event_id]
        if model_event_id
        else [str(item) for item in source_event_ids]
    )
    source_model_event_ids = [model_event_id] if model_event_id else []
    proposal = {
        "proposal_id": f"prop_{uuid.uuid4().hex}",
        "proposal_version": 1,
        "case_id": case_id,
        "proposal_type": "update_criterion",
        "target_criterion_id": criterion_id,
        "target_criterion_version_at_proposal": core._criterion_version(criteria_data, criterion_id),
        "source_implicit_grade_ids": [],
        "source_grade_digests": [],
        "source_event_ids": source_event_ids,
        "source_event_digests": source_event_digests,
        "source_teacher_event_ids": source_teacher_event_ids,
        "source_model_event_ids": source_model_event_ids,
        "scoring_diff": deepcopy(comparison) if comparison else None,
        "status": "pending",
        "criteria_file_name": criteria_file_name,
        "criteria_file_version_at_proposal": rubric_version,
        "suggested_criterion": _coerce_suggested_criterion(raw, criterion),
        "rubric_patch": raw.get("rubric_patch"),
        "rationale": raw.get("rationale"),
        "model_id": core.GRADING_MODEL_ID,
        "confidence": raw.get("confidence"),
        "confirmed_event_ids": [],
        "failure_reason": None,
        "created_at": now,
        "updated_at": now,
        "confirmed_at": None,
        "confirmed_by": None,
    }
    proposals = core._read_proposals(case_id)
    match_key = _proposal_match_key(proposal)
    stored = next(
        (
            item for item in proposals
            if item.get("status") == "pending" and _proposal_match_key(item) == match_key
        ),
        None,
    )
    if stored:
        merged_ids = list(stored.get("source_event_ids") or [])
        merged_digests = list(stored.get("source_event_digests") or [])
        for source_id, source_digest in zip(source_event_ids, source_event_digests):
            if source_id not in {str(item) for item in merged_ids}:
                merged_ids.append(source_id)
                merged_digests.append(source_digest)
        stored["source_event_ids"] = merged_ids
        stored["source_event_digests"] = merged_digests
        for key in ("source_teacher_event_ids", "source_model_event_ids"):
            stored[key] = list(
                dict.fromkeys(
                    [
                        *[str(item) for item in stored.get(key) or []],
                        *[str(item) for item in proposal.get(key) or []],
                    ]
                )
            )
        if proposal.get("scoring_diff"):
            stored["scoring_diff"] = deepcopy(proposal["scoring_diff"])
        stored["proposal_version"] = int(stored.get("proposal_version") or 1) + 1
        stored["updated_at"] = now
    else:
        stored = proposal
        proposals.append(stored)
    core._write_proposals(case_id, proposals)
    return stored
