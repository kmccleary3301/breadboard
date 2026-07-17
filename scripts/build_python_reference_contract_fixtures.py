from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointSummary, build_checkpoint_metadata_record
from agentic_coder_prototype.longrun.checkpoint import build_longrun_checkpoint_metadata_record
from agentic_coder_prototype.conductor_execution import (
    build_tool_execution_outcome_record,
    build_tool_model_render_record,
)
from agentic_coder_prototype.orchestration.coordination import (
    build_blocked_signal_proposal,
    build_signal_proposal,
    validate_signal_proposal,
)
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.schema import TeamConfig
from agentic_coder_prototype.provider import ProviderInvoker
from agentic_coder_prototype.provider.runtime import ProviderResult
from agentic_coder_prototype.state.session_state import SessionState
from scripts.e4_parity.validators.registries import schema_generation_default

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "conformance" / "engine_fixtures"

FROZEN_REFERENCE_FIXTURE_SHA256 = {
    "coordination/delegated_verification_fail_fixture.json": "e6e92e261594f8ba37c1b7b2a703c47bf92aa59db5df355a858e343ae0bbb7f2",
    "coordination/delegated_verification_pass_fixture.json": "f5371d81f693022a4504702b7059355b039c0bfec9d58ae25f8a63beaa145723",
    "coordination/intervention_continue_fixture.json": "46c54d836912acb568853cd4372846149f7cc80a6ad835fdf3041b7cfa3fe27c",
    "coordination/longrun_human_required_fixture.json": "f20107388c4d3c5cad56b9f6e02cdaa2f51518712596c2289b4786c737018785",
    "coordination/longrun_no_progress_fixture.json": "fb6819370c98f89610649adc8057ccc31b75fb9b66b3b914c753b462da2f43ea",
    "coordination/longrun_retryable_failure_fixture.json": "74e936b6b970f1bc43534614c36f68e56d9f597714655a0afa8bca2b329776f9",
    "coordination/multi_worker_blocked_fixture.json": "f3134d3e3a39e85bb956a658463007c200bb8838f36868035f40ab34c2fbe7ab",
    "coordination/multi_worker_complete_fixture.json": "378321935a59048dd4ad715182a20dadaf4d2fb14cbc95292f426ae445abb3d5",
    "coordination/reference_blocked_fixture.json": "1ed4d339b055124447b1a4b9acfc86ed21f944cf7a859456f3aa094b816db8f0",
    "coordination/reference_complete_fixture.json": "890bf0805d89ba54db7e84ba6bf14f9490fedb8f6f724b9c2c92ee68d4826dd8",
    "task_subagent/reference_background_fixture.json": "d529b3d25279bb66dbdac38b84c98346d1085fe31b6947e2c62d3b88f2c8809c",
    "task_subagent/reference_fixture.json": "2c225df18c8c857077d2eacb9fd6e44648e174c2d112f396033aba749ef86fef",
}
FROZEN_REFERENCE_FIXTURE_PATHS = frozenset(FROZEN_REFERENCE_FIXTURE_SHA256)


def _coordination_team() -> TeamConfig:
    return TeamConfig.from_dict({"team": {"id": "coord-fixture"}})


