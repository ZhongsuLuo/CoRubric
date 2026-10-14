import json

from fastapi.testclient import TestClient

from backend.app import alignment, main, services


def test_delete_appends_history_and_removes_effective_grade(tmp_path, monkeypatch):
    case_root = tmp_path / "case"
    criteria_root = tmp_path / "criteria"
    report_dir = case_root / "case-a" / "markdown" / "report-1"
    report_dir.mkdir(parents=True)
    (report_dir / "report-1.md").write_text("# report\n\nevidence", encoding="utf-8")
    (report_dir / "report-1_text_only.md").write_text("# report\n\nevidence", encoding="utf-8")
    criteria_root.mkdir()
    (criteria_root / "rubric.json").write_text(json.dumps({
        "schema_version": "structured-rubric-v1",
        "title": "Rubric", "background": "", "language": "en", "base_score": 20,
        "criteria": [{
            "criteria_id": "1", "criteria_name": "Accuracy", "description": "Accuracy", "original_description": "Accuracy", "max_score": 5, "section": "Methods", "few_shot_examples": [],
            "scoring_points": [{"point_id": "1.p1", "summary": "Accuracy", "deduction_rules": [{"rule_id": "1.p1.r1", "condition": "The method is inaccurate", "deduct": 5}], "max_deduction": 5, "aggregation": "max_triggered"}],
            "scoring_aggregation": {"mode": "sum_deductions_capped", "cap": 5},
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(alignment, "CASE_ROOT", case_root)
    monkeypatch.setattr(alignment, "CRITERIA_DIR", criteria_root)
    monkeypatch.setattr(services, "CASE_ROOT", case_root)
    monkeypatch.setattr(main, "CASE_ROOT", case_root)
    client = TestClient(main.app)
    grade = {
        "criteria_id": "1",
        "criteria_score": 3,
        "score_reason": "teacher decision",
        "anchor_text": "evidence",
        "scoring_point_scores": [{"point_id": "1.p1", "score": 3}],
        "criteria_file_name": "rubric.json",
    }
    assert client.post("/cases/case-a/reports/report-1/grade", json=grade).status_code == 200
    response = client.request("DELETE", "/cases/case-a/reports/report-1/grade", json={"criteria_id": "1"})
    assert response.status_code == 200
    assert response.json()["data"]["result"]["criteria"] == []
    events = json.loads((case_root / "case-a" / "alignment" / "events.json").read_text())
    assert [event["event_type"] for event in events] == ["grade_authored", "grade_deleted"]
    assert events[0]["authored_grade"]["criteria_score"] == 3
