from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

from ..grade_validation import (
    validate_evidence_anchors as _validated_evidence_anchors,
    validate_model_scoring_point_scores as _validated_model_scoring_point_scores,
)
from ..schemas import CaseReportModelGradeAcceptRequest
from .adapters import AlignmentEvaluator, GradingModel
from .evidence_catalog import EvidenceSelectionError, resolve_evidence_selections


class _CoreProxy:
    def __getattr__(self, name: str):
        from .. import alignment

        return getattr(alignment, name)


core = _CoreProxy()


def _canonical_model_rule_ids(
    criterion: Dict[str, Any], rule_ids: List[Any]
) -> List[str]:
    """Normalize cosmetic model labels only when they map to rubric rule IDs."""
    valid_rule_ids = {
        str(rule.get("rule_id") or "")
        for point in criterion.get("scoring_points") or []
        for rule in point.get("deduction_rules") or []
    }
    canonical: List[str] = []
    for value in rule_ids:
        raw = str(value).strip()
        if raw in valid_rule_ids:
            canonical.append(raw)
            continue

        lowered = raw.casefold()
        candidate = None
        for prefix in ("rule ", "rule:"):
            if lowered.startswith(prefix):
                candidate = raw[len(prefix) :].strip()
                break
        canonical.append(candidate if candidate in valid_rule_ids else raw)
    return canonical


def _validated_model_grade_payload(
    criterion: Dict[str, Any], grade: Dict[str, Any]
) -> Dict[str, Any]:
    if not isinstance(grade, dict):
        raise ValueError("model grade must be a JSON object")
    try:
        score = float(grade.get("criteria_score"))
    except (TypeError, ValueError) as exc:
        raise ValueError("model grade has no numeric criteria_score") from exc
    max_score = float(criterion.get("max_score", 0) or 0)
    if score < 0 or score > max_score:
        raise ValueError("model grade is outside criterion score range")
    if not core._normalized(grade.get("score_reason")):
        raise ValueError("model grade must include score_reason")
    for field in ("matched_scoring_points", "matched_deduction_rules"):
        if field in grade and not isinstance(grade.get(field), list):
            raise ValueError(f"model grade must include {field} as an array")
    if not isinstance(grade.get("scoring_point_scores"), list):
        raise ValueError("model grade must include scoring_point_scores as an array")

    matched_points = [str(value) for value in grade.get("matched_scoring_points") or []]
    matched_rules = _canonical_model_rule_ids(
        criterion, grade.get("matched_deduction_rules") or []
    )
    point_scores = _validated_model_scoring_point_scores(
        criterion,
        grade["scoring_point_scores"],
        score,
        matched_points,
        matched_rules,
    )
    return {
        **grade,
        "criteria_score": score,
        "matched_scoring_points": matched_points,
        "matched_deduction_rules": matched_rules,
        "scoring_point_scores": point_scores,
    }


def _repair_model_grade_once(
    model: GradingModel,
    *,
    report_text: str,
    criterion: Dict[str, Any],
    boundary: Dict[str, Any],
    invalid_grade: Dict[str, Any],
    validation_error: Exception,
) -> tuple[Dict[str, Any] | None, Dict[str, Any]]:
    diagnostic = {
        "diagnostic_id": f"diag_{uuid.uuid4().hex}",
        "initial_response": deepcopy(invalid_grade),
        "initial_validation_error": str(validation_error),
        "repair_attempted": False,
        "outcome": "repair_unavailable",
    }
    repair_grade = getattr(model, "repair_grade", None)
    if not callable(repair_grade):
        return None, diagnostic

    diagnostic["repair_attempted"] = True
    try:
        repaired_grade = repair_grade(
            report_text,
            criterion,
            boundary,
            deepcopy(invalid_grade),
            str(validation_error),
        )
    except Exception as exc:
        diagnostic["repair_error"] = str(exc)
        diagnostic["outcome"] = "repair_call_failed"
        return None, diagnostic

    diagnostic["repair_response"] = deepcopy(repaired_grade)
    try:
        validated_grade = _validated_model_grade_payload(criterion, repaired_grade)
    except Exception as exc:
        diagnostic["repair_validation_error"] = str(exc)
        diagnostic["outcome"] = "repair_validation_failed"
        return None, diagnostic
    diagnostic["outcome"] = "repaired"
    return validated_grade, diagnostic


