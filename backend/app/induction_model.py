from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Protocol

from .prompt_constraints import RUBRIC_CATEGORY_RULES, RUBRIC_REFINEMENT_PRESERVATION_RULES


class InductionModel(Protocol):
    def propose(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ...


def _parse_json_completion(content: str) -> Dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```json"):
        normalized = normalized[7:]
    if normalized.endswith("```"):
        normalized = normalized[:-3]
    parsed = json.loads(normalized.strip())
    return parsed if isinstance(parsed, dict) else {}


def _missing_induction_fields(proposal: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    proposal_type = proposal.get("proposal_type")
    if proposal_type not in {"create_criterion", "update_criterion"}:
        missing.append("proposal_type")
    if proposal_type == "update_criterion" and not proposal.get("target_criterion_id"):
        missing.append("target_criterion_id")
    for field in ("criteria_name", "description"):
        if not str(proposal.get(field) or "").strip():
            missing.append(field)
    if not isinstance(proposal.get("max_score"), (int, float)):
        missing.append("max_score")
    if not isinstance(proposal.get("scoring_points"), list) or not proposal.get("scoring_points"):
        missing.append("scoring_points")
    if not isinstance(proposal.get("scoring_aggregation"), dict):
        missing.append("scoring_aggregation")
    if not str(proposal.get("section") or "").strip():
        missing.append("section")
    return missing


class LiveInductionModel:
    def propose(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from backend.libs.config import GRADING_MODEL_CONFIG, get_chat_client

        config = GRADING_MODEL_CONFIG
        prompt = {
            "task": (
                "Infer a rubric criterion proposal from implicit teacher grading acts. "
                "All human-readable output fields (criteria_name, description, original_description, section, scoring point summaries, "
                "deduction conditions, rationale, and generated_score_reason) MUST use the natural language designated by current_rubric.language "
                "(for example, 'zh' means Chinese and 'en' means English). "
                "The selected evidence anchor is the primary evidence for what is being graded; "
                "anchor_context is a short supporting window only: use it to resolve a reference or fragmented selected text, "
                "never to introduce a new scoring fact, choose a criterion, or override the anchor. "
                "The saved teacher score is authoritative evidence that the selected anchor is gradable. "
                "Always infer the narrowest useful criterion directly from that evidence; do not abstain or return null required fields. "
                "First decide whether the implicit grades belong to an existing current_rubric criterion. "
                "If yes, return proposal_type='update_criterion' and target_criterion_id for that criterion. "
                "Only return proposal_type='create_criterion' when no existing criterion can explain the grading acts. "
                + RUBRIC_CATEGORY_RULES
                + "Return strict JSON."
            ),
            "allowed_schema": {
                "proposal_type": "create_criterion or update_criterion",
                "target_criterion_id": "string or null",
                "criteria_name": "short name",
                "description": "full grading rule with score logic",
                "original_description": "raw concise rule",
                "max_score": "number",
                "section": "non-empty semantic grading category string",
                "scoring_points": "array of structured points; each has point_id, summary, full_credit_condition, deduction_rules",
                "scoring_aggregation": "object describing how deductions combine and cap at max_score",
                "source_grade_assignments": "array, one per implicit_grade_id: {implicit_grade_id, generated_score_reason (a concise evidence-grounded explanation of that saved teacher score), scoring_point_scores (every point_id and retained score), matched_scoring_points, matched_deduction_rules}",
                "rationale": "why these grades imply this criterion",
                "confidence": "number 0-1",
            },
            "context": context,
        }
        system_message = (
            "You induce or update teacher rubric criteria from grading examples. "
            "Use the natural language declared by current_rubric.language for every natural-language field in your JSON. "
            "A saved teacher score is an explicit instruction to infer a criterion from the selected anchor; never abstain. "
            "Nearby context is non-authoritative and may only clarify the anchor; it must not add a grading condition absent from the anchor. "
            "Prefer updating an existing criterion only when the anchor, score, and rule truly fit it. "
            + RUBRIC_REFINEMENT_PRESERVATION_RULES
            + RUBRIC_CATEGORY_RULES
            + "Keep description as the overall summary and put concrete score/deduction logic in scoring_points. "
            "For every implicit_grade_id, return a concise generated_score_reason grounded in its selected anchor, plus retained scores for every proposed structured scoring point; their sum must equal that teacher grade's score. "
            "Do not split one criterion into multiple criteria; use multiple scoring_points instead. "
            "Return only JSON."
        )
        client = get_chat_client(config.provider)
        completion = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.0,
            **config.request_options(),
        )
        content = completion.choices[0].message.content or ""
        try:
            proposal = _parse_json_completion(content)
        except (json.JSONDecodeError, TypeError):
            proposal = {}
        missing_fields = _missing_induction_fields(proposal)
        if not missing_fields:
            return proposal

        repair_prompt = {
            "task": "Repair and complete the rubric proposal. Return every required field as strict JSON.",
            "missing_or_invalid_fields": missing_fields,
            "previous_response": content[:12000],
            "original_request": prompt,
        }
        repaired = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)},
            ],
            temperature=0.0,
            **config.request_options(),
        )
        repaired_proposal = _parse_json_completion(repaired.choices[0].message.content or "")
        repaired_missing_fields = _missing_induction_fields(repaired_proposal)
        if repaired_missing_fields:
            raise ValueError(f"induction model omitted required fields: {repaired_missing_fields}")
        return repaired_proposal
