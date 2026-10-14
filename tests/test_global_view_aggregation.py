from backend.app.global_view.aggregation import build_report_summaries


def test_report_summary_aggregates_confirmed_reasons_in_rubric_order():
    criteria = {
        "base_score": 20,
        "criteria": [
            {"criteria_id": "1", "max_score": 5},
            {"criteria_id": "2", "max_score": 5},
            {"criteria_id": "3", "max_score": 5},
            {"criteria_id": "4", "max_score": 5},
        ],
    }
    summaries = [
        {
            "criterion_id": "1",
            "points": [
                {
                    "report_id": "report-a",
                    "score": 4,
                    "max_score": 5,
                    "included_in_statistics": True,
                    "author": "teacher",
                    "score_reason": "Evidence is mostly complete.",
                }
            ],
        },
        {
            "criterion_id": "2",
            "points": [
                {
                    "report_id": "report-a",
                    "score": 5,
                    "max_score": 5,
                    "included_in_statistics": True,
                    "author": "model",
                    "score_reason": "Evidence is mostly complete.",
                }
            ],
        },
        {
            "criterion_id": "3",
            "points": [
                {
                    "report_id": "report-a",
                    "conflict": True,
                    "score_reason": "Unconfirmed conflict reason.",
                }
            ],
        },
        {
            "criterion_id": "4",
            "points": [
                {
                    "report_id": "report-a",
                    "score": 3,
                    "max_score": 5,
                    "included_in_statistics": False,
                    "score_reason": "Invalid projection reason.",
                }
            ],
        },
    ]

    reports = build_report_summaries(["report-a", "report-b"], criteria, summaries)

    assert reports[0]["analysis_summary"] == "Evidence is mostly complete."
    assert reports[0]["score"] == 19
    assert reports[1]["analysis_summary"] == ""


def test_report_summary_preserves_distinct_reason_order():
    criteria = {
        "base_score": 10,
        "criteria": [
            {"criteria_id": "first", "max_score": 5},
            {"criteria_id": "second", "max_score": 5},
        ],
    }
    summaries = [
        {
            "criterion_id": "second",
            "points": [
                {
                    "report_id": "report-a",
                    "score": 4,
                    "max_score": 5,
                    "included_in_statistics": True,
                    "score_reason": "Second reason.",
                }
            ],
        },
        {
            "criterion_id": "first",
            "points": [
                {
                    "report_id": "report-a",
                    "score": 5,
                    "max_score": 5,
                    "included_in_statistics": True,
                    "score_reason": "First reason.",
                }
            ],
        },
    ]

    reports = build_report_summaries(["report-a"], criteria, summaries)

    assert reports[0]["analysis_summary"] == "First reason. Second reason."
