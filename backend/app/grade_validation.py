"""Validation for immutable grade evidence and structured point allocations."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from math import isfinite
from typing import Any, Dict, List

from .image_evidence import is_safe_report_image_src
from .schemas import ScoringPointScore, structured_point_deduction_capacity


def validate_scoring_point_scores(
    criterion: Dict[str, Any],
    point_scores: List[Any],
    criteria_score: float,
    *,
    require_complete: bool = False,
    require_reasons: bool = False,
    matched_scoring_points: List[str] | None = None,
    matched_deduction_rules: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Validate point scores against the current structured-rubric snapshot."""
    scoring_points = list(criterion.get("scoring_points") or [])
    if not point_scores:
        if require_complete and scoring_points:
            raise ValueError(
                "scoring_point_scores must include every structured scoring point"
            )
        return []

    capacities = {
        str(point.get("point_id")): structured_point_deduction_capacity(point)
        for point in scoring_points
    }
    if not capacities:
        raise ValueError("scoring_point_scores requires structured scoring_points")
    try:
        expected_total = float(criteria_score)
    except (TypeError, ValueError) as error:
        raise ValueError("criterion score must be numeric") from error
    if not isfinite(expected_total):
        raise ValueError("criterion score must be finite")

    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in point_scores:
        item = (
            raw.model_dump(mode="json")
            if hasattr(raw, "model_dump")
            else ScoringPointScore.model_validate(raw).model_dump(mode="json")
        )
        point_id = str(item.get("point_id") or "")
        if point_id not in capacities:
            raise ValueError(f"unknown scoring point: {point_id}")
        if point_id in seen:
            raise ValueError(f"duplicate scoring point score: {point_id}")
        seen.add(point_id)

        max_score = capacities[point_id]
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid score for scoring point: {point_id}") from error
        if not isfinite(score) or score < -1e-6 or score > max_score + 1e-6:
            raise ValueError(
                f"score for {point_id} must be between 0 and {max_score:g}"
            )
        reason = item.get("reason")
        if require_reasons and not str(reason or "").strip():
            raise ValueError(f"reason is required for scoring point: {point_id}")
        normalized_item = {
            "point_id": point_id,
            "score": min(max(score, 0.0), max_score),
            "max_score": max_score,
        }
        if reason is not None:
            normalized_item["reason"] = str(reason)
        normalized.append(normalized_item)

    missing = sorted(set(capacities) - seen)
    if missing:
        raise ValueError(f"missing scores for scoring points: {', '.join(missing)}")
    total = sum(item["score"] for item in normalized)
    if abs(total - expected_total) > 1e-6:
        raise ValueError(
            f"sum of scoring point scores ({total:g}) must equal criterion score "
            f"({expected_total:g})"
        )
    if require_reasons:
        _validate_model_deduction_matches(
            scoring_points,
            normalized,
            matched_scoring_points or [],
            matched_deduction_rules or [],
        )
    return normalized


def validate_model_scoring_point_scores(
    criterion: Dict[str, Any],
    point_scores: List[Any],
    criteria_score: float,
    matched_scoring_points: List[str],
    matched_deduction_rules: List[str],
) -> List[Dict[str, Any]]:
    """Apply the strict structured-allocation contract for new model grades."""
    return validate_scoring_point_scores(
        criterion,
        point_scores,
        criteria_score,
        require_complete=True,
        require_reasons=True,
        matched_scoring_points=matched_scoring_points,
        matched_deduction_rules=matched_deduction_rules,
    )


