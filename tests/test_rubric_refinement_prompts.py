from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from backend.app import alignment, implicit_grading
from backend.libs import config as model_config


class CapturingChatClient:
    def __init__(self, response: dict[str, Any]):
        self.calls: list[dict[str, Any]] = []
        self._response = response
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self._response, ensure_ascii=False))
                )
            ]
        )


def _assert_rule_preservation_semantics(messages: list[dict[str, str]]) -> None:
    prompt = " ".join(message["content"] for message in messages).lower()

    assert "preserve its existing scoring points and deduction rules by default" in prompt
    assert "substantive reason to change the grading standard" in prompt
    assert "explicitly identify every removed point_id or rule_id" in prompt
    assert "explain in rationale why each removal is necessary" in prompt
    assert "irrelevant to, unmentioned in, or untriggered by the current report" in prompt
    assert "never a valid reason to remove it" in prompt
    assert "retained or modified scoring points and deduction rules" in prompt
    assert "keep their existing point_id and rule_id" in prompt


def _existing_criterion() -> dict[str, Any]:
    return {
        "criteria_id": "1",
        "criteria_name": "Evidence quality",
        "description": "Assess evidence quality.",
        "original_description": "Assess evidence quality.",
        "max_score": 10,
        "section": "analysis",
        "scoring_points": [
            {
                "point_id": "1.p1",
                "summary": "Evidence supports the conclusion.",
                "deduction_rules": [
                    {
                        "rule_id": "1.p1.r1",
                        "condition": "Evidence is missing.",
                        "deduct": 10,
                    }
                ],
            }
        ],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
    }


def test_teacher_event_refinement_prompt_preserves_existing_rules(monkeypatch) -> None:
    criterion = _existing_criterion()
    expected = {
        "decision": "update_criterion",
        "suggested_criterion": criterion,
        "rationale": "Retain all existing rules and add one missing score boundary.",
        "confidence": 0.9,
    }
    client = CapturingChatClient(expected)
    monkeypatch.setattr(model_config, "get_chat_client", lambda _provider: client)

    result = alignment.LLMAlignmentEvaluator().propose_criterion_update(
        "Selected report evidence.",
        criterion,
        {
            "rubric_metadata": {"language": "en"},
            "authored_grade": {
                "criteria_score": 8,
                "score_reason": "Evidence is mostly complete.",
                "anchor_text": "Selected report evidence.",
            },
        },
    )

    assert result == expected
    assert len(client.calls) == 1
    messages = client.calls[0]["messages"]
    _assert_rule_preservation_semantics(messages)
    assert "all human-readable json fields" in messages[0]["content"].lower()


def test_implicit_update_refinement_prompt_preserves_existing_rules(monkeypatch) -> None:
    criterion = _existing_criterion()
    expected = {
        "proposal_type": "update_criterion",
        "target_criterion_id": "1",
        "criteria_name": criterion["criteria_name"],
        "description": criterion["description"],
        "original_description": criterion["original_description"],
        "max_score": criterion["max_score"],
        "section": criterion["section"],
        "scoring_points": criterion["scoring_points"],
        "scoring_aggregation": criterion["scoring_aggregation"],
        "source_grade_assignments": [],
        "rationale": "Retain all existing rules and add one missing score boundary.",
        "confidence": 0.9,
    }
    client = CapturingChatClient(expected)
    monkeypatch.setattr(model_config, "get_chat_client", lambda _provider: client)

    result = implicit_grading.LiveInductionModel().propose(
        {
            "current_rubric": {
                "language": "en",
                "criteria": [criterion],
            },
            "implicit_grades": [],
        }
    )

    assert result == expected
    assert len(client.calls) == 1
    messages = client.calls[0]["messages"]
    _assert_rule_preservation_semantics(messages)
    assert "natural language declared by current_rubric.language" in messages[0]["content"].lower()
    combined_prompt = " ".join(message["content"] for message in messages).lower()
    assert "semantic grading category" in combined_prompt
    assert "never a report heading or evidence location" in combined_prompt
    assert "avoid creating a singleton category" in combined_prompt
    allowed_schema = json.loads(messages[1]["content"])["allowed_schema"]
    assert set(expected).issubset(allowed_schema)
