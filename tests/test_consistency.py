from backend.app import consistency


POLICY = {
    "threshold": 0.8,
    "alpha": 1.0,
    "beta": 1.0,
    "calibrating_review_rate": 0.5,
    "reliable_review_rate": 0.1,
    "unreliable_review_rate": 1.0,
}


def _record():
    store = consistency.empty_store()
    return consistency.ensure_record(
        store,
        case_id="case-a",
        criteria_file_name="criteria.json",
        criterion_id="1",
        criterion_hash="criterion-v1",
        scorer_revision="scorer-v1",
        **POLICY,
    )


def _identity(**overrides):
    values = {
        "case_id": "case-a",
        "criteria_file_name": "criteria.json",
        "criterion_id": "1",
        "criterion_hash": "criterion-v1",
        "scorer_revision": "scorer-v1",
        "scorer_component_hash": "component-v1",
        "model_version": "model-v1",
        "prompt_revision": "prompt-v1",
    }
    values.update(overrides)
    return consistency.RecordIdentity(**values)


def _feedback(record, index, verdict="accepted"):
    model_score = 8.0
    teacher_score = 8.0 if verdict == "accepted" else 7.0
    consistency.upsert_outcome(
        record,
        model_event_id=f"model-{index}",
        report_name=f"report-{index}",
        verdict=verdict,
        model_score=model_score,
        teacher_score=teacher_score,
        trigger="test",
        teacher_event_id=f"teacher-{index}",
    )
    consistency.recompute_record(
        record,
        **POLICY,
        trigger="test",
        trigger_event_id=f"teacher-{index}",
    )


def test_three_acceptances_reach_reliable_threshold():
    record = _record()
    assert record["state"] == "calibrating"
    assert record["smoothed_agreement"] == 0.5

    _feedback(record, 1)
    assert record["smoothed_agreement"] == 2 / 3
    assert record["state"] == "calibrating"
    _feedback(record, 2)
    assert record["smoothed_agreement"] == 3 / 4
    assert record["state"] == "calibrating"
    _feedback(record, 3)
    assert record["smoothed_agreement"] == 4 / 5
    assert record["state"] == "reliable"
    assert record["review_rate"] == 0.1


def test_correction_is_unreliable_and_can_recover():
    record = _record()
    _feedback(record, 1, "corrected")
    assert record["state"] == "unreliable"
    assert record["review_rate"] == 1.0
    for index in range(2, 9):
        _feedback(record, index)
    assert record["agreement_count"] == 7
    assert record["correction_count"] == 1
    assert record["smoothed_agreement"] == 0.8
    assert record["state"] == "reliable"


def test_downgrade_promotes_existing_unreviewed_assignments_to_full_review():
    record = _record()
    for index in range(1, 4):
        _feedback(record, index)
    assignment = consistency.assign_review(
        record,
        model_event_id="model-pending",
        report_name="report-pending",
        policy_version="v1",
    )
    assert assignment["review_rate"] == 0.1

    _feedback(record, 1, "corrected")

    assert record["state"] == "unreliable"
    assert assignment["review_rate"] == 1.0
    assert assignment["review_required"] is True


def test_one_model_event_has_only_one_effective_outcome():
    record = _record()
    _feedback(record, 1)
    assert record["agreement_count"] == 1
    _feedback(record, 1, "corrected")
    assert record["agreement_count"] == 0
    assert record["correction_count"] == 1
    assert len(record["outcomes"]) == 1
    assert len(record["outcome_history"]) == 1


def test_duplicate_feedback_is_idempotent():
    record = _record()
    _feedback(record, 1)
    transition_count = len(record["transitions"])
    _feedback(record, 1)
    assert record["agreement_count"] == 1
    assert len(record["outcome_history"]) == 0
    assert len(record["transitions"]) == transition_count


def test_delete_removes_effective_evidence_without_counting_error():
    record = _record()
    for index in range(1, 4):
        _feedback(record, index)
    assert record["state"] == "reliable"
    assert consistency.remove_outcomes(record, ["model-3"], trigger_event_id="delete-1")
    consistency.recompute_record(record, **POLICY, trigger="delete", trigger_event_id="delete-1")
    assert record["agreement_count"] == 2
    assert record["correction_count"] == 0
    assert record["state"] == "calibrating"


