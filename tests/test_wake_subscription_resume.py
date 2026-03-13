from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.orchestration.coordination import build_signal_proposal
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.replay import load_event_log
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team() -> TeamConfig:
    return TeamConfig.from_dict({"team": {"id": "coord"}})


def test_accepted_signal_emits_wakeup_and_persists_cursor(tmp_path: Path) -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    log_path = tmp_path / "events.jsonl"
    orchestrator.set_event_log_path(str(log_path))

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
                    "subscription_id": "sub_complete_worker_1",
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
            "task_kind": "background",
            "wake_conditions": [],
        },
    )

    signal = build_signal_proposal(
        code="complete",
        task_id="task_worker_1",
        parent_task_id="task_supervisor_1",
        source_kind="worker",
        emitter_role="worker",
        signal_id="signal_complete_1",
        payload={"completion_reason": "worker_done"},
    )
    signal["status"] = "accepted"
    signal["validation"] = {"accepted": True, "reasons": ["signal_valid"], "validated_by": "engine"}

    orchestrator.emit_coordination_signal(worker.job, signal)
    orchestrator.persist_event_log()

    wakeups = [ev for ev in orchestrator.event_log.events if ev.type == "agent.wakeup_emitted"]
    assert len(wakeups) == 1
    payload = wakeups[0].payload
    assert payload["job_id"] == supervisor.job.job_id
    assert payload["subscription_id"] == "sub_complete_worker_1"
    assert payload["trigger_signal_id"] == "signal_complete_1"
    assert payload["trigger_code"] == "complete"
    assert payload["resume_reason"] == "signal:complete"
    assert payload["source_task_id"] == "task_worker_1"
    assert orchestrator.subscription_cursors(supervisor.job.job_id)["sub_complete_worker_1"]["trigger_signal_id"] == "signal_complete_1"

    reloaded = MultiAgentOrchestrator(_make_team(), event_log=load_event_log(str(log_path)))
    reloaded_worker = reloaded.job_manager.get(worker.job.job_id)
    assert reloaded_worker is not None
    reloaded.emit_coordination_signal(reloaded_worker, signal)
    wakeups_after = [ev for ev in reloaded.event_log.events if ev.type == "agent.wakeup_emitted"]
    assert len(wakeups_after) == 1


def test_legacy_manual_wakeup_path_still_works() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    worker = orchestrator.spawn_subagent(owner_agent="main", agent_id="worker", async_mode=True)
    orchestrator.emit_wakeup(worker.job, reason="completed", message="legacy wakeup")

    wakeups = [ev for ev in orchestrator.event_log.events if ev.type == "agent.wakeup_emitted"]
    assert len(wakeups) == 1
    assert wakeups[0].payload["message"] == "legacy wakeup"
