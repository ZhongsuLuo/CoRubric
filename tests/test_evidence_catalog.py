from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import alignment
from backend.app.grade_validation import validate_evidence_anchors
from backend.app.grading import auto_grade
from backend.app.grading.evidence_catalog import (
    EvidenceSelectionError,
    build_evidence_catalog,
    render_catalogued_report,
    resolve_evidence_selections,
)


def _entry_containing(catalog, text):
    return next(entry for entry in catalog if text in entry.source_text)


def test_catalog_resolves_text_and_image_to_exact_report_source():
    report = (
        "# 结果\n\n"
        "由此可得出最佳 Sr(II) 加入量为 0.5mL。\n\n"
        "> **[Image]** A chart showing a maximum at 0.5 mL.\n"
        "> *Source: images/p5_crop0.png*\n"
    )
    catalog = build_evidence_catalog(report)
    text = _entry_containing(catalog, "最佳 Sr(II)")
    image = next(entry for entry in catalog if entry.anchor_type == "image")

    anchors = resolve_evidence_selections(
        report,
        [
            {"anchor_type": "text", "evidence_ids": [text.evidence_id]},
            {"anchor_type": "image", "evidence_id": image.evidence_id},
        ],
    )

    assert anchors[0]["text"] == text.source_text
    assert report[anchors[0]["start_offset"] : anchors[0]["end_offset"]] == text.source_text
    assert anchors[1] == {
        "anchor_type": "image",
        "image_src": "images/p5_crop0.png",
        "description": "A chart showing a maximum at 0.5 mL.",
    }
    assert validate_evidence_anchors(report, anchors) == anchors


def test_catalog_entry_type_overrides_incorrect_model_anchor_type():
    report = (
        "# 结果\n\n结论文本。\n\n"
        "> **[Image]** 结果数据表。\n"
        "> *Source: images/result-table.png*\n"
    )
    catalog = build_evidence_catalog(report)
    text = _entry_containing(catalog, "结论文本")
    image = next(entry for entry in catalog if entry.anchor_type == "image")

    anchors = resolve_evidence_selections(
        report,
        [
            {"anchor_type": "image", "evidence_id": text.evidence_id},
            {"anchor_type": "text", "evidence_id": image.evidence_id},
        ],
    )

    assert anchors[0]["anchor_type"] == "text"
    assert anchors[0]["text"] == text.source_text
    assert anchors[1] == {
        "anchor_type": "image",
        "image_src": "images/result-table.png",
        "description": "结果数据表。",
    }
    assert validate_evidence_anchors(report, anchors) == anchors


def test_mixed_catalog_ids_resolve_independently_without_anchor_type():
    report = (
        "# 结果\n\n由表格可确定最佳加入量。\n\n"
        "> **[Image]** 加入量与吸光度数据表。\n"
        "> *Source: images/addition-table.png*\n"
    )
    catalog = build_evidence_catalog(report)
    text = _entry_containing(catalog, "最佳加入量")
    image = next(entry for entry in catalog if entry.anchor_type == "image")

    anchors = resolve_evidence_selections(
        report,
        [
            {
                "anchor_type": "text",
                "evidence_ids": [
                    image.evidence_id,
                    text.evidence_id,
                    image.evidence_id,
                ],
            }
        ],
    )

    assert [anchor["anchor_type"] for anchor in anchors] == ["image", "text"]
    assert anchors[0]["image_src"] == image.image_src
    assert anchors[1]["text"] == text.source_text
    assert validate_evidence_anchors(report, anchors) == anchors


def test_catalog_keeps_repeated_text_occurrences_distinct():
    report = "重复证据。\n\n中间内容。\n\n重复证据。"
    repeated = [
        entry for entry in build_evidence_catalog(report) if entry.source_text == "重复证据。"
    ]

    anchors = resolve_evidence_selections(
        report,
        [{"evidence_id": repeated[1].evidence_id}],
    )

    assert anchors[0]["occurrence"] == 1
    assert validate_evidence_anchors(report, anchors)[0]["occurrence"] == 1


