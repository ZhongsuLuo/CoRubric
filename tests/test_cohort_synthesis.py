from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

import pytest

from backend.app.global_view import synthesis


@pytest.fixture(autouse=True)
def clear_synthesis_cache():
    with synthesis._SYNTHESIS_LOCK:
        synthesis._SYNTHESIS_CACHE.clear()
    yield
    with synthesis._SYNTHESIS_LOCK:
        synthesis._SYNTHESIS_CACHE.clear()


def _ref(
    report_id: str,
    reason: str,
    anchor: str,
    *,
    event_id: str | None = None,
) -> Dict[str, Any]:
    return {
        "report_id": report_id,
        "event_id": event_id or f"event-{report_id}",
        "criterion_id": "1",
        "point_id": "1.p1",
        "loss": 2.0,
        "score_reason": reason,
        "evidence_anchors": [{"text": anchor}],
    }


def _synthesis_inputs(refs: list[Dict[str, Any]]) -> Dict[str, Any]:
    report_ids = [ref["report_id"] for ref in refs]
    return {
        "case_id": "cohort-synthesis-unit-case",
        "criteria_name": "criteria.json",
        "criterion_id": "1",
        "criteria_data": {
            "language": "zh",
            "criteria": [
                {
                    "criteria_id": "1",
                    "criteria_name": "数据表达",
                    "scoring_points": [{"point_id": "1.p1", "summary": "图表与单位"}],
                }
            ],
        },
        "criterion_summaries": [
            {
                "criterion_id": "1",
                "point_loss_summaries": [
                    {
                        "point_id": "1.p1",
                        "summary": "图表与单位",
                        "average_loss": 2.0,
                        "affected_report_ids": report_ids,
                        "refs": deepcopy(refs),
                    }
                ],
            }
        ],
        "projection_hash": "projection-hash-v1",
        "model_id": "test-model",
    }


class _StaticModel:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.calls = 0
        self.payload = None

    def synthesize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        self.payload = deepcopy(payload)
        return deepcopy(self.raw)


def test_synthesis_prompt_specifies_reason_clustering_and_grounding_contract():
    prompt = synthesis._SYNTHESIS_SYSTEM_PROMPT

    assert "BOTH score_reason and evidence_anchors" in prompt
    assert "overall mode" in prompt and "criterion + reason" in prompt
    assert "criterion mode" in prompt and "scoring point + reason" in prompt
    assert "at most four" in prompt
    assert "24 Chinese characters" in prompt and "8 English words" in prompt
    assert "multiple independent problems" in prompt
    assert "Never invent a report, criterion, scoring point, reason" in prompt
    assert "without putting metrics in the label" in prompt
    assert "Never put average_loss" in prompt
    assert "analysis" in prompt
    assert "adds interpretation beyond the chart" in prompt
    assert "In criterion mode" in prompt and "compare scoring points" in prompt
    assert "payload language field is authoritative" in prompt
    assert "never use English narrative words" in prompt
    assert "findings contains at most three" in prompt
    assert "MUST contain 2-4 distinct criterion_ids" in prompt
    assert "single-criterion patterns belong only in insights" in prompt
    assert "exactly three distinct cross-criterion themes" in prompt
    assert "Do not force a cross-criterion link" in prompt
    assert "short observable title" in prompt
    assert "2-sentence interpretive summary" in prompt


def test_qa_synthesis_clusters_two_reasons_for_one_scoring_point_and_deduplicates_reports():
    refs = [
        _ref("report-a", "单位缺失", "结果值后未标注单位"),
        _ref("report-b", "单位缺失", "表格数值未标注单位"),
        _ref("report-b", "单位缺失", "表格数值未标注单位"),
        _ref("report-c", "图表标题缺失", "图表上方没有标题"),
    ]

    result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(refs),
        qa_mode=True,
    )

    by_summary = {item["summary"]: item for item in result["insights"]}
    assert result["analysis"].startswith("从该评分标准内部看")
    assert set(by_summary) == {"单位缺失", "图表标题缺失"}
    assert by_summary["单位缺失"]["criterion_ids"] == ["1"]
    assert by_summary["单位缺失"]["point_ids"] == ["1.p1"]
    assert by_summary["单位缺失"]["report_ids"] == ["report-a", "report-b"]
    assert len(by_summary["单位缺失"]["supporting_refs"]) == 2
    assert by_summary["图表标题缺失"]["report_ids"] == ["report-c"]


