from __future__ import annotations

import json

import pytest

from backend.app import image_evidence
from backend.libs import grade


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _CaptureCompletions:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    def create(self, **kwargs):
        self.messages = kwargs["messages"]
        return _FakeCompletion(self.content)


class _SequenceCompletions:
    def __init__(self, contents: list[str]):
        self.contents = iter(contents)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion(next(self.contents))


class _FakeChat:
    def __init__(self, completions: _CaptureCompletions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _CaptureCompletions):
        self.chat = _FakeChat(completions)


def test_grading_prompt_requests_complete_evidence_region(monkeypatch):
    completions = _CaptureCompletions(json.dumps({
        "criteria_score": 4,
        "file_original_content": "姓名：王金杰",
        "target_section": None,
        "score_reason": "Earned 4 out of 4.",
    }))
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))

    result = grade.grade_single_criterion(
        "# 实验报告\n\n专业：化学工程与工艺\n姓名：王金杰\n学号：3240101860\n实验名称：原子吸收光谱法测定饮用水中的镁含量\n",
        {
            "criteria_id": "1",
            "description": "报告必须包含标题和姓名。",
            "original_description": "标题或姓名",
            "max_score": 4,
            "few_shot_examples": [],
            "scoring_points": [{
                "point_id": "1.p1",
                "summary": "标题和姓名",
                "deduction_rules": [{"rule_id": "1.p1.r1", "condition": "标题或姓名缺失", "deduct": 4}],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        },
        {"title": "Rubric", "background": "", "language": "zh"},
    )

    user_prompt = completions.messages[1]["content"]
    assert "complete, contiguous evidence region" in user_prompt
    assert "Do NOT return only a single keyword" in user_prompt
    assert "whole header block" in user_prompt
    assert "Select evidence by ID" in user_prompt
    assert "evidence_selections" in user_prompt
    assert '{"evidence_ids": ["T0001", "I0002"]}' in user_prompt
    assert "server infers text/image anchor" in user_prompt
    assert "`anchor_type` is optional for existing evidence" in user_prompt
    assert "anchor_type:'absence'" in user_prompt
    assert "Every item MUST include a non-empty reason" in user_prompt
    assert "Never add a `Rule ` prefix" in user_prompt
    assert "score_reason is a concise overall summary" in user_prompt
    assert "Do not concatenate or copy the point reasons" in user_prompt
    assert result["criteria_score"] == 4


def test_grade_submission_enforces_and_preserves_model_point_reasons(
    tmp_path, monkeypatch
):
    submission_path = tmp_path / "report.md"
    criteria_path = tmp_path / "criteria.json"
    output_dir = tmp_path / "output"
    submission_path.write_text("# Report\nmeasured value: 1.9 mg/L\n", encoding="utf-8")
    criteria_path.write_text(json.dumps({
        "schema_version": "structured-rubric-v1",
        "title": "Rubric",
        "background": "",
        "language": "en",
        "base_score": 100,
        "criteria": [{
            "criteria_id": "1",
            "criteria_name": "Result",
            "description": "Report the measured value.",
            "original_description": "Measured value",
            "max_score": 4,
            "few_shot_examples": [],
            "scoring_points": [{
                "point_id": "1.p1",
                "summary": "Measured value",
                "deduction_rules": [{
                    "rule_id": "1.p1.missing",
                    "condition": "The measured value is missing.",
                    "deduct": 4,
                }],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        }],
    }), encoding="utf-8")
    model_result = {
        "criteria_score": 4,
        "score_reason": "The measured value is present, for a total of 4/4.",
        "file_original_content": "measured value: 1.9 mg/L",
        "target_section": "Report",
        "matched_scoring_points": [],
        "matched_deduction_rules": [],
        "scoring_point_scores": [{"point_id": "1.p1", "score": 4}],
    }
    monkeypatch.setattr(grade, "grade_single_criterion", lambda *_args: model_result)

    with pytest.raises(ValueError, match="reason is required for scoring point: 1.p1"):
        grade.grade_submission(submission_path, criteria_path, output_dir)

    point_reason = "The report states 1.9 mg/L, which satisfies the measured-value requirement."
    model_result["scoring_point_scores"][0]["reason"] = point_reason
    grade.grade_submission(submission_path, criteria_path, output_dir)

    saved = json.loads((output_dir / "report_grade.json").read_text(encoding="utf-8"))
    assert saved["criteria"][0]["scoring_point_scores"][0]["reason"] == point_reason


def test_grade_repair_prompt_rechecks_final_values_and_includes_validation_error(
    monkeypatch
):
    completions = _CaptureCompletions(json.dumps({
        "criteria_score": 4,
        "score_reason": "The explicitly reported final value has two significant figures, so the total is 4/4.",
        "file_original_content": "Final result: 1.9 mg/L",
        "target_section": "Results",
        "matched_scoring_points": [],
        "matched_deduction_rules": [],
        "scoring_point_scores": [{
            "point_id": "9.1",
            "score": 4,
            "reason": "The final reported value is 1.9 mg/L, which has two significant figures.",
        }],
    }))
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))
    invalid_grade = {
        "criteria_score": 0,
        "score_reason": "Invalid total.",
        "matched_scoring_points": [],
        "matched_deduction_rules": [],
        "scoring_point_scores": [
            {"point_id": "9.1", "score": 4, "reason": "Full credit."}
        ],
    }

    repaired = grade.repair_single_criterion_grade(
        "Intermediate: 1.8695 mg/L\nFinal result: 1.9 mg/L\n",
        {
            "criteria_id": "9",
            "criteria_name": "Significant figures",
            "description": "The final result must have two significant figures.",
            "original_description": "Two significant figures",
            "max_score": 4,
            "scoring_points": [{
                "point_id": "9.1",
                "summary": "Final result",
                "deduction_rules": [{
                    "rule_id": "9.1.wrong_sigfig",
                    "condition": "The final result has the wrong number of significant figures.",
                    "deduct": 4,
                }],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        },
        {"title": "Rubric", "background": "", "language": "en"},
        invalid_grade,
        "sum of scoring point scores (4) must equal criterion score (0)",
    )

    repair_payload = json.loads(completions.messages[1]["content"])
    assert repair_payload["invalid_grade"] == invalid_grade
    assert "must equal criterion score" in repair_payload["validation_error"]
    assert "final conclusions" in repair_payload["task"]
    assert "must literally include each matched rule_id" in repair_payload["task"]
    assert repair_payload["required_output"]["allowed_rule_ids"] == [
        "9.1.wrong_sigfig"
    ]
    evidence_contract = repair_payload["required_output"]["evidence_selections"]
    assert "server infers text/image type per ID" in evidence_contract
    assert "anchor_type: absence" in evidence_contract
    assert repaired["criteria_score"] == 4


def test_grading_repairs_malformed_json_response_once(monkeypatch):
    malformed = '{"criteria_score": 4 "score_reason": "missing comma"}'
    repaired = {
        "criteria_score": 4,
        "score_reason": "报告给出了结果，保留4分。",
        "target_section": "结果",
        "matched_scoring_points": [],
        "matched_deduction_rules": [],
        "scoring_point_scores": [
            {"point_id": "8.1", "score": 4, "reason": "报告结果满足8.1。"}
        ],
        "evidence_selections": [],
    }
    completions = _SequenceCompletions(
        [malformed, json.dumps(repaired, ensure_ascii=False)]
    )
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))

    result = grade.grade_single_criterion(
        "# 结果\n\n镁浓度为1.9 mg/L。\n",
        {
            "criteria_id": "8",
            "description": "报告水样镁浓度。",
            "original_description": "水样镁浓度结果",
            "max_score": 4,
            "few_shot_examples": [],
            "scoring_points": [{
                "point_id": "8.1",
                "summary": "镁浓度结果",
                "deduction_rules": [{
                    "rule_id": "8.1.missing_or_wrong",
                    "condition": "结果缺失或错误",
                    "deduct": 4,
                }],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        },
        {"title": "Rubric", "background": "", "language": "zh"},
    )

    repair_payload = json.loads(completions.calls[1]["messages"][1]["content"])
    assert len(completions.calls) == 2
    assert repair_payload["invalid_grade"] == {"raw_model_response": malformed}
    assert "invalid JSON" in repair_payload["validation_error"]
    assert result["criteria_score"] == 4


def test_model_evidence_selection_is_reconstructed_from_source(monkeypatch):
    submission = "# 原理\n\n定量基础：A = Kc，即吸光度与浓度成正比。\n"
    selected = next(
        entry
        for entry in grade.build_evidence_catalog(submission)
        if "A = Kc" in entry.source_text
    )
    completions = _CaptureCompletions(json.dumps({
        "criteria_score": 4,
        "file_original_content": "模型改写的内容，不应作为锚点",
        "target_section": "原理",
        "score_reason": "满足要求。",
        "evidence_selections": [{
            "anchor_type": "text",
            "evidence_id": selected.evidence_id,
        }],
        "scoring_point_scores": [{"point_id": "2.1", "score": 4}],
    }, ensure_ascii=False))
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))

    result = grade.grade_single_criterion(
        submission,
        {
            "criteria_id": "2",
            "description": "提及朗伯-比尔定律并给出公式。",
            "original_description": "朗伯-比尔定律与公式",
            "max_score": 4,
            "few_shot_examples": [],
            "scoring_points": [{
                "point_id": "2.1",
                "summary": "公式",
                "deduction_rules": [{"rule_id": "2.1.missing", "condition": "缺失", "deduct": 4}],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
        },
        {"title": "Rubric", "background": "", "language": "zh"},
    )

    assert result["evidence_anchors"][0]["text"] == selected.source_text
    assert selected.source_text in result["anchor_text"]
    assert result["anchor_text"] in submission
    assert "模型改写" not in result["anchor_text"]


def test_short_model_evidence_expands_to_neighboring_context():
    submission = (
        "# 实验报告\n\n"
        "专业：化学工程与工艺\n"
        "姓名：王金杰\n"
        "学号：3240101860\n"
        "实验名称：原子吸收光谱法测定饮用水中的镁含量\n"
        "\n"
        "一、实验原理\n"
        "正文内容。\n"
    )

    expanded = grade._expand_evidence_window(submission, "姓名：王金杰", min_chars=80)

    assert "姓名：王金杰" in expanded
    assert "专业：化学工程与工艺" in expanded
    assert "学号：3240101860" in expanded
    assert "实验名称：原子吸收光谱法测定饮用水中的镁含量" in expanded


def test_structured_scoring_points_are_rendered_and_matches_preserved(monkeypatch):
    completions = _CaptureCompletions(json.dumps({
        "criteria_score": 3,
        "file_original_content": "calculation omitted blank correction",
        "target_section": "calculation",
        "score_reason": "Earned 3 out of 4; triggered c1.p1/r1.",
        "matched_scoring_points": ["c1.p1"],
        "matched_deduction_rules": ["c1.p1.r1"],
    }))
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))

    result = grade.grade_single_criterion(
        "# Report\ncalculation omitted blank correction\n",
        {
            "criteria_id": "c1",
            "description": "Calculate magnesium concentration correctly.",
            "original_description": "calculation",
            "max_score": 4,
            "scoring_points": [
                {
                    "point_id": "c1.p1",
                    "summary": "Blank correction",
                    "full_credit_condition": "Uses blank-corrected absorbance.",
                    "deduction_rules": [
                        {"rule_id": "c1.p1.r1", "condition": "blank correction is omitted", "deduct": 1},
                    ],
                    "max_deduction": 1,
                    "aggregation": "max_triggered",
                }
            ],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
            "few_shot_examples": [],
        },
        {"title": "Rubric", "background": "", "language": "en"},
    )

    user_prompt = completions.messages[1]["content"]
    assert "Structured Scoring Points" in user_prompt
    assert "Point c1.p1" in user_prompt
    assert "rule_id=c1.p1.r1: Deduct 1" in user_prompt
    assert "Mode: sum_deductions_capped" in user_prompt
    assert result["matched_scoring_points"] == ["c1.p1"]
    assert result["matched_deduction_rules"] == ["c1.p1.r1"]


def test_image_evidence_is_grounded_from_text_only_description(monkeypatch):
    description = "A chart showing absorbance increases with concentration."
    submission = (
        "# Results\n\n"
        f"> **[Image]** {description}\n"
        "> *Source: images/p3_crop0.png*\n"
    )
    completions = _CaptureCompletions(json.dumps({
        "criteria_score": 4,
        "file_original_content": description,
        "target_section": "Results",
        "score_reason": "Earned 4 out of 4 from the chart trend.",
        "evidence_anchors": [{
            "anchor_type": "image",
            "description": description,
        }],
        "scoring_point_scores": [{"point_id": "c1.p1", "score": 4}],
    }))
    monkeypatch.setattr(grade, "get_chat_client", lambda provider: _FakeClient(completions))

    result = grade.grade_single_criterion(
        submission,
        {
            "criteria_id": "c1",
            "description": "Interpret the trend.",
            "original_description": "trend",
            "max_score": 4,
            "scoring_points": [{
                "point_id": "c1.p1",
                "summary": "Trend",
                "deduction_rules": [{"rule_id": "c1.p1.r1", "condition": "wrong", "deduct": 4}],
                "max_deduction": 4,
                "aggregation": "max_triggered",
            }],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 4},
            "few_shot_examples": [],
        },
        {"title": "Rubric", "background": "", "language": "en"},
    )

    assert result["evidence_anchors"] == [{
        "anchor_type": "image",
        "image_src": "images/p3_crop0.png",
        "description": description,
    }]
    assert "Grade from the description text" in completions.messages[1]["content"]


def test_legacy_quoted_image_block_becomes_image_anchor():
    description = "A diagram with a light source and detector."
    block = f"> **[Image]** {description}\n> *Source: images/p1_crop0.png*"
    result = image_evidence.normalize_model_image_evidence(
        block,
        {"file_original_content": block, "score_reason": "Supported by diagram."},
    )

    assert result["evidence_anchors"] == [{
        "anchor_type": "image",
        "image_src": "images/p1_crop0.png",
        "description": description,
    }]

    structured_legacy = image_evidence.normalize_model_image_evidence(
        block,
        {
            "evidence_anchors": [{"anchor_type": "text", "text": block}],
            "score_reason": "Supported by diagram.",
        },
    )
    assert structured_legacy["evidence_anchors"][0]["anchor_type"] == "image"
    assert structured_legacy["evidence_anchors"][0]["image_src"] == "images/p1_crop0.png"


def test_image_catalog_rejects_unsafe_and_external_sources():
    submission = (
        "> **[Image]** Traversal\n> *Source: images/../secret.png*\n\n"
        "> **[Image]** External\n> *Source: https://example.com/image.png*\n"
        "> **[Image]** Encoded traversal\n> *Source: images/%2e%2e/secret.png*\n"
        "> **[Image]** Double encoded traversal\n"
        "> *Source: images/%252e%252e/secret.png*\n"
        "> **[Image]** Protocol relative\n"
        "> *Source: //example.com/case-files/image.png*\n"
    )

    assert image_evidence.build_image_catalog(submission) == []


def test_standard_markdown_image_is_also_cataloged():
    assert image_evidence.build_image_catalog("![Calibration chart](images/chart.png)") == [{
        "image_src": "images/chart.png",
        "description": "Calibration chart",
        "source_block": "![Calibration chart](images/chart.png)",
    }]
