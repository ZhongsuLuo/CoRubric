"""Parse and ground image evidence without exposing arbitrary image URLs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit


TEXT_ONLY_IMAGE_PATTERN = re.compile(
    r"^>\s*\*\*\[Image\]\*\*\s*(?P<description>[^\r\n]*)\r?\n"
    r">\s*\*Source:\s*(?P<src>[^*\r\n]+?)\s*\*\s*$",
    re.MULTILINE,
)
MARKDOWN_IMAGE_PATTERN = re.compile(
    r"!\[(?P<description>[^\]]*)\]\((?P<src>[^)\s]+)\)"
)
SOURCE_PATTERN = re.compile(r"Source:\s*([^*\r\n]+)")

ImageCatalogEntry = dict[str, str]


def _fully_unquote(value: str) -> str:
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def normalize_report_image_src(value: Any) -> str:
    """Normalize a server-owned report image path, rejecting unsafe sources."""
    raw = str(value or "").strip().replace("\\", "/")
    parsed = urlsplit(raw)
    if not raw or parsed.scheme or parsed.netloc:
        return ""

    path = _fully_unquote(parsed.path)
    parts = [part for part in path.removeprefix("./").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return ""
    if path.startswith("/case-files/"):
        return raw
    return "/".join(parts) if parts and parts[0] == "images" else ""


def is_safe_report_image_src(value: Any) -> bool:
    return bool(normalize_report_image_src(value))


def build_image_catalog(submission: str) -> list[ImageCatalogEntry]:
    """Extract unique image descriptions and identities from grading input."""
    catalog: list[ImageCatalogEntry] = []
    seen: set[str] = set()

    for pattern in (TEXT_ONLY_IMAGE_PATTERN, MARKDOWN_IMAGE_PATTERN):
        for match in pattern.finditer(submission):
            image_src = normalize_report_image_src(match.group("src"))
            description = match.group("description").strip()
            if not image_src or not description or image_src in seen:
                continue
            seen.add(image_src)
            catalog.append(
                {
                    "image_src": image_src,
                    "description": description,
                    "source_block": match.group(0).strip(),
                }
            )
    return catalog


def _catalog_matches_in_text(
    text: str,
    catalog: list[ImageCatalogEntry],
) -> list[ImageCatalogEntry]:
    by_src = {entry["image_src"]: entry for entry in catalog}
    block_matches = [entry for entry in catalog if entry["source_block"] in text]
    if block_matches:
        return block_matches

    sources = (normalize_report_image_src(value) for value in SOURCE_PATTERN.findall(text))
    return [by_src[source] for source in sources if source in by_src]


def _normalized_image_anchor(
    anchor: dict[str, Any],
    catalog: list[ImageCatalogEntry],
) -> dict[str, Any] | None:
    by_src = {entry["image_src"]: entry for entry in catalog}
    image_src = normalize_report_image_src(anchor.get("image_src"))
    match = by_src.get(image_src)

    if match is None:
        description = str(anchor.get("image_alt") or anchor.get("description") or "")
        description = description.strip().casefold()
        candidates = [
            entry
            for entry in catalog
            if description and entry["description"].casefold() == description
        ]
        match = candidates[0] if len(candidates) == 1 else None
    if match is None:
        return None

    return {
        **anchor,
        "anchor_type": "image",
        "image_src": match["image_src"],
        "description": str(anchor.get("description") or match["description"]).strip(),
    }


def _anchors_for_catalog_entries(
    entries: list[ImageCatalogEntry],
) -> list[dict[str, str]]:
    return [
        {
            "anchor_type": "image",
            "image_src": entry["image_src"],
            "description": entry["description"],
        }
        for entry in entries
    ]


def normalize_model_image_evidence(submission: str, result: dict[str, Any]) -> dict[str, Any]:
    """Ground model image anchors against descriptions present in the submission."""
    catalog = build_image_catalog(submission)
    if not catalog:
        return result

    normalized: list[dict[str, Any]] = []
    for raw_anchor in result.get("evidence_anchors") or []:
        if not isinstance(raw_anchor, dict):
            continue
        anchor = dict(raw_anchor)
        if str(anchor.get("anchor_type") or "text") == "image":
            normalized.append(_normalized_image_anchor(anchor, catalog) or anchor)
            continue

        legacy_matches = _catalog_matches_in_text(str(anchor.get("text") or ""), catalog)
        normalized.extend(_anchors_for_catalog_entries(legacy_matches) or [anchor])

    if normalized:
        result["evidence_anchors"] = normalized
        return result

    legacy_text = str(result.get("anchor_text") or result.get("file_original_content") or "")
    legacy_matches = _catalog_matches_in_text(legacy_text, catalog)
    if legacy_matches:
        result["evidence_anchors"] = _anchors_for_catalog_entries(legacy_matches)
    return result