def _compute_grade_item(
    item: Dict[str, Any],
    *,
    case_id: str,
    report_text: str,
    rubric_version: str,
    model: GradingModel,
    evaluator: AlignmentEvaluator,
) -> Dict[str, Any]:
    """Run the side-effect-free inference phase for one complete Card."""
    criterion = item["criterion"]
    criterion_id = str(criterion.get("criteria_id"))
    blocked_reason = (
        "criterion_repair_requires_teacher_confirmation"
        if item.get("repair_blocked")
        else "consistency_full_batch_not_authorized"
        if item.get("full_batch_blocked")
        else None
    )
    if blocked_reason:
        return {
            "status": "abstained",
            "criterion_id": criterion_id,
            "admission": {
                "decision": "uncertain",
                "matched_rule_ids": [],
                "reason": blocked_reason,
            },
        }

    state = item["state"]
    admission = _boundary_admission(
        report_text,
        criterion,
        state,
        rubric_version,
        evaluator,
        assignment_wide=bool(item.get("revision_regrade")),
    )
    if admission["decision"] != "inside":
        return {
            "status": "abstained",
            "criterion_id": criterion_id,
            "admission": admission,
        }
    try:
        candidate = core._load_candidate_snapshot(case_id, criterion_id, state)
        boundary = state.get("boundary") or {}
        grade = model.grade(report_text, candidate, boundary, [])
        diagnostic = None
        try:
            grade = _validated_model_grade_payload(candidate, grade)
        except Exception as initial_error:
            grade, diagnostic = _repair_model_grade_once(
                model,
                report_text=report_text,
                criterion=candidate,
                boundary=boundary,
                invalid_grade=grade,
                validation_error=initial_error,
            )
            if grade is None:
                repair_error = diagnostic.get("repair_validation_error") or diagnostic.get(
                    "repair_error"
                )
                error = str(initial_error)
                if repair_error:
                    error = f"{error}; model grade repair failed: {repair_error}"
                return {
                    "status": "failed",
                    "criterion_id": criterion_id,
                    "error": error,
                    "candidate_hash": str(state["candidate"]["candidate_hash"]),
                    "model_grade_diagnostic": diagnostic,
                }
        return {
            "status": "done",
            "criterion_id": criterion_id,
            "criterion": candidate,
            "rubric_criterion_hash": core._sha256_data(criterion),
            "candidate_hash": str(state["candidate"]["candidate_hash"]),
            "grade": grade,
            **(
                {"model_grade_diagnostic": diagnostic}
                if diagnostic is not None
                else {}
            ),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "criterion_id": criterion_id,
            "error": str(exc),
        }


def _boundary_admission(
    report_text: str,
    criterion: Dict[str, Any],
    state: Dict[str, Any],
    rubric_version: str,
    evaluator: AlignmentEvaluator,
    *,
    assignment_wide: bool = False,
) -> Dict[str, Any]:
    criterion_id = str(criterion.get("criteria_id"))
    boundary = state.get("boundary") or {}
    if not report_text:
        return {"decision": "uncertain", "matched_rule_ids": [], "reason": "missing_text_only_markdown"}
    if ".partial" in report_text[:500].lower():
        return {"decision": "uncertain", "matched_rule_ids": [], "reason": "partial_output"}
    if assignment_wide:
        # A confirmed Card revision invalidates that Card on every report in
        # the assignment. Its replacement batch must therefore grade every
        # target against the new Card, even while selective-readiness state is
        # being recalibrated for the new rubric version.
        return {
            "decision": "inside",
            "matched_rule_ids": [],
            "reason": "criterion_revision_assignment_scope",
        }
    if state.get("lifecycle") != "active_selective":
        return {"decision": "outside", "matched_rule_ids": [], "reason": "criterion_not_active"}
    if boundary.get("rubric_version") != rubric_version or str(boundary.get("criterion_id")) != str(criterion_id):
        return {"decision": "outside", "matched_rule_ids": [], "reason": "schema_or_rubric_mismatch"}
    if boundary.get("source") == "rubric_only_bootstrap":
        # The selected rubric applies to every report in this case. Evidence
        # absence is a possible grading outcome, not proof that the criterion is
        # out of scope; the grading model must be allowed to apply its deduction.
        return {
            "decision": "inside",
            "matched_rule_ids": boundary.get("covered_rule_ids") or [],
            "reason": "rubric_only_assignment_scope",
        }
    try:
        result = evaluator.admit(report_text, criterion, boundary)
    except Exception as exc:
        return {"decision": "uncertain", "matched_rule_ids": [], "reason": f"boundary_evaluator_unavailable:{type(exc).__name__}"}
    decision = result.get("decision")
    if decision not in {"inside", "outside", "uncertain"}:
        return {"decision": "uncertain", "matched_rule_ids": [], "reason": "invalid_boundary_evaluator_response"}
    return {
        "decision": decision,
        "matched_rule_ids": result.get("matched_rule_ids") or [],
        "reason": result.get("reason") or "independent_boundary_evaluation",
    }