def test_validation_accepts_browser_rendered_text_from_markdown_table():
    report = (
        "| 浓度 (mg/L) | 吸光度 |\n"
        "|---:|---:|\n"
        "| 0.000 | 0.365 |\n"
        "| 0.200 | 0.640 |\n"
    )
    anchors = [
        {"anchor_type": "text", "text": "0.000 0.365", "occurrence": 0}
    ]

    assert validate_evidence_anchors(report, anchors) == anchors


def test_validation_accepts_browser_rendered_text_across_markdown_formatting():
    report = "**姓名**: AL MULFI, NAWAF\n**学号**: 3240300139"
    anchors = [
        {
            "anchor_type": "text",
            "text": "姓名: AL MULFI, NAWAF 学号: 3240300139",
            "occurrence": 0,
        }
    ]

    assert validate_evidence_anchors(report, anchors) == anchors


def test_rendered_markdown_validation_preserves_occurrence_checks():
    report = "| 左列 | 右列 |\n|---|---|\n| A | B |\n| A | B |"
    second = [{"anchor_type": "text", "text": "A B", "occurrence": 1}]
    missing = [{"anchor_type": "text", "text": "A B", "occurrence": 2}]

    assert validate_evidence_anchors(report, second) == second
    with pytest.raises(ValueError, match="occurrence is not present"):
        validate_evidence_anchors(report, missing)


def test_rendered_markdown_fallback_still_rejects_unrelated_text():
    report = "| 浓度 | 吸光度 |\n|---|---|\n| 0.000 | 0.365 |"

    with pytest.raises(ValueError, match="could not be located"):
        validate_evidence_anchors(
            report,
            [{"anchor_type": "text", "text": "0.000 0.999", "occurrence": 0}],
        )


def test_catalog_rejects_unknown_id_without_nearby_fallback():
    report = "# 原理\n\nA = Kc，即吸光度与浓度成正比。"

    with pytest.raises(EvidenceSelectionError, match="unknown evidence id"):
        resolve_evidence_selections(
            report, [{"anchor_type": "text", "evidence_id": "T9999"}]
        )


@pytest.mark.parametrize(
    "selections, error",
    [
        ([], "selections are required"),
        ([None], "must be an object or identifier"),
        ([{}], "requires evidence_id"),
        ([{"evidence_id": ""}], "requires evidence_id"),
        ([{"evidence_ids": []}], "requires evidence_id"),
    ],
)
def test_catalog_rejects_empty_or_malformed_ordinary_selections(selections, error):
    with pytest.raises(EvidenceSelectionError, match=error):
        resolve_evidence_selections("# 原理\n\n报告原文。", selections)


def test_auto_grade_prefers_selection_and_never_falls_back_to_legacy_text():
    report = "# 原理\n\nA = Kc，即吸光度与浓度成正比。"
    selected = _entry_containing(build_evidence_catalog(report), "A = Kc")

    anchors = auto_grade._ground_model_evidence_anchors(
        report,
        {
            "evidence_selections": [
                {"anchor_type": "text", "evidence_id": selected.evidence_id}
            ],
            "evidence_anchors": [
                {"anchor_type": "text", "text": "模型改写，不在报告中"}
            ],
            "anchor_text": "# 原理",
        },
    )
    assert anchors[0]["text"] == selected.source_text

    with pytest.raises(alignment.ModelEvidenceUnavailable, match="unknown evidence id"):
        auto_grade._ground_model_evidence_anchors(
            report,
            {
                "evidence_selections": [
                    {"anchor_type": "text", "evidence_id": "T9999"}
                ],
                "anchor_text": "# 原理",
            },
        )

    with pytest.raises(
        alignment.ModelEvidenceUnavailable, match="selections are required"
    ):
        auto_grade._ground_model_evidence_anchors(
            report,
            {"evidence_selections": [], "anchor_text": "# 原理"},
        )


