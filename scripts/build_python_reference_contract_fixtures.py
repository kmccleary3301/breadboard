from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointSummary, build_checkpoint_metadata_record
from agentic_coder_prototype.longrun.checkpoint import build_longrun_checkpoint_metadata_record
from agentic_coder_prototype.longrun.controller import LongRunController
from agentic_coder_prototype.conductor_execution import (
    build_tool_execution_outcome_record,
    build_tool_model_render_record,
)
from agentic_coder_prototype.orchestration.coordination import (
    build_blocked_signal_proposal,
    build_human_required_signal_proposal,
    build_signal_proposal,
    validate_signal_proposal,
)
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.schema import TeamConfig
from agentic_coder_prototype.provider_invoker import ProviderInvoker
from agentic_coder_prototype.provider_runtime import ProviderResult
from agentic_coder_prototype.state.session_state import SessionState

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "conformance" / "engine_fixtures"


def _coordination_team() -> TeamConfig:
    return TeamConfig.from_dict({"team": {"id": "coord-fixture"}})


def _coordination_multi_worker_team() -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-fixture-multi",
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


def _coordination_longrun_fixture_output(
    *,
    scenario_id: str,
    stop_reason: str,
    controller_out: Dict[str, Any],
) -> Dict[str, Any]:
    macro_summary = controller_out.get("macro_summary") if isinstance(controller_out, dict) else {}
    macro_summary = macro_summary if isinstance(macro_summary, dict) else {}
    coordination = macro_summary.get("coordination") if isinstance(macro_summary.get("coordination"), dict) else {}
    replacements: Dict[str, str] = {}
    for idx, signal in enumerate(coordination.get("signals") or [], start=1):
        if isinstance(signal, dict):
            signal_id = str(signal.get("signal_id") or "").strip()
            if signal_id:
                replacements[signal_id] = f"signal_longrun_{idx}"
    for idx, verdict in enumerate(coordination.get("review_verdicts") or [], start=1):
        if isinstance(verdict, dict):
            verdict_id = str(verdict.get("verdict_id") or "").strip()
            if verdict_id:
                replacements[verdict_id] = f"review_longrun_{idx}"
    for idx, directive in enumerate(coordination.get("directives") or [], start=1):
        if isinstance(directive, dict):
            directive_id = str(directive.get("directive_id") or "").strip()
            if directive_id:
                replacements[directive_id] = f"directive_longrun_{idx}"
    output = {
        "schema_version": "bb.coordination_longrun_reference_slice.v1",
        "scenario_id": scenario_id,
        "stop_reason": str(stop_reason),
        "episodes_run": int(macro_summary.get("episodes_run") or 0),
        "signals": list(coordination.get("signals") or []),
        "review_verdicts": list(coordination.get("review_verdicts") or []),
        "directives": list(coordination.get("directives") or []),
        "events": list(coordination.get("events") or []),
    }
    return _zero_validation_timestamps(_normalize_fixture_value(output, replacements))


def _coordination_intervention_team() -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-fixture-intervention",
                "coordination": {
                    "intervention": {
                        "host_allowed_actions": ["continue", "checkpoint", "terminate"],
                        "require_evidence_refs": False,
                    }
                },
            }
        }
    )


