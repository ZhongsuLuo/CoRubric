from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .alignment import (
    AlignmentEvaluator,
    GradingModel,
    append_teacher_grade_event,
    case_alignment_lock,
    defer_conflicts_for_pending_update,
    enqueue_consistency_audit,
    enqueue_consistency_audits_for_criteria_file,
    ensure_criterion_revision_workers_for_jobs,
    record_refinement_rejection,
)
from .config import CASE_ROOT, CRITERIA_DIR, GRADING_MODEL_ID, GRADING_TEACHER_ID
from .criteria_lock import canonical_criteria_file_name, criteria_file_lock
from .criteria_versions import criterion_version as grading_criterion_version
from .criteria_versions import rubric_version as grading_rubric_version
from .induction_model import InductionModel, LiveInductionModel
from .grading.proposal_sources import proposal_source_event_digest
from .grading.rubric_refinement import _model_comparison_for_teacher_event
from .schemas import (
    CaseReportGradeRequest,
    CriterionProposalConfirmRequest,
    CriterionProposalItem,
    CriterionProposalRejectRequest,
    CriteriaItem,
    CriteriaSchema,
    EvidenceAnchor,
    ImplicitGradeCreateRequest,
    ImplicitGradeUpdateRequest,
    structured_point_deduction_capacity,
    validate_structured_score_budgets,
)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
MAX_INDUCTION_BATCH = 5
MAX_ANCHOR_CONTEXT_CHARS = 600
MAX_ANCHOR_TEXT_CHARS = 1200
MAX_NOTES_CHARS = 1200
MAX_LABEL_CHARS = 300
MAX_RUBRIC_CRITERIA = 30
MAX_RUBRIC_FIELD_CHARS = 1200
ACTIVE_IMPLICIT_STATUSES = {"queued", "inducing", "proposal_ready", "uninduced"}


class ImplicitStoreError(RuntimeError):
    pass


