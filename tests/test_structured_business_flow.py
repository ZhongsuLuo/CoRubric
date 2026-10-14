from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from backend.app import alignment, implicit_grading, main, services
from backend.app.schemas import CriteriaItem
import backend.libs.update_criteria as update_criteria_lib


class FakeModel:
    def grade(self, report_text, criterion, boundary, examples):
        return {
            "criteria_score": 8,
            "score_reason": "报告解释不完整，综合得分为 8/10。",
            "anchor_text": "validated pendulum evidence",
            "target_section": criterion.get("section"),
            "matched_scoring_points": ["1.p1"],
            "matched_deduction_rules": ["1.p1.r1"],
            "scoring_point_scores": [{
                "point_id": "1.p1",
                "score": 8,
                "reason": "报告包含 validated pendulum evidence，但解释不完整，触发 1.p1.r1。",
            }],
        }


class FakeEvaluator:
    def compare_teacher_cases(self, criterion, prior_text, prior_grade, new_text, new_grade):
        return {"comparable": True, "reason_evidence_consistent": True, "reason": "fake audit"}

    def reason_and_evidence_agree(self, criterion, expected, candidate):
        return {"agree": True, "reason": "fake semantic check"}

    def admit(self, report_text, criterion, boundary):
        return {
            "decision": "inside",
            "matched_rule_ids": boundary.get("covered_rule_ids", []),
            "reason": "fake boundary",
        }

    def interpret_grade(self, report_text, current_criterion, event):
        return {
            "evidence_source": "teacher_selected",
            "inferred_evidence": "",
            "fit": "consistent",
            "confidence": 0.9,
            "issue_type": None,
            "severity": "none",
            "matched_rules": [str(current_criterion.get("criteria_id")), "1.p1", "1.p1.r1"],
            "reason": "fake audit consistent with structured rubric",
        }


class StructuredInductionModel:
    def propose(self, context):
        return {
            "proposal_type": "create_criterion",
            "target_criterion_id": None,
            "criteria_name": "结构化隐式评分项",
            "description": "根据教师选中的证据，对实验现象解释进行结构化扣分。",
            "original_description": "实验现象解释评分",
            "max_score": 4,
            "section": "analysis",
            "scoring_points": [
                {
                    "point_id": "implicit.p1",
                    "summary": "现象解释完整性",
                    "full_credit_condition": "能够结合选中证据解释实验现象。",
                    "deduction_rules": [
                        {
                            "rule_id": "implicit.p1.r1",
                            "condition": "只描述现象，没有解释原因。",
                            "deduct": 1,
                        },
                        {
                            "rule_id": "implicit.p1.r2",
                            "condition": "解释与选中证据相矛盾。",
                            "deduct": 4,
                        },
                    ],
                    "max_deduction": 4,
                    "aggregation": "sum_deductions_capped",
                }
            ],
            "scoring_aggregation": {
                "mode": "sum_deductions_capped",
                "cap": 4,
                "score_formula": "criteria_score = max_score - min(sum_triggered_deductions, max_score)",
            },
            "rationale": "教师隐式评分提供了证据和分数边界。",
            "confidence": 0.9,
        }


