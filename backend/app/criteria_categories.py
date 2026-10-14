from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol

from .config import CRITERIA_DIR
from .criteria_lock import criteria_file_lock
from .criteria_versions import criteria_content_hash
from .schemas import CriteriaCategoryApplyRequest
from .services import load_criteria_file, save_criteria_file


class StaleCategoryPreviewError(RuntimeError):
    pass


class CriteriaCategoryModel(Protocol):
    def suggest(self, criteria_data: Dict[str, Any]) -> Dict[str, Any]: ...


def _parse_json_completion(content: str) -> Dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```json"):
        normalized = normalized[7:]
    if normalized.endswith("```"):
        normalized = normalized[:-3]
    parsed = json.loads(normalized.strip())
    if not isinstance(parsed, dict):
        raise ValueError("category model must return a JSON object")
    return parsed


class LiveCriteriaCategoryModel:
    @staticmethod
    def _messages(criteria_data: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        system_message = (
            "You reorganize a complete grading rubric into semantic grading categories. "
            "A category describes what is being assessed, never where content appears in a report. "
            "Return only strict JSON."
        )
        prompt = {
            "task": (
                "Assign every criterion to exactly one concise, non-empty semantic category. "
                "Do not use report headings such as Introduction, Principle, Results, Discussion, or Questions. "
                "Prefer a small, coherent category set and reuse good existing categories. "
                "Avoid singleton categories whenever the number and meaning of criteria allow; normally each category should contain at least two criteria. "
                "Semantic coherence is more important than perfectly equal category sizes. "
                "Do not change, merge, split, add, or omit criteria."
            ),
            "output_language": criteria_data.get("language"),
            "allowed_schema": {
                "assignments": "array containing every criterion exactly once: {criteria_id, section}",
                "rationale": "brief explanation of the overall organization",
            },
            "rubric": {
                "title": criteria_data.get("title"),
                "background": criteria_data.get("background"),
                "language": criteria_data.get("language"),
                "criteria": [
                    {
                        "criteria_id": item.get("criteria_id"),
                        "criteria_name": item.get("criteria_name"),
                        "description": item.get("description"),
                        "current_section": item.get("section"),
                    }
                    for item in criteria_data.get("criteria", [])
                ],
            },
        }
        return system_message, prompt

    @staticmethod
    def _complete(system_message: str, prompt: Dict[str, Any]) -> str:
        from backend.libs.config import GRADING_MODEL_CONFIG, get_chat_client

        config = GRADING_MODEL_CONFIG
        completion = get_chat_client(config.provider).chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.0,
            **config.request_options(),
        )
        return completion.choices[0].message.content or ""

    def suggest(self, criteria_data: Dict[str, Any]) -> Dict[str, Any]:
        system_message, prompt = self._messages(criteria_data)
        content = self._complete(system_message, prompt)
        try:
            proposal = _parse_json_completion(content)
            _validated_assignments(criteria_data, proposal.get("assignments"))
            return proposal
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            repair_prompt = {
                "task": "Repair the category proposal so it follows the original request exactly.",
                "validation_error": str(error),
                "previous_response": content[:12000],
                "original_request": prompt,
            }
            return _parse_json_completion(self._complete(system_message, repair_prompt))


def _validated_assignments(
    criteria_data: Dict[str, Any],
    raw_assignments: Any,
) -> List[Dict[str, str]]:
    if not isinstance(raw_assignments, list):
        raise ValueError("assignments must be a list")

    expected_ids = [str(item.get("criteria_id")) for item in criteria_data.get("criteria", [])]
    expected_id_set = set(expected_ids)
    assignments: Dict[str, str] = {}
    for raw in raw_assignments:
        if not isinstance(raw, dict):
            raise ValueError("every assignment must be an object")
        criterion_id = str(raw.get("criteria_id") or "").strip()
        section = str(raw.get("section") or "").strip()
        if not criterion_id or not section:
            raise ValueError("every assignment requires criteria_id and a non-empty section")
        if criterion_id in assignments:
            raise ValueError(f"criterion {criterion_id} is assigned more than once")
        assignments[criterion_id] = section

    missing = [criterion_id for criterion_id in expected_ids if criterion_id not in assignments]
    unknown = [criterion_id for criterion_id in assignments if criterion_id not in expected_id_set]
    if missing or unknown:
        raise ValueError(f"assignment coverage mismatch; missing={missing}, unknown={unknown}")
    return [
        {"criteria_id": criterion_id, "section": assignments[criterion_id]}
        for criterion_id in expected_ids
    ]


def _category_counts(assignments: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for assignment in assignments:
        section = assignment["section"]
        counts[section] = counts.get(section, 0) + 1
    return [{"section": section, "count": count} for section, count in counts.items()]


def preview_criteria_categories(
    file_name: str,
    model: Optional[CriteriaCategoryModel] = None,
) -> Dict[str, Any]:
    criteria_data = load_criteria_file(file_name)
    if not criteria_data.get("criteria"):
        raise ValueError("cannot categorize an empty rubric")
    proposal = (model or LiveCriteriaCategoryModel()).suggest(criteria_data)
    assignments = _validated_assignments(criteria_data, proposal.get("assignments"))
    criteria_by_id = {
        str(item["criteria_id"]): item
        for item in criteria_data.get("criteria", [])
    }
    preview_assignments = [
        {
            **assignment,
            "criteria_name": criteria_by_id[assignment["criteria_id"]].get("criteria_name", ""),
            "old_section": criteria_by_id[assignment["criteria_id"]].get("section"),
        }
        for assignment in assignments
    ]
    return {
        "source_content_hash": criteria_content_hash(criteria_data),
        "assignments": preview_assignments,
        "categories": _category_counts(assignments),
        "rationale": str(proposal.get("rationale") or ""),
    }


def apply_criteria_categories(file_name: str, request: CriteriaCategoryApplyRequest) -> Dict[str, Any]:
    with criteria_file_lock(CRITERIA_DIR, file_name):
        current = load_criteria_file(file_name)
        if criteria_content_hash(current) != request.source_content_hash:
            raise StaleCategoryPreviewError(
                "criteria changed after the category preview was generated; generate a new preview"
            )

        assignments = _validated_assignments(
            current,
            [assignment.model_dump(mode="json") for assignment in request.assignments],
        )
        section_by_id = {
            assignment["criteria_id"]: assignment["section"]
            for assignment in assignments
        }
        updated = deepcopy(current)
        for criterion in updated["criteria"]:
            criterion["section"] = section_by_id[str(criterion["criteria_id"])]
        save_criteria_file(file_name, updated, start_revision_workers=False)
    return {
        "item": updated,
        "categories": _category_counts(assignments),
        "content_hash": criteria_content_hash(updated),
    }
