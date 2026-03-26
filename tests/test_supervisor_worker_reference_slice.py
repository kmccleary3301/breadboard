from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.orchestration.coordination import (
    build_blocked_signal_proposal,
    build_signal_proposal,
    validate_signal_proposal,
)
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.replay import load_event_log
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team() -> TeamConfig:
    return TeamConfig.from_dict({"team": {"id": "coord-ref"}})


def _spawn_reference_lane(orchestrator: MultiAgentOrchestrator):
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_supervisor_1",
            "task_kind": "background",
            "wake_conditions": ["timer:30s"],
            "wake_subscriptions": [
                {
                    "schema_version": "bb.wake_subscription.v1",
                    "subscription_id": "sub_worker_state",
                    "on_codes": ["complete", "blocked"],
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
            "wake_conditions": [],
        },
    )
    unrelated = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker-2",
        payload={"role": "worker"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_worker_2",
            "parent_task_id": "task_supervisor_1",
            "task_kind": "background",
            "wake_conditions": [],
        },
    )
    return supervisor, worker, unrelated


def test_supervisor_validates_complete_separately_from_worker_done() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker, _ = _spawn_reference_lane(orchestrator)

    deliverable_ref = "artifact://deliverables/worker-report.md"
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_complete_reference",
            evidence_refs=[deliverable_ref],
            payload={
                "completion_reason": "worker_done",
                "deliverable_refs": [deliverable_ref],
            },
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(worker.job, signal)

    wakeups = [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"]
    assert len(wakeups) == 1
    assert wakeups[0].payload["job_id"] == supervisor.job.job_id
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "accepted"

    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[deliverable_ref],
    )

    assert verdict.type == "coordination.review_verdict"
    assert verdict.payload["verdict_code"] == "validated"
    assert verdict.payload["mission_completed"] is True
    assert verdict.payload["subject"]["trigger_signal_id"] == "signal_complete_reference"
    assert verdict.payload["deliverable_refs"] == [deliverable_ref]
    assert verdict.payload["missing_deliverable_refs"] == []
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "completed"
    assert [event for event in orchestrator.event_log.events if event.type == "coordination.directive"] == []

    completion_events = [event for event in orchestrator.event_log.events if event.type == "agent.job_completed"]
    assert completion_events
    assert completion_events[-1].payload["job_id"] == supervisor.job.job_id
    assert completion_events[-1].payload["trigger_signal_id"] == "signal_complete_reference"


def test_supervisor_keeps_worker_complete_pending_when_required_deliverable_missing() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker, _ = _spawn_reference_lane(orchestrator)

    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_complete_missing_artifact",
            payload={"completion_reason": "worker_done", "deliverable_refs": []},
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=["artifact://deliverables/worker-report.md"],
    )

    assert verdict.payload["verdict_code"] == "pending_validation"
    assert verdict.payload["mission_completed"] is False
    assert verdict.payload["missing_deliverable_refs"] == ["artifact://deliverables/worker-report.md"]
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "accepted"
    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "continue"
    assert directives[0].payload["target_task_id"] == "task_worker_1"


def test_blocked_signal_wakes_supervisor_and_preserves_structured_payload() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker, unrelated = _spawn_reference_lane(orchestrator)

    unrelated_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_2",
            parent_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_unrelated_complete",
            payload={"deliverable_refs": ["artifact://deliverables/unrelated.md"]},
        ),
        mission_owner_role="supervisor",
    )
    orchestrator.emit_coordination_signal(unrelated.job, unrelated_signal)
    assert [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"] == []

    blocked_signal = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="missing sandbox capability",
            recommended_next_action="retry",
            support_claim_ref="support://sandbox/capability/bash",
            evidence_refs=["support://sandbox/capability/bash"],
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(worker.job, blocked_signal)
    wakeups = [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"]
    assert len(wakeups) == 1
    assert wakeups[0].payload["trigger_code"] == "blocked"
    assert wakeups[0].payload["source_task_id"] == "task_worker_1"

    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    assert verdict.payload["verdict_code"] == "retry"
    assert verdict.payload["mission_completed"] is False
    assert verdict.payload["metadata"]["blocked_action"] == "retry"
    assert verdict.payload["blocking_reason"] == "missing sandbox capability"
    assert verdict.payload["support_claim_ref"] == "support://sandbox/capability/bash"
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "accepted"
    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "retry"
    assert directives[0].payload["target_task_id"] == "task_worker_1"
    directive_wakeups = [
        event
        for event in orchestrator.event_log.events
        if event.type == "agent.wakeup_emitted" and str((event.payload or {}).get("directive_code") or "") == "retry"
    ]
    assert len(directive_wakeups) == 1


def test_blocked_signal_resume_is_idempotent_across_reload(tmp_path: Path) -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    log_path = tmp_path / "events.jsonl"
    orchestrator.set_event_log_path(str(log_path))
    supervisor, worker, _ = _spawn_reference_lane(orchestrator)

    blocked_signal = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="provider quota exhausted",
            recommended_next_action="checkpoint",
            evidence_refs=["evidence://quota/worker-1"],
        ),
        mission_owner_role="supervisor",
    )
    blocked_signal["signal_id"] = "signal_blocked_reload"

    orchestrator.emit_coordination_signal(worker.job, blocked_signal)
    first_verdict = orchestrator.supervisor_review_signal(supervisor.job)
    orchestrator.persist_event_log()

    reloaded = MultiAgentOrchestrator(_make_team(), event_log=load_event_log(str(log_path)))
    reloaded_worker = reloaded.job_manager.get(worker.job.job_id)
    reloaded_supervisor = reloaded.job_manager.get(supervisor.job.job_id)
    assert reloaded_worker is not None
    assert reloaded_supervisor is not None

    reloaded.emit_coordination_signal(reloaded_worker, blocked_signal)
    wakeups_after = [event for event in reloaded.event_log.events if event.type == "agent.wakeup_emitted"]
    assert len(wakeups_after) == 2

    second_verdict = reloaded.supervisor_review_signal(reloaded_supervisor)
    decision_events = [
        event for event in reloaded.event_log.events if event.type == "coordination.review_verdict"
    ]
    assert len(decision_events) == 1
    assert second_verdict.payload["verdict_code"] == "checkpoint"
    assert second_verdict.payload["subject"]["trigger_signal_id"] == "signal_blocked_reload"
    assert second_verdict.payload == first_verdict.payload
    directive_events = [event for event in reloaded.event_log.events if event.type == "coordination.directive"]
    assert len(directive_events) == 1
