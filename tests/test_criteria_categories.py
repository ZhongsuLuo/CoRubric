from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import alignment, criteria_categories, main, services
from backend.app.criteria_categories import StaleCategoryPreviewError
from backend.app.criteria_versions import grading_rubric_payload
from backend.app.schemas import CriteriaCategoryApplyRequest
from backend.libs import transform_criteria


def _criterion(criteria_id: str, name: str, section: str) -> dict:
    return {
        "criteria_id": criteria_id,
        "criteria_name": name,
        "description": f"Evaluate {name}",
        "original_description": f"Evaluate {name}",
        "max_score": 1,
        "section": section,
        "few_shot_examples": [],
        "scoring_points": [
            {
                "point_id": f"{criteria_id}.p1",
                "summary": name,
                "full_credit_condition": f"Includes {name}",
                "deduction_rules": [
                    {
                        "rule_id": f"{criteria_id}.p1.r1",
                        "condition": f"Missing {name}",
                        "deduct": 1,
                    }
                ],
                "max_deduction": 1,
            }
        ],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 1},
    }


def _rubric() -> dict:
    return {
        "schema_version": "structured-rubric-v1",
        "title": "Lab rubric",
        "background": "",
        "language": "en",
        "base_score": 2,
        "criteria": [
            _criterion("1", "Identity", "Report title"),
            _criterion("2", "Calculation", "Results"),
        ],
    }


class FixedCategoryModel:
    def suggest(self, _criteria_data):
        return {
            "assignments": [
                {"criteria_id": "1", "section": "Completeness and expression"},
                {"criteria_id": "2", "section": "Data calculation and analysis"},
            ],
            "rationale": "Organized by grading dimensions.",
        }


@pytest.fixture
def criteria_dir(tmp_path, monkeypatch):
    root = tmp_path / "criteria"
    root.mkdir()
    (root / "criteria.json").write_text(json.dumps(_rubric()), encoding="utf-8")
    monkeypatch.setattr(criteria_categories, "CRITERIA_DIR", root)
    monkeypatch.setattr(services, "CRITERIA_DIR", root)
    monkeypatch.setattr(alignment, "CRITERIA_DIR", root)
    monkeypatch.setattr(services, "enqueue_consistency_audits_for_criteria_file", lambda *_args: [])
    return root


def test_category_preview_covers_the_complete_rubric(criteria_dir):
    preview = criteria_categories.preview_criteria_categories(
        "criteria.json", model=FixedCategoryModel()
    )

    assert preview["source_content_hash"]
    assert [item["criteria_id"] for item in preview["assignments"]] == ["1", "2"]
    assert preview["assignments"][0]["old_section"] == "Report title"
    assert preview["rationale"] == "Organized by grading dimensions."


def test_category_apply_changes_only_sections_and_preserves_grading_versions(criteria_dir):
    before = services.load_criteria_file("criteria.json")
    _, rubric_version_before, _ = alignment._load_criteria("criteria.json")
    criterion_version_before = alignment._criterion_version(before, "1")
    preview = criteria_categories.preview_criteria_categories(
        "criteria.json", model=FixedCategoryModel()
    )

    result = criteria_categories.apply_criteria_categories(
        "criteria.json",
        CriteriaCategoryApplyRequest(
            source_content_hash=preview["source_content_hash"],
            assignments=preview["assignments"],
        ),
    )

    after = result["item"]
    _, rubric_version_after, _ = alignment._load_criteria("criteria.json")
    assert grading_rubric_payload(after) == grading_rubric_payload(before)
    assert services._changed_criteria_ids(before, after) == []
    assert rubric_version_after == rubric_version_before
    assert alignment._criterion_version(after, "1") == criterion_version_before
    assert [item["section"] for item in after["criteria"]] == [
        "Completeness and expression",
        "Data calculation and analysis",
    ]


def test_category_apply_rejects_a_stale_preview(criteria_dir):
    preview = criteria_categories.preview_criteria_categories(
        "criteria.json", model=FixedCategoryModel()
    )
    changed = deepcopy(_rubric())
    changed["criteria"][0]["description"] = "Changed after preview"
    (criteria_dir / "criteria.json").write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(StaleCategoryPreviewError):
        criteria_categories.apply_criteria_categories(
            "criteria.json",
            CriteriaCategoryApplyRequest(
                source_content_hash=preview["source_content_hash"],
                assignments=preview["assignments"],
            ),
        )


def test_category_preview_rejects_missing_assignments(criteria_dir):
    class IncompleteModel:
        def suggest(self, _criteria_data):
            return {"assignments": [{"criteria_id": "1", "section": "Completeness"}]}

    with pytest.raises(ValueError, match="coverage mismatch"):
        criteria_categories.preview_criteria_categories(
            "criteria.json", model=IncompleteModel()
        )


def test_category_api_reports_a_stale_preview_as_conflict(monkeypatch):
    def stale_apply(*_args):
        raise StaleCategoryPreviewError("stale preview")

    monkeypatch.setattr(main, "apply_criteria_categories", stale_apply)

    response = TestClient(main.app).post(
        "/criteria/criteria.json/categories/apply",
        json={
            "source_content_hash": "old-hash",
            "assignments": [{"criteria_id": "1", "section": "Completeness"}],
        },
    )

    assert response.status_code == 409
    assert "stale preview" in response.text


def test_raw_rubric_transform_prompt_requires_balanced_semantic_categories(
    tmp_path, monkeypatch
):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            content = json.dumps({
                "title": "Rubric",
                "background": "",
                "language": "en",
                "base_score": 0,
                "criteria": [],
            })
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    source = tmp_path / "rubric.txt"
    source.write_text("Raw criteria", encoding="utf-8")
    monkeypatch.setattr(transform_criteria, "CRITERIA_TRANSFORM", ("fake", "fake-model"))
    monkeypatch.setattr(transform_criteria, "get_chat_client", lambda _provider: client)

    transform_criteria.criteria_to_json_file(str(source), str(tmp_path / "output"))

    prompt = " ".join(message["content"] for message in captured["messages"])
    assert "Required non-empty semantic grading category" in prompt
    assert "never where the content appears in a report" in prompt
    assert "avoid singleton categories" in prompt
