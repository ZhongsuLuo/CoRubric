from pathlib import Path

from backend.app import services, storage


def test_list_case_reports_uses_natural_filename_order(tmp_path: Path, monkeypatch):
    case_root = tmp_path / "case"
    markdown_root = case_root / "case 1" / "markdown"
    for report_name in ("10 Student", "02 Student", "01 Student"):
        report_dir = markdown_root / report_name
        report_dir.mkdir(parents=True)
        (report_dir / f"{report_name}.md").write_text(report_name, encoding="utf-8")

    monkeypatch.setattr(services, "CASE_ROOT", str(case_root))

    reports = services.list_case_reports("case 1")

    assert [report["name"] for report in reports] == [
        "01 Student",
        "02 Student",
        "10 Student",
    ]


def test_list_cases_only_exposes_configured_demo_cases(tmp_path: Path, monkeypatch):
    case_root = tmp_path / "case"
    for case_id in ("case 2", "archived-case", "case 1"):
        (case_root / case_id).mkdir(parents=True)

    monkeypatch.setattr(services, "CASE_ROOT", str(case_root))
    monkeypatch.setattr(services, "VISIBLE_CASE_IDS", ("case 1", "case 2"))

    assert services.list_cases() == ["case 1", "case 2"]


def test_criteria_list_only_exposes_the_selected_case_rubric(tmp_path: Path, monkeypatch):
    criteria_root = tmp_path / "criteria"
    criteria_root.mkdir()
    for file_name in ("case-1.json", "case-2.json", "historical.json"):
        (criteria_root / file_name).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(storage, "CRITERIA_DIR", criteria_root)
    monkeypatch.setattr(
        storage,
        "CASE_CRITERIA_FILES",
        {"case 1": "case-1.json", "case 2": "case-2.json"},
    )

    assert storage.criteria_list("case 1") == ["case-1.json"]
    assert storage.criteria_list("case 2") == ["case-2.json"]
    assert storage.criteria_list("archived-case") == []