def _coordination_verification_team() -> TeamConfig:
    return TeamConfig.from_dict(
        {
            "team": {
                "id": "coord-fixture-verification",
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


def _coordination_descriptor(task_id: str, *, parent_task_id: str | None = None, subscribed: bool = False) -> Dict[str, Any]:
    descriptor: Dict[str, Any] = {
        "schema_version": "bb.distributed_task_descriptor.v1",
        "task_id": task_id,
        "task_kind": "background",
        "wake_conditions": ["timer:30s"] if subscribed else [],
    }
    if parent_task_id:
        descriptor["parent_task_id"] = parent_task_id
    if subscribed:
        descriptor["wake_subscriptions"] = [
            {
                "schema_version": "bb.wake_subscription.v1",
                "subscription_id": "sub_worker_state",
                "on_codes": ["complete", "blocked"],
                "action": "resume",
                "from_task_ids": ["task_worker_1"],
                "include_descendants": False,
                "coalesce_window_ms": 0,
            }
        ]
    return descriptor


def _serialize_events(orchestrator: MultiAgentOrchestrator) -> list[Dict[str, Any]]:
    return [
        {
            "event_id": event.event_id,
            "type": event.type,
            "agent_id": event.agent_id,
            "parent_agent_id": event.parent_agent_id,
            "causal_parent_event_id": event.causal_parent_event_id,
            "payload": dict(event.payload or {}),
        }
        for event in orchestrator.event_log.events
    ]


def _project_review_verdict_as_supervisor_decision(verdict: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(verdict or {})
    subject = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "job_id": str(metadata.get("job_id") or ""),
        "supervisor_task_id": str(payload.get("reviewer_task_id") or ""),
        "subscription_id": str(subject.get("subscription_id") or ""),
        "trigger_signal_id": str(subject.get("trigger_signal_id") or subject.get("signal_id") or ""),
        "trigger_event_id": subject.get("trigger_event_id"),
        "trigger_code": str(subject.get("trigger_code") or subject.get("signal_code") or ""),
        "source_task_id": str(subject.get("source_task_id") or ""),
        "decision": str(metadata.get("legacy_decision") or payload.get("verdict_code") or ""),
        "mission_completed": bool(payload.get("mission_completed")),
        "required_deliverable_refs": list(payload.get("required_deliverable_refs") or []),
        "deliverable_refs": list(payload.get("deliverable_refs") or []),
        "missing_deliverable_refs": list(payload.get("missing_deliverable_refs") or []),
        "blocking_reason": payload.get("blocking_reason"),
        "recommended_next_action": payload.get("recommended_next_action"),
        "blocked_action": metadata.get("blocked_action"),
        "support_claim_ref": payload.get("support_claim_ref"),
        "signal_evidence_refs": list(payload.get("signal_evidence_refs") or []),
    }


def _normalize_fixture_value(value: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_fixture_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_fixture_value(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def _zero_validation_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        out = {key: _zero_validation_timestamps(item) for key, item in value.items()}
        validation = out.get("validation")
        if isinstance(validation, dict) and "validated_at" in validation:
            validation["validated_at"] = 0.0
        return out
    if isinstance(value, list):
        return [_zero_validation_timestamps(item) for item in value]
    return value


def _build_coordination_reference_complete_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_team())
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor=_coordination_descriptor("task_supervisor_1", subscribed=True),
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_worker_1", parent_task_id="task_supervisor_1"),
    )

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
            payload={"deliverable_refs": [deliverable_ref], "completion_reason": "worker_done"},
        ),
        mission_owner_role="supervisor",
    )
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[deliverable_ref],
    )
    verdict.payload["validation"]["validated_at"] = 0.0
    wakeup = next(event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted")
    completion = next(event for event in orchestrator.event_log.events if event.type == "agent.job_completed")
    reference_output = {
        "schema_version": "bb.coordination_reference_slice.v1",
        "scenario_id": "coordination_supervisor_complete_python_reference",
        "supervisor_task_id": "task_supervisor_1",
        "worker_task_id": "task_worker_1",
        "subscription_id": "sub_worker_state",
        "accepted_signal": signal,
        "wakeup_payload": dict(wakeup.payload or {}),
        "supervisor_decision": _project_review_verdict_as_supervisor_decision(dict(verdict.payload or {})),
        "completion_payload": dict(completion.payload or {}),
        "events": _serialize_events(orchestrator),
    }
    reference_output = _normalize_fixture_value(
        reference_output,
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coordination_supervisor_complete_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_reference_blocked_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_team())
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor=_coordination_descriptor("task_supervisor_1", subscribed=True),
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_worker_1", parent_task_id="task_supervisor_1"),
    )

    signal = validate_signal_proposal(
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
    signal["signal_id"] = "signal_blocked_reference"
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    verdict.payload["validation"]["validated_at"] = 0.0
    directive = next(event for event in orchestrator.event_log.events if event.type == "coordination.directive")
    directive.payload["validation"]["validated_at"] = 0.0
    wakeup = next(event for event in orchestrator.event_log.events if event.type == "agent.wakeup_emitted")
    reference_output = {
        "schema_version": "bb.coordination_reference_slice.v1",
        "scenario_id": "coordination_supervisor_blocked_python_reference",
        "supervisor_task_id": "task_supervisor_1",
        "worker_task_id": "task_worker_1",
        "subscription_id": "sub_worker_state",
        "accepted_signal": signal,
        "wakeup_payload": dict(wakeup.payload or {}),
        "supervisor_decision": _project_review_verdict_as_supervisor_decision(dict(verdict.payload or {})),
        "events": _serialize_events(orchestrator),
    }
    reference_output = _normalize_fixture_value(
        reference_output,
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coordination_supervisor_blocked_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_reviewed_complete_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_team())
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor=_coordination_descriptor("task_supervisor_1", subscribed=True),
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_worker_1", parent_task_id="task_supervisor_1"),
    )

    deliverable_ref = "artifact://deliverables/worker-report.md"
    signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_complete_reviewed",
            evidence_refs=[deliverable_ref],
            payload={"deliverable_refs": [deliverable_ref], "completion_reason": "worker_done"},
        ),
        mission_owner_role="supervisor",
    )
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[deliverable_ref],
    )
    verdict.payload["validation"]["validated_at"] = 0.0
    reference_output = _normalize_fixture_value(
        dict(verdict.payload or {}),
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_reviewed_complete_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.review_verdict.v1",
        "reference_output": reference_output,
    }


