from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()
def _rubric_has_independent_grounding(criterion: Dict[str, Any]) -> bool:
    return bool(
        criterion.get("few_shot_examples")
        or criterion.get("examples")
        or criterion.get("rules")
        or criterion.get("scoring_points")
    )


def _boundary_for(criterion_id: str, rubric_version: str, trusted: List[Dict[str, Any]]) -> Dict[str, Any]:
    sections = sorted({
        (event.get("authored_grade") or {}).get("target_section")
        for event in trusted
        if (event.get("authored_grade") or {}).get("target_section")
    })
    scores = [float((event.get("authored_grade") or {}).get("criteria_score", 0) or 0) for event in trusted]
    metadata = trusted[0].get("rubric_metadata") or {}
    return {
        "schema_version": "1.0",
        "criterion_id": str(criterion_id),
        "rubric_version": rubric_version,
        "covered_rule_ids": [str(criterion_id), *[str(point.get("point_id")) for point in (trusted[0].get("criterion_snapshot") or {}).get("scoring_points", []) if isinstance(point, dict) and point.get("point_id")]],
        "allowed_evidence_types": ["text", "anchor_text"],
        "allowed_sections": sections,
        "observed_score_range": [min(scores), max(scores)],
        "required_evidence_signals": ["text"],
        "exclusion_signals": ["partial", "unknown_criterion", "rubric_mismatch"],
        "uncertainty_signals": ["missing_text_only_markdown", "evaluator_unavailable"],
        "rubric_title": metadata.get("title"),
        "language": metadata.get("language") or "zh",
        "evaluator_id": core.GRADING_MODEL_ID,
    }


