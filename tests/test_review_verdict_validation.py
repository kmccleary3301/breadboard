from __future__ import annotations

from agentic_coder_prototype.orchestration.coordination import (
    build_review_verdict,
    validate_review_verdict,
)


def test_review_verdict_validated_complete_passes_validation() -> None:
    verdict = validate_review_verdict(
        build_review_verdict(
            reviewer_task_id="task_supervisor_1",
            reviewer_role="supervisor",
            subject_signal={
                "signal_id": "signal_complete_1",
                "code": "complete",
                "task_id": "task_worker_1",
                "mission_task_id": "task_supervisor_1",
            },
            subject_event_id=11,
            subscription_id="sub_worker_state",
            verdict_code="validated",
            mission_completed=True,
            required_deliverable_refs=["artifact://deliverables/report.md"],
            deliverable_refs=["artifact://deliverables/report.md"],
            signal_evidence_refs=["artifact://deliverables/report.md"],
        )
    )

    assert verdict["validation"]["accepted"] is True
    assert verdict["verdict_code"] == "validated"
    assert verdict["mission_completed"] is True
    assert verdict["subject"]["signal_code"] == "complete"


def test_review_verdict_rejects_validated_complete_without_deliverable() -> None:
    verdict = validate_review_verdict(
        build_review_verdict(
            reviewer_task_id="task_supervisor_1",
            reviewer_role="supervisor",
            subject_signal={
                "signal_id": "signal_complete_1",
                "code": "complete",
                "task_id": "task_worker_1",
            },
            verdict_code="validated",
            mission_completed=True,
            required_deliverable_refs=["artifact://deliverables/report.md"],
            deliverable_refs=[],
        )
    )

    assert verdict["validation"]["accepted"] is False
    assert "validated_verdict_missing_deliverable_refs" in verdict["validation"]["reasons"]


def test_review_verdict_rejects_retry_verdict_for_complete_signal() -> None:
    verdict = validate_review_verdict(
        build_review_verdict(
            reviewer_task_id="task_supervisor_1",
            reviewer_role="supervisor",
            subject_signal={
                "signal_id": "signal_complete_1",
                "code": "complete",
                "task_id": "task_worker_1",
            },
            verdict_code="retry",
            blocking_reason="not actually blocked",
            recommended_next_action="retry",
        )
    )

    assert verdict["validation"]["accepted"] is False
    assert "review_action_verdict_requires_actionable_signal" in verdict["validation"]["reasons"]


def test_review_verdict_accepts_retryable_failure_retry_verdict() -> None:
    verdict = validate_review_verdict(
        build_review_verdict(
            reviewer_task_id="task_longrun_controller",
            reviewer_role="system",
            subject_signal={
                "signal_id": "signal_retryable_failure_1",
                "code": "retryable_failure",
                "task_id": "task_longrun_controller",
            },
            verdict_code="retry",
            blocking_reason="provider timeout",
            recommended_next_action="retry",
        )
    )

    assert verdict["validation"]["accepted"] is True
    assert verdict["verdict_code"] == "retry"
