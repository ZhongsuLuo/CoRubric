from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import alignment, implicit_grading, main, services
from scripts.seed_qa_business_case import (
    QA_CASE_ID,
    QA_CRITERIA_FILE,
    QA_LARGE_CASE_ID,
    QA_READY_CASE_ID,
    QA_SCALE_CASES,
    seed_qa_case,
)


@pytest.fixture()
def qa_fixture(tmp_path, monkeypatch):
    case_root = tmp_path / "case"
    criteria_dir = tmp_path / "criteria"
    manifest = seed_qa_case(case_root, criteria_dir)
    for module in (alignment, services, main, implicit_grading):
        monkeypatch.setattr(module, "CASE_ROOT", case_root)
        monkeypatch.setattr(module, "CRITERIA_DIR", criteria_dir)
    return manifest


def test_qa_fixture_global_aggregates_match_business_expectations(qa_fixture):
    summary = alignment.alignment_summary(QA_CASE_ID, criteria_file_name=QA_CRITERIA_FILE)
    by_id = {item["criterion_id"]: item for item in summary["criterion_global_summaries"]}

    criterion1 = by_id["1"]
    assert criterion1["total_reports"] == 8
    assert criterion1["scored_count"] == 5
    assert criterion1["statistical_count"] == 4
    assert criterion1["average_score"] == pytest.approx(8.375)
    assert criterion1["average_loss"] == pytest.approx(1.625)
    assert criterion1["teacher_count"] == 3
    assert criterion1["model_count"] == 2
    assert criterion1["conflict_count"] == 1
    assert criterion1["eligible_report_names"] == []
    assert criterion1["retryable_report_names"] == ["QA-06-failed"]
    assert criterion1["job_counts"] == {
        "pending": 1,
        "running": 0,
        "done": 2,
        "abstained": 1,
        "failed": 1,
        "stale": 0,
    }
    points = {item["report_name"]: item for item in criterion1["points"]}
    assert points["QA-01-teacher-conflict"]["author"] == "teacher"
    assert points["QA-01-teacher-conflict"]["conflict"] is True
    assert points["QA-01-teacher-conflict"]["anchor_text"] == "可定位的证据"
    assert points["QA-03-model-accepted"]["author"] == "model"
    assert points["QA-03-model-accepted"]["acceptance_status"] == "accepted_model"
    assert points["QA-03-model-accepted"]["anchor_text"] == "可定位的证据"
    assert points["QA-05-manual-exception"]["acceptance_status"] == "manual_exception"
    assert points["QA-05-manual-exception"]["anchor_text"] == "教师保留 6 分"
    assert "QA-07-abstained" not in points
    assert points["QA-01-teacher-conflict"]["included_in_statistics"] is False
    assert points["QA-03-model-accepted"]["scoring_point_scores"] == [
        {"point_id": "1.p1", "score": 8.5, "max_score": 10}
    ]
    point_loss = criterion1["point_loss_summaries"][0]
    assert point_loss["point_id"] == "1.p1"
    assert point_loss["covered_report_count"] == 4
    assert point_loss["affected_report_count"] == 3

    criterion2 = by_id["2"]
    assert criterion2["scored_count"] == 3
    assert criterion2["average_score"] == pytest.approx(4.5)
    assert criterion2["eligible_count"] == 5
    criterion2_points = {item["report_name"]: item for item in criterion2["points"]}
    assert criterion2_points["QA-02-teacher-clean"]["anchor_text"] == "平均值"
    assert criterion2_points["QA-03-model-accepted"]["anchor_text"] == "校准图"
    assert criterion2_points["QA-03-model-accepted"]["acceptance_status"] == "model_only"

    criterion3 = by_id["3"]
    assert criterion3["scored_count"] == 1
    assert criterion3["model_count"] == 0
    assert criterion3["migration_conflict_count"] == 0

    criterion4 = by_id["4"]
    assert criterion4["scored_count"] == 0
    assert criterion4["average_score"] is None
    assert criterion4["eligible_count"] == 8