class _FakeCompletionMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeCompletionChoice:
    def __init__(self, content: str):
        self.message = _FakeCompletionMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content: str):
        self.choices = [_FakeCompletionChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeCompletionResponse(self.content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = _FakeChat(completions)


def _point(prefix: str = "1") -> Dict[str, Any]:
    return {
        "point_id": f"{prefix}.p1",
        "summary": "证据解释准确性",
        "full_credit_condition": "引用并解释 validated pendulum evidence。",
        "deduction_rules": [
            {"rule_id": f"{prefix}.p1.r1", "condition": "解释不完整。", "deduct": 2},
            {"rule_id": f"{prefix}.p1.r2", "condition": "证据缺失。", "deduct": 8},
        ],
        "max_deduction": 10,
        "aggregation": "sum_deductions_capped",
    }


def _criteria_data() -> Dict[str, Any]:
    return {
        "schema_version": "structured-rubric-v1",
        "title": "Structured Rubric",
        "background": "",
        "language": "zh",
        "base_score": 100,
        "criteria": [
            {
                "criteria_id": "1",
                "criteria_name": "证据解释",
                "description": "总体评价报告是否准确解释选中证据。",
                "original_description": "证据解释",
                "max_score": 10,
                "section": "analysis",
                "few_shot_examples": [],
                "scoring_points": [_point("1")],
                "scoring_aggregation": {
                    "mode": "sum_deductions_capped",
                    "cap": 10,
                    "score_formula": "criteria_score = max_score - min(sum_triggered_deductions, max_score)",
                },
            }
        ],
    }


def _write_report(case_root: Path, case_id: str, report_name: str, text: str = "validated pendulum evidence") -> None:
    report_dir = case_root / case_id / "markdown" / report_name
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{report_name}.md").write_text(f"# {report_name}\n{text}\n", encoding="utf-8")
    (report_dir / f"{report_name}_text_only.md").write_text(f"SECTION: analysis\n{text}\n", encoding="utf-8")
    (case_root / case_id / f"{report_name}.pdf").write_bytes(b"pdf")


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case_root = tmp_path / "case"
    criteria_dir = tmp_path / "criteria"
    results_dir = tmp_path / "results"
    case_id = "case-a"
    criteria_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    _write_report(case_root, case_id, "report-a")
    _write_report(case_root, case_id, "report-b")
    _write_report(case_root, case_id, "report-c")
    (criteria_dir / "criteria.json").write_text(json.dumps(_criteria_data(), ensure_ascii=False, indent=2), encoding="utf-8")

    for module in (alignment, implicit_grading, services, main):
        monkeypatch.setattr(module, "CASE_ROOT", case_root)
        monkeypatch.setattr(module, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(services, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(main, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(alignment, "LiveGradingModel", lambda: FakeModel())
    monkeypatch.setattr(alignment, "LLMAlignmentEvaluator", lambda: FakeEvaluator())

    return {
        "case_root": case_root,
        "criteria_dir": criteria_dir,
        "results_dir": results_dir,
        "case_id": case_id,
        "client": TestClient(main.app),
    }


def _ok(response):
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    return payload["data"]


def test_api_business_flow_preserves_structured_rubric_from_criteria_to_grade_projection_delete_and_autograde(api_env):
    client: TestClient = api_env["client"]
    case_id = api_env["case_id"]
    case_root: Path = api_env["case_root"]

    criteria = _ok(client.get("/criteria/criteria.json"))["item"]
    assert criteria["criteria"][0]["scoring_points"][0]["point_id"] == "1.p1"

        # 模拟前端保存人工评分：前端只能提交本次评分；评分标准 snapshot 完全由后端持有。
    grade_payload = {
        "criteria_id": "1",
        "criteria_score": 8,
        "score_reason": "解释基本正确，扣 2 分。",
        "anchor_text": "validated pendulum evidence",
        "criteria_file_name": "criteria.json",
        "matched_scoring_points": ["1.p1"],
        "matched_deduction_rules": ["1.p1.r1"],
        "scoring_point_scores": [{"point_id": "1.p1", "score": 8}],
    }
    result = _ok(client.post(f"/cases/{case_id}/reports/report-a/grade", json=grade_payload))["result"]
    item = result["criteria"][0]
    assert item["scoring_points"][0]["point_id"] == "1.p1"
    assert item["matched_deduction_rules"] == ["1.p1.r1"]
    assert result["file_score"] == 98

    events = json.loads((case_root / case_id / "alignment" / "events.json").read_text(encoding="utf-8"))
    assert events[-1]["criterion_snapshot"]["scoring_points"][0]["point_id"] == "1.p1"
    assert events[-1]["authored_grade"]["scoring_points"][0]["point_id"] == "1.p1"

    projected_path = case_root / case_id / "grade_result" / "report-a_grade.json"
    projected = json.loads(projected_path.read_text(encoding="utf-8"))
    assert projected["criteria"][0]["scoring_points"][0]["deduction_rules"][0]["rule_id"] == "1.p1.r1"

    loaded_report = _ok(client.get(f"/cases/{case_id}/reports/report-a?criteria_file_name=criteria.json"))
    assert loaded_report["result"]["criteria"][0]["scoring_aggregation"]["cap"] == 10

    deleted = _ok(client.post(f"/cases/{case_id}/reports/report-a/grade/delete", json={"criteria_id": "1", "criteria_file_name": "criteria.json"}))["result"]
    assert deleted["criteria"] == []

    # 再用两个教师样本建立 readiness，模拟前端触发 auto-grade；fake 模型返回 matched fields。
    for report_name in ("report-a", "report-b"):
        _ok(client.post(f"/cases/{case_id}/reports/{report_name}/grade", json={**grade_payload, "criteria_score": 8}))
    audit = _ok(client.post(f"/cases/{case_id}/alignment/audit", json={"criteria_id": "1", "criteria_file_name": "criteria.json"}))
    assert audit["items"][0]["job"]["status"] == "done"

    auto = _ok(client.post(f"/cases/{case_id}/reports/report-c/auto-grade", json={"criteria_file_name": "criteria.json", "criteria_ids": ["1"]}))
    assert auto["grades"]
    assert auto["grades"][0]["scoring_points"][0]["point_id"] == "1.p1"
    assert auto["grades"][0]["matched_scoring_points"] == ["1.p1"]
    assert auto["grades"][0]["matched_deduction_rules"] == ["1.p1.r1"]

    accepted = _ok(client.post(f"/cases/{case_id}/reports/report-c/grade/accept-model", json={"criteria_id": "1", "criteria_file_name": "criteria.json"}))
    assert accepted["accepted"] is True
    assert accepted["result"]["criteria"][0]["scoring_points"][0]["point_id"] == "1.p1"
    assert accepted["result"]["criteria"][0]["acceptance_status"] == "accepted_model"


def test_api_business_flow_updates_criteria_and_confirms_implicit_structured_proposal(api_env, monkeypatch: pytest.MonkeyPatch):
    client: TestClient = api_env["client"]
    case_id = api_env["case_id"]
    criteria_dir: Path = api_env["criteria_dir"]

    refined = {
        "description": "总体评价报告是否准确解释选中证据，并按结构化扣分点扣分。",
        "scoring_points": [
            {
                **_point("1"),
                "deduction_rules": [
                    {"rule_id": "1.p1.r1", "condition": "解释不完整或缺少关键因果链。", "deduct": 2},
                    {"rule_id": "1.p1.r3", "condition": "出现与证据相反的解释。", "deduct": 8},
                ],
            }
        ],
        "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 10},
    }
    completions = _FakeCompletions(json.dumps(refined, ensure_ascii=False))
    monkeypatch.setattr(update_criteria_lib, "get_chat_client", lambda provider: _FakeClient(completions))

    update = _ok(
        client.post(
            "/criteria/update",
            json={
                "criteria_path": str(criteria_dir / "criteria.json"),
                "criteria_id": "1",
                "few_shot_example": {
                    "example_input": "validated pendulum evidence",
                    "example_output_description": "解释不完整，扣 2 分。",
                    "example_output_score": 8,
                },
            },
        )
    )
    assert update["updated"] is True
    assert update["structured_changed"] is True
    assert update["before_scoring_points"][0]["point_id"] == "1.p1"
    assert update["after_scoring_points"][0]["deduction_rules"][1]["rule_id"] == "1.p1.r3"

    # 模拟隐式评分创建：endpoint 同步运行 fake induction，避免真实 LLM 和浏览器依赖。
    def run_fake_induction(case_id_arg, criteria_file_name):
        implicit_grading.run_induction_for_case(case_id_arg, criteria_file_name, StructuredInductionModel())

    monkeypatch.setattr(main, "_run_induction_async", run_fake_induction)
    created = _ok(
        client.post(
            f"/cases/{case_id}/implicit-grades",
            json={
                "report_name": "report-a",
                "anchor": {"anchor_type": "text", "text": "validated pendulum evidence"},
                "score": 3,
                "max_score": 4,
                "notes": "解释存在但不完整。",
                "temporary_label": "现象解释",
                "criteria_file_name": "criteria.json",
                "request_id": "req-structured-flow",
            },
        )
    )["item"]
    assert created["status"] == "queued"

    proposals = _ok(client.get(f"/cases/{case_id}/criterion-proposals?criteria_file_name=criteria.json"))["items"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["suggested_criterion"]["scoring_points"][0]["point_id"] == "implicit.p1"

    edited = CriteriaItem(**proposal["suggested_criterion"]).model_dump(mode="json")
    edited["scoring_points"][0]["deduction_rules"].append(
        {"rule_id": "implicit.p1.r3", "condition": "解释未关联选中证据。", "deduct": 2}
    )
    confirmed = _ok(
        client.post(
            f"/cases/{case_id}/criterion-proposals/{proposal['proposal_id']}/confirm",
            json={
                "proposal_version": proposal["proposal_version"],
                "criteria_file_name": "criteria.json",
                "suggested_criterion": edited,
                "confirmed_by": "teacher-a",
            },
        )
    )["item"]
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_event_ids"]

    saved_criteria = json.loads((criteria_dir / "criteria.json").read_text(encoding="utf-8"))
    saved_new = next(item for item in saved_criteria["criteria"] if item["criteria_id"] == edited["criteria_id"])
    assert saved_new["scoring_points"][0]["deduction_rules"][-1]["rule_id"] == "implicit.p1.r3"

    promoted_result = json.loads((api_env["case_root"] / case_id / "grade_result" / "report-a_grade.json").read_text(encoding="utf-8"))
    promoted_item = next(item for item in promoted_result["criteria"] if item["criteria_id"] == edited["criteria_id"])
    assert promoted_item["scoring_points"][0]["point_id"] == "implicit.p1"
