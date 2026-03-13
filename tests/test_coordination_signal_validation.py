from __future__ import annotations

from agentic_coder_prototype.orchestration.coordination import (
    build_blocked_signal_proposal,
    build_signal_proposal,
    is_accepted_signal,
    validate_signal_proposal,
)


def test_validate_signal_accepts_legacy_complete_ingress() -> None:
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="main",
            source_kind="text_sentinel",
            emitter_role="assistant",
            payload={"completion_reason": "explicit_completion_marker"},
        )
    )

    assert signal["status"] == "accepted"
    assert is_accepted_signal(signal) is True
    assert signal["validation"]["reasons"] == ["signal_valid"]


def test_validate_signal_rejects_worker_mission_self_completion() -> None:
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            authority_scope="mission",
        ),
        mission_owner_role="supervisor",
    )

    assert signal["status"] == "rejected"
    assert "worker_cannot_self_finalize_mission" in signal["validation"]["reasons"]


def test_validate_signal_rejects_missing_evidence_for_human_required() -> None:
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="human_required",
            task_id="task_worker_1",
            source_kind="worker",
            emitter_role="worker",
        )
    )

    assert signal["status"] == "rejected"
    assert "missing_required_evidence:human_required" in signal["validation"]["reasons"]


def test_validate_signal_accepts_structured_blocked_payload() -> None:
    signal = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            blocking_reason="missing sandbox capability",
            recommended_next_action="retry",
            evidence_refs=["support://sandbox/bash"],
        )
    )

    assert signal["status"] == "accepted"
    assert signal["payload"]["blocking_reason"] == "missing sandbox capability"
    assert signal["payload"]["recommended_next_action"] == "retry"


def test_validate_signal_rejects_blocked_payload_without_structured_reason() -> None:
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="blocked",
            task_id="task_worker_1",
            source_kind="worker",
            emitter_role="worker",
            payload={"recommended_next_action": "sleep_on_it"},
        )
    )

    assert signal["status"] == "rejected"
    assert "missing_blocking_reason" in signal["validation"]["reasons"]
    assert "invalid_recommended_next_action:sleep_on_it" in signal["validation"]["reasons"]
