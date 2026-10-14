import json
import pytest

pytestmark = pytest.mark.local_data
from pathlib import Path

from backend.app.config import (
    ATOMIC_ABSORPTION_CRITERIA_FILE,
    CASE_ONE_CRITERIA_FILE,
)
from backend.app.schemas import CriteriaSchema


DATA_ROOT = Path(__file__).resolve().parents[1] / "backend" / "data"


def test_case_one_has_one_empty_rubric_and_fourteen_numbered_reports():
    rubric = CriteriaSchema.model_validate(
        json.loads((DATA_ROOT / "criteria" / CASE_ONE_CRITERIA_FILE).read_text(encoding="utf-8"))
    )
    report_names = sorted(
        path.name for path in (DATA_ROOT / "case" / "case 1" / "markdown").iterdir()
        if path.is_dir()
    )

    assert rubric.criteria == []
    assert len(report_names) == 14
    assert [name.split(" ", 1)[0] for name in report_names] == [
        f"{index:02d}" for index in range(1, 15)
    ]


def test_case_two_baseline_contains_a_complete_model_grading_snapshot():
    baseline_root = DATA_ROOT / "case_baselines" / "case 2" / "alignment"
    events = json.loads((baseline_root / "events.json").read_text(encoding="utf-8"))
    decisions = json.loads((baseline_root / "decisions.json").read_text(encoding="utf-8"))
    rubric = CriteriaSchema.model_validate(
        json.loads(
            (
                DATA_ROOT
                / "criteria_baselines"
                / ATOMIC_ABSORPTION_CRITERIA_FILE
            ).read_text(encoding="utf-8")
        )
    )

    assert len(rubric.criteria) == 15
    assert len(events) == 14 * len(rubric.criteria)
    assert len(decisions) == len(events)
    assert {event["event_id"] for event in events} == {
        event_id for decision in decisions for event_id in decision["event_ids"]
    }