def test_overall_findings_can_ground_one_conclusion_across_multiple_criteria():
    loss_items = [
        {
            "criterion_id": "1",
            "point_id": None,
            "average_loss": 2.0,
            "affected_report_ids": ["report-a"],
            "refs": [_ref("report-a", "数据处理步骤缺失", "数据处理过程不完整")],
        },
        {
            "criterion_id": "2",
            "point_id": None,
            "average_loss": 1.5,
            "affected_report_ids": ["report-b"],
            "refs": [_ref("report-b", "数据处理依据不足", "数据处理缺少来源说明")],
        },
    ]

    findings = synthesis._ground_findings(
        {
            "findings": [
                {
                    "title": "数据处理规范不足",
                    "summary": (
                        "数据处理步骤缺失与依据不足同时出现在多个评分维度。"
                        "重复模式说明结果可靠性受到共同的数据处理规范问题影响。"
                    ),
                    "criterion_ids": ["1", "2"],
                    "point_ids": [],
                    "report_ids": ["report-a", "report-b"],
                }
            ]
        },
        loss_items,
        [],
        language="zh",
        criterion_mode=False,
    )

    assert len(findings) == 1
    assert findings[0]["title"] == "数据处理规范不足"
    assert findings[0]["criterion_ids"] == ["1", "2"]
    assert findings[0]["report_ids"] == ["report-a", "report-b"]
    assert len(findings[0]["supporting_refs"]) == 2


def test_overall_findings_fall_back_to_grounded_insights_when_model_ids_are_invalid():
    grounded_insight = {
        "insight_id": "insight-1",
        "summary": "单位缺失",
        "criterion_ids": ["1"],
        "point_ids": [],
        "report_ids": ["report-a"],
        "supporting_refs": [_ref("report-a", "单位缺失", "结果未标注单位")],
    }

    findings = synthesis._ground_findings(
        {
            "findings": [
                {
                    "summary": "单位缺失：平均失分2.00分",
                    "criterion_ids": ["invented"],
                    "point_ids": [],
                    "report_ids": ["report-a"],
                }
            ]
        },
        [],
        [grounded_insight],
        language="zh",
        criterion_mode=False,
    )

    assert findings[0]["insight_id"] == "finding-1"
    assert findings[0]["title"] == "单位缺失"
    assert findings[0]["summary"].startswith("‘单位缺失’")


def test_overall_findings_reject_universal_claims_without_full_cohort_coverage():
    loss_items = [
        {
            "criterion_id": "1",
            "label": "数据处理",
            "average_loss": 2.0,
            "affected_report_ids": ["report-a", "report-b"],
            "refs": [
                _ref("report-a", "数据处理步骤缺失", "数据处理过程不完整"),
                _ref("report-b", "数据处理依据不足", "数据处理缺少来源说明"),
            ],
        }
    ]

    findings = synthesis._ground_findings(
        {
            "findings": [
                {
                    "title": "所有报告均缺少数据处理步骤",
                    "summary": (
                        "数据处理步骤缺失在报告中反复出现。"
                        "这一重复模式说明数据处理规范需要作为批次共同问题复核。"
                    ),
                    "criterion_ids": ["1"],
                    "point_ids": [],
                    "report_ids": ["report-a"],
                }
            ]
        },
        loss_items,
        [],
        language="zh",
        criterion_mode=False,
    )

    assert findings == []


