from __future__ import annotations

from pathlib import Path

import pytest

from agentic_coder_prototype.orchestration.coordination import (
    build_review_verdict,
    build_human_required_signal_proposal,
    validate_signal_proposal,
)
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.replay import load_event_log
from agentic_coder_prototype.orchestration.schema import TeamConfig


def _make_team(
    *,
    require_evidence_refs: bool = False,
    mission_owner_role: str = "supervisor",
    allowed_reviewer_roles: list[str] | None = None,
    require_supervisor_escalate: bool = True,
    support_claim_limited_actions: list[str] | None = None,
) -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-intervention",
                "coordination": {
                    "mission_owner_role": mission_owner_role,
                    "review": {
                        "allowed_reviewer_roles": allowed_reviewer_roles or ["supervisor", "system"],
                    },
                    "intervention": {
                        "host_allowed_actions": ["continue", "checkpoint", "terminate"],
                        "require_evidence_refs": require_evidence_refs,
                        "require_supervisor_escalate": require_supervisor_escalate,
                        "support_claim_limited_actions": support_claim_limited_actions or ["checkpoint", "terminate"],
                    }
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
                    "on_codes": ["human_required"],
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


def _emit_human_required(orchestrator: MultiAgentOrchestrator, worker_job, *, support_claim_ref: str | None = None) -> None:
    signal = validate_signal_proposal(
        build_human_required_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            required_input="Approve rerun after guarded failure",
            blocking_reason="Operator approval required before continuing",
            source_kind="runtime",
            emitter_role="runtime",
            evidence_refs=["evidence://human-required/worker-1"],
            payload={"support_claim_ref": support_claim_ref} if support_claim_ref else None,
        ),
        mission_owner_role="supervisor",
    )
    signal["signal_id"] = "signal_human_required_1"
    orchestrator.emit_coordination_signal(worker_job, signal)


def test_host_continue_intervention_wakes_worker_and_resolves_snapshot() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker = _spawn_lane(orchestrator)

    _emit_human_required(orchestrator, worker.job)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    assert verdict.payload["verdict_code"] == "human_required"

    before = orchestrator.coordination_inspection_snapshot()
    assert len(before["unresolved_interventions"]) == 1
    assert before["resolved_interventions"] == []

    directive_event = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="continue",
    )

    assert directive_event.payload["issuer_role"] == "host"
    assert directive_event.payload["directive_code"] == "continue"
    assert directive_event.payload["target_task_id"] == "task_worker_1"

    wakeups = [
        event
        for event in orchestrator.event_log.events
        if event.type == "agent.wakeup_emitted" and str((event.payload or {}).get("directive_code") or "") == "continue"
    ]
    assert len(wakeups) == 1
    assert wakeups[0].payload["job_id"] == worker.job.job_id
    assert wakeups[0].payload["based_on_verdict_id"] == verdict.payload["verdict_id"]

    after = orchestrator.coordination_inspection_snapshot()
    assert after["unresolved_interventions"] == []
    assert len(after["resolved_interventions"]) == 1
    assert after["resolved_interventions"][0]["host_responses"][0]["directive_id"] == directive_event.payload["directive_id"]
    assert after["resolved_interventions"][0]["allowed_host_actions"] == ["continue", "checkpoint", "terminate"]


def test_host_terminate_intervention_is_durable_without_worker_wakeup() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker = _spawn_lane(orchestrator)

    _emit_human_required(orchestrator, worker.job)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    directive_event = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="terminate",
        note="Stop and await a manual patch.",
    )

    assert directive_event.payload["directive_code"] == "terminate"
    assert directive_event.payload["payload"]["wake_target"] is False
    assert directive_event.payload["payload"]["note"] == "Stop and await a manual patch."

    terminate_wakeups = [
        event
        for event in orchestrator.event_log.events
        if event.type == "agent.wakeup_emitted" and str((event.payload or {}).get("directive_code") or "") == "terminate"
    ]
    assert terminate_wakeups == []