def _coordination_descriptor(task_id: str, *, parent_task_id: str | None = None, subscribed: bool = False) -> Dict[str, Any]:
    descriptor: Dict[str, Any] = {
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


def _normalize_fixture_value(value: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_fixture_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_fixture_value(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


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


def build_active_python_reference_contract_fixtures() -> Dict[str, Dict[str, Any]]:
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
        "schema_version": schema_generation_default("kernel_event_log"),
        "event_id": "evt-ref-1",
        "run_id": "run-ref-1",
        "session_id": kernel_record["session_id"],
        "seq": kernel_record["seq"],
        "occurred_at_utc": "2026-03-08T00:00:00Z",
        "actor": {"actor_kind": "agent", "actor_id": "python_reference"},
        "visibility": {"model_visible": False, "provider_visible": False, "host_visible": True},
        "kind": kernel_record["type"],
        "payload": kernel_record["payload"],
        "payload_schema_version": None,
        "turn_id": "turn-1",
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
        "schema_version": schema_generation_default("session_transcript_snapshot"),
        "session_id": "sess-ref-1",
        "run_id": "run-ref-1",
        "event_cursor": 1,
        "items": [
            {
                "kind": "assistant_message",
                "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True},
                "content": {"text": "hello"},
                "content_schema_version": None,
                "event_id": "evt-ref-1",
                "seq": 1,
                "provenance": {"source": "fixture", "source_ref": "python_reference"},
            }
        ],
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
        "schema_version": schema_generation_default("tool_execution_outcome"),
        "call_id": "call-run-shell-1",
        "terminal_state": outcome["terminal_state"],
        "completed_at_utc": "2026-03-08T00:00:00Z",
        "result": outcome["raw"],
        "metadata": {"tool": outcome["tool"], "ok": outcome["ok"]},
    }
    render = build_tool_model_render_record("run_shell", {"stdout": "hi", "exit_code": 0})
    tool_render_contract = {
        "schema_version": schema_generation_default("tool_render"),
        "call_id": "call-run-shell-1",
        "parts": [{"part_kind": "text", "content": render["preview"], "truncated": bool(render["truncated"])}],
        "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True},
        "truncation": {"truncated": bool(render["truncated"])},
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
        "schema_version": schema_generation_default("tool_execution_outcome"),
        "call_id": "call-run-shell-denied-1",
        "terminal_state": denied_outcome["terminal_state"],
        "completed_at_utc": "2026-03-08T00:00:00Z",
        "result": denied_outcome["raw"],
        "metadata": {"tool": denied_outcome["tool"], "ok": denied_outcome["ok"]},
    }
    tool_denied_render_contract = {
        "schema_version": schema_generation_default("tool_render"),
        "call_id": "call-run-shell-denied-1",
        "parts": [{"part_kind": "error", "content": denied_render["preview"], "truncated": bool(denied_render["truncated"])}],
        "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True},
        "truncation": {"truncated": bool(denied_render["truncated"])},
        "metadata": {"tool": denied_render["tool"], "terminal_state": denied_render["terminal_state"]},
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
            "contract": schema_generation_default("kernel_event_log"),
            "reference_output": kernel_contract,
        },
        "session_transcript/reference_fixture.json": {
            "fixture_family": "session_transcript",
            "fixture_id": "session_transcript_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": schema_generation_default("session_transcript_snapshot"),
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
            "contract": schema_generation_default("tool_execution_outcome"),
            "reference_output": tool_execution_contract,
        },
        "tool_lifecycle/reference_render_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_render_python_reference",
            "comparator_class": "model-visible-equal",
            "support_tier": "reference-engine",
            "contract": schema_generation_default("tool_render"),
            "reference_output": tool_render_contract,
        },
        "tool_lifecycle/reference_denied_execution_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_execution_denied_python_reference",
            "comparator_class": "normalized-trace-equal",
            "support_tier": "reference-engine",
            "contract": schema_generation_default("tool_execution_outcome"),
            "reference_output": tool_denied_execution_contract,
        },
        "tool_lifecycle/reference_denied_render_fixture.json": {
            "fixture_family": "tool_lifecycle",
            "fixture_id": "tool_render_denied_python_reference",
            "comparator_class": "model-visible-equal",
            "support_tier": "reference-engine",
            "contract": schema_generation_default("tool_render"),
            "reference_output": tool_denied_render_contract,
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
        "coordination/reviewed_complete_fixture.json": _build_coordination_reviewed_complete_fixture(),
        "coordination/reviewed_blocked_fixture.json": _build_coordination_reviewed_blocked_fixture(),
        "coordination/directive_retry_fixture.json": _build_coordination_directive_retry_fixture(),
    }


def _read_verified_frozen_reference_fixtures() -> Dict[str, bytes]:
    verified = {}
    for relative_path, expected_digest in sorted(FROZEN_REFERENCE_FIXTURE_SHA256.items()):
        try:
            payload = (FIXTURE_ROOT / relative_path).read_bytes()
        except OSError as exc:
            raise ValueError(f"invalid frozen reference fixture: {relative_path}") from exc
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ValueError(f"frozen reference fixture digest mismatch: {relative_path}")
        verified[relative_path] = payload
    return verified


def load_python_reference_contract_fixtures() -> Dict[str, Dict[str, Any]]:
    frozen_fixtures = _read_verified_frozen_reference_fixtures()
    fixtures = build_active_python_reference_contract_fixtures()
    for relative_path, frozen_bytes in frozen_fixtures.items():
        try:
            payload = json.loads(frozen_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid frozen reference fixture: {relative_path}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("reference_output"), dict):
            raise ValueError(f"invalid frozen reference fixture payload: {relative_path}")
        fixtures[relative_path] = payload
    return fixtures


def write_active_python_reference_contract_fixtures() -> None:
    for rel, payload in load_python_reference_contract_fixtures().items():
        if rel in FROZEN_REFERENCE_FIXTURE_PATHS:
            continue
        target = FIXTURE_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_active_python_reference_contract_fixtures()
    print("ok")