def _build_coordination_reviewed_blocked_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_team())
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor=_coordination_descriptor("task_supervisor_1", subscribed=True),
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_worker_1", parent_task_id="task_supervisor_1"),
    )

    signal = validate_signal_proposal(
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
    signal["signal_id"] = "signal_blocked_reviewed"
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    verdict.payload["validation"]["validated_at"] = 0.0
    reference_output = _normalize_fixture_value(
        dict(verdict.payload or {}),
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_reviewed_blocked_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.review_verdict.v1",
        "reference_output": reference_output,
    }


def _build_coordination_directive_retry_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_team())
    supervisor = orchestrator.spawn_subagent(
        owner_agent="root",
        agent_id="supervisor",
        payload={"role": "supervisor"},
        task_descriptor=_coordination_descriptor("task_supervisor_1", subscribed=True),
    )
    worker = orchestrator.spawn_subagent(
        owner_agent="supervisor",
        agent_id="worker",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_worker_1", parent_task_id="task_supervisor_1"),
    )

    signal = validate_signal_proposal(
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
    signal["signal_id"] = "signal_blocked_directive"
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    orchestrator.supervisor_review_signal(supervisor.job)
    directive = next(event for event in orchestrator.event_log.events if event.type == "coordination.directive")
    directive.payload["validation"]["validated_at"] = 0.0
    reference_output = _normalize_fixture_value(
        dict(directive.payload or {}),
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_directive_retry_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.directive.v1",
        "reference_output": reference_output,
    }


def _build_coordination_multi_worker_complete_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_multi_worker_team())
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
        task_descriptor=_coordination_descriptor("task_shard_1", parent_task_id="task_reducer_1"),
    )
    shard_2 = orchestrator.spawn_subagent(
        owner_agent="reducer",
        agent_id="shard-2",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_shard_2", parent_task_id="task_reducer_1"),
    )

    shard_signals = []
    for shard_task_id, shard_job, artifact_ref in (
        ("task_shard_1", shard_1.job, "artifact://shards/1/report.md"),
        ("task_shard_2", shard_2.job, "artifact://shards/2/report.md"),
    ):
        signal = validate_signal_proposal(
            build_signal_proposal(
                code="complete",
                task_id=shard_task_id,
                parent_task_id="task_reducer_1",
                mission_task_id="task_supervisor_1",
                source_kind="worker",
                emitter_role="worker",
                signal_id=f"signal_{shard_task_id}_complete",
                evidence_refs=[artifact_ref],
                payload={"deliverable_refs": [artifact_ref]},
            ),
            mission_owner_role="supervisor",
        )
        signal["validation"]["validated_at"] = 0.0
        shard_signals.append(signal)
        orchestrator.emit_coordination_signal(shard_job, signal)

    aggregate_ref = "artifact://aggregate/shard-aggregate.md"
    reducer_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_reducer_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_reducer_complete_multi",
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
    reducer_signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(reducer.job, reducer_signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=[aggregate_ref],
    )
    verdict.payload["validation"]["validated_at"] = 0.0
    reference_output = _normalize_fixture_value(
        {
            "schema_version": "bb.coordination_multi_worker_reference_slice.v1",
            "scenario_id": "coordination_multi_worker_complete_python_reference",
            "supervisor_task_id": "task_supervisor_1",
            "reducer_task_id": "task_reducer_1",
            "shard_task_ids": ["task_shard_1", "task_shard_2"],
            "shard_signals": shard_signals,
            "reducer_signal": reducer_signal,
            "review_verdict": dict(verdict.payload or {}),
            "directive": None,
            "events": _serialize_events(orchestrator),
        },
        {
            supervisor.job.job_id: "job_supervisor_1",
            reducer.job.job_id: "job_reducer_1",
            shard_1.job.job_id: "job_shard_1",
            shard_2.job.job_id: "job_shard_2",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_multi_worker_complete_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_multi_worker_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_multi_worker_blocked_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_multi_worker_team())
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
        task_descriptor=_coordination_descriptor("task_shard_1", parent_task_id="task_reducer_1"),
    )
    shard_2 = orchestrator.spawn_subagent(
        owner_agent="reducer",
        agent_id="shard-2",
        payload={"role": "worker"},
        task_descriptor=_coordination_descriptor("task_shard_2", parent_task_id="task_reducer_1"),
    )

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
    shard_blocked["signal_id"] = "signal_shard_1_blocked"
    shard_blocked["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(shard_1.job, shard_blocked)

    reducer_signal = validate_signal_proposal(
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
    reducer_signal["signal_id"] = "signal_reducer_blocked_multi"
    reducer_signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(reducer.job, reducer_signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    verdict.payload["validation"]["validated_at"] = 0.0
    directive = next(event for event in orchestrator.event_log.events if event.type == "coordination.directive")
    directive.payload["validation"]["validated_at"] = 0.0
    reference_output = _normalize_fixture_value(
        {
            "schema_version": "bb.coordination_multi_worker_reference_slice.v1",
            "scenario_id": "coordination_multi_worker_blocked_python_reference",
            "supervisor_task_id": "task_supervisor_1",
            "reducer_task_id": "task_reducer_1",
            "shard_task_ids": ["task_shard_1", "task_shard_2"],
            "shard_signals": [shard_blocked],
            "reducer_signal": reducer_signal,
            "review_verdict": dict(verdict.payload or {}),
            "directive": dict(directive.payload or {}),
            "events": _serialize_events(orchestrator),
        },
        {
            supervisor.job.job_id: "job_supervisor_1",
            reducer.job.job_id: "job_reducer_1",
            shard_1.job.job_id: "job_shard_1",
            shard_2.job.job_id: "job_shard_2",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_multi_worker_blocked_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_multi_worker_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_longrun_no_progress_fixture() -> Dict[str, Any]:
    controller = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "budgets": {"max_episodes": 10},
                "recovery": {"no_progress_max_episodes": 2},
            },
            "coordination": {"review": {"no_progress_action": "checkpoint"}},
        }
    )
    controller_out = controller.run(lambda _idx: {"completed": False, "completion_reason": "not_done"})
    reference_output = _coordination_longrun_fixture_output(
        scenario_id="coordination_longrun_no_progress_python_reference",
        stop_reason="no_progress_threshold_reached",
        controller_out=controller_out,
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_longrun_no_progress_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_longrun_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_longrun_retryable_failure_fixture() -> Dict[str, Any]:
    calls = {"count": 0}
    controller = LongRunController(
        {
            "long_running": {
                "enabled": True,
                "budgets": {"max_episodes": 3, "max_retries_per_item": 2},
                "recovery": {
                    "backoff_base_seconds": 0.01,
                    "backoff_max_seconds": 0.01,
                    "backoff_disable_jitter": True,
                },
            },
            "coordination": {"review": {"retryable_failure_action": "retry"}},
        },
        sleep_fn=lambda _seconds: None,
    )

    def runner(_idx: int) -> Dict[str, Any]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("provider timeout")
        return {"completed": True, "completion_reason": "done_after_retry"}

    controller_out = controller.run(runner)
    reference_output = _coordination_longrun_fixture_output(
        scenario_id="coordination_longrun_retryable_failure_python_reference",
        stop_reason="episode_completed",
        controller_out=controller_out,
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_longrun_retryable_failure_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_longrun_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_longrun_human_required_fixture() -> Dict[str, Any]:
    controller = LongRunController({"long_running": {"enabled": True, "budgets": {"max_episodes": 2}}})
    controller_out = controller.run(
        lambda _idx: {
            "completed": False,
            "completion_reason": "human_required",
            "human_required": {
                "required_input": "operator_confirmation",
                "blocking_reason": "needs approval before continuing",
                "evidence_refs": ["artifact://meta/operator_note.json"],
            },
        }
    )
    reference_output = _coordination_longrun_fixture_output(
        scenario_id="coordination_longrun_human_required_python_reference",
        stop_reason="human_required",
        controller_out=controller_out,
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_longrun_human_required_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_longrun_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_intervention_continue_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_intervention_team())
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
                    "subscription_id": "sub_worker_human_required",
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
    signal["signal_id"] = "signal_human_required_intervention"
    signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    verdict.payload["validation"]["validated_at"] = 0.0
    unresolved = orchestrator.coordination_inspection_snapshot()["unresolved_interventions"]
    host_directive = orchestrator.issue_host_intervention_directive(
        based_on_verdict_id=str(verdict.payload.get("verdict_id") or ""),
        directive_code="continue",
    )
    host_directive.payload["validation"]["validated_at"] = 0.0
    resolved = orchestrator.coordination_inspection_snapshot()["resolved_interventions"]
    supervisor_directive = next(
        event
        for event in orchestrator.event_log.events
        if event.type == "coordination.directive"
        and str((event.payload or {}).get("issuer_role") or "") == "supervisor"
    )
    supervisor_directive.payload["validation"]["validated_at"] = 0.0

    reference_output = _normalize_fixture_value(
        {
            "schema_version": "bb.coordination_intervention_reference_slice.v1",
            "scenario_id": "coordination_intervention_continue_python_reference",
            "supervisor_task_id": "task_supervisor_1",
            "worker_task_id": "task_worker_1",
            "signal": signal,
            "review_verdict": dict(verdict.payload or {}),
            "supervisor_directive": dict(supervisor_directive.payload or {}),
            "host_directive": dict(host_directive.payload or {}),
            "unresolved_interventions_before": unresolved,
            "resolved_interventions_after": resolved,
            "events": _serialize_events(orchestrator),
        },
        {
            supervisor.job.job_id: "job_supervisor_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_intervention_continue_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_intervention_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_delegated_verification_pass_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_verification_team())
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

    worker_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_worker_complete_1",
            evidence_refs=["artifact://deliverables/worker-report.md"],
            payload={
                "deliverable_refs": ["artifact://deliverables/worker-report.md"],
                "completion_reason": "worker_done",
            },
        ),
        mission_owner_role="supervisor",
    )
    worker_signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(worker.job, worker_signal)

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
    verifier_signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(verifier.job, verifier_signal)
    verdict = orchestrator.supervisor_review_signal(
        supervisor.job,
        required_deliverable_refs=["artifact://deliverables/worker-report.md"],
    )
    verdict.payload["validation"]["validated_at"] = 0.0

    reference_output = _normalize_fixture_value(
        {
            "schema_version": "bb.coordination_delegated_verification_reference_slice.v1",
            "scenario_id": "coordination_delegated_verification_pass_python_reference",
            "supervisor_task_id": "task_supervisor_1",
            "worker_task_id": "task_worker_1",
            "verifier_task_id": "task_verifier_1",
            "worker_signal": worker_signal,
            "verifier_signal": verifier_signal,
            "review_verdict": dict(verdict.payload or {}),
            "directive": None,
            "events": _serialize_events(orchestrator),
        },
        {
            supervisor.job.job_id: "job_supervisor_1",
            verifier.job.job_id: "job_verifier_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_delegated_verification_pass_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_delegated_verification_reference_slice.v1",
        "reference_output": reference_output,
    }


def _build_coordination_delegated_verification_fail_fixture() -> Dict[str, Any]:
    orchestrator = MultiAgentOrchestrator(_coordination_verification_team())
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

    worker_signal = validate_signal_proposal(
        build_signal_proposal(
            code="complete",
            task_id="task_worker_1",
            parent_task_id="task_supervisor_1",
            mission_task_id="task_supervisor_1",
            source_kind="worker",
            emitter_role="worker",
            signal_id="signal_worker_complete_1",
            evidence_refs=["artifact://deliverables/worker-report.md"],
            payload={
                "deliverable_refs": ["artifact://deliverables/worker-report.md"],
                "completion_reason": "worker_done",
            },
        ),
        mission_owner_role="supervisor",
    )
    worker_signal["validation"]["validated_at"] = 0.0
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
    verifier_signal["validation"]["validated_at"] = 0.0
    orchestrator.emit_coordination_signal(verifier.job, verifier_signal)
    verdict = orchestrator.supervisor_review_signal(supervisor.job)
    verdict.payload["validation"]["validated_at"] = 0.0
    directive = next(event for event in orchestrator.event_log.events if event.type == "coordination.directive")
    directive.payload["validation"]["validated_at"] = 0.0

    reference_output = _normalize_fixture_value(
        {
            "schema_version": "bb.coordination_delegated_verification_reference_slice.v1",
            "scenario_id": "coordination_delegated_verification_fail_python_reference",
            "supervisor_task_id": "task_supervisor_1",
            "worker_task_id": "task_worker_1",
            "verifier_task_id": "task_verifier_1",
            "worker_signal": worker_signal,
            "verifier_signal": verifier_signal,
            "review_verdict": dict(verdict.payload or {}),
            "directive": dict(directive.payload or {}),
            "events": _serialize_events(orchestrator),
        },
        {
            supervisor.job.job_id: "job_supervisor_1",
            verifier.job.job_id: "job_verifier_1",
            worker.job.job_id: "job_worker_1",
        },
    )
    return {
        "fixture_family": "coordination",
        "fixture_id": "coord_delegated_verification_fail_python_reference",
        "comparator_class": "normalized-trace-equal",
        "support_tier": "reference-engine",
        "contract": "bb.coordination_delegated_verification_reference_slice.v1",
        "reference_output": reference_output,
    }


def build_python_reference_contract_fixtures() -> Dict[str, Dict[str, Any]]:
    state = SessionState("/tmp/ws", "python-dev:latest", {})
    state.set_provider_metadata("session_id", "sess-ref-1")
    state.begin_turn(1)

    kernel_record = state.build_kernel_event_record(
        "assistant_message",
        {"message": {"role": "assistant", "content": "hello"}},
        turn=1,
        seq=1,
    )
    kernel_contract = {
        "schemaVersion": "bb.kernel_event.v1",
        "eventId": "evt-ref-1",
        "runId": "run-ref-1",
        "sessionId": kernel_record["session_id"],
        "seq": kernel_record["seq"],
        "ts": "2026-03-08T00:00:00Z",
        "actor": "engine",
        "visibility": "host",
        "kind": kernel_record["type"],
        "payload": kernel_record["payload"],
        "turnId": "turn-1",
    }

    state.add_transcript_entry(
        {
            "kind": "assistant_message",
            "visibility": "model",
            "content": {"text": "hello"},
            "provenance": {"source": "reference-fixture"},
        }
    )
    transcript_contract = {
        "schemaVersion": "bb.session_transcript.v1",
        "sessionId": "sess-ref-1",
        "runId": "run-ref-1",
        "eventCursor": 1,
        "items": state.derive_transcript_contract_items(),
        "metadata": {"source": "python_reference"},
    }

    permission_request = state.build_permission_record(
        "permission_request",
        {
            "id": "perm-ref-1",
            "items": [
                {
                    "category": "shell",
                    "pattern": "npm install *",
                    "metadata": {"tool": "bash", "summary": "npm install pkg"},
                }
            ],
        },
    )
    permission_response = state.build_permission_record(
        "permission_response",
        {"request_id": "perm-ref-1", "responses": {"default": "always"}},
    )
    permission_contract = {
        "schema_version": "bb.permission.v1",
        "request_id": permission_request["request_id"],
        "category": permission_request["category"],
        "pattern": permission_request["pattern"],
        "scope": "workspace",
        "metadata": dict(permission_request.get("metadata") or {}),
        "decision": permission_response["decision"],
        "decision_source": "user_prompt",
        "reason": "reference fixture",
        "requires_host_interaction": True,
        "audit_refs": [],
    }

    state._last_ctree_node_id = "ctree-ref-1"
    state._last_ctree_snapshot = {"node_count": 1}
    task_record = state.build_task_record(
        {
            "kind": "subagent_spawned",
            "taskId": "task-ref-1",
            "sessionId": "sess-child-ref-1",
            "subagentType": "explore",
            "status": "running",
            "description": "Explore repository surface",
        }
    )
    task_contract = {
        "schema_version": "bb.task.v1",
        "task_id": task_record["task_id"],
        "parent_task_id": None,
        "session_id": task_record["session_id"],
        "kind": task_record["kind"],
        "task_type": task_record.get("subagent_type"),
        "status": task_record["lifecycle_status"],
        "depth": 1,
        "description": task_record.get("description"),
        "visibility": "host",
        "metadata": {
            "ctree_node_id": task_record.get("ctree_node_id"),
            "ctree_snapshot": task_record.get("ctree_snapshot"),
        },
    }

    checkpoint_contract = build_checkpoint_metadata_record(
        CheckpointSummary(
            checkpoint_id="ckpt-ref-1",
            created_at=1234567890,
            preview="reference fixture",
            tracked_files=2,
            additions=4,
            deletions=1,
            has_untracked_changes=False,
        )
    )

    runtime = SimpleNamespace(descriptor=SimpleNamespace(provider_id="openai", runtime_id="responses_api"))
    request_record = ProviderInvoker._build_exchange_request_record(
        object(),
        runtime=runtime,
        model="openai/gpt-5.2",
        send_messages=[{"role": "user", "content": "hello"}],
        tools_schema=[{"name": "bash"}],
        stream=False,
        route_id="primary",
        turn_index=1,
    )
    request_record["exchange_id"] = "px-ref-1"
    provider_result = ProviderResult(
        messages=[SimpleNamespace(finish_reason="stop")],
        raw_response={},
        usage={"input_tokens": 3, "output_tokens": 2},
        model="openai/gpt-5.2",
        metadata={"latency_ms": 10},
        encrypted_reasoning=None,
    )
    exchange_record = ProviderInvoker._build_exchange_response_record(
        object(),
        exchange_request=request_record,
        result=provider_result,
    )
    provider_exchange_contract = {
        "schema_version": "bb.provider_exchange.v1",
        "exchange_id": exchange_record["exchange_id"],
        "request": {
            "provider_family": exchange_record["request"]["provider_family"],
            "runtime_id": exchange_record["request"]["runtime_id"],
            "route_id": exchange_record["request"].get("route_id"),
            "model": exchange_record["request"]["model"],
            "stream": exchange_record["request"]["stream"],
            "message_count": exchange_record["request"].get("message_count"),
            "tool_count": exchange_record["request"].get("tool_count"),
            "metadata": dict(exchange_record["request"].get("metadata") or {}),
        },
        "response": dict(exchange_record["response"]),
    }
    provider_fallback_exchange_contract = {
        "schema_version": "bb.provider_exchange.v1",
        "exchange_id": "px-ref-fallback-1",
        "request": {
            "provider_family": "openai",
            "runtime_id": "responses_api",
            "route_id": "fallback",
            "model": "openai/gpt-5.2-mini",
            "stream": False,
            "message_count": 1,
            "tool_count": 0,
            "metadata": {
                "message_roles": ["user"],
                "tool_names": [],
                "route_selected": True,
                "stream_requested": True,
                "transport": "provider_runtime.invoke",
            },
        },
        "response": {
            "message_count": 1,
            "finish_reasons": ["stop"],
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "metadata": {
                "provider_family": "openai",
                "runtime_id": "responses_api",
                "route_id": "fallback",
                "stream_requested": False,
                "fallback_from_stream": True,
                "fallback_reason": "stream_rejected",
                "message_count": 1,
                "finish_reason_count": 1,
            },
            "evidence_refs": [],
        },
    }

    outcome = build_tool_execution_outcome_record("run_shell", {"stdout": "hi", "exit_code": 0})
    tool_execution_contract = {
        "schemaVersion": "bb.tool_execution_outcome.v1",
        "callId": "call-run-shell-1",
        "terminalState": outcome["terminal_state"],
        "result": outcome["raw"],
        "metadata": {"tool": outcome["tool"], "ok": outcome["ok"]},
    }
    render = build_tool_model_render_record("run_shell", {"stdout": "hi", "exit_code": 0})
    tool_render_contract = {
        "schemaVersion": "bb.tool_model_render.v1",
        "callId": "call-run-shell-1",
        "parts": [render],
        "visibility": "model",
        "truncation": {"applied": bool(render["truncated"]), "max_preview_chars": 120},
        "metadata": {"tool": render["tool"], "terminal_state": render["terminal_state"]},
    }

    denied_outcome = build_tool_execution_outcome_record(
        "run_shell",
        {"error": "blocked by policy", "guardrail": "workspace_guard_violation"},
    )
    denied_render = build_tool_model_render_record(
        "run_shell",
        {"error": "blocked by policy", "guardrail": "workspace_guard_violation"},
    )
    tool_denied_execution_contract = {
        "schemaVersion": "bb.tool_execution_outcome.v1",
        "callId": "call-run-shell-denied-1",
        "terminalState": denied_outcome["terminal_state"],
        "result": denied_outcome["raw"],
        "metadata": {"tool": denied_outcome["tool"], "ok": denied_outcome["ok"]},
    }
    tool_denied_render_contract = {
        "schemaVersion": "bb.tool_model_render.v1",
        "callId": "call-run-shell-denied-1",
        "parts": [denied_render],
        "visibility": "model",
        "truncation": {"applied": bool(denied_render["truncated"]), "max_preview_chars": 120},
        "metadata": {"tool": denied_render["tool"], "terminal_state": denied_render["terminal_state"]},
    }

    background_task_contract = {
        "schema_version": "bb.task.v1",
        "task_id": "task-background-1",
        "parent_task_id": "task-root-1",
        "session_id": "sess-ref-1",
        "kind": "background_task",
        "task_type": "background",
        "status": "sleeping",
        "depth": 1,
        "description": "Background polling worker",
        "visibility": "host",
        "metadata": {"wake_policy": "event_driven"},
    }

    checkpoint_longrun_contract = build_longrun_checkpoint_metadata_record(
        path="meta/checkpoints/longrun_state_ep_2_resume.json",
        episode=2,
        phase="resume",
        updated_at=123.5,
    )

    replay_session_reference = {
        "schema_version": "bb.replay_session.v1",
        "scenario_id": "provider-fallback-replay",
        "lane_id": "python-reference-semantic",
        "comparator_class": "normalized-trace-equal",
        "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        "tool_results": [{"call_id": "call-run-shell-1", "status": "completed"}],
        "completion_summary": {"completed": True, "reason": "stop"},
        "evidence_refs": ["provider_exchange/reference_fixture.json"],
        "strictness": "semantic-reference",
        "notes": "Reference replay session anchored to normalized provider exchange and transcript outputs.",
    }

    return {
        "kernel_event/reference_fixture.json": {
            "fixture_family": "kernel_event",
            "fixture_id": "kernel_event_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.kernel_event.v1",
            "reference_output": kernel_contract,
        },
        "session_transcript/reference_fixture.json": {
            "fixture_family": "session_transcript",
            "fixture_id": "session_transcript_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.session_transcript.v1",
            "reference_output": transcript_contract,
        },
        "permission/reference_fixture.json": {
            "fixture_family": "permission",
            "fixture_id": "permission_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.permission.v1",
            "reference_output": permission_contract,
        },
        "task_subagent/reference_fixture.json": {
            "fixture_family": "task_subagent",
            "fixture_id": "task_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.task.v1",
            "reference_output": task_contract,
        },
        "checkpoint_metadata/reference_fixture.json": {
            "fixture_family": "checkpoint_metadata",
            "fixture_id": "checkpoint_metadata_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.checkpoint_metadata.v1",
            "reference_output": checkpoint_contract,
        },
        "provider_exchange/reference_fixture.json": {
            "fixture_family": "provider_exchange",
            "fixture_id": "provider_exchange_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.provider_exchange.v1",
            "reference_output": provider_exchange_contract,
        },
        "provider_exchange/reference_fallback_fixture.json": {
            "fixture_family": "provider_exchange",
            "fixture_id": "provider_exchange_fallback_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.provider_exchange.v1",
            "reference_output": provider_fallback_exchange_contract,
        },
        "tool_lifecycle/reference_execution_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_execution_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.tool_execution_outcome.v1",
            "reference_output": tool_execution_contract,
        },
        "tool_lifecycle/reference_render_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_render_python_reference",
            "comparator_class": "model-visible-equal",
            "support_tier": "reference-engine",
            "contract": "bb.tool_model_render.v1",
            "reference_output": tool_render_contract,
        },
        "tool_lifecycle/reference_denied_execution_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_execution_denied_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.tool_execution_outcome.v1",
            "reference_output": tool_denied_execution_contract,
        },
        "tool_lifecycle/reference_denied_render_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_render_denied_python_reference",
            "comparator_class": "model-visible-equal",
            "support_tier": "reference-engine",
            "contract": "bb.tool_model_render.v1",
            "reference_output": tool_denied_render_contract,
        },
        "task_subagent/reference_background_fixture.json": {
            "fixture_family": "task_subagent",
            "fixture_id": "task_background_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.task.v1",
            "reference_output": background_task_contract,
        },
        "checkpoint_metadata/reference_longrun_fixture.json": {
            "fixture_family": "checkpoint_metadata",
            "fixture_id": "checkpoint_metadata_longrun_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.checkpoint_metadata.v1",
            "reference_output": checkpoint_longrun_contract,
        },
        "replay_session/reference_fixture.json": {
            "fixture_family": "replay_session",
            "fixture_id": "replay_session_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": "bb.replay_session.v1",
            "reference_output": replay_session_reference,
        },
        "coordination/reference_complete_fixture.json": _build_coordination_reference_complete_fixture(),
        "coordination/reference_blocked_fixture.json": _build_coordination_reference_blocked_fixture(),
        "coordination/reviewed_complete_fixture.json": _build_coordination_reviewed_complete_fixture(),
        "coordination/reviewed_blocked_fixture.json": _build_coordination_reviewed_blocked_fixture(),
        "coordination/directive_retry_fixture.json": _build_coordination_directive_retry_fixture(),
        "coordination/multi_worker_complete_fixture.json": _build_coordination_multi_worker_complete_fixture(),
        "coordination/multi_worker_blocked_fixture.json": _build_coordination_multi_worker_blocked_fixture(),
        "coordination/longrun_no_progress_fixture.json": _build_coordination_longrun_no_progress_fixture(),
        "coordination/longrun_retryable_failure_fixture.json": _build_coordination_longrun_retryable_failure_fixture(),
        "coordination/longrun_human_required_fixture.json": _build_coordination_longrun_human_required_fixture(),
        "coordination/intervention_continue_fixture.json": _build_coordination_intervention_continue_fixture(),
        "coordination/delegated_verification_pass_fixture.json": _build_coordination_delegated_verification_pass_fixture(),
        "coordination/delegated_verification_fail_fixture.json": _build_coordination_delegated_verification_fail_fixture(),
    }


def write_python_reference_contract_fixtures() -> None:
    for rel, payload in build_python_reference_contract_fixtures().items():
        target = FIXTURE_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_python_reference_contract_fixtures()
    print("ok")