def test_host_continue_intervention_is_idempotent_across_reload(tmp_path: Path) -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    log_path = tmp_path / "events.jsonl"
    orchestrator.set_event_log_path(str(log_path))
    supervisor, worker = _spawn_lane(orchestrator)

    _emit_human_required(orchestrator, worker.job)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    first = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="continue",
    )
    orchestrator.persist_event_log()

    reloaded = MultiAgentOrchestrator(_make_team(), event_log=load_event_log(str(log_path)))
    second = reloaded.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="continue",
    )

    directives = [event for event in reloaded.event_log.events if event.type == "coordination.directive"]
    continue_directives = [event for event in directives if str((event.payload or {}).get("issuer_role") or "") == "host"]
    continue_wakeups = [
        event
        for event in reloaded.event_log.events
        if event.type == "agent.wakeup_emitted" and str((event.payload or {}).get("directive_code") or "") == "continue"
    ]
    assert first.payload == second.payload
    assert len(continue_directives) == 1
    assert len(continue_wakeups) == 1


def test_host_intervention_can_require_evidence_refs() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team(require_evidence_refs=True))
    supervisor, worker = _spawn_lane(orchestrator)

    _emit_human_required(orchestrator, worker.job)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)

    with pytest.raises(ValueError, match="requires evidence_refs"):
        orchestrator.issue_host_intervention_directive(
            based_on_verdict_id=verdict.payload["verdict_id"],
            directive_code="checkpoint",
        )

    event = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="checkpoint",
        evidence_refs=["evidence://host-response/checkpoint"],
    )
    assert event.payload["evidence_refs"] == ["evidence://host-response/checkpoint"]


def test_host_intervention_requires_prior_supervisor_escalate_directive() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker = _spawn_lane(orchestrator)

    signal = validate_signal_proposal(
        build_human_required_signal_proposal(
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            required_input="Approve rerun after guarded failure",
            blocking_reason="Operator approval required before continuing",
            source_kind="runtime",
            emitter_role="runtime",
            evidence_refs=["evidence://human-required/worker-1"],
        ),
        mission_owner_role="supervisor",
    )
    signal["signal_id"] = "signal_human_required_no_escalate"
    signal_event = orchestrator.emit_coordination_signal(worker.job, signal)
    review_verdict = build_review_verdict(
        reviewer_task_id="task_supervisor_1",
        reviewer_role="supervisor",
        subject_signal=signal,
        verdict_code="human_required",
        verdict_id="review_no_escalate",
        subject_event_id=signal_event.event_id,
        trigger_signal_id="signal_human_required_no_escalate",
        trigger_event_id=signal_event.event_id,
        trigger_code="human_required",
        mission_completed=False,
        blocking_reason="Operator approval required before continuing",
        signal_evidence_refs=["evidence://human-required/worker-1"],
        metadata={"job_id": supervisor.job.job_id},
    )
    orchestrator.event_log.add(
        "coordination.review_verdict",
        agent_id=supervisor.job.agent_id,
        parent_agent_id=supervisor.job.owner_agent,
        causal_parent_event_id=signal_event.event_id,
        payload=review_verdict,
    )

    with pytest.raises(ValueError, match="prior supervisor escalate directive"):
        orchestrator.issue_host_intervention_directive(
            based_on_verdict_id="review_no_escalate",
            directive_code="continue",
        )


def test_host_intervention_limits_actions_when_support_claim_is_present() -> None:
    orchestrator = MultiAgentOrchestrator(_make_team())
    supervisor, worker = _spawn_lane(orchestrator)

    _emit_human_required(
        orchestrator,
        worker.job,
        support_claim_ref="support://sandbox/capability/bash",
    )
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    snapshot = orchestrator.coordination_inspection_snapshot()

    assert snapshot["unresolved_interventions"][0]["allowed_host_actions"] == ["checkpoint", "terminate"]
    assert verdict.payload["support_claim_ref"] == "support://sandbox/capability/bash"

    with pytest.raises(ValueError, match="not allowed for host intervention"):
        orchestrator.issue_host_intervention_directive(
            based_on_verdict_id=verdict.payload["verdict_id"],
            directive_code="continue",
        )

    directive_event = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=verdict.payload["verdict_id"],
        directive_code="checkpoint",
    )
    assert directive_event.payload["directive_code"] == "checkpoint"