def _validate_model_deduction_matches(
    scoring_points: List[Dict[str, Any]],
    point_scores: List[Dict[str, Any]],
    matched_scoring_points: List[str],
    matched_deduction_rules: List[str],
) -> None:
    point_by_id = {
        str(point.get("point_id") or ""): point for point in scoring_points
    }
    rule_by_id = {
        str(rule.get("rule_id") or ""): (point_id, rule)
        for point_id, point in point_by_id.items()
        for rule in point.get("deduction_rules") or []
    }
    matched_points = [str(point_id) for point_id in matched_scoring_points]
    matched_rules = [str(rule_id) for rule_id in matched_deduction_rules]
    if len(set(matched_points)) != len(matched_points):
        raise ValueError("matched_scoring_points contains duplicates")
    if len(set(matched_rules)) != len(matched_rules):
        raise ValueError("matched_deduction_rules contains duplicates")

    unknown_points = sorted(set(matched_points) - set(point_by_id))
    if unknown_points:
        raise ValueError(f"unknown matched scoring points: {', '.join(unknown_points)}")
    unknown_rules = sorted(set(matched_rules) - set(rule_by_id))
    if unknown_rules:
        raise ValueError(f"unknown matched deduction rules: {', '.join(unknown_rules)}")

    score_by_point = {item["point_id"]: item for item in point_scores}
    deducted_points = {
        point_id
        for point_id, item in score_by_point.items()
        if item["score"] < item["max_score"] - 1e-6
    }
    if set(matched_points) != deducted_points:
        raise ValueError(
            "matched_scoring_points must exactly identify scoring points with deductions"
        )

    rules_by_point: Dict[str, List[str]] = {point_id: [] for point_id in point_by_id}
    for rule_id in matched_rules:
        rules_by_point[rule_by_id[rule_id][0]].append(rule_id)
    for point_id in deducted_points:
        if not rules_by_point[point_id]:
            raise ValueError(
                f"deducted scoring point must include a matched deduction rule: {point_id}"
            )
    for point_id, rule_ids in rules_by_point.items():
        item = score_by_point[point_id]
        reason = str(item.get("reason") or "")
        point = point_by_id[point_id]
        deductions = [
            float(rule_by_id[rule_id][1].get("deduct") or 0)
            for rule_id in rule_ids
        ]
        deduction = (
            max(deductions, default=0.0)
            if point.get("aggregation") == "max_triggered"
            else sum(deductions)
        )
        if point.get("max_deduction") is not None:
            deduction = min(deduction, float(point["max_deduction"]))
        expected_score = max(item["max_score"] - deduction, 0.0)
        if abs(item["score"] - expected_score) > 1e-6:
            raise ValueError(
                f"score for {point_id} is inconsistent with its matched deduction rules"
            )
        missing_mentions = [rule_id for rule_id in rule_ids if rule_id not in reason]
        if missing_mentions:
            raise ValueError(
                f"reason for {point_id} must mention matched deduction rules: "
                f"{', '.join(missing_mentions)}"
            )


def _normalized_evidence_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


_TABLE_SEPARATOR_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?(?:[ \t]*\|[ \t]*:?-{3,}:?)+[ \t]*\|?[ \t]*$",
    re.MULTILINE,
)
_MARKDOWN_PREFIX_RES = (
    re.compile(r"^(#{1,6}[ \t]*)", re.MULTILINE),
    re.compile(r"^([ \t]{0,3}>[ \t]?)", re.MULTILINE),
    re.compile(r"^([ \t]*[-*+][ \t]+)", re.MULTILINE),
    re.compile(r"^([ \t]*\d+[.)][ \t]+)", re.MULTILINE),
)
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\u2060\uFEFF]")
_QUOTE_TRANSLATION = str.maketrans({
    "′": "'",
    "＇": "'",
    "‘": "'",
    "’": "'",
    "`": "'",
})


def _rendered_evidence_index(value: Any) -> tuple[str, List[tuple[int, int]]]:
    """Return rendered Markdown text as a compact search index plus source offsets.

    Browser selections contain rendered text, while the persisted report is Markdown.
    This deliberately small Markdown projection mirrors the syntax the frontend strips
    for highlighting; it is only used after the legacy exact-text check fails.
    """
    source = str(value or "")
    skipped = [False] * len(source)

    def skip(start: int, end: int) -> None:
        for offset in range(start, end):
            skipped[offset] = True

    for match in _TABLE_SEPARATOR_RE.finditer(source):
        skip(*match.span())
    for pattern in _MARKDOWN_PREFIX_RES:
        for match in pattern.finditer(source):
            skip(*match.span(1))
    for match in _MARKDOWN_LINK_RE.finditer(source):
        label_start, label_end = match.span(1)
        skip(match.start(), label_start)
        skip(label_end, match.end())

    normalized: List[str] = []
    offsets: List[tuple[int, int]] = []
    markdown_delimiters = frozenset("*_~`$|")
    for offset, char in enumerate(source):
        if skipped[offset] or char in markdown_delimiters or char.isspace():
            continue
        rendered = unicodedata.normalize("NFKC", char)
        rendered = _ZERO_WIDTH_RE.sub("", rendered).translate(_QUOTE_TRANSLATION).casefold()
        for part in rendered:
            if not part.isspace():
                normalized.append(part)
                offsets.append((offset, offset + len(char)))
    return "".join(normalized), offsets