class StaleProposalError(ImplicitStoreError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_data(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _safe_name(name: str) -> str:
    if os.path.basename(name) != name or name in {"", ".", ".."}:
        raise ValueError("unsafe file name")
    return name


def _case_dir(case_id: str) -> Path:
    _safe_name(case_id)
    return Path(CASE_ROOT) / case_id


def _report_dir(case_id: str, report_name: str) -> Path:
    _safe_name(report_name)
    return _case_dir(case_id) / "markdown" / report_name


def _lock_for(case_id: str) -> threading.RLock:
    key = str(_case_dir(case_id) / "implicit_grades.json")
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _implicit_path(case_id: str) -> Path:
    return _case_dir(case_id) / "implicit_grades.json"


def _proposal_path(case_id: str) -> Path:
    return _case_dir(case_id) / "criterion_proposals.json"


def _alignment_store_paths(case_id: str) -> List[Path]:
    root = _case_dir(case_id) / "alignment"
    return [
        root / "events.json",
        root / "decisions.json",
        root / "conflicts.json",
        root / "readiness.json",
    ]


def _promotion_side_effect_paths(case_id: str, report_names: List[str]) -> List[Path]:
    paths = _alignment_store_paths(case_id)
    paths.append(_case_dir(case_id) / "alignment" / "candidates")
    for report_name in sorted(set(report_names)):
        _safe_name(report_name)
        paths.extend([
            _case_dir(case_id) / "grade_result" / f"{report_name}_grade.json",
        ])
    return paths


def _snapshot_files(paths: List[Path]) -> Dict[str, Any]:
    files: Dict[Path, Optional[bytes]] = {}
    dirs: List[Path] = []
    for path in paths:
        if path.is_dir():
            dirs.append(path)
            for child in path.rglob("*"):
                if child.is_file():
                    files[child] = child.read_bytes()
        else:
            files[path] = path.read_bytes() if path.exists() else None
    return {"files": files, "dirs": dirs}


def _restore_files(snapshot: Dict[str, Any]) -> None:
    files: Dict[Path, Optional[bytes]] = snapshot.get("files", {})
    for root in snapshot.get("dirs", []):
        if not root.exists():
            continue
        for child in root.rglob("*"):
            if child.is_file() and child not in files:
                child.unlink()
    for path, content in files.items():
        if content is None:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for root in sorted(snapshot.get("dirs", []), key=lambda p: len(p.parts), reverse=True):
        if not root.exists():
            continue
        for child in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass


def _criteria_path(criteria_file_name: str) -> Path:
    name = canonical_criteria_file_name(criteria_file_name)
    _safe_name(name)
    return Path(CRITERIA_DIR) / name


def _criteria_name(criteria_file_name: Optional[str]) -> str:
    return canonical_criteria_file_name(criteria_file_name or "criteria.json")


def _load_criteria(criteria_file_name: Optional[str]) -> tuple[str, Dict[str, Any], str]:
    name = _criteria_name(criteria_file_name)
    path = _criteria_path(name)
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = CriteriaSchema.model_validate(_read_json(path, {})).model_dump(mode="json")
    return name, data, grading_rubric_version(CRITERIA_DIR, data)


def _write_criteria(criteria_file_name: str, data: Dict[str, Any]) -> str:
    path = _criteria_path(_criteria_name(criteria_file_name))
    normalized = CriteriaSchema.model_validate(data).model_dump(mode="json")
    _atomic_write_json(path, normalized)
    return grading_rubric_version(CRITERIA_DIR, normalized)


def _source_digest(case_id: str, report_name: str, payload: Dict[str, Any]) -> str:
    return _sha256_data({"case_id": case_id, "report_name": report_name, **payload})


def _implicit_source_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "anchor": item.get("anchor"),
        "score": item.get("score"),
        "max_score": item.get("max_score"),
        "notes": item.get("notes"),
        "temporary_label": item.get("temporary_label"),
        "source": item.get("source"),
        "criteria_file_name": item.get("criteria_file_name"),
    }


def _implicit_source_digest(case_id: str, item: Dict[str, Any]) -> str:
    return _source_digest(case_id, str(item.get("report_name")), _implicit_source_payload(item))


def _report_text(case_id: str, report_name: str) -> str:
    report_dir = _report_dir(case_id, report_name)
    preferred = report_dir / f"{report_name}_text_only.md"
    path = preferred if preferred.exists() else next(iter(report_dir.glob("*_text_only.md")), None)
    return path.read_text(encoding="utf-8") if path and path.exists() else ""


def _anchor_context(report_text: str, anchor: Dict[str, Any], limit: int = MAX_ANCHOR_CONTEXT_CHARS) -> str:
    anchor_text = str(anchor.get("text") or "")
    if not report_text:
        return ""
    if not anchor_text:
        return report_text[:limit]

    start = anchor.get("start_offset")
    end = anchor.get("end_offset")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(report_text):
        anchor_start, anchor_end = start, end
    else:
        occurrence = anchor.get("occurrence")
        target_occurrence = occurrence if isinstance(occurrence, int) and occurrence and occurrence > 0 else 1
        anchor_start = -1
        search_from = 0
        for _ in range(target_occurrence):
            anchor_start = report_text.find(anchor_text, search_from)
            if anchor_start < 0:
                break
            search_from = anchor_start + len(anchor_text)
        if anchor_start < 0:
            return anchor_text[:limit]
        anchor_end = anchor_start + len(anchor_text)

    if limit <= 0 or anchor_end - anchor_start >= limit:
        return report_text[anchor_start:anchor_end][:limit]
    padding = max((limit - (anchor_end - anchor_start)) // 2, 0)
    left = max(anchor_start - padding, 0)
    right = min(anchor_end + padding, len(report_text))
    return report_text[left:right]


def _truncate(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def _bounded_anchor(anchor: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "anchor_type": anchor.get("anchor_type") or "text",
        "text": _truncate(anchor.get("text"), MAX_ANCHOR_TEXT_CHARS),
        "occurrence": anchor.get("occurrence"),
        "start_offset": anchor.get("start_offset"),
        "end_offset": anchor.get("end_offset"),
        "image_src": _truncate(anchor.get("image_src"), MAX_RUBRIC_FIELD_CHARS),
        "image_alt": _truncate(anchor.get("image_alt"), MAX_ANCHOR_TEXT_CHARS),
        "region": anchor.get("region"),
    }


def _bounded_scoring_points(points: Any) -> List[Dict[str, Any]]:
    bounded: List[Dict[str, Any]] = []
    if not isinstance(points, list):
        return bounded
    for point in points[:20]:
        if not isinstance(point, dict):
            continue
        bounded.append({
            "point_id": _truncate(point.get("point_id"), MAX_LABEL_CHARS),
            "summary": _truncate(point.get("summary"), MAX_RUBRIC_FIELD_CHARS),
            "full_credit_condition": _truncate(point.get("full_credit_condition"), MAX_RUBRIC_FIELD_CHARS),
            "applies_when": _truncate(point.get("applies_when"), MAX_RUBRIC_FIELD_CHARS),
            "max_deduction": point.get("max_deduction"),
            "aggregation": _truncate(point.get("aggregation"), MAX_LABEL_CHARS),
            "deduction_rules": [
                {
                    "rule_id": _truncate(rule.get("rule_id"), MAX_LABEL_CHARS),
                    "condition": _truncate(rule.get("condition"), MAX_RUBRIC_FIELD_CHARS),
                    "deduct": rule.get("deduct"),
                    "note": _truncate(rule.get("note"), MAX_RUBRIC_FIELD_CHARS),
                }
                for rule in (point.get("deduction_rules") or [])[:20]
                if isinstance(rule, dict)
            ],
        })
    return bounded


def _bounded_rubric(criteria_data: Dict[str, Any]) -> Dict[str, Any]:
    criteria = []
    for item in list(criteria_data.get("criteria", []))[:MAX_RUBRIC_CRITERIA]:
        criteria.append({
            "criteria_id": _truncate(item.get("criteria_id"), MAX_LABEL_CHARS),
            "criteria_name": _truncate(item.get("criteria_name"), MAX_LABEL_CHARS),
            "description": _truncate(item.get("description"), MAX_RUBRIC_FIELD_CHARS),
            "original_description": _truncate(item.get("original_description"), MAX_RUBRIC_FIELD_CHARS),
            "max_score": item.get("max_score"),
            "section": _truncate(item.get("section"), MAX_LABEL_CHARS),
            "scoring_points": _bounded_scoring_points(item["scoring_points"]),
            "scoring_aggregation": item.get("scoring_aggregation"),
        })
    return {
        "title": _truncate(criteria_data.get("title"), MAX_LABEL_CHARS),
        "language": _truncate(criteria_data.get("language"), MAX_LABEL_CHARS),
        "base_score": criteria_data.get("base_score"),
        "criteria": criteria,
        "criteria_count": len(criteria_data.get("criteria", [])),
    }


def _bounded_implicit_for_prompt(case_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    anchor = item.get("anchor") or {}
    report_text = _report_text(case_id, str(item.get("report_name")))
    return {
        "implicit_grade_id": item.get("implicit_grade_id"),
        "case_id": item.get("case_id"),
        "report_name": item.get("report_name"),
        "source": item.get("source"),
        "teacher_id": item.get("teacher_id"),
        "score": item.get("score"),
        "max_score": item.get("max_score"),
        "notes": _truncate(item.get("notes"), MAX_NOTES_CHARS),
        "temporary_label": _truncate(item.get("temporary_label"), MAX_LABEL_CHARS),
        "anchor": _bounded_anchor(anchor),
        "anchor_context": _anchor_context(report_text, anchor),
        "source_digest": item.get("source_digest"),
    }


def _read_implicit(case_id: str) -> List[Dict[str, Any]]:
    return _read_json(_implicit_path(case_id), [])


def _write_implicit(case_id: str, items: List[Dict[str, Any]]) -> None:
    _atomic_write_json(_implicit_path(case_id), items)


def _read_proposals(case_id: str) -> List[Dict[str, Any]]:
    return _read_json(_proposal_path(case_id), [])


def _write_proposals(case_id: str, proposals: List[Dict[str, Any]]) -> None:
    _atomic_write_json(_proposal_path(case_id), proposals)


def list_implicit_grades(
    case_id: str,
    criteria_file_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _safe_name(case_id)
    target_criteria_file = _criteria_name(criteria_file_name) if criteria_file_name else None
    items = _read_implicit(case_id)
    if criteria_file_name:
        items = [item for item in items if item.get("criteria_file_name") == target_criteria_file]
    return items


def list_criterion_proposals(case_id: str, criteria_file_name: Optional[str] = None) -> List[Dict[str, Any]]:
    _safe_name(case_id)
    target_criteria_file = _criteria_name(criteria_file_name) if criteria_file_name else None
    proposals = _read_proposals(case_id)
    if criteria_file_name:
        proposals = [item for item in proposals if item.get("criteria_file_name") == target_criteria_file]
    return proposals


def create_implicit_grade(case_id: str, payload: ImplicitGradeCreateRequest) -> Dict[str, Any]:
    if not _report_dir(case_id, payload.report_name).exists():
        raise FileNotFoundError(str(_report_dir(case_id, payload.report_name)))
    now = _now()
    anchor = payload.anchor.model_dump()
    criteria_file_name = _criteria_name(payload.criteria_file_name)
    source_payload = {
        "anchor": anchor,
        "score": float(payload.score),
        "max_score": payload.max_score,
        "notes": payload.notes,
        "temporary_label": payload.temporary_label,
        "matched_scoring_points": payload.matched_scoring_points,
        "matched_deduction_rules": payload.matched_deduction_rules,
        "scoring_point_scores": [item.model_dump(mode="json") for item in payload.scoring_point_scores],
        "source": payload.source,
        "criteria_file_name": criteria_file_name,
    }
    digest = _source_digest(case_id, payload.report_name, source_payload)
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        existing = next(
            (
                item for item in items
                if item.get("source_digest") == digest and item.get("status") in ACTIVE_IMPLICIT_STATUSES
            ),
            None,
        )
        if existing:
            return existing
        item = {
            "implicit_grade_id": f"ig_{uuid.uuid4().hex}",
            "case_id": case_id,
            "report_name": payload.report_name,
            "source": payload.source,
            "teacher_id": GRADING_TEACHER_ID if payload.source == "teacher" else None,
            "criteria_file_name": criteria_file_name,
            "anchor": anchor,
            "score": float(payload.score),
            "max_score": payload.max_score,
            "notes": payload.notes,
            "temporary_label": payload.temporary_label,
            "matched_scoring_points": list(payload.matched_scoring_points),
            "matched_deduction_rules": list(payload.matched_deduction_rules),
            "scoring_point_scores": [item.model_dump(mode="json") for item in payload.scoring_point_scores],
            "status": "queued",
            "proposal_id": None,
            "promoted_criterion_id": None,
            "confirmed_event_id": None,
            "source_digest": digest,
            "failure_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        items.append(item)
        _write_implicit(case_id, items)
        return item


def update_implicit_grade(
    case_id: str,
    implicit_grade_id: str,
    payload: ImplicitGradeUpdateRequest,
    *,
    allow_lifecycle: bool = False,
) -> Dict[str, Any]:
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        item = next((entry for entry in items if entry.get("implicit_grade_id") == implicit_grade_id), None)
        if not item:
            raise FileNotFoundError(implicit_grade_id)
        patch = payload.model_dump(exclude_unset=True)
        lifecycle_fields = {"status", "proposal_id"}
        if not allow_lifecycle and lifecycle_fields.intersection(patch):
            raise ValueError("implicit grade lifecycle can only change through proposal/archive flows")
        source_fields = {"anchor", "score", "max_score", "notes", "temporary_label"}
        if item.get("proposal_id") and source_fields.intersection(patch):
            raise StaleProposalError("implicit grade source is already linked to a proposal")
        if "anchor" in patch and patch["anchor"] is not None:
            patch["anchor"] = payload.anchor.model_dump() if payload.anchor else None
        item.update({k: v for k, v in patch.items() if v is not None})
        if source_fields.intersection(patch):
            item["source_digest"] = _implicit_source_digest(case_id, item)
        item["updated_at"] = _now()
        _write_implicit(case_id, items)
        return item


def archive_implicit_grade(case_id: str, implicit_grade_id: str) -> Dict[str, Any]:
    return update_implicit_grade(case_id, implicit_grade_id, ImplicitGradeUpdateRequest(status="archived"), allow_lifecycle=True)


def retry_implicit_grade(case_id: str, implicit_grade_id: str) -> Dict[str, Any]:
    """Queue an unresolved teacher grading act for rubric induction again."""
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        item = next((entry for entry in items if entry.get("implicit_grade_id") == implicit_grade_id), None)
        if not item:
            raise FileNotFoundError(implicit_grade_id)
        if item.get("status") not in {"failed", "uninduced"}:
            raise ValueError("only failed or uninduced implicit grades can be retried")
        if item.get("proposal_id"):
            raise ValueError("implicit grade is already linked to a proposal")

        item["status"] = "queued"
        item["failure_reason"] = None
        item["updated_at"] = _now()
        _write_implicit(case_id, items)
        return item


def _fallback_criterion_id(name: str, existing_ids: set[str]) -> str:
    base = "induced_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    candidate = base
    idx = 2
    while candidate in existing_ids:
        candidate = f"{base}_{idx}"
        idx += 1
    return candidate


def _label_key(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _matching_existing_criterion_id(proposal_raw: Dict[str, Any], criteria_data: Dict[str, Any]) -> Optional[str]:
    criteria = criteria_data.get("criteria", [])
    by_id = {str(item.get("criteria_id")): item for item in criteria if item.get("criteria_id") is not None}
    target_id = str(proposal_raw.get("target_criterion_id") or proposal_raw.get("criteria_id") or "")
    if target_id in by_id:
        return target_id

    proposed_name = _label_key(proposal_raw.get("criteria_name") or proposal_raw.get("name"))
    proposed_section = _label_key(proposal_raw.get("section"))
    if not proposed_name:
        return None
    for item in criteria:
        item_name = _label_key(item.get("criteria_name"))
        if not item_name:
            continue
        item_section = _label_key(item.get("section"))
        if proposed_name == item_name and (not proposed_section or not item_section or proposed_section == item_section):
            return str(item.get("criteria_id"))
    return None


def _merge_criterion_update(existing: Dict[str, Any], edited: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**existing, **edited, "criteria_id": existing.get("criteria_id")}
    merged["scoring_points"] = edited["scoring_points"]
    merged["scoring_aggregation"] = edited["scoring_aggregation"]
    examples: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for example in [*existing.get("few_shot_examples", []), *edited.get("few_shot_examples", [])]:
        key = _sha256_data(example)
        if key in seen:
            continue
        seen.add(key)
        examples.append(example)
    merged["few_shot_examples"] = examples
    return merged


def _single_point_scoring_structure(
    criterion_id: str,
    criteria_name: str,
    description: str,
    max_score: float,
    language: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    condition = (
        f"未满足评分要求：{description}"
        if language.lower().startswith("zh")
        else f"The evidence does not satisfy the grading requirement: {description}"
    )
    points = [{
        "point_id": f"{criterion_id}.p1",
        "summary": criteria_name,
        "full_credit_condition": description,
        "deduction_rules": [{
            "rule_id": f"{criterion_id}.p1.r1",
            "condition": condition,
            "deduct": max_score,
        }],
        "max_deduction": max_score,
        "aggregation": "max_triggered",
    }]
    aggregation = {
        "mode": "sum_deductions_capped",
        "cap": max_score,
    }
    return points, aggregation


def _repair_suggested_criterion_structure(
    suggested: Dict[str, Any],
    existing_target: Optional[Dict[str, Any]],
    highest_teacher_score: float,
    language: str,
) -> Dict[str, Any]:
    repaired = deepcopy(suggested)
    if existing_target and float(existing_target.get("max_score", 0) or 0) >= highest_teacher_score:
        repaired["max_score"] = float(existing_target["max_score"])
        repaired["scoring_points"] = deepcopy(existing_target["scoring_points"])
        repaired["scoring_aggregation"] = deepcopy(existing_target["scoring_aggregation"])
        return repaired

    points, aggregation = _single_point_scoring_structure(
        str(repaired["criteria_id"]),
        str(repaired["criteria_name"]),
        str(repaired["description"]),
        float(repaired["max_score"]),
        language,
    )
    repaired["scoring_points"] = points
    repaired["scoring_aggregation"] = aggregation
    return repaired


def _induction_candidates(items: List[Dict[str, Any]], criteria_name: str) -> List[Dict[str, Any]]:
    return [
        item for item in items
        if item.get("status") in {"queued", "uninduced"}
        and item.get("source") == "teacher"
        and item.get("criteria_file_name") == criteria_name
        and not item.get("proposal_id")
    ][:MAX_INDUCTION_BATCH]


def _mark_candidates_inducing(case_id: str, criteria_name: str) -> List[Dict[str, Any]]:
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        candidates = _induction_candidates(items, criteria_name)
        if not candidates:
            return []
        now = _now()
        candidate_ids = {item["implicit_grade_id"] for item in candidates}
        for item in items:
            if item.get("implicit_grade_id") in candidate_ids:
                item["status"] = "inducing"
                item["updated_at"] = now
        _write_implicit(case_id, items)
        return candidates


def _induction_context(
    case_id: str,
    criteria_name: str,
    criteria_data: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "criteria_file_name": criteria_name,
        "current_rubric": _bounded_rubric(criteria_data),
        "implicit_grades": [_bounded_implicit_for_prompt(case_id, item) for item in candidates],
        "limits": {
            "max_batch": MAX_INDUCTION_BATCH,
            "max_anchor_context_chars": MAX_ANCHOR_CONTEXT_CHARS,
            "max_anchor_text_chars": MAX_ANCHOR_TEXT_CHARS,
            "max_notes_chars": MAX_NOTES_CHARS,
            "max_label_chars": MAX_LABEL_CHARS,
            "max_rubric_criteria": MAX_RUBRIC_CRITERIA,
            "max_rubric_field_chars": MAX_RUBRIC_FIELD_CHARS,
        },
    }


def _proposal_target(
    proposal_raw: Dict[str, Any],
    criteria_data: Dict[str, Any],
) -> tuple[str, Optional[str]]:
    matched_existing_id = _matching_existing_criterion_id(proposal_raw, criteria_data)
    requested_type = proposal_raw.get("proposal_type")
    proposal_type = requested_type if requested_type in {"create_criterion", "update_criterion"} else "create_criterion"
    if matched_existing_id:
        return "update_criterion", matched_existing_id
    if proposal_type == "update_criterion":
        return "create_criterion", None
    return proposal_type, None


def _suggested_criterion(
    proposal_raw: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    criteria_data: Dict[str, Any],
    target_id: Optional[str],
    proposal_type: str,
) -> Dict[str, Any]:
    existing_ids = {str(item.get("criteria_id")) for item in criteria_data.get("criteria", [])}
    existing_target = next(
        (
            item
            for item in criteria_data.get("criteria", [])
            if target_id and str(item.get("criteria_id")) == str(target_id)
        ),
        None,
    )
    criteria_name_value = str(proposal_raw.get("criteria_name") or proposal_raw.get("name") or "Induced criterion")
    criterion_id = str(target_id or proposal_raw.get("criteria_id") or _fallback_criterion_id(criteria_name_value, existing_ids))
    if proposal_type == "create_criterion" and criterion_id in existing_ids:
        criterion_id = _fallback_criterion_id(criteria_name_value, existing_ids)
    description = str(proposal_raw.get("description") or proposal_raw.get("scoring_logic") or criteria_name_value)
    proposed_section = str(proposal_raw.get("section") or (existing_target or {}).get("section") or "").strip()
    if not proposed_section:
        raise ValueError("induced criteria require a non-empty semantic category")
    highest_teacher_score = max(float(item.get("score", 0) or 0) for item in candidates)
    proposed_max_score = proposal_raw.get("max_score")
    fallback_max_score = (existing_target or {}).get("max_score") or highest_teacher_score or 1
    max_score = max(float(proposed_max_score or fallback_max_score), highest_teacher_score)
    suggested = {
        "criteria_id": criterion_id,
        "criteria_name": criteria_name_value,
        "description": description,
        "original_description": str(proposal_raw.get("original_description") or proposal_raw.get("description") or criteria_name_value),
        "max_score": max_score,
        "section": proposed_section,
        "scoring_points": proposal_raw.get("scoring_points"),
        "scoring_aggregation": proposal_raw.get("scoring_aggregation"),
        "few_shot_examples": [
            {
                "example_input": str((item.get("anchor") or {}).get("text") or ""),
                "example_output_description": str(item.get("notes") or item.get("temporary_label") or ""),
                "example_output_score": float(item.get("score", 0) or 0),
            }
            for item in candidates
        ],
    }
    try:
        return CriteriaItem.model_validate(suggested).model_dump(mode="json")
    except ValueError:
        # Teacher input is intentionally only evidence plus a score. Structured
        # scoring details are model/system output, so a malformed model response
        # must not invalidate the teacher's grading act.
        repaired = _repair_suggested_criterion_structure(
            suggested,
            existing_target,
            highest_teacher_score,
            str(criteria_data.get("language") or ""),
        )
        return CriteriaItem.model_validate(repaired).model_dump(mode="json")


def _criterion_version(criteria_data: Dict[str, Any], criterion_id: Optional[str]) -> Optional[str]:
    return grading_criterion_version(CRITERIA_DIR, criteria_data, criterion_id)


def _derived_source_assignment(item: Dict[str, Any], criterion: Dict[str, Any]) -> Dict[str, Any]:
    """Keep every promoted structured grade internally consistent when a model omits its mapping."""
    capacities = [(str(point["point_id"]), structured_point_deduction_capacity(point)) for point in criterion.get("scoring_points", [])]
    remaining = min(max(float(item.get("score", 0) or 0), 0.0), float(criterion.get("max_score", 0) or 0))
    scores = []
    for point_id, capacity in capacities:
        earned = min(remaining, capacity)
        scores.append({"point_id": point_id, "score": earned, "max_score": capacity})
        remaining -= earned
    return {
        "implicit_grade_id": str(item["implicit_grade_id"]),
        "generated_score_reason": None,
        "scoring_point_scores": scores,
        "matched_scoring_points": [],
        "matched_deduction_rules": [],
        "allocation_source": "derived",
    }


def _source_grade_assignments(proposal_raw: Dict[str, Any], candidates: List[Dict[str, Any]], criterion: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_by_id = {
        str(item.get("implicit_grade_id")): item
        for item in (proposal_raw.get("source_grade_assignments") or [])
        if isinstance(item, dict) and item.get("implicit_grade_id")
    }
    assignments = []
    known_points = {str(point["point_id"]) for point in criterion.get("scoring_points", [])}
    for candidate in candidates:
        raw = raw_by_id.get(str(candidate["implicit_grade_id"]))
        scores = list((raw or {}).get("scoring_point_scores") or [])
        supplied_ids = {str(item.get("point_id")) for item in scores if isinstance(item, dict)}
        supplied_total = sum(float(item.get("score", 0) or 0) for item in scores if isinstance(item, dict))
        if raw and supplied_ids == known_points and abs(supplied_total - float(candidate.get("score", 0) or 0)) <= 1e-6:
            assignments.append({
                "implicit_grade_id": str(candidate["implicit_grade_id"]),
                "generated_score_reason": str(raw.get("generated_score_reason") or "").strip() or None,
                "scoring_point_scores": scores,
                "matched_scoring_points": list(raw.get("matched_scoring_points") or []),
                "matched_deduction_rules": list(raw.get("matched_deduction_rules") or []),
                "allocation_source": "model",
            })
        else:
            derived = _derived_source_assignment(candidate, criterion)
            derived["generated_score_reason"] = str((raw or {}).get("generated_score_reason") or "").strip() or None
            assignments.append(derived)
    return assignments


def _build_proposal(
    case_id: str,
    criteria_name: str,
    rubric_hash: str,
    proposal_raw: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    criteria_data: Dict[str, Any],
) -> Dict[str, Any]:
    proposal_type, target_id = _proposal_target(proposal_raw, criteria_data)
    suggested = _suggested_criterion(proposal_raw, candidates, criteria_data, target_id, proposal_type)
    now = _now()
    proposal = {
        "proposal_id": f"prop_{uuid.uuid4().hex}",
        "proposal_version": 1,
        "case_id": case_id,
        "proposal_type": proposal_type,
        "target_criterion_id": str(target_id) if target_id else None,
        "target_criterion_version_at_proposal": _criterion_version(criteria_data, str(target_id) if target_id else None),
        "source_implicit_grade_ids": [str(item["implicit_grade_id"]) for item in candidates],
        "source_grade_digests": [str(item["source_digest"]) for item in candidates],
        "source_grade_assignments": _source_grade_assignments(proposal_raw, candidates, suggested),
        "status": "pending",
        "criteria_file_name": criteria_name,
        "criteria_file_version_at_proposal": rubric_hash,
        "suggested_criterion": suggested,
        "rubric_patch": proposal_raw.get("rubric_patch"),
        "rationale": proposal_raw.get("rationale"),
        "model_id": GRADING_MODEL_ID,
        "confidence": proposal_raw.get("confidence"),
        "confirmed_event_ids": [],
        "failure_reason": None,
        "created_at": now,
        "updated_at": now,
        "confirmed_at": None,
        "confirmed_by": None,
    }
    CriterionProposalItem(**proposal)
    return proposal


def _proposal_match_key(proposal: Dict[str, Any]) -> tuple[str, str, str, str]:
    suggested = proposal.get("suggested_criterion") or {}
    criteria_name = _criteria_name(str(proposal.get("criteria_file_name") or "criteria.json"))
    proposal_type = str(proposal.get("proposal_type") or "")
    if proposal_type == "update_criterion":
        return (criteria_name, proposal_type, str(proposal.get("target_criterion_id") or ""), "")
    name = " ".join(str(suggested.get("criteria_name") or "").lower().split())
    section = " ".join(str(suggested.get("section") or "").lower().split())
    return (criteria_name, proposal_type, name, section)


def _merge_proposal_sources(existing: Dict[str, Any], proposal: Dict[str, Any], now: str) -> Dict[str, Any]:
    for key in ("source_implicit_grade_ids", "source_grade_digests", "source_event_ids", "source_event_digests"):
        merged = list(existing.get(key) or [])
        seen = {str(item) for item in merged}
        for item in proposal.get(key) or []:
            value = str(item)
            if value not in seen:
                merged.append(value)
                seen.add(value)
        existing[key] = merged
    existing["proposal_version"] = int(existing.get("proposal_version") or 1) + 1
    existing["updated_at"] = now
    return existing


def _store_proposal(case_id: str, proposal: Dict[str, Any]) -> Dict[str, Any]:
    now = str(proposal["updated_at"])
    source_ids = set(proposal.get("source_implicit_grade_ids") or [])
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        proposals = _read_proposals(case_id)
        match_key = _proposal_match_key(proposal)
        stored = next(
            (
                item for item in proposals
                if item.get("status") == "pending" and _proposal_match_key(item) == match_key
            ),
            None,
        )
        if stored:
            stored = _merge_proposal_sources(stored, proposal, now)
        else:
            stored = proposal
            proposals.append(stored)
        for item in items:
            if item.get("implicit_grade_id") in source_ids:
                item["status"] = "proposal_ready"
                item["proposal_id"] = stored["proposal_id"]
                item["failure_reason"] = None
                item["updated_at"] = now
        _write_proposals(case_id, proposals)
        _write_implicit(case_id, items)
        return stored


def _mark_induction_failed(case_id: str, candidates: List[Dict[str, Any]], exc: Exception) -> None:
    candidate_ids = {c["implicit_grade_id"] for c in candidates}
    with _lock_for(case_id):
        items = _read_implicit(case_id)
        now = _now()
        for item in items:
            if item.get("implicit_grade_id") in candidate_ids:
                item["status"] = "failed"
                item["failure_reason"] = f"{type(exc).__name__}: {exc}"
                item["updated_at"] = now
        _write_implicit(case_id, items)


def run_induction_for_case(
    case_id: str,
    criteria_file_name: Optional[str] = None,
    model: Optional[InductionModel] = None,
) -> Optional[Dict[str, Any]]:
    model = model or LiveInductionModel()
    criteria_name = _criteria_name(criteria_file_name)
    candidates = _mark_candidates_inducing(case_id, criteria_name)
    if not candidates:
        return None

    try:
        with criteria_file_lock(CRITERIA_DIR, criteria_name):
            criteria_name, criteria_data, rubric_hash = _load_criteria(criteria_name)
        context = _induction_context(case_id, criteria_name, criteria_data, candidates)
        proposal_raw = model.propose(context)
        if not proposal_raw:
            raise ValueError("induction model returned no proposal")
        proposal = _build_proposal(case_id, criteria_name, rubric_hash, proposal_raw, candidates, criteria_data)
        stored = _store_proposal(case_id, proposal)
        if stored.get("proposal_type") == "update_criterion" and stored.get("target_criterion_id"):
            # A pending rubric change is the first resolution path. Any old
            # teacher-vs-rubric conflict is stale until the teacher accepts or
            # rejects this proposal and the rubric is re-audited.
            defer_conflicts_for_pending_update(
                case_id,
                criteria_name,
                str(stored["target_criterion_id"]),
                str(stored["proposal_id"]),
            )
        return stored
    except Exception as exc:
        _mark_induction_failed(case_id, candidates, exc)
        return None


def confirm_proposal(
    case_id: str,
    proposal_id: str,
    payload: CriterionProposalConfirmRequest,
    evaluator: Optional[AlignmentEvaluator] = None,
    validation_model: Optional[GradingModel] = None,
    start_revision_workers: bool = True,
) -> Dict[str, Any]:
    with _lock_for(case_id):
        proposals = _read_proposals(case_id)
        proposal = next((item for item in proposals if item.get("proposal_id") == proposal_id), None)
        if not proposal:
            raise FileNotFoundError(proposal_id)
        if proposal.get("status") == "confirmed":
            return proposal
        if proposal.get("status") != "pending" or int(proposal.get("proposal_version", 0)) != payload.proposal_version:
            raise StaleProposalError("proposal status or version is stale")

        proposal_criteria_file = _criteria_name(str(proposal.get("criteria_file_name") or "criteria.json"))
        if payload.criteria_file_name and _criteria_name(payload.criteria_file_name) != proposal_criteria_file:
            raise StaleProposalError("proposal belongs to a different criteria file")
        with criteria_file_lock(CRITERIA_DIR, proposal_criteria_file):
            with case_alignment_lock(case_id):
                return _confirm_proposal_locked(
                    case_id,
                    proposal_id,
                    proposal,
                    payload,
                    proposal_criteria_file,
                    evaluator,
                    validation_model,
                    start_revision_workers,
                )


def _apply_criterion_patch(
    criteria_data: Dict[str, Any],
    proposal: Dict[str, Any],
    edited: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], str]:
    criteria = list(criteria_data.get("criteria", []))
    existing_ids = {str(item.get("criteria_id")) for item in criteria}
    if proposal.get("proposal_type") == "update_criterion":
        target_id = str(proposal.get("target_criterion_id") or edited.get("criteria_id"))
        for idx, item in enumerate(criteria):
            if str(item.get("criteria_id")) == target_id:
                edited["criteria_id"] = target_id
                criteria[idx] = _merge_criterion_update(item, edited)
                return criteria, target_id
        raise ValueError(f"target criterion not found: {target_id}")

    criterion_id = str(edited.get("criteria_id") or "")
    if not criterion_id or criterion_id in existing_ids:
        criterion_id = _fallback_criterion_id(str(edited.get("criteria_name") or "Induced criterion"), existing_ids)
        edited["criteria_id"] = criterion_id
    criteria.append(edited)
    return criteria, criterion_id


def _proposal_with_review_target(
    proposal: Dict[str, Any],
    payload: CriterionProposalConfirmRequest,
    criteria_data: Dict[str, Any],
) -> tuple[Dict[str, Any], bool]:
    """Apply the teacher's review-time target choice without mutating stored proposal state."""
    if "target_criterion_id" not in payload.model_fields_set:
        return proposal, False

    target_id = payload.target_criterion_id
    if target_id is None:
        already_creating = (
            proposal.get("proposal_type") == "create_criterion"
            and proposal.get("target_criterion_id") is None
        )
        if already_creating:
            return proposal, False
        return {
            **proposal,
            "proposal_type": "create_criterion",
            "target_criterion_id": None,
            "target_criterion_version_at_proposal": None,
        }, True

    target = str(target_id)
    if not any(str(item.get("criteria_id")) == target for item in criteria_data.get("criteria", [])):
        raise ValueError(f"target criterion not found: {target}")
    target_is_unchanged = (
        proposal.get("proposal_type") == "update_criterion"
        and str(proposal.get("target_criterion_id") or "") == target
    )
    if target_is_unchanged:
        # The review form posts its currently selected target. Sending the same
        # target is not a retargeting decision, so preserve the proposal-time
        # target version and let the criterion-level stale check below decide.
        return proposal, False
    return {
        **proposal,
        "proposal_type": "update_criterion",
        "target_criterion_id": target,
        "target_criterion_version_at_proposal": _criterion_version(criteria_data, target),
    }, True


def _source_items_for_proposal(case_id: str, proposal: Dict[str, Any]) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    items = _read_implicit(case_id)
    by_id = {item["implicit_grade_id"]: item for item in items}
    source_ids = [str(implicit_id) for implicit_id in proposal.get("source_implicit_grade_ids", [])]
    expected_digests = [str(digest) for digest in proposal.get("source_grade_digests", [])]
    current_digests = [str(by_id[implicit_id].get("source_digest")) for implicit_id in source_ids if implicit_id in by_id]
    if expected_digests != current_digests:
        raise StaleProposalError("proposal source implicit grades changed since proposal was created")

    for implicit_id in source_ids:
        item = by_id.get(implicit_id)
        if not item:
            raise ValueError(f"source implicit grade not found: {implicit_id}")
        if item.get("source") != "teacher":
            raise ValueError(f"source implicit grade is not teacher-authored: {implicit_id}")
        anchor = item.get("anchor") or {}
        if not _report_dir(case_id, str(item.get("report_name"))).exists():
            raise FileNotFoundError(str(item.get("report_name")))
        if not item.get("confirmed_event_id") and not (anchor.get("text") or anchor.get("image_alt") or anchor.get("image_src")):
            raise ValueError(f"source implicit grade has no evidence anchor: {implicit_id}")
    return by_id, source_ids


def _alignment_events_for_proposal(case_id: str) -> Dict[str, Dict[str, Any]]:
    path = _case_dir(case_id) / "alignment" / "events.json"
    events = _read_json(path, [])
    return {str(event.get("event_id")): event for event in events if event.get("event_id")}


def _source_events_for_proposal(case_id: str, proposal: Dict[str, Any]) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    source_ids = [str(event_id) for event_id in proposal.get("source_event_ids", [])]
    if not source_ids:
        return {}, []
    by_id = _alignment_events_for_proposal(case_id)
    expected_digests = [str(digest) for digest in proposal.get("source_event_digests", [])]
    if len(expected_digests) != len(source_ids):
        raise StaleProposalError("proposal source teacher events changed since proposal was created")
    event_store = {"events": list(by_id.values())}
    source_events = [by_id[event_id] for event_id in source_ids if event_id in by_id]
    compared_model_ids = {
        str(event.get("compared_model_event_id"))
        for event in source_events
        if event.get("grader_type") == "teacher"
        and event.get("compared_model_event_id")
    }
    for event_id, expected_digest in zip(source_ids, expected_digests):
        event = by_id.get(event_id)
        if not event:
            raise ValueError(f"source teacher event not found: {event_id}")
        accepted_digests = {
            proposal_source_event_digest(event),
            _sha256_data(event),
        }
        # Proposals created before stable source digests included this
        # audit-only comparison. Reconstruct that exact legacy snapshot so an
        # already-visible proposal remains confirmable after the fix.
        comparison = _model_comparison_for_teacher_event(event_store, event)
        if comparison:
            accepted_digests.add(_sha256_data({**event, "model_comparison": comparison}))
        if expected_digest not in accepted_digests:
            raise StaleProposalError(
                "proposal source teacher events changed since proposal was created"
            )
        grader_type = event.get("grader_type")
        if grader_type == "model" and event_id not in compared_model_ids:
            raise ValueError(f"source model event is not linked to a teacher correction: {event_id}")
        if grader_type not in {"teacher", "model"}:
            raise ValueError(f"source event has unsupported provenance: {event_id}")
        if event.get("event_type", "grade_authored") != "grade_authored":
            raise ValueError(f"source event is not a grade event: {event_id}")
    return by_id, source_ids


def _grade_payload_from_implicit(
    item: Dict[str, Any],
    criterion_id: str,
    edited: Dict[str, Any],
    criteria_file_name: str,
    assignment: Optional[Dict[str, Any]] = None,
) -> CaseReportGradeRequest:
    anchor = item.get("anchor") or {}
    max_score = float(edited.get("max_score", 0) or 0)
    score = max(float(item.get("score", 0) or 0), 0.0)
    if score > max_score + 1e-6:
        raise ValueError(
            f"proposal max_score ({max_score:g}) cannot be lower than the saved teacher score ({score:g})"
        )
    assignment = assignment or _derived_source_assignment(item, edited)
    return CaseReportGradeRequest(
        criteria_id=criterion_id,
        criteria_score=score,
        score_reason=(
            item.get("notes")
            or assignment.get("generated_score_reason")
            or ""
        ),
        anchor_text=anchor.get("text") or anchor.get("image_alt") or anchor.get("image_src") or "",
        matched_scoring_points=list(assignment.get("matched_scoring_points") or item.get("matched_scoring_points") or []),
        matched_deduction_rules=list(assignment.get("matched_deduction_rules") or item.get("matched_deduction_rules") or []),
        scoring_point_scores=list(assignment.get("scoring_point_scores") or item.get("scoring_point_scores") or []),
        criteria_file_name=criteria_file_name,
    )


def _mark_proposal_failed(case_id: str, proposal_id: str, exc: Exception) -> None:
    proposals = _read_proposals(case_id)
    proposal = next(item for item in proposals if item.get("proposal_id") == proposal_id)
    proposal["status"] = "failed"
    proposal["failure_reason"] = f"promotion_failed:{type(exc).__name__}: {exc}"
    proposal["updated_at"] = _now()
    _write_proposals(case_id, proposals)


def _mark_proposal_confirmed(
    case_id: str,
    proposal_id: str,
    payload: CriterionProposalConfirmRequest,
    edited: Dict[str, Any],
    criterion_id: str,
    confirmed_event_ids: List[str],
    event_by_implicit_id: Dict[str, str],
    confirmed_proposal: Dict[str, Any],
) -> Dict[str, Any]:
    now = _now()
    proposals = _read_proposals(case_id)
    items = _read_implicit(case_id)
    proposal = next(item for item in proposals if item.get("proposal_id") == proposal_id)
    source_ids = set(proposal.get("source_implicit_grade_ids", []))

    proposal["status"] = "confirmed"
    proposal["proposal_version"] = int(proposal.get("proposal_version", 1)) + 1
    proposal["suggested_criterion"] = edited
    proposal["proposal_type"] = confirmed_proposal["proposal_type"]
    proposal["target_criterion_id"] = confirmed_proposal.get("target_criterion_id")
    proposal["target_criterion_version_at_proposal"] = confirmed_proposal.get("target_criterion_version_at_proposal")
    proposal["confirmed_event_ids"] = sorted(set([*proposal.get("confirmed_event_ids", []), *confirmed_event_ids]))
    proposal["confirmed_at"] = now
    proposal["confirmed_by"] = payload.confirmed_by or GRADING_TEACHER_ID
    proposal["updated_at"] = now
    for item in items:
        if item.get("implicit_grade_id") in source_ids:
            item["status"] = "confirmed"
            item["promoted_criterion_id"] = criterion_id
            if not item.get("confirmed_event_id") and item.get("implicit_grade_id") in event_by_implicit_id:
                item["confirmed_event_id"] = event_by_implicit_id[str(item["implicit_grade_id"])]
            item["updated_at"] = now
    _write_proposals(case_id, proposals)
    _write_implicit(case_id, items)
    return proposal


def _confirm_proposal_locked(
    case_id: str,
    proposal_id: str,
    proposal: Dict[str, Any],
    payload: CriterionProposalConfirmRequest,
    criteria_file_name: str,
    evaluator: Optional[AlignmentEvaluator],
    validation_model: Optional[GradingModel],
    start_revision_workers: bool,
) -> Dict[str, Any]:
    _, criteria_data, current_rubric_hash = _load_criteria(criteria_file_name)
    reviewed_proposal, target_was_reviewed = _proposal_with_review_target(proposal, payload, criteria_data)
    rubric_changed = (
        proposal.get("criteria_file_version_at_proposal")
        and proposal.get("criteria_file_version_at_proposal") != current_rubric_hash
    )
    if target_was_reviewed and rubric_changed:
        raise StaleProposalError("criteria file changed since proposal was created; reopen review and choose a target again")
    if rubric_changed and reviewed_proposal.get("proposal_type") != "create_criterion":
        expected_target_version = reviewed_proposal.get("target_criterion_version_at_proposal")
        current_target_version = _criterion_version(criteria_data, str(reviewed_proposal.get("target_criterion_id") or ""))
        if not expected_target_version or expected_target_version != current_target_version:
            raise StaleProposalError("criteria file changed since proposal was created")
    edited = payload.suggested_criterion.model_dump()
    criteria, criterion_id = _apply_criterion_patch(criteria_data, reviewed_proposal, edited)
    candidate_criteria_data = {**criteria_data, "criteria": criteria}
    validate_structured_score_budgets(candidate_criteria_data)
    event_by_implicit_id: Dict[str, str] = {}
    confirmed_event_ids: List[str] = []
    by_id, source_ids = _source_items_for_proposal(case_id, proposal)
    source_events_by_id, source_event_ids = _source_events_for_proposal(case_id, proposal)
    for implicit_id in source_ids:
        item = by_id[implicit_id]
        if item.get("confirmed_event_id"):
            confirmed_event_ids.append(str(item["confirmed_event_id"]))
    confirmed_event_ids.extend(source_event_ids)

    before_criteria = deepcopy(criteria_data)
    report_names = [
        *[str(by_id[implicit_id].get("report_name")) for implicit_id in source_ids],
        *[str(source_events_by_id[event_id].get("report_name")) for event_id in source_event_ids if event_id in source_events_by_id],
    ]
    promotion_snapshot = _snapshot_files(_promotion_side_effect_paths(case_id, report_names))
    criteria_data["criteria"] = criteria
    _write_criteria(criteria_file_name, criteria_data)
    assignment_source = payload.source_grade_assignments if payload.source_grade_assignments is not None else proposal.get("source_grade_assignments", [])
    assignments = {
        str(item.implicit_grade_id if hasattr(item, "implicit_grade_id") else item.get("implicit_grade_id")): (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        )
        for item in assignment_source
    }
    promotion_failed: Optional[Exception] = None
    for implicit_id in source_ids:
        item = by_id.get(implicit_id)
        if not item or item.get("confirmed_event_id"):
            continue
        grade_payload = _grade_payload_from_implicit(
            item,
            criterion_id,
            edited,
            criteria_file_name,
            assignments.get(str(implicit_id)),
        )
        try:
            result = append_teacher_grade_event(case_id, str(item.get("report_name")), grade_payload, evaluator, validation_model)
            event_id = result["event"]["event_id"]
            confirmed_event_ids.append(event_id)
            event_by_implicit_id[str(implicit_id)] = event_id
        except Exception as exc:
            promotion_failed = exc
            _write_criteria(criteria_file_name, before_criteria)
            _restore_files(promotion_snapshot)
            break

    if promotion_failed:
        _mark_proposal_failed(case_id, proposal_id, promotion_failed)
        raise promotion_failed

    # Use the same save path as manual Card edits. This registers a complete
    # Card revision, invalidates every old result for this criterion, and
    # schedules the all-report re-evaluation workflow.
    audit_jobs = enqueue_consistency_audits_for_criteria_file(
        criteria_file_name,
        [criterion_id],
        "criterion_proposal_confirmed",
    )
    if start_revision_workers:
        ensure_criterion_revision_workers_for_jobs(audit_jobs)
    return _mark_proposal_confirmed(
        case_id,
        proposal_id,
        payload,
        edited,
        criterion_id,
        confirmed_event_ids,
        event_by_implicit_id,
        reviewed_proposal,
    )


def reject_proposal(case_id: str, proposal_id: str, payload: CriterionProposalRejectRequest) -> Dict[str, Any]:
    with _lock_for(case_id):
        proposals = _read_proposals(case_id)
        proposal = next((item for item in proposals if item.get("proposal_id") == proposal_id), None)
        if not proposal:
            raise FileNotFoundError(proposal_id)
        if proposal.get("status") != "pending" or int(proposal.get("proposal_version", 0)) != payload.proposal_version:
            raise StaleProposalError("proposal status or version is stale")
        proposal["status"] = "rejected"
        proposal["proposal_version"] = int(proposal.get("proposal_version", 1)) + 1
        proposal["failure_reason"] = payload.reason
        proposal["updated_at"] = _now()
        record_refinement_rejection(case_id, proposal, payload.reason)
        items = _read_implicit(case_id)
        for item in items:
            if item.get("implicit_grade_id") in proposal.get("source_implicit_grade_ids", []):
                item["status"] = "uninduced"
                item["proposal_id"] = None
                item["updated_at"] = _now()
        _write_proposals(case_id, proposals)
        _write_implicit(case_id, items)
        if proposal.get("target_criterion_id"):
            enqueue_consistency_audit(
                case_id,
                proposal.get("criteria_file_name"),
                str(proposal.get("target_criterion_id")),
                "criterion_proposal_rejected",
                write=True,
                mode="new_teacher_grade",
                focus_event_ids=[
                    str(event_id)
                    for event_id in proposal.get("source_event_ids") or []
                    if (
                        _alignment_events_for_proposal(case_id).get(str(event_id))
                        or {}
                    ).get("grader_type")
                    == "teacher"
                ],
            )
        return proposal