def test_auto_grade_uses_catalog_type_and_never_falls_back_to_model_text():
    report = (
        "# 结果\n\n服务器原始结论。\n\n"
        "> **[Image]** 原始数据表。\n"
        "> *Source: images/source-table.png*\n"
    )
    image = next(
        entry for entry in build_evidence_catalog(report) if entry.anchor_type == "image"
    )

    anchors = auto_grade._ground_model_evidence_anchors(
        report,
        {
            "evidence_selections": [
                {
                    "anchor_type": "text",
                    "evidence_id": image.evidence_id,
                    "image_src": "images/model-invented.png",
                    "description": "模型伪造的图片描述",
                    "text": "模型伪造的文本",
                }
            ],
            "evidence_anchors": [
                {"anchor_type": "text", "text": "模型生成的虚假证据"}
            ],
            "anchor_text": "服务器原始结论。",
            "file_original_content": "模型改写的附近文本",
        },
    )

    assert anchors == [
        {
            "anchor_type": "image",
            "image_src": "images/source-table.png",
            "description": "原始数据表。",
        }
    ]


def test_absence_selection_resolves_only_catalog_or_document_boundary():
    report = "# 思考题\n\n没有回答。"
    entry = build_evidence_catalog(report)[0]

    anchor = resolve_evidence_selections(
        report,
        [
            {
                "anchor_type": "absence",
                "expected_location_id": entry.evidence_id,
                "missing_item": "干扰因素与消除方法",
            }
        ],
    )[0]
    assert anchor["expected_location"] == entry.source_text
    assert validate_evidence_anchors(report, [anchor]) == [anchor]

    boundary = resolve_evidence_selections(
        report,
        [
            {
                "anchor_type": "absence",
                "expected_location_id": "document:end",
                "missing_item": "干扰因素与消除方法",
            }
        ],
    )[0]
    assert boundary["expected_location"] == "document:end"


@pytest.mark.parametrize(
    "selection, error",
    [
        (
            {"anchor_type": "absence", "expected_location_id": "document:end"},
            "requires missing_item",
        ),
        (
            {"anchor_type": "absence", "missing_item": "实验结论"},
            "unknown evidence id.*<empty>",
        ),
        (
            {
                "anchor_type": "absence",
                "expected_location_id": "T9999",
                "missing_item": "实验结论",
            },
            "unknown evidence id.*T9999",
        ),
    ],
)
def test_malformed_absence_selection_is_rejected(selection, error):
    with pytest.raises(EvidenceSelectionError, match=error):
        resolve_evidence_selections("# 结果\n\n没有结论。", [selection])


def test_wang_jinjie_failed_criteria_resolve_from_catalog():
    report_path = Path(
        "backend/data/case/case 1/markdown/王金杰/王金杰_text_only.md"
    )
    if not report_path.exists():
        pytest.skip("local regression fixture is not available")
    report = report_path.read_text(encoding="utf-8")
    catalog = build_evidence_catalog(report)
    expected = {
        "C2": "定量基础：A = Kc，即吸光度与浓度成正比。",
        "C7": "由此可得出最佳 Sr(II) 加入量为 0.5mL。",
        "C15": "（6）原子吸收主要干扰和克服方法",
    }

    for label, source_text in expected.items():
        entry = _entry_containing(catalog, source_text)
        anchors = resolve_evidence_selections(
            report,
            [{"anchor_type": "text", "evidence_id": entry.evidence_id}],
        )
        assert source_text in anchors[0]["text"], label
        assert validate_evidence_anchors(report, anchors) == anchors

    c7_text = _entry_containing(
        catalog, "最佳 Sr(II) 加入量为 0.5mL"
    )
    c7_anchors = resolve_evidence_selections(
        report,
        [
            {
                "anchor_type": "text",
                "evidence_ids": ["I0002", c7_text.evidence_id],
            }
        ],
    )
    assert [anchor["anchor_type"] for anchor in c7_anchors] == ["image", "text"]
    assert c7_anchors[0]["image_src"] == "images/p4_crop0.png"
    assert "最佳 Sr(II) 加入量为 0.5mL" in c7_anchors[1]["text"]
    assert validate_evidence_anchors(report, c7_anchors) == c7_anchors

    annotated = render_catalogued_report(catalog)
    assert "[Evidence T" in annotated
    assert "[Evidence I" in annotated
