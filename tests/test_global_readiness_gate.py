from backend.app.global_view.readiness import build_global_readiness


def _readiness(*, proposals=None, conflicts=None, authorizations=None):
    return build_global_readiness(
        criteria_name="criteria.json",
        proposals=proposals or [],
        authorizations=authorizations or [],
        open_conflicts=conflicts or [],
        repair_required_state="repair_required",
    )


def test_pending_model_confirmation_does_not_block_global_view():
    assert _readiness() == {"allowed": True, "blockers": [], "blocker_count": 0}


def test_pending_rubric_proposal_blocks_global_view():
    readiness = _readiness(
        proposals=[
            {
                "proposal_id": "proposal-1",
                "criteria_file_name": "criteria.json",
                "status": "pending",
                "target_criterion_id": "1",
            }
        ]
    )

    assert readiness["allowed"] is False
    assert readiness["blockers"][0]["kind"] == "rubric_proposal"


def test_open_conflict_blocks_global_view_and_preserves_navigation_target():
    readiness = _readiness(
        conflicts=[
            {
                "conflict_id": "conflict-1",
                "criteria_file_name": "criteria.json",
                "criteria_id": "1",
                "suspect_events": [
                    {"event_id": "event-1", "report_name": "report-a"}
                ],
            }
        ]
    )

    assert readiness["allowed"] is False
    assert readiness["blockers"][0] == {
        "kind": "conflict",
        "label": "Resolve grading conflict",
        "report_name": "report-a",
        "criterion_id": "1",
        "event_id": "event-1",
    }
