from __future__ import annotations

from agentic_coder_prototype.orchestration.coordination import build_directive, validate_directive


def test_retry_directive_passes_validation() -> None:
    directive = validate_directive(
        build_directive(
            directive_code="retry",
            issuer_task_id="task_supervisor_1",
            issuer_role="supervisor",
            target_task_id="task_worker_1",
            target_job_id="job_worker_1",
            based_on_verdict={
                "verdict_id": "review_blocked_retry_1",
                "subject": {"signal_id": "signal_blocked_1"},
            },
            payload={"wake_target": True},
            evidence_refs=["evidence://quota/worker-1"],
        )
    )

    assert directive["validation"]["accepted"] is True
    assert directive["directive_code"] == "retry"
    assert directive["based_on_verdict_id"] == "review_blocked_retry_1"


def test_continue_directive_requires_wake_target() -> None:
    directive = validate_directive(
        build_directive(
            directive_code="continue",
            issuer_task_id="task_supervisor_1",
            issuer_role="supervisor",
            target_task_id="task_worker_1",
            based_on_verdict={
                "verdict_id": "review_continue_1",
                "subject": {"signal_id": "signal_complete_1"},
            },
            payload={"wake_target": False},
        )
    )

    assert directive["validation"]["accepted"] is False
    assert "directive_requires_wake_target" in directive["validation"]["reasons"]