def test_overall_findings_reject_single_criterion_cards_for_a_broad_cohort():
    loss_items = [
        {
            "criterion_id": str(index),
            "label": f"评分点{index}",
            "average_loss": 1.0,
            "affected_report_ids": [f"report-{index}"],
            "refs": [
                _ref(
                    f"report-{index}",
                    "数据处理步骤缺失",
                    "数据处理过程不完整",
                )
            ],
        }
        for index in range(1, 7)
    ]

    findings = synthesis._ground_findings(
        {
            "findings": [
                {
                    "title": "单一评分点问题",
                    "summary": (
                        "数据处理步骤缺失在一个评分维度中反复出现。"
                        "该表述没有形成跨评分点的批次主题，因此不应进入顶部聚合卡片。"
                    ),
                    "criterion_ids": ["1"],
                    "point_ids": [],
                    "report_ids": ["report-1"],
                }
            ]
        },
        loss_items,
        [
            {
                "insight_id": "insight-1",
                "summary": "数据处理步骤缺失",
                "criterion_ids": ["1"],
                "point_ids": [],
                "report_ids": ["report-1"],
                "supporting_refs": loss_items[0]["refs"],
            }
        ],
        language="zh",
        criterion_mode=False,
    )

    assert findings == []


def test_qa_synthesis_limits_each_scoring_point_to_four_reasons():
    reasons = ["单位缺失", "标题缺失", "图例缺失", "刻度缺失", "来源缺失"]
    refs = [
        _ref(
            f"report-{index}",
            reason,
            f"{reason}已在表格中观察到",
        )
        for index, reason in enumerate(reasons)
    ]

    result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(refs),
        qa_mode=True,
    )

    assert len(result["insights"]) == 4
    assert all(item["point_ids"] == ["1.p1"] for item in result["insights"])


def test_grounding_rejects_invented_report_point_criterion_and_reason():
    refs = [
        _ref("report-a", "单位缺失", "结果值后未标注单位"),
        _ref("report-b", "标题缺失", "图表上方没有标题"),
        _ref("report-c", "单位缺失", "表格数值未标注单位"),
    ]
    model = _StaticModel(
        {
            "insights": [
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a", "report-a"],
                },
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-c"],
                },
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["invented-report"],
                },
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["invented-point"],
                    "report_ids": ["report-b"],
                },
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["invented-criterion"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-b"],
                },
                {
                    "summary": "内容抄袭",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-b"],
                },
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a", "report-b"],
                },
            ]
        }
    )

    result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(refs),
        qa_mode=False,
        model=model,
    )

    assert len(result["insights"]) == 1
    insight = result["insights"][0]
    assert insight["summary"] == "单位缺失"
    assert insight["criterion_ids"] == ["1"]
    assert insight["point_ids"] == ["1.p1"]
    assert insight["report_ids"] == ["report-a", "report-c"]
    assert [ref["report_id"] for ref in insight["supporting_refs"]] == [
        "report-a",
        "report-c",
    ]


def test_insufficient_reason_evidence_falls_back_to_scope_label_without_metric():
    refs = [_ref("report-a", "", "")]
    refs[0]["evidence_anchors"] = []

    result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(refs),
        qa_mode=True,
    )

    assert len(result["insights"]) == 1
    insight = result["insights"][0]
    assert insight["summary"] == "图表与单位"
    assert len(insight["summary"]) <= 24
    assert insight["report_ids"] == ["report-a"]


def test_projection_hash_and_synthesis_cache_behavior_are_unchanged():
    refs = [_ref("report-a", "单位缺失", "结果值后未标注单位")]
    inputs = _synthesis_inputs(refs)
    first_model = _StaticModel(
        {
            "insights": [
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a"],
                }
            ]
        }
    )
    second_model = _StaticModel({"insights": []})

    first = synthesis.generate_cohort_synthesis(
        **inputs,
        qa_mode=False,
        model=first_model,
    )
    first["insights"][0]["summary"] = "mutated caller copy"
    second = synthesis.generate_cohort_synthesis(
        **inputs,
        qa_mode=False,
        model=second_model,
    )

    assert first_model.calls == 1
    assert second_model.calls == 0
    assert first_model.payload["projection_hash"] == "projection-hash-v1"
    assert second["projection_hash"] == "projection-hash-v1"
    assert second["insights"][0]["summary"] == "单位缺失"


