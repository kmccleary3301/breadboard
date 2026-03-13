from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.orchestration.coordination import build_signal_proposal, validate_signal_proposal
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.replay import load_event_log
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team() -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-review",
                "coordination": {
                    "mission_owner_role": "supervisor",
                    "done": {"require_deliverable_refs": True},
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
                    "subscription_id": "sub_worker_state",
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
    return supervisor, worker


def test_review_verdict_is_idempotent_across_reload(tmp_path: Path) -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    log_path = tmp_path / "events.jsonl"
    orchestrator.set_event_log_path(str(log_path))
    supervisor, worker = _spawn_lane(orchestrator)

    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_complete_reload",
            evidence_refs=["artifact://deliverables/report.md"],
            payload={"deliverable_refs": ["artifact://deliverables/report.md"]},
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(worker.job, signal)
    first_verdict = orchestrator.supervisor_review_signal(supervisor.job)
    orchestrator.persist_event_log()

    reloaded = MultiAgentOrchestrator(_make_team(), event_log=load_event_log(str(log_path)))
    reloaded_worker = reloaded.job_manager.get(worker.job.job_id)
    reloaded_supervisor = reloaded.job_manager.get(supervisor.job.job_id)
    assert reloaded_worker is not None
    assert reloaded_supervisor is not None

    reloaded.emit_coordination_signal(reloaded_worker, signal)
    second_verdict = reloaded.supervisor_review_signal(reloaded_supervisor)

    verdict_events = [
        event for event in reloaded.event_log.events if event.type == "coordination.review_verdict"
    ]
    assert len(verdict_events) == 1
    assert second_verdict.payload == first_verdict.payload
