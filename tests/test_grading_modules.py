from backend.app.grading import batch
from backend.app.grading.common import natural_name_key, sha256_data, validate_safe_name


def test_report_name_order_has_one_natural_numeric_policy():
    names = ["报告10", "报告2", "报告1", "报告01"]
    assert sorted(names, key=natural_name_key) == ["报告1", "报告01", "报告2", "报告10"]


def test_batch_progress_uses_shared_terminal_status_policy():
    result = batch.progress(
        {
            "targets": [
                {"status": "done"},
                {"status": "abstained"},
                {"status": "superseded"},
                {"status": "pending"},
            ]
        }
    )
    assert result == {
        "total": 4,
        "completed": 3,
        "percent": 75.0,
        "pending": 1,
        "running": 0,
        "done": 1,
        "abstained": 1,
        "failed": 0,
        "stale": 0,
        "superseded": 1,
    }


def test_canonical_hash_is_independent_of_mapping_order():
    assert sha256_data({"b": 2, "a": 1}) == sha256_data({"a": 1, "b": 2})


def test_safe_name_rejects_paths():
    try:
        validate_safe_name("../criteria.json")
    except ValueError as exc:
        assert str(exc) == "unsafe file name"
    else:
        raise AssertionError("path-like names must be rejected")