def test_teacher_override_does_not_fabricate_evidence_and_releases_on_correction():
    record = _record()
    consistency.activate_override(record, authorized_by="teacher", rubric_version="rubric-v1")
    consistency.recompute_record(record, **POLICY, trigger="override")
    assert record["state"] == "reliable"
    assert record["state_source"] == "teacher_override"
    assert record["agreement_count"] == record["correction_count"] == 0

    _feedback(record, 1, "corrected")
    consistency.release_override(record, reason="corrected", trigger_event_id="teacher-1")
    consistency.recompute_record(record, **POLICY, trigger="corrected")
    assert record["state"] == "unreliable"
    assert record["state_source"] == "evidence"


def test_review_sampling_is_deterministic_and_conflict_forces_review():
    record = _record()
    first = consistency.assign_review(
        record,
        model_event_id="model-1",
        report_name="report-1",
        policy_version="v1",
    )
    second = consistency.assign_review(
        record,
        model_event_id="model-1",
        report_name="report-1",
        policy_version="v1",
    )
    assert first["sample"] == second["sample"]
    forced = consistency.assign_review(
        record,
        model_event_id="model-2",
        report_name="report-2",
        policy_version="v1",
        conflict_override=True,
    )
    assert forced["review_required"] is True
    assert forced["review_rate"] == 1.0


def test_conflict_overlay_requires_review_without_changing_evidence():
    record = _record()
    for index in range(1, 4):
        _feedback(record, index)
    before = (record["agreement_count"], record["correction_count"], record["state"])

    visible = consistency.summary(record, has_open_conflict=True)

    assert visible["effective_review_rate"] == 1.0
    assert visible["conflict_override"] is True
    assert (record["agreement_count"], record["correction_count"], record["state"]) == before


def test_criterion_and_scorer_revisions_have_distinct_records():
    store = consistency.empty_store()
    first = consistency.ensure_record(
        store,
        case_id="case-a",
        criteria_file_name="criteria.json",
        criterion_id="1",
        criterion_hash="criterion-v1",
        scorer_revision="scorer-v1",
        **POLICY,
    )
    second = consistency.ensure_record(
        store,
        case_id="case-a",
        criteria_file_name="criteria.json",
        criterion_id="1",
        criterion_hash="criterion-v2",
        scorer_revision="scorer-v1",
        **POLICY,
    )
    third = consistency.ensure_record(
        store,
        case_id="case-a",
        criteria_file_name="criteria.json",
        criterion_id="1",
        criterion_hash="criterion-v1",
        scorer_revision="scorer-v2",
        **POLICY,
    )
    assert len(store["criteria"]) == 3
    assert len({first["record_key"], second["record_key"], third["record_key"]}) == 3


def test_record_identity_prefers_exact_revision_and_supports_bounded_migration():
    exact = {
        **_record(),
        "scorer_component_hash": "old-component",
    }
    identity = _identity()
    assert consistency.record_matches_identity(exact, identity)

    migrated = {
        **exact,
        "scorer_revision": "legacy-whole-rubric-revision",
        "scorer_component_hash": identity.scorer_component_hash,
        "model_version": identity.model_version,
        "prompt_revision": identity.prompt_revision,
    }
    assert consistency.record_matches_identity(migrated, identity)
    assert not consistency.record_matches_identity(
        migrated,
        identity,
        allow_component_compatibility=False,
    )


def test_record_identity_rejects_real_scorer_or_criterion_changes():
    identity = _identity()
    record = {
        **_record(),
        "scorer_revision": "legacy-whole-rubric-revision",
        "scorer_component_hash": identity.scorer_component_hash,
        "model_version": identity.model_version,
        "prompt_revision": identity.prompt_revision,
    }

    assert not consistency.record_matches_identity(
        record,
        _identity(prompt_revision="prompt-v2"),
    )
    assert not consistency.record_matches_identity(
        record,
        _identity(model_version="model-v2"),
    )
    assert not consistency.record_matches_identity(
        record,
        _identity(criterion_hash="criterion-v2"),
    )


def test_score_comparison_only_tolerates_serialization_noise():
    assert consistency.scores_equal(8.0, 8.0 + 1e-7)
    assert not consistency.scores_equal(8.0, 7.9)


def test_policy_validation_rejects_invalid_values():
    consistency.validate_policy(**POLICY)
    invalid = dict(POLICY, threshold=1.1)
    try:
        consistency.validate_policy(**invalid)
    except ValueError as exc:
        assert "threshold" in str(exc)
    else:
        raise AssertionError("invalid policy was accepted")