def locate_rendered_evidence_text(
    report_text: str,
    evidence_text: str,
    occurrence: int = 0,
) -> tuple[int, int] | None:
    """Locate browser-rendered evidence in its Markdown source."""
    if occurrence < 0:
        return None
    normalized_report, offsets = _rendered_evidence_index(report_text)
    normalized_text, _ = _rendered_evidence_index(evidence_text)
    if not normalized_text:
        return None

    start = 0
    found = -1
    for _ in range(occurrence + 1):
        found = normalized_report.find(normalized_text, start)
        if found < 0:
            return None
        start = found + max(len(normalized_text), 1)
    return offsets[found][0], offsets[found + len(normalized_text) - 1][1]


def _text_anchor_match_count(report_text: str, evidence_text: str) -> int:
    """Preserve exact matching, then accept the equivalent rendered Markdown text."""
    normalized_report = _normalized_evidence_text(report_text)
    normalized_text = _normalized_evidence_text(evidence_text)
    exact_count = normalized_report.count(normalized_text) if normalized_text else 0
    if exact_count:
        return exact_count
    rendered_report, _ = _rendered_evidence_index(report_text)
    rendered_text, _ = _rendered_evidence_index(evidence_text)
    return rendered_report.count(rendered_text) if rendered_text else 0


def validate_evidence_anchors(
    report_text: str,
    raw_anchors: List[Any],
    legacy_anchor_text: str = "",
) -> List[Dict[str, Any]]:
    """Validate immutable evidence identities against the report being graded."""
    anchors = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else deepcopy(item)
        for item in raw_anchors
    ]
    if not anchors and _normalized_evidence_text(legacy_anchor_text):
        anchors = [
            {
                "anchor_type": "text",
                "text": legacy_anchor_text,
                "occurrence": 0,
            }
        ]
    if not anchors:
        raise ValueError("grade evidence is required")

    normalized_report = _normalized_evidence_text(report_text)
    validated: List[Dict[str, Any]] = []
    for index, anchor in enumerate(anchors):
        anchor_type = str(anchor.get("anchor_type") or "text")
        if anchor_type == "text":
            text = str(anchor.get("text") or "").strip()
            normalized_text = _normalized_evidence_text(text)
            match_count = _text_anchor_match_count(report_text, text)
            if not normalized_text or not match_count:
                raise ValueError(
                    f"text evidence anchor {index + 1} could not be located in the report"
                )
            occurrence = int(anchor.get("occurrence") or 0)
            if occurrence < 0 or match_count <= occurrence:
                raise ValueError(
                    f"text evidence anchor {index + 1} occurrence is not present in the report"
                )
            anchor["text"] = text
            anchor["occurrence"] = occurrence
        elif anchor_type == "image":
            image_src = str(anchor.get("image_src") or "").strip()
            image_alt = str(anchor.get("image_alt") or "").strip()
            description = str(anchor.get("description") or image_alt).strip()
            if not (image_src or image_alt):
                raise ValueError(
                    f"image evidence anchor {index + 1} requires an image identity"
                )
            if image_src and not is_safe_report_image_src(image_src):
                raise ValueError(
                    f"image evidence anchor {index + 1} has an unsafe image source"
                )
            if not (
                (image_src and image_src in report_text)
                or (
                    image_alt
                    and _normalized_evidence_text(image_alt) in normalized_report
                )
            ):
                raise ValueError(
                    f"image evidence anchor {index + 1} could not be located in the report"
                )
            if not description:
                raise ValueError(
                    f"image evidence anchor {index + 1} requires a grading description"
                )
            anchor["description"] = description
        elif anchor_type == "absence":
            expected_location = str(anchor.get("expected_location") or "").strip()
            missing_item = str(anchor.get("missing_item") or "").strip()
            is_document_boundary = expected_location.casefold() in {
                "document:start",
                "document:end",
            }
            if not expected_location or not missing_item:
                raise ValueError(
                    f"absence evidence anchor {index + 1} requires expected_location "
                    "and missing_item"
                )
            if (
                not is_document_boundary
                and _normalized_evidence_text(expected_location) not in normalized_report
            ):
                raise ValueError(
                    f"absence evidence anchor {index + 1} has no trustworthy expected location"
                )
        else:
            raise ValueError(f"unsupported evidence anchor type: {anchor_type}")
        validated.append(anchor)
    return validated
