from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import alignment, experiment_reset, main
from backend.app.config import ATOMIC_ABSORPTION_CRITERIA_FILE, CASE_ONE_CRITERIA_FILE


CRITERIA_FILE = CASE_ONE_CRITERIA_FILE
CASE_TWO_CRITERIA_FILE = ATOMIC_ABSORPTION_CRITERIA_FILE
REPOSITORY_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "rubrics"
    / CRITERIA_FILE
)
CASE_TWO_REPOSITORY_BASELINE = REPOSITORY_BASELINE.with_name(CASE_TWO_CRITERIA_FILE)


def _reset_fixture(tmp_path: Path, monkeypatch):
    case_root = tmp_path / "case"
    criteria_dir = tmp_path / "criteria"
    baseline_dir = tmp_path / "criteria_baselines"
    case_baseline_dir = tmp_path / "case_baselines"
    case_dir = case_root / "case 1"
    report_dir = case_dir / "markdown" / "report-a"
    report_dir.mkdir(parents=True)
    criteria_dir.mkdir()
    baseline_dir.mkdir()
    case_baseline_dir.mkdir()

    baseline = json.loads(REPOSITORY_BASELINE.read_text(encoding="utf-8"))
    mutated = deepcopy(baseline)
    mutated["background"] = "changed"

    (baseline_dir / CRITERIA_FILE).write_text(
        json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
    )
    (criteria_dir / CRITERIA_FILE).write_text(
        json.dumps(mutated, ensure_ascii=False), encoding="utf-8"
    )
    report_path = report_dir / "report-a.md"
    report_path.write_text("source report", encoding="utf-8")

    monkeypatch.setattr(alignment, "CASE_ROOT", case_root)
    monkeypatch.setattr(experiment_reset, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(experiment_reset, "CRITERIA_BASELINE_DIR", baseline_dir)
    monkeypatch.setattr(experiment_reset, "CASE_ROOT", case_root)
    monkeypatch.setattr(experiment_reset, "CASE_BASELINE_DIR", case_baseline_dir)

    store = alignment._store("case 1")
    store["events"] = [{"event_id": "event-a"}]
    store["conflicts"] = [{"conflict_id": "conflict-a"}]
    store["refinement_rejections"] = [{"rejection_id": "rejection-a"}]
    alignment._write_store("case 1", store)
    grade_path = case_dir / "grade_result" / "report-a_grade.json"
    alignment._atomic_write_json(grade_path, {"criteria": []})

    return baseline, criteria_dir / CRITERIA_FILE, report_path, grade_path


def test_reset_experiment_restores_baseline_and_clears_only_derived_state(
    tmp_path, monkeypatch
):
    baseline, rubric_path, report_path, grade_path = _reset_fixture(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/cases/case%201/debug/reset-experiment",
        json={"confirm_case_id": "case 1", "criteria_file_name": CRITERIA_FILE},
    )

    assert response.status_code == 200, response.text
    assert json.loads(rubric_path.read_text(encoding="utf-8")) == baseline
    assert alignment._store("case 1")["events"] == []
    assert alignment._store("case 1")["conflicts"] == []
    assert alignment._store("case 1")["refinement_rejections"] == []
    assert not grade_path.exists()
    assert report_path.read_text(encoding="utf-8") == "source report"


def test_reset_experiment_restores_case_specific_grading_snapshot(tmp_path, monkeypatch):
    case_root = tmp_path / "case"
    criteria_dir = tmp_path / "criteria"
    rubric_baseline_dir = tmp_path / "criteria_baselines"
    case_baseline_dir = tmp_path / "case_baselines"
    report_dir = case_root / "case 2" / "markdown" / "report-a"
    snapshot_alignment_dir = case_baseline_dir / "case 2" / "alignment"
    report_dir.mkdir(parents=True)
    criteria_dir.mkdir()
    rubric_baseline_dir.mkdir()
    snapshot_alignment_dir.mkdir(parents=True)

    rubric = json.loads(CASE_TWO_REPOSITORY_BASELINE.read_text(encoding="utf-8"))
    (criteria_dir / CASE_TWO_CRITERIA_FILE).write_text(
        json.dumps(rubric), encoding="utf-8"
    )
    (rubric_baseline_dir / CASE_TWO_CRITERIA_FILE).write_text(
        json.dumps(rubric), encoding="utf-8"
    )
    expected_events = [{"event_id": "baseline-event", "report_name": "report-a"}]
    expected_decisions = [{"event_id": "baseline-event", "status": "accepted"}]
    (snapshot_alignment_dir / "events.json").write_text(
        json.dumps(expected_events), encoding="utf-8"
    )
    (snapshot_alignment_dir / "decisions.json").write_text(
        json.dumps(expected_decisions), encoding="utf-8"
    )

    monkeypatch.setattr(alignment, "CASE_ROOT", case_root)
    monkeypatch.setattr(experiment_reset, "CRITERIA_DIR", criteria_dir)
    monkeypatch.setattr(experiment_reset, "CRITERIA_BASELINE_DIR", rubric_baseline_dir)
    monkeypatch.setattr(experiment_reset, "CASE_ROOT", case_root)
    monkeypatch.setattr(experiment_reset, "CASE_BASELINE_DIR", case_baseline_dir)

    changed_store = alignment._store("case 2")
    changed_store["events"] = [{"event_id": "changed"}]
    changed_store["decisions"] = [{"event_id": "changed"}]
    alignment._write_store("case 2", changed_store)
    client = TestClient(main.app)
    response = client.post(
        "/cases/case%202/debug/reset-experiment",
        json={"confirm_case_id": "case 2", "criteria_file_name": CASE_TWO_CRITERIA_FILE},
    )

    assert response.status_code == 200, response.text
    assert alignment._store("case 2")["events"] == expected_events
    assert alignment._store("case 2")["decisions"] == expected_decisions
    assert response.json()["data"]["restored"] == {"files": 2}


def test_reset_experiment_rejects_a_stale_case_confirmation(tmp_path, monkeypatch):
    _, rubric_path, _, grade_path = _reset_fixture(tmp_path, monkeypatch)
    before = rubric_path.read_text(encoding="utf-8")
    client = TestClient(main.app)

    response = client.post(
        "/cases/case%201/debug/reset-experiment",
        json={"confirm_case_id": "case 2", "criteria_file_name": CRITERIA_FILE},
    )

    assert response.status_code == 400
    assert rubric_path.read_text(encoding="utf-8") == before
    assert grade_path.exists()


def test_reset_experiment_rejects_a_non_baseline_rubric_without_clearing_state(
    tmp_path, monkeypatch
):
    _, rubric_path, _, grade_path = _reset_fixture(tmp_path, monkeypatch)
    before = rubric_path.read_text(encoding="utf-8")
    client = TestClient(main.app)

    response = client.post(
        "/cases/case%201/debug/reset-experiment",
        json={"confirm_case_id": "case 1", "criteria_file_name": "other.json"},
    )

    assert response.status_code == 400
    assert rubric_path.read_text(encoding="utf-8") == before
    assert grade_path.exists()
