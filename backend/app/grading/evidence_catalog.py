"""Build immutable report evidence choices for grading models.

The model chooses catalog identifiers. The server reconstructs anchors from the
original report, so model paraphrases never become highlight identities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..image_evidence import build_image_catalog


MAX_TEXT_BLOCK_CHARS = 1600
_NONEMPTY_BLOCK = re.compile(r"\S(?:.*?)(?=\n[ \t]*\n|\Z)", re.DOTALL)


class EvidenceSelectionError(ValueError):
    """Raised when a model selects evidence that is not in the source catalog."""


@dataclass(frozen=True)
class EvidenceCatalogEntry:
    evidence_id: str
    anchor_type: str
    source_text: str
    start_offset: int
    end_offset: int
    image_src: str | None = None
    description: str | None = None


def _trimmed_span(report_text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and report_text[start].isspace():
        start += 1
    while end > start and report_text[end - 1].isspace():
        end -= 1
    return start, end


def _bounded_text_spans(report_text: str, start: int, end: int) -> Iterable[tuple[int, int]]:
    """Split a text region at paragraph/line boundaries without changing it."""
    region = report_text[start:end]
    for match in _NONEMPTY_BLOCK.finditer(region):
        block_start, block_end = _trimmed_span(
            report_text, start + match.start(), start + match.end()
        )
        while block_end - block_start > MAX_TEXT_BLOCK_CHARS:
            limit = block_start + MAX_TEXT_BLOCK_CHARS
            newline = report_text.rfind("\n", block_start, limit + 1)
            split_at = newline if newline > block_start else limit
            part_start, part_end = _trimmed_span(report_text, block_start, split_at)
            if part_start < part_end:
                yield part_start, part_end
            block_start, _ = _trimmed_span(report_text, split_at, block_end)
        if block_start < block_end:
            yield block_start, block_end


def build_evidence_catalog(report_text: str) -> list[EvidenceCatalogEntry]:
    """Return deterministic text/image choices backed by exact source offsets."""
    image_spans: list[tuple[int, int, dict[str, str]]] = []
    search_from = 0
    for image in build_image_catalog(report_text):
        source_block = image["source_block"]
        start = report_text.find(source_block, search_from)
        if start < 0:
            start = report_text.find(source_block)
        if start < 0:
            continue
        end = start + len(source_block)
        image_spans.append((start, end, image))
        search_from = end

    raw_entries: list[tuple[int, int, str, dict[str, str] | None]] = []
    cursor = 0
    for image_start, image_end, image in sorted(image_spans):
        if cursor < image_start:
            raw_entries.extend(
                (start, end, "text", None)
                for start, end in _bounded_text_spans(report_text, cursor, image_start)
            )
        raw_entries.append((image_start, image_end, "image", image))
        cursor = max(cursor, image_end)
    if cursor < len(report_text):
        raw_entries.extend(
            (start, end, "text", None)
            for start, end in _bounded_text_spans(report_text, cursor, len(report_text))
        )

    text_number = 0
    image_number = 0
    catalog: list[EvidenceCatalogEntry] = []
    for start, end, anchor_type, image in sorted(raw_entries, key=lambda item: item[0]):
        if anchor_type == "image":
            image_number += 1
            evidence_id = f"I{image_number:04d}"
        else:
            text_number += 1
            evidence_id = f"T{text_number:04d}"
        catalog.append(
            EvidenceCatalogEntry(
                evidence_id=evidence_id,
                anchor_type=anchor_type,
                source_text=report_text[start:end],
                start_offset=start,
                end_offset=end,
                image_src=image.get("image_src") if image else None,
                description=image.get("description") if image else None,
            )
        )
    return catalog


def render_catalogued_report(catalog: list[EvidenceCatalogEntry]) -> str:
    """Render an annotated copy of the report for model evidence selection."""
    rendered: list[str] = []
    for entry in catalog:
        rendered.extend(
            [
                f"[Evidence {entry.evidence_id} | {entry.anchor_type}]",
                entry.source_text,
                f"[/Evidence {entry.evidence_id}]",
            ]
        )
    return "\n\n".join(rendered)


def _selection_ids(selection: dict[str, Any]) -> list[str]:
    raw_ids = selection.get("evidence_ids")
    if raw_ids is None:
        raw_ids = [selection.get("evidence_id")]
    elif not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    return [str(value).strip() for value in raw_ids if str(value or "").strip()]


def _resolve_absence_selection(
    selection: dict[str, Any],
    by_id: dict[str, EvidenceCatalogEntry],
    index: int,
) -> dict[str, Any]:
    missing_item = str(selection.get("missing_item") or "").strip()
    expected_id = str(selection.get("expected_location_id") or "").strip()
    if not missing_item:
        raise EvidenceSelectionError(
            f"absence evidence selection {index} requires missing_item"
        )
    if expected_id.casefold() in {"document:start", "document:end"}:
        expected_location = expected_id.casefold()
    else:
        entry = by_id.get(expected_id)
        if entry is None:
            raise EvidenceSelectionError(
                f"unknown evidence id in selection {index}: {expected_id or '<empty>'}"
            )
        expected_location = entry.source_text
    return {
        "anchor_type": "absence",
        "expected_location": expected_location,
        "missing_item": missing_item,
    }


def _anchor_for_entry(entry: EvidenceCatalogEntry, report_text: str) -> dict[str, Any]:
    if entry.anchor_type == "image":
        return {
            "anchor_type": "image",
            "image_src": entry.image_src,
            "description": entry.description,
        }
    normalized_text = " ".join(entry.source_text.split()).casefold()
    normalized_prefix = " ".join(report_text[: entry.start_offset].split()).casefold()
    return {
        "anchor_type": "text",
        "text": entry.source_text,
        "occurrence": normalized_prefix.count(normalized_text),
        "start_offset": entry.start_offset,
        "end_offset": entry.end_offset,
    }


def resolve_evidence_selections(
    report_text: str, raw_selections: Any
) -> list[dict[str, Any]]:
    """Resolve model-selected IDs into anchors copied from the immutable report."""
    if not isinstance(raw_selections, list) or not raw_selections:
        raise EvidenceSelectionError("model evidence selections are required")

    catalog = build_evidence_catalog(report_text)
    by_id = {entry.evidence_id: entry for entry in catalog}
    anchors: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    for index, raw in enumerate(raw_selections):
        selection = {"evidence_id": raw} if isinstance(raw, str) else raw
        if not isinstance(selection, dict):
            raise EvidenceSelectionError(
                f"evidence selection {index + 1} must be an object or identifier"
            )
        if str(selection.get("anchor_type") or "") == "absence":
            anchors.append(_resolve_absence_selection(selection, by_id, index + 1))
            continue

        evidence_ids = _selection_ids(selection)
        if not evidence_ids:
            raise EvidenceSelectionError(
                f"evidence selection {index + 1} requires evidence_id"
            )
        for evidence_id in evidence_ids:
            if evidence_id in selected_ids:
                continue
            entry = by_id.get(evidence_id)
            if entry is None:
                raise EvidenceSelectionError(
                    f"unknown evidence id in selection {index + 1}: {evidence_id}"
                )
            selected_ids.add(evidence_id)
            anchors.append(_anchor_for_entry(entry, report_text))
    if not anchors:
        raise EvidenceSelectionError("model evidence selections resolved to no anchors")
    return anchors
