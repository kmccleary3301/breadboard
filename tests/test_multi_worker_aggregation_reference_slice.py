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
                "id": "coord-reducer",
                "coordination": {
                    "mission_owner_role": "supervisor",
                    "done": {
                        "require_deliverable_refs": True,
                        "require_all_required_refs": True,
                    },
                    "merge": {
                        "reducer_result_contract": "shard_aggregate_v1",
                    },
                },
            }
        }
    )


def _spawn_multi_worker_lane(orchestrator: MultiAgentOrchestrator):
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
                    "subscription_id": "sub_reducer_state",
                    "on_codes": ["complete", "blocked"],
                    "action": "resume",
                    "from_task_ids": ["task_reducer_1"],
                    "include_descendants": False,
                    "coalesce_window_ms": 0,
                }
            ],
        },
    )
    reducer = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="reducer",
        payload={"role": "reducer"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_reducer_1",
            "parent_task_id": "task_supervisor_1",
            "task_kind": "background",
            "wake_subscriptions": [
                {
                    "schema_version": "bb.wake_subscription.v1",
                    "subscription_id": "sub_shard_updates",
                    "on_codes": ["complete", "blocked"],
                    "action": "resume",
                    "from_task_ids": ["task_shard_1", "task_shard_2"],
                    "include_descendants": False,
                    "coalesce_window_ms": 0,
                }
            ],
        },
    )
    shard_1 = orchestrator.spawn_subagent(
        owner_agent="reducer",
        agent_id="shard-1",
        payload={"role": "worker"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_shard_1",
            "parent_task_id": "task_reducer_1",
            "task_kind": "background",
        },
    )
    shard_2 = orchestrator.spawn_subagent(
        owner_agent="reducer",
        agent_id="shard-2",
        payload={"role": "worker"},
        task_descriptor={
            "schema_version": "bb.distributed_task_descriptor.v1",
            "task_id": "task_shard_2",
            "parent_task_id": "task_reducer_1",
            "task_kind": "background",
        },
    )
    return supervisor, reducer, shard_1, shard_2


def test_multi_worker_reducer_complete_flow_validates_aggregate_contract() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, reducer, shard_1, shard_2 = _spawn_multi_worker_lane(orchestrator)

    shard_1_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_shard_1",
            parent_task_id="task_reducer_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_shard_1_complete",
            evidence_refs=["artifact://shards/1/report.md"],
            payload={"deliverable_refs": ["artifact://shards/1/report.md"]},
        ),
        mission_owner_role="supervisor",
    )
    shard_2_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_shard_2",
            parent_task_id="task_reducer_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_shard_2_complete",
            evidence_refs=["artifact://shards/2/report.md"],
            payload={"deliverable_refs": ["artifact://shards/2/report.md"]},
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(shard_1.job, shard_1_signal)
    orchestrator.emit_coordination_signal(shard_2.job, shard_2_signal)

    wakeups = [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"]
    reducer_wakeups = [event for event in wakeups if event.payload["job_id"] == reducer.job.job_id]
    supervisor_wakeups = [event for event in wakeups if event.payload["job_id"] == supervisor.job.job_id]
    assert len(reducer_wakeups) == 2
    assert supervisor_wakeups == []

    aggregate_ref = "artifact://aggregate/shard-aggregate.md"
    reducer_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_reducer_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_reducer_complete",
            evidence_refs=[aggregate_ref],
            payload={
                "deliverable_refs": [aggregate_ref],
                "aggregate_result_contract": "shard_aggregate_v1",
                "aggregate_result": {
                    "required_worker_task_ids": ["task_shard_1", "task_shard_2"],
                    "completed_worker_task_ids": ["task_shard_1", "task_shard_2"],
                    "aggregate_artifact_refs": [aggregate_ref],
                    "summary": "2 shard results merged",
                },
            },
        ),
        mission_owner_role="supervisor",
    )

    orchestrator.emit_coordination_signal(reducer.job, reducer_signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[aggregate_ref],
    )

    assert verdict.payload["verdict_code"] == "validated"
    assert verdict.payload["mission_completed"] is True
    assert verdict.payload["metadata"]["aggregate_contract_required"] == "shard_aggregate_v1"
    assert verdict.payload["metadata"]["aggregate_contract_received"] == "shard_aggregate_v1"
    assert verdict.payload["metadata"]["aggregate_missing_worker_task_ids"] == []
    assert orchestrator.job_manager.get(supervisor.job.job_id).state == "completed"