def test_analysis_is_model_generated_and_uses_the_rubric_language():
    refs = [
        _ref("report-a", "单位缺失", "结果值后未标注单位"),
        _ref("report-b", "单位缺失", "表格数值未标注单位"),
    ]
    inputs = _synthesis_inputs(refs)
    inputs["criterion_id"] = None
    inputs["criterion_summaries"] = [
        {
            "criterion_id": "1",
            "average_loss": 2.0,
            "points": [
                {
                    "report_id": ref["report_id"],
                    "event_id": ref["event_id"],
                    "score": 8.0,
                    "max_score": 10.0,
                    "score_reason": ref["score_reason"],
                    "evidence_anchors": ref["evidence_anchors"],
                    "included_in_statistics": True,
                }
                for ref in refs
            ],
        }
    ]
    model = _StaticModel(
        {
            "analysis": (
                "综合来看，本批次的失分集中在结果表达的规范性上，影响范围较广，" "说明这是一项共性薄弱环节，而不是少数报告的孤立问题。"
            ),
            "insights": [
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": [],
                    "report_ids": ["report-a", "report-b"],
                }
            ],
        }
    )

    result = synthesis.generate_cohort_synthesis(
        **inputs,
        qa_mode=False,
        model=model,
    )

    assert model.payload["language"] == "zh"
    assert result["language"] == "zh"
    assert result["analysis"].startswith("综合来看")
    assert "单位缺失（2/2" not in result["analysis"]
    assert result["findings"][0]["title"] == "单位缺失"
    assert result["findings"][0]["summary"].startswith("‘单位缺失’")
    assert result["findings"][0]["insight_id"] == "finding-1"


def test_analysis_rejects_text_in_the_wrong_language():
    assert (
        synthesis._ground_analysis(
            {"analysis": "This analysis is in the wrong language."},
            language="zh",
        )
        == ""
    )
    assert (
        synthesis._ground_analysis(
            {"analysis": "这段分析使用了错误语言。"},
            language="en",
        )
        == ""
    )


def test_chinese_analysis_localizes_common_ui_terms_and_rejects_other_english_prose():
    normalized = synthesis._ground_analysis(
        {
            "analysis": (
                "本批次的计算规范是 cohort 层面最明显的共性弱点，"
                "但 AAS 与 UV-Vis 等技术术语可以保留。"
            )
        },
        language="zh",
    )

    assert normalized == (
        "本批次的计算规范是批次层面最明显的共性弱点，"
        "但 AAS 与 UV-Vis 等技术术语可以保留。"
    )
    assert (
        synthesis._ground_analysis(
            {"analysis": "本批次存在 systematic weakness，需要重点改进。"},
            language="zh",
        )
        == ""
    )
    assert (
        synthesis._ground_analysis(
            {"analysis": "本批次存在 SYSTEMATIC WEAKNESS，需要重点改进。"},
            language="zh",
        )
        == ""
    )


def test_insight_language_guard_rejects_prose_but_preserves_allowlisted_technical_terms():
    prose_refs = [_ref("report-a", "SYSTEMATIC WEAKNESS", "结果值后未标注单位")]
    prose_model = _StaticModel(
        {
            "analysis": "本批次的失分集中于图表表达规范。",
            "insights": [
                {
                    "summary": "SYSTEMATIC WEAKNESS",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a"],
                }
            ],
        }
    )

    prose_result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(prose_refs),
        qa_mode=False,
        model=prose_model,
    )

    assert prose_result["insights"][0]["summary"] == "图表与单位"

    technical_refs = [_ref("report-a", "AAS图表缺少mL单位", "AAS图表缺少mL单位")]
    technical_model = _StaticModel(
        {
            "analysis": "本批次的 AAS 图表缺少 mL 单位。",
            "insights": [
                {
                    "summary": "AAS图表缺少mL单位",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a"],
                }
            ],
        }
    )
    technical_inputs = _synthesis_inputs(technical_refs)
    technical_inputs["projection_hash"] = "projection-hash-technical"

    technical_result = synthesis.generate_cohort_synthesis(
        **technical_inputs,
        qa_mode=False,
        model=technical_model,
    )

    assert technical_result["analysis"] == "本批次的 AAS 图表缺少 mL 单位。"
    assert technical_result["insights"][0]["summary"] == "AAS图表缺少mL单位"


