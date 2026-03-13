from __future__ import annotations

from agentic_coder_prototype.orchestration.coordination import (
    build_blocked_signal_proposal,
    build_signal_proposal,
    validate_signal_proposal,
)
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team() -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-verification",
                "coordination": {
                    "mission_owner_role": "supervisor",
                    "done": {
                        "require_deliverable_refs": True,
                        "require_all_required_refs": True,
                    },
                    "review": {
                        "verification_result_contract": "bb.coordination_verification_result.v1",
                    },
                },
            }
        }
    )


def _spawn_lane(orchestrator: MultiAgentOrchestrator):
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_supervisor_1",
            "task_kind": "background",
            "wake_subscriptions": [
                {
                    "schema_version": "bb.wake_subscription.v1",
                    "subscription_id": "sub_verifier_state",
                    "on_codes": ["complete", "blocked"],
                    "action": "resume",
                    "from_task_ids": ["task_verifier_1"],
                    "include_descendants": False,
                    "coalesce_window_ms": 0,
                }
            ],
        },
    )
    verifier = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="verifier",
        payload={"role": "verifier"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_verifier_1",
            "parent_task_id": "task_supervisor_1",
            "task_kind": "background",
            "wake_subscriptions": [
                {
                    "schema_version": "bb.wake_subscription.v1",
                    "subscription_id": "sub_worker_complete",
                    "on_codes": ["complete"],
                    "action": "resume",
                    "from_task_ids": ["task_worker_1"],
                    "include_descendants": False,
                    "coalesce_window_ms": 0,
                }
            ],
        },
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_worker_1",
            "parent_task_id": "task_supervisor_1",
            "task_kind": "background",
        },
    )
    return supervisor, verifier, worker


def _worker_complete_signal() -> dict:
    deliverable_ref = "artifact://deliverables/worker-report.md"
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_worker_complete_1",
            evidence_refs=[deliverable_ref],
            payload={
                "deliverable_refs": [deliverable_ref],
                "completion_reason": "worker_done",
            },
        ),
        mission_owner_role="supervisor",
    )
    return signal


def test_delegated_verification_pass_allows_supervisor_validation() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, verifier, worker = _spawn_lane(orchestrator)

    worker_signal = _worker_complete_signal()
    orchestrator.emit_coordination_signal(worker.job, worker_signal)

    wakeups = [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"]
    verifier_wakeups = [event for event in wakeups if event.payload["job_id"] == verifier.job.job_id]
    supervisor_wakeups = [event for event in wakeups if event.payload["job_id"] == supervisor.job.job_id]
    assert len(verifier_wakeups) == 1
    assert supervisor_wakeups == []

    verifier_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_verifier_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_verifier_complete_1",
            evidence_refs=["artifact://verification/pass.json"],
            payload={
                "deliverable_refs": ["artifact://deliverables/worker-report.md"],
                "verification_result_contract": "bb.coordination_verification_result.v1",
                "verification_result": {
                    "schema_version": "bb.coordination_verification_result.v1",
                    "subject_signal_id": "signal_worker_complete_1",
                    "subject_task_id": "task_worker_1",
                    "validator_task_id": "task_verifier_1",
                    "status": "pass",
                    "verification_artifact_refs": ["artifact://verification/pass.json"],
                    "summary": "Verifier confirmed worker deliverable.",
                },
            },
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(verifier.job, verifier_signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=["artifact://deliverables/worker-report.md"],
    )

    assert verdict.payload["verdict_code"] == "validated"
    assert verdict.payload["mission_completed"] is True
    assert verdict.payload["metadata"]["verification_contract_required"] == "bb.coordination_verification_result.v1"
    assert verdict.payload["metadata"]["verification_contract_received"] == "bb.coordination_verification_result.v1"
    assert verdict.payload["metadata"]["verification_status"] == "pass"
    assert verdict.payload["metadata"]["verification_subject_signal_id"] == "signal_worker_complete_1"
    assert verdict.payload["metadata"]["verification_subject_task_id"] == "task_worker_1"
    assert verdict.payload["metadata"]["verification_subject_signal_code"] == "complete"
    assert verdict.payload["metadata"]["verification_subject_signal_found"] is True
    assert verdict.payload["metadata"]["verification_artifact_refs"] == ["artifact://verification/pass.json"]
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "completed"


def test_delegated_verification_fail_issues_checkpoint_to_verifier() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, verifier, worker = _spawn_lane(orchestrator)

    worker_signal = _worker_complete_signal()
    orchestrator.emit_coordination_signal(worker.job, worker_signal)

    verifier_signal = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_verifier_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="verification failed on diff mismatch",
            recommended_next_action="checkpoint",
            evidence_refs=["artifact://verification/fail.json"],
            payload={
                "verification_result_contract": "bb.coordination_verification_result.v1",
                "verification_result": {
                    "schema_version": "bb.coordination_verification_result.v1",
                    "subject_signal_id": "signal_worker_complete_1",
                    "subject_task_id": "task_worker_1",
                    "validator_task_id": "task_verifier_1",
                    "status": "fail",
                    "verification_artifact_refs": ["artifact://verification/fail.json"],
                    "summary": "Verifier found a blocking mismatch.",
                },
            },
        ),
        mission_owner_role="supervisor",
    )
    verifier_signal["signal_id"] = "signal_verifier_blocked_1"

    orchestrator.emit_coordination_signal(verifier.job, verifier_signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)

    assert verdict.payload["verdict_code"] == "checkpoint"
    assert verdict.payload["mission_completed"] is False
    assert verdict.payload["metadata"]["verification_contract_required"] == "bb.coordination_verification_result.v1"
    assert verdict.payload["metadata"]["verification_status"] == "fail"
    assert verdict.payload["metadata"]["verification_subject_signal_found"] is True

    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "checkpoint"
    assert directives[0].payload["target_task_id"] == "task_verifier_1"


def test_delegated_verification_complete_stays_pending_when_contract_is_missing() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, verifier, worker = _spawn_lane(orchestrator)

    worker_signal = _worker_complete_signal()
    orchestrator.emit_coordination_signal(worker.job, worker_signal)

    verifier_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_verifier_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_verifier_complete_missing_contract",
            evidence_refs=["artifact://verification/pass.json"],
            payload={
                "deliverable_refs": ["artifact://deliverables/worker-report.md"],
            },
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(verifier.job, verifier_signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=["artifact://deliverables/worker-report.md"],
    )

    assert verdict.payload["verdict_code"] == "pending_validation"
    assert verdict.payload["metadata"]["verification_contract_required"] == "bb.coordination_verification_result.v1"
    assert verdict.payload["metadata"]["verification_contract_received"] is None
    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "continue"
    assert directives[0].payload["target_task_id"] == "task_verifier_1"