def test_multi_worker_reducer_complete_stays_pending_when_aggregate_contract_incomplete() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, reducer, shard_1, _ = _spawn_multi_worker_lane(orchestrator)

    shard_1_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_shard_1",
            parent_task_id="task_reducer_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_shard_1_only",
            evidence_refs=["artifact://shards/1/report.md"],
            payload={"deliverable_refs": ["artifact://shards/1/report.md"]},
        ),
        mission_owner_role="supervisor",
    )
    orchestrator.emit_coordination_signal(shard_1.job, shard_1_signal)

    aggregate_ref = "artifact://aggregate/shard-aggregate.md"
    reducer_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_reducer_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_reducer_incomplete",
            evidence_refs=[aggregate_ref],
            payload={
                "deliverable_refs": [aggregate_ref],
                "aggregate_result_contract": "shard_aggregate_v1",
                "aggregate_result": {
                    "required_worker_task_ids": ["task_shard_1", "task_shard_2"],
                    "completed_worker_task_ids": ["task_shard_1"],
                    "aggregate_artifact_refs": [aggregate_ref],
                    "summary": "partial aggregation",
                },
            },
        ),
        mission_owner_role="supervisor",
    )
    orchestrator.emit_coordination_signal(reducer.job, reducer_signal)

    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[aggregate_ref],
    )

    assert verdict.payload["verdict_code"] == "pending_validation"
    assert verdict.payload["mission_completed"] is False
    assert verdict.payload["metadata"]["aggregate_missing_worker_task_ids"] == ["task_shard_2"]
    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "continue"
    assert directives[0].payload["target_task_id"] == "task_reducer_1"


def test_multi_worker_blocked_shard_path_issues_checkpoint_directive_to_reducer() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, reducer, shard_1, _ = _spawn_multi_worker_lane(orchestrator)

    shard_blocked = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_shard_1",
            parent_task_id="task_reducer_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="provider quota exhausted",
            recommended_next_action="checkpoint",
            evidence_refs=["evidence://quota/shard-1"],
        ),
        mission_owner_role="supervisor",
    )
    orchestrator.emit_coordination_signal(shard_1.job, shard_blocked)

    wakeups = [event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted"]
    reducer_wakeups = [event for event in wakeups if event.payload["job_id"] == reducer.job.job_id]
    supervisor_wakeups = [event for event in wakeups if event.payload["job_id"] == supervisor.job.job_id]
    assert len(reducer_wakeups) == 1
    assert supervisor_wakeups == []

    reducer_blocked = validate_signal_proposal(
        build_blocked_signal_proposal(
            task_id="task_reducer_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            blocking_reason="shard task blocked",
            recommended_next_action="checkpoint",
            evidence_refs=["evidence://quota/shard-1"],
            payload={"blocked_shard_task_id": "task_shard_1"},
        ),
        mission_owner_role="supervisor",
    )
    reducer_blocked["signal_id"] = "signal_reducer_blocked"
    orchestrator.emit_coordination_signal(reducer.job, reducer_blocked)

    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    assert verdict.payload["verdict_code"] == "checkpoint"
    assert verdict.payload["metadata"]["blocked_action"] == "checkpoint"

    directives = [event for event in orchestrator.event_log.events if event.type == "coordination.directive"]
    assert len(directives) == 1
    assert directives[0].payload["directive_code"] == "checkpoint"
    assert directives[0].payload["target_task_id"] == "task_reducer_1"
    directive_wakeups = [
        event
        for event in orchestrator.event_log.events
        if event.type == "agent.wakeup_emitted"
        and str((event.payload or {}).get("directive_code") or "") == "checkpoint"
    ]
    assert len(directive_wakeups) == 1
