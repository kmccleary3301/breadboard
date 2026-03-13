from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.orchestration.coordination import build_blocked_signal_proposal, validate_signal_proposal
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.replay import load_event_log
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team() -> TeamConfig:
    return TeamConfig.from_dict({"team": {"id": "coord-directive"}})


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
                    "subscription_id": "sub_worker_state",
                    "on_codes": ["blocked"],
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
    return supervisor, worker


def test_retry_directive_is_idempotent_across_reload(tmp_path: Path) -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    log_path = tmp_path / "events.jsonl"
    orchestrator.set_event_log_path(str(log_path))
    supervisor, worker = _spawn_lane(orchestrator)

    blocked_signal = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="provider quota exhausted",
            recommended_next_action="retry",
            evidence_refs=["evidence://quota/worker-1"],
        ),
        mission_owner_role="supervisor",
    )
    blocked_signal["signal_id"] = "signal_blocked_retry_reload"

    orchestrator.emit_coordination_signal(worker.job, blocked_signal)
    orchestrator.supervisor_review_signal(supervisor.job)
    orchestrator.persist_event_log()

    reloaded = MultiAgentOrchestrator(_make_team(), event_log=load_event_log(str(log_path)))
    reloaded_worker = reloaded.job_manager.get(worker.job.job_id)
    reloaded_supervisor = reloaded.job_manager.get(supervisor.job.job_id)
    assert reloaded_worker is not None
    assert reloaded_supervisor is not None

    reloaded.emit_coordination_signal(reloaded_worker, blocked_signal)
    reloaded.supervisor_review_signal(reloaded_supervisor)

    directives = [event for event in reloaded.event_log.events if event.type == "coordination.directive"]
    directive_wakeups = [
        event
        for event in reloaded.event_log.events
        if event.type == "agent.wakeup_emitted" and str((event.payload or {}).get("directive_code") or "") == "retry"
    ]
    assert len(directives) == 1
    assert len(directive_wakeups) == 1
