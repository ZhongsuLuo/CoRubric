import json
from copy import deepcopy
from pathlib import Path

from backend.app.schemas import CriteriaSchema
from backend.app.config import ATOMIC_ABSORPTION_CRITERIA_FILE


BASELINE_NAME = ATOMIC_ABSORPTION_CRITERIA_FILE
LEGACY_BASELINE_NAME = "“原子吸收光谱法测定饮用水中的镁含量”实验评分标准_structured_sample.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "tests/fixtures/rubrics" / BASELINE_NAME


def test_atomic_absorption_baseline_is_a_valid_structured_rubric():
    baseline = CriteriaSchema.model_validate(
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    )

    assert baseline.title == "“原子吸收光谱法测定饮用水中的镁含量”实验评分标准"
    assert baseline.criteria


def test_beer_lambert_baseline_requires_both_law_and_formula():
    baseline = CriteriaSchema.model_validate(
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    )
    criterion = next(item for item in baseline.criteria if item.criteria_id == "2")
    point = next(item for item in criterion.scoring_points if item.point_id == "2.1")

    assert "必须同时" in criterion.description
    assert "必须同时" in point.full_credit_condition
    assert "定律与公式必须同时出现" in (point.deduction_rules[0].note or "")
    assert "只要说出定律或者公式" not in json.dumps(
        criterion.model_dump(mode="json"), ensure_ascii=False
    )

    for example in criterion.few_shot_examples:
        assert "朗伯-比" in example.example_input
        assert "=" in example.example_input
        assert example.example_output_score == criterion.max_score


def test_atomic_absorption_v2_uses_balanced_semantic_categories_and_preserves_legacy():
    current = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    legacy_path = BASELINE_PATH.with_name(LEGACY_BASELINE_NAME)
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))

    current_without_sections = deepcopy(current)
    legacy_without_sections = deepcopy(legacy)
    for criterion in current_without_sections["criteria"]:
        criterion.pop("section", None)
    for criterion in legacy_without_sections["criteria"]:
        criterion.pop("section", None)

    assert current_without_sections == legacy_without_sections
    counts = {}
    for criterion in current["criteria"]:
        counts[criterion["section"]] = counts.get(criterion["section"], 0) + 1
    assert counts == {
        "报告完整性与表达规范": 2,
        "理论知识与概念准确性": 4,
        "数据计算与结果分析": 4,
        "实验方法、仪器与安全规范": 5,
    }
    assert all(not section.startswith(("一、", "二、", "三、")) for section in counts)