def test_qa_fixture_alignment_api_and_report_projection(qa_fixture):
    client = TestClient(main.app)
    response = client.get(
        f"/cases/{QA_CASE_ID}/alignment",
        params={"criteria_file_name": QA_CRITERIA_FILE},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["open_conflicts"]) == 1
    score_conflict = data["open_conflicts"][0]
    assert score_conflict["conflict_type"] == "teacher_teacher"
    assert [item["report_name"] for item in score_conflict["suspect_events"]] == ["QA-01-teacher-conflict"]
    assert [item["report_name"] for item in score_conflict["supporting_events"]] == ["QA-02-teacher-clean"]
    assert len(score_conflict["teacher_event_ids"]) == 2

    report = client.get(
        f"/cases/{QA_CASE_ID}/reports/QA-03-model-accepted",
        params={"criteria_file_name": QA_CRITERIA_FILE},
    )
    assert report.status_code == 200, report.text
    result = report.json()["data"]["result"]
    criterion1 = next(item for item in result["criteria"] if item["criteria_id"] == "1")
    assert criterion1["graded_by"] == "user"
    assert criterion1["provenance"]["grader_type"] == "model"
    assert criterion1["acceptance_status"] == "accepted_model"


def test_qa_large_case_exposes_all_reports_without_truncation(qa_fixture):
    summary = alignment.alignment_summary(QA_LARGE_CASE_ID, criteria_file_name=QA_CRITERIA_FILE)
    items = summary["criterion_global_summaries"]
    assert len(items) == 4
    assert all(item["total_reports"] == 51 for item in items)
    assert all(item["scored_count"] == 0 for item in items)
    assert all(item["eligible_count"] == 51 for item in items)


def test_qa_fixture_exposes_ready_blocked_and_scale_cases(qa_fixture):
    blocked = alignment.alignment_summary(QA_CASE_ID, criteria_file_name=QA_CRITERIA_FILE)
    assert blocked["global_readiness"]["allowed"] is False
    assert any(
        item["kind"] == "repair_required"
        for item in blocked["global_readiness"]["blockers"]
    )

    ready = alignment.alignment_summary(QA_READY_CASE_ID, criteria_file_name=QA_CRITERIA_FILE)
    assert ready["global_readiness"] == {
        "allowed": True,
        "blockers": [],
        "blocker_count": 0,
    }
    assert len(ready["global_report_summaries"]) == 8
    conflict_report = next(
        item
        for item in ready["global_report_summaries"]
        if item["report_name"] == "QA-01-teacher-conflict"
    )
    assert conflict_report["conflict_count"] == 0
    assert conflict_report["partial"] is True
    assert conflict_report["provisional"] is True
    assert conflict_report["score"] == pytest.approx(97.0)

    for report_count, case_id in QA_SCALE_CASES.items():
        summary = alignment.alignment_summary(case_id, criteria_file_name=QA_CRITERIA_FILE)
        assert len(summary["global_report_summaries"]) == report_count
        assert all(
            item["total_reports"] == report_count
            for item in summary["criterion_global_summaries"]
        )


def test_cohort_synthesis_is_grounded_and_versioned(qa_fixture, monkeypatch):
    monkeypatch.setenv("GLOBAL_SYNTHESIS_QA", "1")
    client = TestClient(main.app)
    response = client.post(
        f"/cases/{QA_READY_CASE_ID}/cohort-synthesis",
        json={
            "criteria_file_name": QA_CRITERIA_FILE,
            "criterion_id": "1",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    current = alignment.alignment_summary(
        QA_READY_CASE_ID, criteria_file_name=QA_CRITERIA_FILE
    )
    assert data["projection_hash"] == current["global_projection_hash"]
    assert data["model_id"] == "qa-deterministic-synthesis"
    assert data["insights"]
    insight = data["insights"][0]
    assert len(insight["summary"]) <= 24
    assert insight["point_ids"] == ["1.p1"]
    assert set(insight["report_ids"]).issubset(
        {
            "QA-01-teacher-conflict",
            "QA-03-model-accepted",
            "QA-04-model-batch",
            "QA-05-manual-exception",
        }
    )
    assert all(
        ref["criterion_id"] == "1" and ref["point_id"] == "1.p1"
        for ref in insight["supporting_refs"]
    )

    overall_response = client.post(
        f"/cases/{QA_READY_CASE_ID}/cohort-synthesis",
        json={"criteria_file_name": QA_CRITERIA_FILE},
    )
    assert overall_response.status_code == 200, overall_response.text
    overall_data = overall_response.json()["data"]
    assert overall_data["language"] == "zh"
    assert overall_data["analysis"].startswith("综合来看")
    overall_insights = overall_data["insights"]
    overall_insight = overall_insights[0]
    assert len(overall_insight["summary"]) <= 24
    assert overall_insight["point_ids"] == []
    assert all(
        len(item["criterion_ids"]) == 1 for item in overall_insights
    )
    assert all(
        sum(other["criterion_ids"] == item["criterion_ids"] for other in overall_insights)
        <= 4
        for item in overall_insights
    )