def _attach_confirmed_revision_candidate(
    case_id: str,
    item: Dict[str, Any],
    rubric_version: str,
    working_store: Dict[str, Any],
) -> None:
    """Use the confirmed revised Card when readiness has no promoted candidate."""
    state = item.get("state") or {}
    if not item.get("revision_regrade") or (state.get("candidate") or {}).get(
        "candidate_hash"
    ):
        return
    state["candidate"] = core._persist_rubric_only_candidate(
        case_id,
        item["criterion"],
        rubric_version,
    )
    item["state"] = state
    criterion_id = str(item["criterion"].get("criteria_id"))
    working_store.setdefault("readiness", {}).setdefault("criteria", {})[
        criterion_id
    ] = deepcopy(state)


def _ground_model_evidence_anchors(
    report_text: str,
    grade: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Normalize recoverable model anchor mistakes, then validate against source."""
    selections = grade.get("evidence_selections")
    if "evidence_selections" in grade:
        try:
            selected_anchors = resolve_evidence_selections(report_text, selections)
        except EvidenceSelectionError as exc:
            raise core.ModelEvidenceUnavailable(
                f"model evidence selection could not be grounded: {exc}"
            ) from exc
        return _validated_evidence_anchors(report_text, selected_anchors)

    normalized_report = core._normalized(report_text)
    repaired: List[Dict[str, Any]] = []
    for raw in list(grade.get("evidence_anchors") or []):
        anchor = (
            raw.model_dump(mode="json")
            if hasattr(raw, "model_dump")
            else deepcopy(raw)
        )
        if not isinstance(anchor, dict):
            continue
        anchor_type = str(anchor.get("anchor_type") or "text")
        if anchor_type == "text":
            normalized_text = core._normalized(anchor.get("text"))
            occurrences = (
                normalized_report.count(normalized_text) if normalized_text else 0
            )
            if occurrences:
                try:
                    occurrence = int(anchor.get("occurrence") or 0)
                except (TypeError, ValueError):
                    occurrence = 0
                anchor["occurrence"] = (
                    occurrence if 0 <= occurrence < occurrences else 0
                )
        elif anchor_type == "absence":
            expected_location = str(anchor.get("expected_location") or "").strip()
            normalized_location = core._normalized(expected_location)
            at_document_boundary = expected_location.casefold() in {
                "document:start",
                "document:end",
            }
            if (
                anchor.get("missing_item")
                and not at_document_boundary
                and (
                    not normalized_location
                    or normalized_location not in normalized_report
                )
            ):
                # An absence claim applies to the whole immutable report. A model
                # heading guess is not a stable identity, so ground it at EOF.
                anchor["expected_location"] = "document:end"
        repaired.append(anchor)

    legacy_anchor = str(grade.get("anchor_text") or "")
    try:
        return _validated_evidence_anchors(report_text, repaired, legacy_anchor)
    except (TypeError, ValueError) as primary_error:
        # Legacy model responses may contain only anchor_text. Once the model
        # supplies structured anchors, never replace an invalid image/absence
        # claim with nearby prose: that would turn an evidence failure into a
        # misleading positive text highlight.
        if not repaired and core._normalized(legacy_anchor):
            try:
                return _validated_evidence_anchors(report_text, [], legacy_anchor)
            except (TypeError, ValueError):
                pass
        raise core.ModelEvidenceUnavailable(
            f"model evidence could not be grounded: {primary_error}"
        ) from primary_error


def _append_model_event(
    store: Dict[str, Any], case_id: str, report_name: str, criterion_id: str,
    rubric_file: str, rubric_version: str, criterion: Dict[str, Any],
    grade: Dict[str, Any], candidate_hash: str, report_text: Optional[str] = None,
    rubric_criterion_hash: Optional[str] = None,
) -> Dict[str, Any]:
    grade = _validated_model_grade_payload(criterion, grade)
    evidence_anchors = _ground_model_evidence_anchors(
        report_text if report_text is not None else core._report_text(case_id, report_name),
        grade,
    )
    grade = {
        **grade,
        "evidence_anchors": evidence_anchors,
    }
    key = core._sha256_data({
        "case_id": case_id,
        "report_name": report_name,
        "criterion_id": str(criterion_id),
        "rubric_version": rubric_version,
        "grader_type": "model",
        "grader_id": core.GRADING_MODEL_ID,
        "model_version": core.GRADING_MODEL_ID,
        "candidate_hash": candidate_hash,
        "operation": "auto_grade",
    })
    existing = next((e for e in store["events"] if e.get("idempotency_key") == key), None)
    if existing:
        core._record_model_review_assignment(store, existing)
        return existing
    scorer_component_hash = core._sha256_data(criterion)
    scorer_revision = core.consistency.build_scorer_revision(
        core.GRADING_MODEL_ID,
        scorer_component_hash,
        core.CONSISTENCY_SCORER_REVISION,
    )
    ev = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "event_type": "grade_authored",
        "idempotency_key": key,
        "case_id": case_id,
        "report_name": report_name,
        "criterion_id": str(criterion_id),
        "grader_type": "model",
        "grader_id": core.GRADING_MODEL_ID,
        "model_version": core.GRADING_MODEL_ID,
        "rubric_file_name": rubric_file,
        "rubric_version": rubric_version,
        "candidate_hash": candidate_hash,
        "scorer_component_hash": scorer_component_hash,
        "prompt_revision": core.CONSISTENCY_SCORER_REVISION,
        "criterion_hash": rubric_criterion_hash or core._sha256_data(criterion),
        "scorer_revision": scorer_revision,
        "criterion_snapshot": criterion,
        "authored_grade": grade,
        "created_at": core._now(),
    }
    store["events"].append(ev)
    core._append_decision(store, "model_only", [ev["event_id"]], "model pre-grade excluded from learning", model_id=core.GRADING_MODEL_ID)
    core._record_model_review_assignment(store, ev)
    return ev


def accept_model_grade_event(
    case_id: str,
    report_name: str,
    payload: CaseReportModelGradeAcceptRequest,
) -> Dict[str, Any]:
    """Accept a model pre-grade as construction-only evidence without relabelling it."""
    if not core._report_dir(case_id, report_name).exists():
        raise FileNotFoundError(str(core._report_dir(case_id, report_name)))
    with core._lock_for(case_id):
        store = core._store(case_id)
        criteria_name, _, _ = core._load_criteria(payload.criteria_file_name)
        criterion_id = str(payload.criteria_id)
        active_teacher = next(
            (
                event for event in store["events"]
                if event.get("report_name") == report_name
                and str(event.get("criterion_id")) == criterion_id
                and event.get("rubric_file_name") == criteria_name
                and event.get("grader_type") == "teacher"
                and event.get("event_type", "grade_authored") == "grade_authored"
                and core._event_status(event["event_id"], store["decisions"]) != "superseded"
            ),
            None,
        )
        if active_teacher:
            raise ValueError("teacher grade already exists for this report and criterion")
        candidates = [
            event for event in store["events"]
            if event.get("report_name") == report_name
            and str(event.get("criterion_id")) == criterion_id
            and event.get("rubric_file_name") == criteria_name
            and event.get("grader_type") == "model"
            and event.get("event_type", "grade_authored") == "grade_authored"
            and core._event_status(event["event_id"], store["decisions"]) in {"model_only", "accepted_model"}
        ]
        if not candidates:
            raise FileNotFoundError("model grade not found")
        event = candidates[-1]
        status = core._event_status(event["event_id"], store["decisions"])
        if status != "accepted_model":
            core._append_decision(
                store,
                "accepted_model",
                [event["event_id"]],
                "teacher accepted model pre-grade; construction-only positive excluded from validation, boundary, rubric induction, and conflict audit",
                accepted_by=payload.accepted_by or core.GRADING_TEACHER_ID,
                model_id=event.get("model_version") or event.get("grader_id") or core.GRADING_MODEL_ID,
            )
            core._update_auto_grade_job(
                store,
                case_id=case_id,
                report_name=report_name,
                criterion_id=criterion_id,
                rubric_file=criteria_name,
                rubric_version=event.get("rubric_version"),
                status="done",
                reason="model_grade_accepted_by_teacher",
                decision="accepted_model",
            )
        consistency_record = core._record_consistency_feedback(
            store,
            model_event=event,
            teacher_score=float((event.get("authored_grade") or {}).get("criteria_score", 0) or 0),
            trigger="model_grade_accepted",
        )
        if status != "accepted_model" or consistency_record:
            core._write_store(case_id, store)
        result = core.materialize_projection(case_id, report_name, store, criteria_name, persist=True)
        return {
            "event": event,
            "result": result,
            "accepted": True,
            "duplicate": status == "accepted_model",
            "consistency": (
                core.consistency.summary(
                    consistency_record,
                    has_open_conflict=core._has_open_conflict(store, criterion_id),
                )
                if consistency_record
                else None
            ),
        }


def auto_grade_report(
    case_id: str,
    report_name: str,
    criteria_file_name: Optional[str] = None,
    model: Optional[GradingModel] = None,
    evaluator: Optional[AlignmentEvaluator] = None,
    criteria_ids: Optional[List[str]] = None,
    scheduling_reason: Optional[str] = None,
    on_item_persisted: Optional[
        Callable[[Dict[str, Any], Dict[str, Any]], None]
    ] = None,
) -> Dict[str, Any]:
    report_dir = core._report_dir(case_id, report_name)
    if not report_dir.exists():
        raise FileNotFoundError(str(report_dir))
    text_path = report_dir / f"{report_name}_text_only.md"
    if not text_path.exists():
        md = next(iter(report_dir.glob("*_text_only.md")), None)
        text_path = md if md else text_path
    report_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
    model = model or core.LiveGradingModel()
    evaluator = evaluator or core.LLMAlignmentEvaluator()
    with core._lock_for(case_id):
        store = core._store(case_id)
        rubric_file, rubric_version, criteria_data = core._load_criteria(criteria_file_name)
        if rubric_version == "missing":
            raise ValueError(f"criteria file not found: {rubric_file}")
        states = store.get("readiness", {}).get("criteria", {})
        rubric_metadata = {
            "title": criteria_data.get("title"),
            "background": criteria_data.get("background"),
            "language": criteria_data.get("language"),
        }
        selected_ids = {str(value) for value in criteria_ids} if criteria_ids is not None else None
        work_items = []
        for criterion in criteria_data.get("criteria", []):
            cid = str(criterion.get("criteria_id"))
            if selected_ids is not None and cid not in selected_ids:
                continue
            authorization = core._criterion_authorization(store, rubric_file or "criteria.json", cid)
            repair_blocked = (
                authorization.get("state") == core.AUTH_REPAIR_REQUIRED
                and not authorization.get("repair_confirmed_at")
            )
            revision_regrade = (
                scheduling_reason == "criterion_revision"
                and core._revision_card_is_current(
                    store,
                    rubric_file or "criteria.json",
                    cid,
                    core._sha256_data(criterion),
                )
            )
            if revision_regrade:
                repair_blocked = False
            full_batch_requested = scheduling_reason in {"backfill", "benchmark"}
            full_batch_blocked = False
            if full_batch_requested and core._consistency_adaptive():
                full_batch_blocked = not core._criterion_full_batch_allowed(
                    store,
                    case_id=case_id,
                    criteria_file_name=rubric_file or "criteria.json",
                    rubric_version=rubric_version,
                    criterion=criterion,
                )
            core._update_auto_grade_job(
                store,
                case_id=case_id,
                report_name=report_name,
                criterion_id=cid,
                rubric_file=rubric_file,
                rubric_version=rubric_version,
                status="running",
            )
            work_items.append({
                "criterion": deepcopy(criterion),
                "state": (
                    deepcopy(states.get(cid))
                    if core._readiness_state_reusable(store, cid, rubric_version, states.get(cid))
                    else None
                ),
                "repair_blocked": repair_blocked,
                "full_batch_blocked": full_batch_blocked,
                "full_batch_requested": full_batch_requested,
                "revision_regrade": revision_regrade,
            })
        basis_hash = core._grading_evidence_hash(store)
        working_store = deepcopy(store)
        core._write_store(case_id, store)

    original_criterion_hashes = {
        str(item["criterion"].get("criteria_id")): core._sha256_data(item["criterion"])
        for item in work_items
    }

    # Readiness evaluation may update the candidate store, so keep that phase
    # deterministic. Boundary judging and grading are independent per criterion
    # and already outside the case lock; run only that remote-inference phase
    # with bounded concurrency.
    for item in work_items:
        if item.get("repair_blocked") or item.get("full_batch_blocked"):
            continue
        criterion = item["criterion"]
        cid = str(criterion.get("criteria_id"))
        item["state"] = item["state"] or core.evaluate_readiness_in_store(
            working_store,
            cid,
            rubric_version,
            case_id,
            model,
            evaluator,
            criterion_snapshot=criterion,
            rubric_metadata=rubric_metadata,
        )
        _attach_confirmed_revision_candidate(
            case_id,
            item,
            rubric_version,
            working_store,
        )

    def compute_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return _compute_grade_item(
            item,
            case_id=case_id,
            report_text=report_text,
            rubric_version=rubric_version,
            model=model,
            evaluator=evaluator,
        )

    ordered_criterion_ids = [
        str(item["criterion"].get("criteria_id")) for item in work_items
    ]
    outcomes: Dict[str, tuple[str, Dict[str, Any]]] = {}
    latest_projection: Optional[Dict[str, Any]] = None
    expected_evidence_hash = basis_hash
    evidence_invalidated = False
    readiness_persisted = False

    def persist_item(item: Dict[str, Any]) -> None:
        """Atomically commit one completed Card before exposing its target state."""
        nonlocal evidence_invalidated, expected_evidence_hash
        nonlocal latest_projection, readiness_persisted

        with core._lock_for(case_id):
            store = core._store(case_id)
            diagnostic = item.get("model_grade_diagnostic")
            if diagnostic:
                store.setdefault("model_grade_diagnostics", []).append(
                    {
                        **deepcopy(diagnostic),
                        "case_id": case_id,
                        "report_name": report_name,
                        "criterion_id": str(item["criterion_id"]),
                        "criteria_file_name": rubric_file or "criteria.json",
                        "rubric_version": rubric_version,
                        "candidate_hash": item.get("candidate_hash"),
                        "created_at": core._now(),
                    }
                )
            _, current_rubric_version, current_criteria_data = core._load_criteria(
                rubric_file
            )
            if core._grading_evidence_hash(store) != expected_evidence_hash:
                evidence_invalidated = True
            if not readiness_persisted and not evidence_invalidated:
                readiness = store.setdefault("readiness", {}).setdefault("criteria", {})
                evaluated = working_store.get("readiness", {}).get("criteria", {})
                for work_item in work_items:
                    work_item_id = str(work_item["criterion"].get("criteria_id"))
                    if work_item_id in evaluated:
                        readiness[work_item_id] = evaluated[work_item_id]
                readiness_persisted = True

            cid = str(item["criterion_id"])
            completion: Dict[str, Any]
            try:
                current_criterion = core._require_criterion(current_criteria_data, cid)
            except ValueError:
                current_criterion = None
            criterion_stale = (
                current_criterion is None
                or core._sha256_data(current_criterion)
                != original_criterion_hashes.get(cid)
            )
            if evidence_invalidated or criterion_stale:
                reason = (
                    "criterion_changed_during_auto_grade"
                    if criterion_stale
                    else "teacher_events_changed_during_auto_grade"
                )
                core._update_auto_grade_job(
                    store,
                    case_id=case_id,
                    report_name=report_name,
                    criterion_id=cid,
                    rubric_file=rubric_file,
                    rubric_version=rubric_version,
                    status="stale",
                    reason=reason,
                )
                payload = {"criterion_id": cid, "error": reason}
                outcomes[cid] = ("failure", payload)
                completion = {"criterion_id": cid, "status": "stale", "reason": reason}
            elif (
                item["status"] == "done"
                and scheduling_reason in {"backfill", "benchmark"}
                and core._consistency_adaptive()
                and not core._criterion_full_batch_allowed(
                    store,
                    case_id=case_id,
                    criteria_file_name=rubric_file or "criteria.json",
                    rubric_version=current_rubric_version,
                    criterion=core._require_criterion(current_criteria_data, cid),
                )
            ):
                reason = "consistency_full_batch_not_authorized"
                core._update_auto_grade_job(
                    store,
                    case_id=case_id,
                    report_name=report_name,
                    criterion_id=cid,
                    rubric_file=rubric_file,
                    rubric_version=rubric_version,
                    status="abstained",
                    reason=reason,
                    decision="uncertain",
                )
                payload = {
                    "criterion_id": cid,
                    "decision": "uncertain",
                    "matched_rule_ids": [],
                    "reason": reason,
                }
                outcomes[cid] = ("abstention", payload)
                completion = {"criterion_id": cid, "status": "abstained", "reason": reason}
            elif item["status"] == "abstained":
                admission = item["admission"]
                core._update_auto_grade_job(
                    store,
                    case_id=case_id,
                    report_name=report_name,
                    criterion_id=cid,
                    rubric_file=rubric_file,
                    rubric_version=rubric_version,
                    status="abstained",
                    reason=admission.get("reason"),
                    decision=admission.get("decision"),
                )
                payload = {"criterion_id": cid, **admission}
                outcomes[cid] = ("abstention", payload)
                completion = {
                    "criterion_id": cid,
                    "status": "abstained",
                    "reason": admission.get("reason"),
                }
            elif item["status"] == "failed":
                core._update_auto_grade_job(
                    store,
                    case_id=case_id,
                    report_name=report_name,
                    criterion_id=cid,
                    rubric_file=rubric_file,
                    rubric_version=rubric_version,
                    status="failed",
                    error=item.get("error"),
                )
                payload = {"criterion_id": cid, "error": item.get("error")}
                outcomes[cid] = ("failure", payload)
                completion = {
                    "criterion_id": cid,
                    "status": "failed",
                    "error": item.get("error"),
                }
            else:
                try:
                    ev = _append_model_event(
                        store,
                        case_id,
                        report_name,
                        cid,
                        rubric_file or "criteria.json",
                        rubric_version,
                        item["criterion"],
                        item["grade"],
                        item["candidate_hash"],
                        report_text,
                        item.get("rubric_criterion_hash"),
                    )
                    core._update_auto_grade_job(
                        store,
                        case_id=case_id,
                        report_name=report_name,
                        criterion_id=cid,
                        rubric_file=rubric_file,
                        rubric_version=rubric_version,
                        status="done",
                        reason="model_grade_recorded",
                        decision="inside",
                    )
                    payload = core._item_from_event(ev)
                    outcomes[cid] = ("grade", payload)
                    completion = {
                        "criterion_id": cid,
                        "status": "done",
                        "reason": "model_grade_recorded",
                    }
                except core.ModelEvidenceUnavailable as exc:
                    reason = "model_evidence_unavailable"
                    core._update_auto_grade_job(
                        store,
                        case_id=case_id,
                        report_name=report_name,
                        criterion_id=cid,
                        rubric_file=rubric_file,
                        rubric_version=rubric_version,
                        status="abstained",
                        reason=reason,
                        decision="uncertain",
                        error=str(exc),
                    )
                    payload = {
                        "criterion_id": cid,
                        "decision": "uncertain",
                        "matched_rule_ids": [],
                        "reason": reason,
                    }
                    outcomes[cid] = ("abstention", payload)
                    completion = {
                        "criterion_id": cid,
                        "status": "abstained",
                        "reason": reason,
                    }
                except Exception as exc:
                    core._update_auto_grade_job(
                        store,
                        case_id=case_id,
                        report_name=report_name,
                        criterion_id=cid,
                        rubric_file=rubric_file,
                        rubric_version=rubric_version,
                        status="failed",
                        error=str(exc),
                    )
                    payload = {"criterion_id": cid, "error": str(exc)}
                    outcomes[cid] = ("failure", payload)
                    completion = {
                        "criterion_id": cid,
                        "status": "failed",
                        "error": str(exc),
                    }

            if completion["status"] in {"done", "abstained"} and completion.get(
                "reason"
            ) != "criterion_repair_requires_teacher_confirmation":
                authorization = core._criterion_authorization(
                    store, rubric_file or "criteria.json", cid
                )
                repair_is_confirmed = (
                    authorization.get("state") == core.AUTH_REPAIR_REQUIRED
                    and authorization.get("repair_confirmed_at")
                )
                if repair_is_confirmed:
                    pending = [
                        name
                        for name in (
                            authorization.get("repair_pending_report_names") or []
                        )
                        if str(name) != report_name
                    ]
                    if pending:
                        core._set_criterion_authorization(
                            store,
                            rubric_file or "criteria.json",
                            cid,
                            core.AUTH_REPAIR_REQUIRED,
                            repair_pending_report_names=pending,
                        )
                    else:
                        core._set_criterion_authorization(
                            store,
                            rubric_file or "criteria.json",
                            cid,
                            core.AUTH_BATCH_AUTHORIZED,
                            authorized_rubric_version=rubric_version,
                            authorized_at=core._now(),
                            repair_completed_at=core._now(),
                            repair_pending_report_names=[],
                        )

            # Store the event/job first, then its report projection. The batch target
            # is exposed only after both artifacts are ready for the polling client.
            core._write_store(case_id, store)
            latest_projection = core.materialize_projection(
                case_id, report_name, store, rubric_file, persist=True
            )
            if on_item_persisted:
                on_item_persisted(store, completion)
                core._write_store(case_id, store)
            expected_evidence_hash = core._grading_evidence_hash(store)

    worker_count = min(core.AUTO_GRADE_MAX_WORKERS, max(1, len(work_items)))
    if worker_count == 1:
        for work_item in work_items:
            try:
                computed_item = compute_item(work_item)
            except Exception as exc:
                computed_item = {
                    "status": "failed",
                    "criterion_id": str(work_item["criterion"].get("criteria_id")),
                    "error": str(exc),
                }
            persist_item(computed_item)
    else:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="auto-grade",
        ) as executor:
            futures: Dict[Future[Dict[str, Any]], Dict[str, Any]] = {
                executor.submit(compute_item, work_item): work_item
                for work_item in work_items
            }
            for future in as_completed(futures):
                work_item = futures[future]
                try:
                    computed_item = future.result()
                except Exception as exc:
                    computed_item = {
                        "status": "failed",
                        "criterion_id": str(work_item["criterion"].get("criteria_id")),
                        "error": str(exc),
                    }
                persist_item(computed_item)

    grades = [
        outcomes[cid][1]
        for cid in ordered_criterion_ids
        if cid in outcomes and outcomes[cid][0] == "grade"
    ]
    abstentions = [
        outcomes[cid][1]
        for cid in ordered_criterion_ids
        if cid in outcomes and outcomes[cid][0] == "abstention"
    ]
    failures = [
        outcomes[cid][1]
        for cid in ordered_criterion_ids
        if cid in outcomes and outcomes[cid][0] == "failure"
    ]
    if latest_projection is None:
        with core._lock_for(case_id):
            latest_projection = core.materialize_projection(
                case_id, report_name, criteria_file_name=rubric_file, persist=True
            )
    return {
        "grades": grades,
        "abstentions": abstentions,
        "failures": failures,
        "result": latest_projection,
    }