def _rubric_only_boundary(
    criterion: Dict[str, Any],
    rubric_version: str,
    rubric_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = rubric_metadata or {}
    criterion_id = str(criterion.get("criteria_id"))
    max_score = float(criterion.get("max_score", 0) or 0)
    return {
        "schema_version": "1.0",
        "criterion_id": criterion_id,
        "rubric_version": rubric_version,
        "covered_rule_ids": [
            criterion_id,
            *[
                str(point.get("point_id"))
                for point in criterion.get("scoring_points") or []
                if isinstance(point, dict) and point.get("point_id")
            ],
        ],
        "allowed_evidence_types": ["text", "anchor_text"],
        # A rubric group is an organizational hint, not observed evidence that
        # every report uses the exact same section heading. Only teacher-authored
        # evidence may establish a restrictive section boundary.
        "allowed_sections": [],
        "observed_score_range": [0.0, max_score],
        "required_evidence_signals": ["text"],
        "exclusion_signals": ["partial", "unknown_criterion", "rubric_mismatch"],
        "uncertainty_signals": ["missing_text_only_markdown", "evaluator_unavailable"],
        "rubric_title": metadata.get("title"),
        "rubric_background": metadata.get("background"),
        "language": metadata.get("language") or "zh",
        "evaluator_id": core.GRADING_MODEL_ID,
        "source": "rubric_only_bootstrap",
    }


def _examples_with_text(
    case_id: str,
    events: List[Dict[str, Any]],
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    examples = []
    for event in events:
        enriched = deepcopy(event)
        report_text = core._report_text(case_id, str(event.get("report_name")))
        anchor = str((event.get("authored_grade") or {}).get("anchor_text") or "")
        enriched["selected_evidence"] = anchor
        enriched["source_grader_type"] = str(event.get("grader_type") or "")
        enriched["source_decision_status"] = core._event_status(
            str(event.get("event_id")), decisions or []
        )
        enriched["supporting_context"] = core._teacher_supporting_context(report_text, event)
        examples.append(enriched)
    return examples


def _persist_candidate(
    case_id: str,
    criterion: Dict[str, Any],
    rubric_version: str,
    event_pool: List[Dict[str, Any]],
    check: Dict[str, Any],
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    construction_ids = list(check.get("construction_event_ids") or [])
    validation_ids = [str(check["held_out_event_id"])]
    if set(construction_ids) & set(validation_ids):
        raise ValueError("candidate construction and validation sets must be disjoint")
    by_id = {event["event_id"]: event for event in event_pool}
    snapshot = deepcopy(criterion)
    examples = list(snapshot.get("few_shot_examples") or [])
    for event_id in construction_ids:
        event = by_id[event_id]
        authored = event.get("authored_grade") or {}
        examples.append({
            "example_input": authored.get("anchor_text") or core._report_text(case_id, str(event.get("report_name"))) or "",
            "example_output_description": authored.get("score_reason") or "",
            "example_output_score": authored.get("criteria_score"),
            "source_event_id": event_id,
            "source_grader_type": event.get("grader_type"),
            "source_decision_status": core._event_status(event_id, decisions or []),
        })
    snapshot["few_shot_examples"] = examples
    candidate_hash = core._sha256_data({
        "parent_rubric_version": rubric_version,
        "criterion": snapshot,
        "construction_event_ids": construction_ids,
        "validation_event_ids": validation_ids,
    })
    criterion_id = str(criterion.get("criteria_id"))
    manifest = {
        "candidate_hash": candidate_hash,
        "parent_rubric_version": rubric_version,
        "construction_event_ids": construction_ids,
        "validation_event_ids": validation_ids,
        "criterion_contract_hash": core._sha256_data({
            "criteria_id": criterion_id,
            "original_description": criterion.get("original_description"),
            "max_score": criterion.get("max_score"),
            "scoring_points": criterion.get("scoring_points") or [],
            "scoring_aggregation": criterion.get("scoring_aggregation"),
        }),
        "creation_model": core.GRADING_MODEL_ID,
        "created_at": core._now(),
        "source_events_relabelled": False,
    }
    candidate_dir = core._alignment_dir(case_id) / "candidates" / criterion_id
    core._atomic_write_json(candidate_dir / f"{candidate_hash}.json", snapshot)
    core._atomic_write_json(candidate_dir / f"{candidate_hash}.manifest.json", manifest)
    return manifest


def _persist_rubric_only_candidate(
    case_id: str,
    criterion: Dict[str, Any],
    rubric_version: str,
) -> Dict[str, Any]:
    snapshot = deepcopy(criterion)
    construction_ids: List[str] = []
    validation_ids: List[str] = []
    candidate_hash = core._sha256_data({
        "parent_rubric_version": rubric_version,
        "criterion": snapshot,
        "construction_event_ids": construction_ids,
        "validation_event_ids": validation_ids,
    })
    criterion_id = str(criterion.get("criteria_id"))
    manifest = {
        "candidate_hash": candidate_hash,
        "parent_rubric_version": rubric_version,
        "construction_event_ids": construction_ids,
        "validation_event_ids": validation_ids,
        "criterion_contract_hash": core._sha256_data({
            "criteria_id": criterion_id,
            "original_description": criterion.get("original_description"),
            "max_score": criterion.get("max_score"),
            "scoring_points": criterion.get("scoring_points") or [],
            "scoring_aggregation": criterion.get("scoring_aggregation"),
        }),
        "creation_model": core.GRADING_MODEL_ID,
        "created_at": core._now(),
        "source_events_relabelled": False,
        "bootstrap_mode": "rubric_only",
    }
    candidate_dir = core._alignment_dir(case_id) / "candidates" / criterion_id
    core._atomic_write_json(candidate_dir / f"{candidate_hash}.json", snapshot)
    core._atomic_write_json(candidate_dir / f"{candidate_hash}.manifest.json", manifest)
    return manifest


def _readiness_policy_matches(state: Optional[Dict[str, Any]]) -> bool:
    if not state:
        return False
    if state.get("readiness_policy_version") != core.READINESS_POLICY_VERSION:
        return False
    # States written before this setting existed used the original one-sample gate.
    recorded = int(state.get("min_teacher_samples", 1) or 0)
    return recorded == core.AUTO_GRADE_MIN_TEACHER_SAMPLES


def _has_teacher_history(store: Dict[str, Any], criterion_id: str) -> bool:
    return any(
        event.get("grader_type") == "teacher"
        and str(event.get("criterion_id")) == str(criterion_id)
        for event in store.get("events", [])
    )


def _readiness_state_reusable(
    store: Dict[str, Any],
    criterion_id: str,
    rubric_version: str,
    state: Optional[Dict[str, Any]],
) -> bool:
    if not _readiness_policy_matches(state) or state.get("rubric_version") != rubric_version:
        return False
    if state.get("readiness_basis_hash") != core._readiness_basis_hash(
        store, rubric_version
    ):
        return False
    if state.get("reason") == "rubric_only_bootstrap_enabled" and _has_teacher_history(store, criterion_id):
        return False
    return True


def _build_readiness_state(
    lifecycle: str,
    criterion_id: str,
    rubric_version: str,
    reason: str,
    **details: Any,
) -> Dict[str, Any]:
    return {
        "lifecycle": lifecycle,
        "criterion_id": str(criterion_id),
        "rubric_version": rubric_version,
        "reason": reason,
        **details,
        "min_teacher_samples": core.AUTO_GRADE_MIN_TEACHER_SAMPLES,
        "readiness_policy_version": core.READINESS_POLICY_VERSION,
        "updated_at": core._now(),
    }


def _load_candidate_snapshot(case_id: str, criterion_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    candidate_hash = str((state.get("candidate") or {}).get("candidate_hash") or "")
    if not candidate_hash:
        raise ValueError("active criterion has no promoted candidate")
    path = core._alignment_dir(case_id) / "candidates" / str(criterion_id) / f"{candidate_hash}.json"
    data = core._read_json(path, None)
    if not isinstance(data, dict) or core._sha256_data({
        "parent_rubric_version": state.get("rubric_version"),
        "criterion": data,
        "construction_event_ids": (state.get("candidate") or {}).get("construction_event_ids") or [],
        "validation_event_ids": (state.get("candidate") or {}).get("validation_event_ids") or [],
    }) != candidate_hash:
        raise core.CorruptAlignmentStore("candidate snapshot is missing or does not match its lineage hash")
    return data


def evaluate_readiness_in_store(
    store: Dict[str, Any],
    criterion_id: str,
    rubric_version: str,
    case_id: Optional[str] = None,
    model: Optional[core.GradingModel] = None,
    evaluator: Optional[core.AlignmentEvaluator] = None,
    criterion_snapshot: Optional[Dict[str, Any]] = None,
    rubric_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    criteria = store.setdefault("readiness", {}).setdefault("criteria", {})
    if case_id and core._has_pending_update_for_criterion(case_id, str(criterion_id)):
        state = _build_readiness_state(
            "evaluating",
            criterion_id,
            rubric_version,
            "pending_rubric_update",
        )
        state["readiness_basis_hash"] = core._readiness_basis_hash(store, rubric_version)
        criteria[str(criterion_id)] = state
        return state
    trusted_teacher = core._learning_teacher_events(
        store, criterion_id, rubric_version
    )
    confirmed_positive = core._confirmed_model_learning_events(
        store, criterion_id, rubric_version
    )
    has_teacher_history = _has_teacher_history(store, criterion_id)
    if (
        not trusted_teacher
        and not confirmed_positive
        and not has_teacher_history
        and core.AUTO_GRADE_MIN_TEACHER_SAMPLES == 0
        and case_id
        and criterion_snapshot
    ):
        candidate = _persist_rubric_only_candidate(case_id, criterion_snapshot, rubric_version)
        state = _build_readiness_state(
            "active_selective",
            criterion_id,
            rubric_version,
            "rubric_only_bootstrap_enabled",
            boundary=_rubric_only_boundary(
                criterion_snapshot, rubric_version, rubric_metadata
            ),
            candidate=candidate,
            validation={
                "strategy": "rubric_only_bootstrap",
                "checks": [],
                "trusted_event_ids": [],
            },
        )
    elif len(trusted_teacher) < core.AUTO_GRADE_MIN_TEACHER_SAMPLES:
        state = _build_readiness_state(
            "collecting",
            criterion_id,
            rubric_version,
            "configured_teacher_sample_minimum_not_met",
            teacher_sample_count=len(trusted_teacher),
        )
    elif not trusted_teacher:
        reason = (
            "zero_clean_teacher_grounded_evidence"
            if core._has_open_conflict(store, criterion_id)
            else "zero_teacher_grounded_evidence"
        )
        state = _build_readiness_state(
            "collecting", criterion_id, rubric_version, reason
        )
    elif not case_id:
        state = _build_readiness_state(
            "evaluating",
            criterion_id,
            rubric_version,
            "missing_case_context_for_validation",
        )
    else:
        criterion = trusted_teacher[0].get("criterion_snapshot") or {}
        rubric_grounding = _rubric_has_independent_grounding(criterion)
        model = model or core.LiveGradingModel()
        evaluator = evaluator or core.LLMAlignmentEvaluator()
        boundary = _boundary_for(
            criterion_id, rubric_version, trusted_teacher
        )
        checks: List[Dict[str, Any]] = []
        validation_error: Optional[str] = None
        for held_out in trusted_teacher:
            construction = [
                event
                for event in trusted_teacher
                if event["event_id"] != held_out["event_id"]
            ] + confirmed_positive
            if not construction and not rubric_grounding:
                continue
            target_text = core._report_text(case_id, str(held_out.get("report_name")))
            if not target_text:
                validation_error = "held_out_report_text_missing"
                break
            try:
                candidate = model.grade(
                    target_text,
                    criterion,
                    boundary,
                    _examples_with_text(
                        case_id, construction, store.get("decisions", [])
                    ),
                )
                expected = held_out.get("authored_grade") or {}
                score_agrees = float(candidate.get("criteria_score")) == float(
                    expected.get("criteria_score")
                )
                semantic = evaluator.reason_and_evidence_agree(criterion, expected, candidate)
                semantic_agrees = semantic.get("agree") is True
                checks.append({
                    "held_out_event_id": held_out["event_id"],
                    "construction_event_ids": [event["event_id"] for event in construction],
                    "score_agrees": score_agrees,
                    "reason_evidence_agrees": semantic_agrees,
                    "passed": score_agrees and semantic_agrees,
                    "evaluator_reason": semantic.get("reason"),
                })
            except Exception as exc:
                validation_error = f"validation_unavailable:{type(exc).__name__}"
                break
        if validation_error:
            state = _build_readiness_state(
                "evaluating",
                criterion_id,
                rubric_version,
                validation_error,
                boundary=boundary,
                validation={"checks": checks},
            )
        elif not checks:
            state = _build_readiness_state(
                "evaluating",
                criterion_id,
                rubric_version,
                "awaiting_out_of_construction_evidence",
                boundary=boundary,
                validation={"checks": []},
            )
        elif all(check["passed"] for check in checks):
            candidate = _persist_candidate(
                case_id,
                criterion,
                rubric_version,
                trusted_teacher + confirmed_positive,
                checks[-1],
                store.get("decisions", []),
            )
            validation_strategy = (
                "independent_rubric_grounding"
                if rubric_grounding and len(trusted_teacher) == 1
                else "leave_one_report_out"
            )
            state = _build_readiness_state(
                "active_selective",
                criterion_id,
                rubric_version,
                "out_of_construction_score_and_reason_evidence_agreement_passed",
                boundary=boundary,
                candidate=candidate,
                validation={
                    "strategy": validation_strategy,
                    "checks": checks,
                    "trusted_event_ids": [
                        event["event_id"] for event in trusted_teacher
                    ],
                },
            )
        else:
            state = _build_readiness_state(
                "evaluating",
                criterion_id,
                rubric_version,
                "out_of_construction_agreement_failed",
                boundary=boundary,
                validation={"checks": checks},
            )
    state["readiness_basis_hash"] = core._readiness_basis_hash(store, rubric_version)
    criteria[str(criterion_id)] = state
    return state


def evaluate_alignment(
    case_id: str,
    criterion_id: Optional[str] = None,
    criteria_file_name: Optional[str] = None,
    model: Optional[core.GradingModel] = None,
    evaluator: Optional[core.AlignmentEvaluator] = None,
) -> Dict[str, Any]:
    with core._lock_for(case_id):
        store = core._store(case_id)
        criteria_name, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
        if rubric_version == "missing":
            raise ValueError(f"criteria file not found: {criteria_file_name or 'criteria.json'}")
        ids = {str(e.get("criterion_id")) for e in store["events"] if e.get("grader_type") == "teacher"}
        if criterion_id:
            ids = {str(criterion_id)}
        elif core.AUTO_GRADE_MIN_TEACHER_SAMPLES == 0:
            ids = {str(item.get("criteria_id")) for item in criteria_data.get("criteria", [])}
        basis_hash = core._readiness_basis_hash(store, rubric_version)
        working_store = deepcopy(store)
        criteria_by_id = {
            str(item.get("criteria_id")): item
            for item in criteria_data.get("criteria", [])
        }
        rubric_metadata = {
            "title": criteria_data.get("title"),
            "background": criteria_data.get("background"),
            "language": criteria_data.get("language"),
        }
    for cid in ids:
        evaluate_readiness_in_store(
            working_store,
            cid,
            rubric_version,
            case_id,
            model,
            evaluator,
            criterion_snapshot=criteria_by_id.get(cid),
            rubric_metadata=rubric_metadata,
        )
    with core._lock_for(case_id):
        current = core._store(case_id)
        if core._readiness_basis_hash(current, rubric_version) == basis_hash:
            readiness = current.setdefault("readiness", {}).setdefault("criteria", {})
            evaluated = working_store.get("readiness", {}).get("criteria", {})
            for cid in ids:
                if cid in evaluated:
                    readiness[cid] = evaluated[cid]
            core._write_store(case_id, current)
            return core.alignment_summary(case_id, current, criteria_name)
        return core.alignment_summary(case_id, current, criteria_name)
