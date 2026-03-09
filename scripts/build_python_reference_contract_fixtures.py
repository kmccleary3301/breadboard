from __future__ import annotations

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
from agentic_coder_prototype.provider_invoker import ProviderInvoker
from agentic_coder_prototype.provider_runtime import ProviderResult
from agentic_coder_prototype.state.session_state import SessionState

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "conformance" / "engine_fixtures"


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
    }


def write_python_reference_contract_fixtures() -> None:
    for rel, payload in build_python_reference_contract_fixtures().items():
        target = FIXTURE_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_python_reference_contract_fixtures()
    print("ok")
