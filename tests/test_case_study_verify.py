from scripts.case_study_verify import anchor_is_grounded


def test_rejects_ambiguous_or_unrelated_teacher_text():
    source = "实验原理\nA=Kc\n实验结果\n1.5mg/L\n另一次1.5mg/L"
    assert anchor_is_grounded({"anchor_type": "text", "text": "A=Kc"}, source)
    assert not anchor_is_grounded({"anchor_type": "text", "text": "1.5mg/L"}, source)
    assert not anchor_is_grounded({"anchor_type": "text", "text": "A=-Kc"}, source)
    assert not anchor_is_grounded({"anchor_type": "text", "text": ""}, source)


def test_absence_requires_real_section_and_explicit_missing_item():
    source = "## 实验结果与分析\n只有数据，没有最优体积结论。"
    anchor = {"anchor_type": "absence", "expected_location": "实验结果与分析",
              "missing_item": "最佳Sr加入量结论"}
    assert anchor_is_grounded(anchor, source)
    assert not anchor_is_grounded({**anchor, "expected_location": "不存在的相似章节"}, source)
    assert not anchor_is_grounded({**anchor, "missing_item": ""}, source)