def test_qa_synthesis_uses_english_when_the_rubric_language_is_english():
    refs = [
        _ref("report-a", "Missing chart units", "The chart axis has no unit"),
        _ref("report-b", "Missing chart units", "The result table has no unit"),
    ]
    inputs = _synthesis_inputs(refs)
    inputs["criteria_data"]["language"] = "en"
    inputs["criteria_data"]["criteria"][0]["criteria_name"] = "Data presentation"
    inputs["criteria_data"]["criteria"][0]["scoring_points"][0]["summary"] = (
        "Charts and units"
    )
    inputs["criterion_summaries"][0]["point_loss_summaries"][0]["summary"] = (
        "Charts and units"
    )

    result = synthesis.generate_cohort_synthesis(**inputs, qa_mode=True)

    assert result["language"] == "en"
    assert result["analysis"].startswith("Within this criterion")
    assert not synthesis._CJK_PATTERN.search(result["analysis"])
    assert result["insights"][0]["summary"] == "Missing chart units"


def test_english_synthesis_rejects_a_chinese_insight_and_uses_the_english_fallback():
    refs = [_ref("report-a", "单位缺失", "表格数值未标注单位")]
    inputs = _synthesis_inputs(refs)
    inputs["criteria_data"]["language"] = "en"
    inputs["criteria_data"]["criteria"][0]["criteria_name"] = "Data presentation"
    inputs["criteria_data"]["criteria"][0]["scoring_points"][0]["summary"] = (
        "Charts and units"
    )
    inputs["criterion_summaries"][0]["point_loss_summaries"][0]["summary"] = (
        "Charts and units"
    )
    model = _StaticModel(
        {
            "analysis": "Within this criterion, losses are concentrated in chart labeling.",
            "insights": [
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a"],
                }
            ],
        }
    )

    result = synthesis.generate_cohort_synthesis(
        **inputs,
        qa_mode=False,
        model=model,
    )

    assert result["insights"][0]["summary"] == "Charts and units"
    assert not synthesis._CJK_PATTERN.search(result["insights"][0]["summary"])


def test_wrong_language_model_analysis_falls_back_to_a_grounded_same_language_paragraph():
    refs = [_ref("report-a", "单位缺失", "结果值后未标注单位")]
    model = _StaticModel(
        {
            "analysis": "This analysis uses the wrong language.",
            "insights": [
                {
                    "summary": "单位缺失",
                    "criterion_ids": ["1"],
                    "point_ids": ["1.p1"],
                    "report_ids": ["report-a"],
                }
            ],
        }
    )

    result = synthesis.generate_cohort_synthesis(
        **_synthesis_inputs(refs),
        qa_mode=False,
        model=model,
    )

    assert result["analysis"].startswith("从该评分标准内部看")
    assert "wrong language" not in result["analysis"]
    assert "仅“图表与单位”出现平均失分" in result["analysis"]
    assert "其余评分点" not in result["analysis"]
    assert result["insights"][0]["summary"] == "单位缺失"


def test_fallback_analysis_does_not_choose_a_winner_when_top_losses_are_tied():
    result = synthesis._fallback_analysis(
        {"criterion_id": None, "language": "zh"},
        [
            {"criterion_id": "1", "label": "数据表达", "average_loss": 2.0},
            {"criterion_id": "2", "label": "原理阐述", "average_loss": 2.0},
            {"criterion_id": "3", "label": "结论分析", "average_loss": 1.0},
        ],
    )

    assert "最高平均失分由2个评分维度共同出现" in result
    assert "数据表达" in result and "原理阐述" in result
    assert "单一最突出项" in result
    assert "失分是否集中" in result


def test_fallback_analysis_only_calls_a_unique_top_loss_prominent_when_clearly_separated():
    close = synthesis._fallback_analysis(
        {"criterion_id": None, "language": "zh"},
        [
            {"criterion_id": "1", "label": "数据表达", "average_loss": 2.0},
            {"criterion_id": "2", "label": "原理阐述", "average_loss": 1.8},
        ],
    )
    separated = synthesis._fallback_analysis(
        {"criterion_id": None, "language": "zh"},
        [
            {"criterion_id": "1", "label": "数据表达", "average_loss": 2.0},
            {"criterion_id": "2", "label": "原理阐述", "average_loss": 1.0},
        ],
    )

    assert "差距有限" in close
    assert "不足以判断失分明显集中" in close
    assert "明显高于次高项" in separated
    assert "更突出的薄弱环节" in separated
