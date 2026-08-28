from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from breadboard_engine.api.cli_bridge.events import EventType, SessionEvent
from breadboard_engine.api.cli_bridge.models import (
    SessionCreateRequest,
    SessionInputRequest,
    SessionStatus,
    SessionTurnCancelRequest,
)
from breadboard_engine.api.cli_bridge.registry import (
    SessionRecord,
    SessionRegistry,
    TurnRecord,
)
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.runtime_event_projector import (
    BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES,
    BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES,
    KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES,
    RuntimeProtocolError,
    _strip_completion_sentinels,
)
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.provider.contracts import (
    strip_public_completion_sentinel_tree,
)
from breadboard_engine.state.session_state import SessionState


class EventCollector:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any], Optional[int]]] = []

    def __call__(
        self, event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None
    ) -> None:
        self.events.append((event_type, payload, turn))

    def of_type(
        self, event_type: str
    ) -> List[Tuple[str, Dict[str, Any], Optional[int]]]:
        return [evt for evt in self.events if evt[0] == event_type]


def test_completion_sentinel_scrubbing_is_recursive_and_text_scoped() -> None:
    payload = [
        {"type": "text", "text": "answer\nTASK COMPLETE\n"},
        {
            "type": "tool_call",
            "call_id": "call-1",
            "name": "echo",
            "arguments": {"literal": "TASK COMPLETE"},
        },
        {
            "type": "text",
            "text": "nested",
            "content": [{"type": "text", "text": ">>>>>> END RESPONSE"}],
        },
    ]

    assert _strip_completion_sentinels(payload) == [
        {"type": "text", "text": "answer"},
        {
            "type": "tool_call",
            "call_id": "call-1",
            "name": "echo",
            "arguments": {"literal": "TASK COMPLETE"},
        },
        {
            "type": "text",
            "text": "nested",
            "content": [{"type": "text", "text": ""}],
        },
    ]


def test_public_completion_sentinel_scrubbing_covers_every_nested_field() -> None:
    payload = {
        "summary": {
            "final_message": "answer\nTASK COMPLETE\n",
            "nested": {
                "opaque": [">>>>>> END RESPONSE", {"reason": "safe"}],
            },
        },
        "mode": "build",
    }

    assert strip_public_completion_sentinel_tree(payload) == {
        "summary": {
            "final_message": "answer",
            "nested": {"opaque": ["", {"reason": "safe"}]},
        },
        "mode": "build",
    }


def test_session_state_emits_unique_assistant_events_with_legacy_payload_shape_per_turn() -> (
    None
):
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    # Initial user prompt (no turn yet) should emit a user_message event once.
    state.add_message({"role": "user", "content": "hello"}, to_provider=True)
    user_events = collector.of_type("user_message")
    assert len(user_events) == 1
    assert user_events[0][1]["message"]["content"] == "hello"
    assert user_events[0][2] is None  # No active turn yet

    # Begin a turn and emit assistant messages with both transcript and provider copies.
    collector.events.clear()
    state.begin_turn(1)
    state.add_message(
        {"role": "assistant", "content": "draft patch"}, to_provider=False
    )
    state.add_message({"role": "assistant", "content": "draft patch"}, to_provider=True)

    assistant_events = collector.of_type("assistant_message")
    assert len(assistant_events) == 1
    assert assistant_events[0][1] == {
        "message": {"role": "assistant", "content": "draft patch"},
        "seq": 7,
    }
    assert assistant_events[0][2] == 1
    assistant_messages = [
        message for message in state.messages if message.get("role") == "assistant"
    ]
    assistant_provider_messages = [
        message
        for message in state.provider_messages
        if message.get("role") == "assistant"
    ]
    assert len(assistant_messages) == 1
    assert len(assistant_provider_messages) == 1


def test_session_state_sanitizes_user_event_and_ctree_payloads() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    polluted = (
        "Hello there\n\nindustry_coder_refs/codex/codex-rs/core/gpt_5_codex_prompt.md"
    )
    state.add_message({"role": "user", "content": polluted}, to_provider=True)

    user_events = collector.of_type("user_message")
    assert len(user_events) == 1
    assert user_events[0][1]["message"]["content"] == "Hello there"

    ctree_events = collector.of_type("ctree_node")
    assert ctree_events
    node = (ctree_events[-1][1].get("node") or {}).get("payload") or {}
    assert node.get("role") == "user"
    assert node.get("content") == "Hello there"

    assert state.messages[0]["content"] == polluted


def test_session_state_hides_internal_validation_user_messages_from_transcript_events() -> (
    None
):
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    state.add_message({"role": "user", "content": "real request"}, to_provider=True)
    state.begin_turn(1)
    state.add_message(
        {"role": "user", "content": "<VALIDATION_ERROR>\nretry\n</VALIDATION_ERROR>"},
        to_provider=True,
    )
    state.add_message(
        {
            "role": "user",
            "content": "real request\n\n<WORKSPACE_TOOL_REQUIRED>\nUse tools.\n</WORKSPACE_TOOL_REQUIRED>",
        },
        to_provider=True,
    )

    user_events = collector.of_type("user_message")
    assert len(user_events) == 1
    assert user_events[0][1]["message"]["content"] == "real request"
    assert len(state.provider_messages) == 3


def test_session_state_tool_events_cover_calls_and_results() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)
    state.begin_turn(2)

    state.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": "{}"},
                }
            ],
        },
        to_provider=False,
    )
    state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={"is_run_shell": True},
        result={"stdout": "/tmp\n", "exit": 0},
    )
    state.add_message({"role": "tool", "content": "ok"}, to_provider=False)

    tool_call_events = collector.of_type("tool_call")
    assert len(tool_call_events) == 1
    assert tool_call_events[0][1]["call"]["function"]["name"] == "run_shell"

    tool_result_events = collector.of_type("tool_result")
    assert (
        len(tool_result_events) == 2
    )  # one from record_tool_event, one from tool role message
    names = {
        evt[1].get("tool") or evt[1].get("message", {}).get("role")
        for evt in tool_result_events
    }
    assert "run_shell" in names
    engine_tool_result = next(
        evt for evt in tool_result_events if evt[1].get("tool") == "run_shell"
    )
    assert engine_tool_result[1]["result"]["stdout"] == "/tmp\n"
    assert engine_tool_result[1]["result"]["exit"] == 0


def test_session_state_tracks_successful_user_facing_write_targets() -> None:
    state = SessionState("ws", "image", {})

    state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["dummy_smtp.c", "README.md", "Makefile"],
            "requested_write_targets": ["Makefile"],
            "requested_write_matches": ["Makefile"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )

    assert state.tool_usage_summary["successful_writes"] == 1
    assert state.tool_usage_summary["successful_user_facing_writes"] == 1
    assert state.tool_usage_summary["successful_requested_write_targets"] == [
        "Makefile"
    ]
    assert state.tool_usage_summary["successful_user_facing_write_targets"] == [
        "dummy_smtp.c",
        "README.md",
        "Makefile",
    ]


def test_session_state_emits_ctree_node_events() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    state.add_message({"role": "assistant", "content": "hello"}, to_provider=False)

    ctree_events = collector.of_type("ctree_node")
    assert ctree_events, "Expected ctree_node event emission"
    payload = ctree_events[-1][1]
    node = payload.get("node") or {}
    snapshot = payload.get("snapshot") or {}
    assert node.get("id")
    assert snapshot.get("node_count")


def test_session_state_builds_kernel_event_record_and_normalizes_transcript() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)
    state.set_provider_metadata("session_id", "sess-123")

    record = state.build_kernel_event_record(
        "assistant_message", {"message": {"role": "assistant"}}, turn=4, seq=9
    )
    assert record["type"] == "assistant_message"
    assert record["turn"] == 4
    assert record["seq"] == 9
    assert record["session_id"] == "sess-123"
    assert record["payload"]["seq"] == 9
    assert record["classification"] == "canonical"
    assert record["family"] == "message.assistant"
    assert record["actor"] == "engine"
    assert record["visibility"] == "transcript"

    entry = {"assistant": "hello"}
    state.add_transcript_entry(entry)
    entry["assistant"] = "mutated"
    assert state.transcript[-1]["assistant"] == "hello"
    assert state.derive_transcript_contract_items()[-1] == {
        "kind": "assistant_message",
        "visibility": "model",
        "content": "hello",
        "provenance": {"source": "legacy_transcript_entry", "legacy_key": "assistant"},
    }


def test_session_state_builds_permission_task_and_guardrail_records() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)
    state.set_provider_metadata("session_id", "sess-123")
    state.begin_turn(7)

    permission_request = state.build_permission_record(
        "permission_request",
        {
            "id": "perm-1",
            "items": [
                {
                    "category": "shell",
                    "pattern": "npm install *",
                    "metadata": {"tool": "bash"},
                }
            ],
        },
    )
    assert permission_request["request_id"] == "perm-1"
    assert permission_request["category"] == "shell"
    assert permission_request["pattern"] == "npm install *"

    permission_response = state.build_permission_record(
        "permission_response",
        {"request_id": "perm-1", "responses": {"default": "once"}},
    )
    assert permission_response["decision"] == "once"

    state._last_ctree_node_id = "ctree-7"
    state._last_ctree_snapshot = {"node_count": 3}
    task_record = state.build_task_record(
        {
            "kind": "subagent_spawned",
            "taskId": "task-7",
            "sessionId": "sess-child-7",
            "subagentType": "explore",
            "status": "running",
        }
    )
    assert task_record["task_id"] == "task-7"
    assert task_record["session_id"] == "sess-child-7"
    assert task_record["subagent_type"] == "explore"
    assert task_record["lifecycle_status"] == "running"
    assert task_record["ctree_node_id"] == "ctree-7"

    guardrail_record = state.build_guardrail_record(
        "context_window_warning", {"remaining": 1024}
    )
    assert guardrail_record["type"] == "context_window_warning"
    assert guardrail_record["turn"] == 7
    assert guardrail_record["payload"]["remaining"] == 1024


def test_session_state_classifies_projection_and_legacy_events() -> None:
    state = SessionState("ws", "image", {})
    todo_meta = state.classify_runtime_event_type("todo_event")
    assert todo_meta["classification"] == "projection_only"
    assert todo_meta["family"] == "projection.todo_snapshot"

    unknown_meta = state.classify_runtime_event_type("mystery_event")
    assert unknown_meta["classification"] == "legacy_unclassified"
    assert unknown_meta["family"] == "legacy.unclassified"


def test_session_state_event_family_registry_covers_public_runtime_event_types() -> (
    None
):
    registry = SessionState.event_family_registry()

    expected = {
        "assistant_message": ("canonical", "message.assistant"),
        "user_message": ("canonical", "message.user"),
        "provider_response": ("canonical", "provider.exchange"),
        "tool_call": ("canonical", "tool.called"),
        "tool_result": ("canonical", "tool.completed"),
        "permission_request": ("canonical", "permission.requested"),
        "permission_response": ("canonical", "permission.decided"),
        "task_event": ("canonical", "task.progress"),
        "coordination_signal": ("canonical", "coordination.signal"),
        "coordination_review_verdict": ("canonical", "coordination.review_verdict"),
        "coordination_directive": ("canonical", "coordination.directive"),
        "turn_start": ("canonical", "turn.started"),
        "todo_event": ("projection_only", "projection.todo_snapshot"),
        "ctree_snapshot": ("projection_only", "projection.ctree_snapshot"),
        "guardrail_event": ("audit_only", "warning.guardrail"),
        "lifecycle_event": ("audit_only", "run.lifecycle"),
    }
    for event_type, (classification, family) in expected.items():
        assert registry[event_type]["classification"] == classification
        assert registry[event_type]["family"] == family


def test_session_state_coordination_inspection_snapshot_is_read_only() -> None:
    state = SessionState("ws", "image", {})
    signal = state.record_coordination_signal(
        {
            "signal_id": "sig-1",
            "code": "human_required",
            "task_id": "task_worker_1",
            "payload": {
                "required_input": "Confirm deploy target",
                "blocking_reason": "Production deploy requires operator approval",
            },
        }
    )
    state.record_coordination_review_verdict(
        {
            "verdict_id": "rev-1",
            "verdict_code": "human_required",
            "subject": {
                "signal_id": "sig-1",
                "source_task_id": "task_worker_1",
                "mission_task_id": "task_supervisor_1",
            },
            "blocking_reason": "Production deploy requires operator approval",
        }
    )
    state.record_coordination_directive(
        {
            "directive_id": "dir-1",
            "directive_code": "escalate",
            "based_on_verdict_id": "rev-1",
            "issuer_role": "supervisor",
        }
    )

    snapshot = state.coordination_inspection_snapshot()
    assert snapshot["latest_signal_by_code"]["human_required"]["signal_id"] == "sig-1"
    assert snapshot["unresolved_interventions"][0]["review_verdict_id"] == "rev-1"
    assert (
        snapshot["unresolved_interventions"][0]["required_input"]
        == "Confirm deploy target"
    )
    assert snapshot["unresolved_interventions"][0]["allowed_host_actions"] == []
    assert snapshot["resolved_interventions"] == []

    signal["signal_id"] = "mutated"
    assert snapshot["signals"][0]["signal_id"] == "sig-1"


def test_session_state_coordination_inspection_marks_host_responses_as_resolved() -> (
    None
):
    state = SessionState("ws", "image", {})
    state.record_coordination_signal(
        {
            "signal_id": "sig-2",
            "code": "human_required",
            "task_id": "task_worker_1",
            "payload": {
                "required_input": "Approve rerun",
                "blocking_reason": "Operator sign-off required",
            },
        }
    )
    state.record_coordination_review_verdict(
        {
            "verdict_id": "rev-2",
            "verdict_code": "human_required",
            "subject": {
                "signal_id": "sig-2",
                "source_task_id": "task_worker_1",
                "mission_task_id": "task_supervisor_1",
            },
        }
    )
    state.record_coordination_directive(
        {
            "directive_id": "dir-2",
            "directive_code": "continue",
            "based_on_verdict_id": "rev-2",
            "issuer_role": "host",
        }
    )

    snapshot = state.coordination_inspection_snapshot()
    assert snapshot["unresolved_interventions"] == []
    assert snapshot["resolved_interventions"][0]["review_verdict_id"] == "rev-2"
    assert snapshot["resolved_interventions"][0]["allowed_host_actions"] == []
    assert (
        snapshot["resolved_interventions"][0]["host_responses"][0]["directive_id"]
        == "dir-2"
    )


def test_cli_bridge_runtime_event_sets_match_kernel_vs_projection_boundary() -> None:
    registry = SessionState.event_family_registry()

    assert "assistant_message" in KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES
    assert "todo_event" in KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES
    assert "assistant.message.delta" in BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES
    assert "run_finished" in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES
    assert "limits_update" in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES
    assert "warning" in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES

    for event_type in KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES:
        if event_type in {"tool.result"}:
            continue
        assert event_type in registry, (
            f"{event_type} should be classified in SessionState registry"
        )

    for event_type in (
        BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES | BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES
    ):
        assert event_type not in registry, (
            f"{event_type} should remain outside kernel registry"
        )


def test_session_runner_translates_runtime_events() -> None:
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-1", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="cfg.yaml", task="do work")

    runner = SessionRunner(session=record, registry=registry, request=request)
    event_registry = SessionState.event_family_registry()
    translated = runner._translate_runtime_event(
        "assistant_message",
        {"message": {"role": "assistant", "content": "hi"}},
        turn=3,
    )
    assert translated is not None
    evt_type, payload, turn, contract = translated
    assert evt_type is EventType.ASSISTANT_MESSAGE
    assert payload == {
        "text": "hi",
        "message": {"role": "assistant", "content": "hi"},
    }
    assert turn == 3
    assert contract == event_registry["assistant_message"]

    translated_none = runner._translate_runtime_event(
        "assistant_message",
        {"message": {"role": "assistant", "content": None}},
        turn=3,
    )
    assert translated_none is not None
    evt_type, payload, turn, contract = translated_none
    assert evt_type is EventType.ASSISTANT_MESSAGE
    assert payload["text"] == ""
    assert turn == 3
    assert contract == event_registry["assistant_message"]

    delta_translated = runner._translate_runtime_event(
        "assistant_delta",
        {"text": "chunk", "message_id": "m1"},
        turn=3,
    )
    assert delta_translated is not None
    evt_type, payload, turn, contract = delta_translated
    assert evt_type is EventType.ASSISTANT_DELTA
    assert payload["text"] == "chunk"
    assert payload["message_id"] == "m1"
    assert turn == 3
    assert contract["visibility"] == "transcript"

    for stream_event, stream_payload in (
        ("assistant.message.start", {"message_id": "m1"}),
        ("assistant.message.delta", {"message_id": "m1", "delta": "chunk"}),
        ("assistant.message.end", {"message_id": "m1", "text": "chunk"}),
    ):
        stream_translation = runner._translate_runtime_event(
            stream_event,
            stream_payload,
            turn=3,
        )
        assert stream_translation is not None
        assert stream_translation[3]["family"] == "message.assistant.stream"

    tool_delta_translated = runner._translate_runtime_event(
        "assistant.tool_call.delta",
        {
            "index": 0,
            "call_id": "call-1",
            "tool": "read",
            "arguments_delta": '{"path":',
        },
        turn=3,
    )
    assert tool_delta_translated is not None
    evt_type, payload, turn, contract = tool_delta_translated
    assert evt_type is EventType.ASSISTANT_TOOL_CALL_DELTA
    assert payload["arguments_delta"] == '{"path":'
    assert turn == 3
    assert contract["classification"] == "bridge_stream"
    assert contract["visibility"] == "tool"

    with pytest.raises(RuntimeProtocolError):
        runner._translate_runtime_event("unknown", {}, turn=None)

    tool_call_translated = runner._translate_runtime_event(
        "tool_call",
        {
            "call": {
                "id": "call-1",
                "function": {"name": "run_shell", "arguments": {"command": "pwd"}},
            }
        },
        turn=3,
    )
    assert tool_call_translated is not None
    evt_type, payload, turn, contract = tool_call_translated
    assert evt_type is EventType.TOOL_CALL
    assert payload["tool"] == "run_shell"
    assert "_bb_event_contract" not in payload
    assert turn == 3
    assert contract == event_registry["tool_call"]

    tool_result_translated = runner._translate_runtime_event(
        "tool_result",
        {
            "message": {
                "role": "tool",
                "name": "run_shell",
                "tool_call_id": "call-1",
                "content": "ok",
            }
        },
        turn=3,
    )
    assert tool_result_translated is not None
    evt_type, payload, turn, contract = tool_result_translated
    assert evt_type is EventType.TOOL_RESULT
    assert payload["tool"] == "run_shell"
    assert payload["call_id"] == "call-1"
    assert payload["result"] == "ok"
    assert turn == 3
    assert contract == event_registry["tool_result"]

    todo_translated = runner._translate_runtime_event(
        "todo_event",
        {
            "call_id": "todo:1",
            "todo": {"op": "replace", "revision": 1, "scopeKey": "main", "items": []},
        },
        turn=3,
    )
    assert todo_translated is not None
    evt_type, payload, turn, contract = todo_translated
    assert evt_type is EventType.TOOL_RESULT
    assert isinstance(payload.get("todo"), dict)
    assert payload["todo"]["revision"] == 1
    assert turn is None
    assert "_bb_event_contract" not in payload
    assert contract == event_registry["todo_event"]
    assert isinstance(record.metadata, dict)
    assert isinstance(record.metadata.get("todo_last_update"), dict)


def test_session_event_envelope_carries_visibility_contract() -> None:
    event = SessionEvent(
        type=EventType.ASSISTANT_MESSAGE,
        session_id="sess-1",
        payload={"text": "hi"},
        classification="kernel",
        family="message.assistant",
        actor="engine",
        visibility="transcript",
    )

    envelope = event.asdict()
    assert envelope["classification"] == "kernel"
    assert envelope["family"] == "message.assistant"
    assert envelope["actor"] == {"kind": "engine"}
    assert envelope["visibility"] == "transcript"


def test_session_runner_recognizes_replay_after_injected_system_reminder(
    tmp_path,
) -> None:
    fixture = tmp_path / "fixture.jsonl"
    request = SessionCreateRequest(config_path="cfg.yaml", task="", stream=False)
    runner = SessionRunner(
        session=SessionRecord(
            session_id="sess-replay-reminder", status=SessionStatus.STARTING
        ),
        registry=SessionRegistry(),
        request=request,
    )
    prompt = (
        "<system-reminder>\n"
        "Today: 2026-08-20; current working directory: '/tmp'.\n"
        "</system-reminder>\n"
        f"replay:{fixture}\n\n"
        "Trailing compiled system prompt."
    )
    assert runner._parse_replay_path(prompt) == fixture.resolve()


@pytest.mark.asyncio
async def test_session_input_returns_canonical_idempotent_turn_receipt() -> None:
    registry = SessionRegistry()
    record = SessionRecord(
        session_id="sess-input-receipt", status=SessionStatus.RUNNING
    )

    class Runner:
        def __init__(self) -> None:
            self.inputs: list[
                tuple[str, list[str], str | None, str | None, str | None]
            ] = []

        async def enqueue_input(
            self,
            content: str,
            attachments: list[str],
            *,
            input_id: str | None = None,
            turn_id: str | None = None,
        ) -> str:
            self.inputs.append(
                (content, attachments, input_id, turn_id, record.active_turn_id)
            )
            return content

    runner = Runner()
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)
    request = SessionInputRequest(content="continue", client_message_id="client-1")

    first = await service.send_input(record.session_id, request)
    duplicate = await service.send_input(record.session_id, request)

    assert first.model_dump() == {
        "status": "accepted",
        "client_message_id": "client-1",
        "input_id": first.input_id,
        "turn_id": first.turn_id,
        "disposition": "started",
        "original_disposition": "started",
    }
    assert duplicate.model_dump() == {
        **first.model_dump(),
        "disposition": "deduplicated",
    }
    assert runner.inputs == [
        ("continue", [], first.input_id, first.turn_id, first.turn_id),
    ]
    assert record.active_turn_id == first.turn_id


@pytest.mark.asyncio
async def test_cancel_turn_requests_only_the_active_turn_and_is_idempotent(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="sess-active-cancel", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="client-active",
        content="active",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id

    class Runner:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def request_turn_cancellation(self, turn_id: str) -> bool:
            self.requests.append(turn_id)
            return True

        async def finish_queued_turn_cancellation(
            self, queued_turn: TurnRecord, reason: str,
        ) -> None:
            raise AssertionError((queued_turn, reason))

    runner = Runner()
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)
    request = SessionTurnCancelRequest(
        cancellation_request_key="cancel-active",
        reason="user_requested",
    )

    first = await service.cancel_turn(record.session_id, turn.turn_id, request)
    duplicate = await service.cancel_turn(record.session_id, turn.turn_id, request)

    assert first.disposition == "cancellation_requested"
    assert first.original_disposition == "cancellation_requested"
    assert duplicate.disposition == "deduplicated"
    assert duplicate.cancellation_request_id == first.cancellation_request_id
    assert runner.requests == [turn.turn_id]
    assert turn.cancellation_requested is True
    assert turn.cancellation_reason == "user_requested"
    assert turn.terminal_outcome is None
    assert record.status is SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_cancel_turn_terminalizes_queued_turn_without_stopping_session(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="sess-queued-cancel", status=SessionStatus.RUNNING)
    active = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="client-active",
        content="active",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    queued = TurnRecord(
        input_id="input-queued",
        turn_id="turn-queued",
        client_message_id="client-queued",
        content="queued",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id = {active.turn_id: active, queued.turn_id: queued}
    record.active_turn_id = active.turn_id
    record.queued_turn_ids.append(queued.turn_id)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)

    response = await service.cancel_turn(
        record.session_id,
        queued.turn_id,
        SessionTurnCancelRequest(
            cancellation_request_key="cancel-queued",
            reason="superseded",
        ),
    )

    assert response.disposition == "queued_cancelled"
    assert queued.terminal_outcome == "cancelled"
    assert queued.terminal_resolution_committed is True
    assert record.active_turn_id == active.turn_id
    assert list(record.queued_turn_ids) == []
    assert record.status is SessionStatus.RUNNING
    assert [envelope["turn_id"] for envelope in record.terminal_event_envelopes] == [
        queued.turn_id
    ]


@pytest.mark.asyncio
async def test_finish_turn_promotes_queued_turn_without_stopping_dispatcher(tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-turn-promotion", status=SessionStatus.RUNNING
    )
    first = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="client-1",
        content="first",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    second = TurnRecord(
        input_id="input-2",
        turn_id="turn-2",
        client_message_id="client-2",
        content="second",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id = {first.turn_id: first, second.turn_id: second}
    record.active_turn_id = first.turn_id
    record.queued_turn_ids.append(second.turn_id)
    await registry.create(record)
    service = SessionService(registry=registry)
    await service._ensure_dispatcher(record)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )

    assert await runner._finish_turn(first, "completed") is True
    await asyncio.sleep(0)

    assert first.terminal_resolution_committed is True
    assert len(record.terminal_event_envelopes) == 1
    terminal_envelope = record.terminal_event_envelopes[0]
    assert terminal_envelope["seq"] == 1
    assert record.active_turn_id == second.turn_id
    assert second.state == "active"
    assert not record.queued_turn_ids
    assert record.dispatcher_task is not None
    assert not record.dispatcher_task.done()

    # Completion bookkeeping can advance the observed cursor after the retained
    # terminal envelope. Persist only its head identity, never its payload.
    completion_tail = SessionEvent(
        EventType.COMPLETION,
        record.session_id,
        {"summary": {"private": "completion-payload-must-not-persist"}},
        seq=2,
    )
    replay_head = SessionEvent(
        EventType.RUN_FINISHED,
        record.session_id,
        {"logging_dir": "/private/source/path-must-not-persist"},
        seq=3,
    )
    record.event_log.extend((completion_tail, replay_head))
    record.event_seq = 3
    await registry.persist(record)
    retained_bytes = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "completion-payload-must-not-persist" not in retained_bytes
    assert "path-must-not-persist" not in retained_bytes
    retained = json.loads(retained_bytes)
    assert retained["session"]["event_seq"] == replay_head.seq
    assert retained["session"]["event_head_id"] == replay_head.event_id

    await record.event_queue.put(None)
    await record.dispatcher_task
    restarted = SessionRegistry(state_root=tmp_path)
    restored = await restarted.get(record.session_id)
    assert restored is not None
    summary = restored.to_summary()
    assert summary.head_sequence == replay_head.seq
    assert summary.head_event_id == replay_head.event_id
    assert summary.terminal_event_envelopes == [terminal_envelope]
    stream_open = SessionService._stream_open_event(restored)
    assert stream_open.payload["headSequence"] == replay_head.seq
    assert stream_open.payload["headEventId"] == replay_head.event_id


@pytest.mark.asyncio
async def test_finish_turn_rejects_unknown_provider_completion_semantics(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-invalid-provider-completion",
        status=SessionStatus.RUNNING,
    )
    turn = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="client-1",
        content="first",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    await registry.create(record)
    service = SessionService(registry=registry)
    await service._ensure_dispatcher(record)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )

    with pytest.raises(RuntimeError, match="turn_terminal_persistence_failed"):
        await runner._finish_turn(
            turn,
            "completed",
            completed_payload={"usage": {"unknown_provider_counter": 1}},
        )

    assert turn.terminal_outcome is None
    assert turn.terminal_resolution_committed is False
    assert record.terminal_event_envelopes == []
    assert all(event.type is not EventType.TURN_COMPLETED for event in record.event_log)


@pytest.mark.asyncio
async def test_replay_events_preserve_active_turn_correlation(tmp_path) -> None:
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(
        json.dumps(
            {"type": "assistant_message", "payload": {"text": "done"}, "turn": 1}
        )
        + "\n",
        encoding="utf-8",
    )
    record = SessionRecord(
        session_id="sess-replay-correlation", status=SessionStatus.RUNNING
    )
    turn = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="message-1",
        content="replay",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.active_turn_id = turn.turn_id
    record.turns_by_id[turn.turn_id] = turn
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    published: list[tuple[EventType, dict[str, Any]]] = []

    async def capture(
        event_type: EventType, _payload: Dict[str, Any], **kwargs: Any
    ) -> None:
        published.append((event_type, kwargs))

    runner.publish_event_async = capture  # type: ignore[method-assign]
    await runner._execute_replay_task(
        "<system-reminder>\ncontext\n</system-reminder>\n"
        f"replay:{fixture}\n\ncompiled prompt",
        input_id=turn.input_id,
        turn_id=turn.turn_id,
    )
    assistant = next(
        kwargs
        for event_type, kwargs in published
        if event_type is EventType.ASSISTANT_MESSAGE
    )
    assert assistant["input_id"] == "input-1"
    assert assistant["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_replay_completion_strips_nested_control_sentinels(tmp_path) -> None:
    fixture = tmp_path / "completion-fixture.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "type": "completion",
                "payload": {
                    "summary": {
                        "final_message": "answer\nTASK COMPLETE\n",
                        "nested": {"opaque": ">>>>>> END RESPONSE"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = SessionRecord(
        session_id="sess-replay-completion",
        status=SessionStatus.RUNNING,
    )
    turn = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="message-1",
        content="replay",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.active_turn_id = turn.turn_id
    record.turns_by_id[turn.turn_id] = turn
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    published: list[tuple[EventType, dict[str, Any]]] = []

    async def capture(
        event_type: EventType,
        payload: Dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        published.append((event_type, payload))

    runner.publish_event_async = capture  # type: ignore[method-assign]
    result = await runner._execute_replay_task(
        f"replay:{fixture}",
        input_id=turn.input_id,
        turn_id=turn.turn_id,
    )

    assert published == []
    completion = next(
        payload
        for event_type, payload, _turn, _contract in result["_terminal_events"]
        if event_type is EventType.COMPLETION
    )
    assert completion == {
        "summary": {
            "final_message": "answer",
            "nested": {"opaque": ""},
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_entries",
    [
        [{"type": "session_control", "payload": {"action": "stop"}}],
        [
            {
                "type": "assistant_message",
                "payload": {"text": "late", "unknown_semantic": True},
            }
        ],
        [
            {
                "type": "completion",
                "payload": {"summary": {"completed": True, "reason": "replay"}},
            },
            {"type": "assistant_message", "payload": {"text": "too late"}},
        ],
    ],
)
async def test_replay_rejects_entire_fixture_before_publication(
    tmp_path,
    invalid_entries: List[Dict[str, Any]],
) -> None:
    fixture = tmp_path / "invalid-fixture.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant_message",
                        "payload": {"text": "valid"},
                    }
                ),
                *(json.dumps(entry) for entry in invalid_entries),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    record = SessionRecord(
        session_id="sess-replay-rejected",
        status=SessionStatus.RUNNING,
        metadata={"preserved": True},
    )
    turn = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="message-1",
        content="replay",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.active_turn_id = turn.turn_id
    record.turns_by_id[turn.turn_id] = turn
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(
            config_path="cfg.yaml",
            task="",
            stream=False,
        ),
    )
    published: list[EventType] = []

    async def capture(
        event_type: EventType,
        _payload: Dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        published.append(event_type)

    runner.publish_event_async = capture  # type: ignore[method-assign]
    with pytest.raises(RuntimeProtocolError, match="runtime_protocol_error"):
        await runner._execute_replay_task(
            f"replay:{fixture}",
            input_id=turn.input_id,
            turn_id=turn.turn_id,
        )

    assert published == []
    assert record.metadata["preserved"] is True
    assert "replay_fixture" not in record.metadata


def test_execute_task_withholds_success_terminals_until_exchange_validates() -> None:
    record = SessionRecord(
        session_id="sess-invalid-exchange",
        status=SessionStatus.RUNNING,
    )
    turn = TurnRecord(
        input_id="input-1",
        turn_id="turn-1",
        client_message_id="message-1",
        content="run",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.active_turn_id = turn.turn_id
    record.turns_by_id[turn.turn_id] = turn
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(
            config_path="cfg.yaml",
            task="",
            stream=False,
        ),
    )

    class InvalidExchangeAgent:
        _local_mode = True
        config: Dict[str, Any] = {}

        def run_task(self, _task_text: str, **kwargs: Any) -> Dict[str, Any]:
            emitter = kwargs["event_emitter"]
            emitter(
                "completion",
                {"summary": {"completed": True, "reason": "test"}},
            )
            emitter(
                "run_finished",
                {"completed": True, "reason": "test"},
            )
            return {
                "completion_summary": {"completed": True, "reason": "test"},
                "messages": [{"role": "assistant", "content": "not public"}],
                "provider_exchange": {"schema_version": "invalid"},
            }

    runner._agent = InvalidExchangeAgent()
    published: list[EventType] = []

    def capture(event_type: EventType, *_args: Any, **_kwargs: Any) -> None:
        published.append(event_type)

    runner.publish_event = capture  # type: ignore[method-assign]
    with pytest.raises(RuntimeProtocolError, match="runtime_protocol_error"):
        runner._execute_task(
            "run",
            input_id=turn.input_id,
            turn_id=turn.turn_id,
        )

    assert EventType.COMPLETION not in published
    assert EventType.RUN_FINISHED not in published


@pytest.mark.asyncio
async def test_session_runner_replay_task_skips_agent_init(tmp_path) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="sess-replay", status=SessionStatus.STARTING)

    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(
        "\n".join(
            [
                "# replay fixture",
                json.dumps(
                    {
                        "type": "assistant_delta",
                        "payload": {"message_id": "m1", "text": "hello"},
                    }
                ),
                json.dumps(
                    {
                        "type": "warning",
                        "payload": {"message": "resize 160x45 -> 140x40"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "payload": {
                            "call_id": "todo:1",
                            "status": "ok",
                            "error": False,
                            "todo": {
                                "op": "replace",
                                "revision": 1,
                                "items": [],
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    request = SessionCreateRequest(
        config_path="cfg.yaml", task=f"replay:{fixture}", stream=False
    )

    called = {"count": 0}

    def agent_factory(
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        called["count"] += 1
        raise AssertionError("agent_factory should not be called for replay tasks")

    await registry.create(record)

    runner = SessionRunner(
        session=record, registry=registry, request=request, agent_factory=agent_factory
    )
    await runner.start()

    seen_types: List[str] = []
    for _ in range(30):
        evt = await asyncio.wait_for(record.event_queue.get(), timeout=2.0)
        if evt is None:
            break
        seen_types.append(evt.type.value)
        if evt.type is EventType.RUN_FINISHED:
            break

    await runner.stop()
    assert called["count"] == 0
    assert runner._agent is None
    assert "assistant_delta" in seen_types
    assert "warning" in seen_types
    assert "run_finished" in seen_types
    assert isinstance(record.metadata.get("todo_last_update"), dict)


@pytest.mark.asyncio
async def test_session_runner_lazy_init_initializes_agent_for_non_replay(
    tmp_path, monkeypatch
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="sess-nonreplay", status=SessionStatus.STARTING)
    await registry.create(record)
    request = SessionCreateRequest(config_path="cfg.yaml", task="hello", stream=False)
    monkeypatch.delenv("BREADBOARD_ENABLE_REMOTE_STREAM", raising=False)

    called = {"count": 0}

    class FakeAgent:
        def __init__(self) -> None:
            self._local_mode = True
            self.workspace_dir = str(tmp_path / "ws")
            self.config = {"providers": {}}

        def initialize(self) -> None:
            Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)

        def run_task(self, task_text: str, **kwargs) -> Dict[str, Any]:
            kwargs["event_emitter"]("turn_start", {"turn": 1, "seq": 8}, turn=1)
            kwargs["event_emitter"](
                "user_message",
                {"message": {"role": "user", "content": task_text}},
            )
            return {
                "completion_summary": {"completed": True, "reason": "test"},
                "reward_metrics_payload": {},
                "messages": [{"role": "assistant", "content": "ok"}],
                "logging_dir": None,
                "provider_exchange": {
                    "schema_version": "bb.provider_exchange.v2",
                    "exchange_id": "px-session-test",
                    "correlation": {
                        "session_id": kwargs["context"]["session_id"],
                        "input_id": kwargs["context"]["input_id"],
                        "turn_id": kwargs["context"]["turn_id"],
                    },
                    "provider": {
                        "provider_id": "mock",
                        "runtime_id": "mock_chat",
                        "route_id": "mock/dev",
                        "model": "dev",
                    },
                    "request": {
                        "stream": False,
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "hello"}],
                            }
                        ],
                        "tools": [],
                    },
                    "events": [
                        {"sequence": 0, "kind": "response_start"},
                        {
                            "sequence": 1,
                            "kind": "text_start",
                            "content_index": 0,
                            "message_id": "message-1",
                        },
                        {
                            "sequence": 2,
                            "kind": "text_delta",
                            "content_index": 0,
                            "message_id": "message-1",
                            "delta": "ok",
                        },
                        {
                            "sequence": 3,
                            "kind": "text_end",
                            "content_index": 0,
                            "message_id": "message-1",
                        },
                    ],
                    "terminal": {
                        "kind": "done",
                        "output_emitted": True,
                        "finish_reason": "length",
                        "raw_provider_finish": "incomplete",
                        "usage": {
                            "inputTokens": 0,
                            "extensions": {"providerBucket": "test"},
                        },
                        "assistant_messages": [
                            {
                                "role": "assistant",
                                "message_id": "message-1",
                                "content": [{"type": "text", "text": "ok"}],
                            }
                        ],
                        "provider_replay": [],
                        "evidence_refs": [],
                    },
                },
            }

    def agent_factory(
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        called["count"] += 1
        return FakeAgent()

    runner = SessionRunner(
        session=record, registry=registry, request=request, agent_factory=agent_factory
    )
    monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {})
    await runner.prepare_start()
    runner.schedule_start()
    runner.authorize_start()
    correlated_events: List[SessionEvent] = []

    # Expect the runner to publish at least completion + run_finished.
    seen_finished = False
    for _ in range(20):
        evt = await asyncio.wait_for(record.event_queue.get(), timeout=2.0)
        if evt is None:
            break
        if evt.type in {
            EventType.TURN_START,
            EventType.USER_MESSAGE,
            EventType.ASSISTANT_MESSAGE,
            EventType.COMPLETION,
            EventType.RUN_FINISHED,
        }:
            correlated_events.append(evt)
        if evt.type is EventType.RUN_FINISHED:
            assert any(
                envelope["type"] == EventType.TURN_COMPLETED.value
                for envelope in record.terminal_event_envelopes
            )
            seen_finished = True
            break

    await runner.stop()
    assert called["count"] == 1
    assert runner._agent is not None
    assert seen_finished
    assert {event.type for event in correlated_events} == {
        EventType.TURN_START,
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.COMPLETION,
        EventType.RUN_FINISHED,
    }
    correlations = {(event.input_id, event.turn_id) for event in correlated_events}
    assert len(correlations) == 1
    assert (
        next(
            event for event in correlated_events if event.type is EventType.TURN_START
        ).payload
        == {}
    )
    reloaded_registry = SessionRegistry(state_root=tmp_path)
    reloaded_record = await reloaded_registry.get(record.session_id)
    assert reloaded_record is not None
    completed_event = next(
        event
        for event in reloaded_record.event_log
        if event.type is EventType.TURN_COMPLETED
    )
    assert completed_event.payload == {
        "exchange_ref": {
            "exchange_id": "px-session-test",
            "schema_version": "bb.provider_exchange.v2",
        },
        "finish_reason": "length",
        "output_emitted": True,
        "raw_provider_finish": "incomplete",
        "usage": {
            "inputTokens": 0,
            "extensions": {"providerBucket": "test"},
        },
    }
    assert all(correlation is not None for correlation in next(iter(correlations)))


@pytest.mark.asyncio
async def test_session_runner_emits_completion_final_message_when_agent_has_no_assistant_event(
    tmp_path, monkeypatch
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-final-message", status=SessionStatus.STARTING
    )
    request = SessionCreateRequest(config_path="cfg.yaml", task="hello", stream=False)
    await registry.create(record)
    monkeypatch.delenv("BREADBOARD_ENABLE_REMOTE_STREAM", raising=False)

    expected_final_message = (
        "Files changed: calc.c\nVerification: make clean all && bash smoke_test.sh"
    )
    final_message = f"{expected_final_message}\nTASK COMPLETE\n"

    class FakeAgent:
        def __init__(self) -> None:
            self._local_mode = True
            self.workspace_dir = str(tmp_path / "ws")
            self.config = {"providers": {}}

        def initialize(self) -> None:
            Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)

        def run_task(self, task_text: str, **kwargs) -> Dict[str, Any]:
            return {
                "completion_summary": {
                    "completed": True,
                    "reason": "mark_task_complete",
                    "final_message": final_message,
                    "nested": {"opaque": ">>>>>> END RESPONSE"},
                },
                "reward_metrics_payload": {},
                "messages": [],
                "logging_dir": None,
            }

    runner = SessionRunner(
        session=record,
        registry=registry,
        request=request,
        agent_factory=lambda config_path, workspace_dir, overrides: FakeAgent(),
    )
    monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {})
    await runner.start()

    seen_assistant = None
    seen_completion = None
    for _ in range(20):
        evt = await asyncio.wait_for(record.event_queue.get(), timeout=2.0)
        if evt is None:
            break
        if evt.type is EventType.ASSISTANT_MESSAGE:
            seen_assistant = evt
        if evt.type is EventType.COMPLETION:
            seen_completion = evt
        if evt.type is EventType.RUN_FINISHED:
            break

    for _ in range(100):
        if any(
            turn.terminal_resolution_committed for turn in record.turns_by_id.values()
        ):
            break
        await asyncio.sleep(0.01)
    assert any(
        turn.terminal_resolution_committed for turn in record.turns_by_id.values()
    )
    await runner.stop()
    assert seen_assistant is not None
    assert seen_assistant.visibility == "transcript"
    assert seen_assistant.payload["text"] == expected_final_message
    assert seen_completion is not None
    assert seen_completion.payload["summary"] == {
        "completed": True,
        "reason": "mark_task_complete",
        "final_message": expected_final_message,
        "nested": {"opaque": ""},
    }


def test_session_runner_queue_pump_processes_events() -> None:
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-2", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="cfg.yaml", task="do work")

    runner = SessionRunner(session=record, registry=registry, request=request)

    captured: List[Tuple[str, Dict[str, Any], Optional[int]]] = []

    def capture(
        event_type: str, payload: Dict[str, Any], turn: Optional[int] = None
    ) -> None:
        captured.append((event_type, payload, turn))

    class FakeQueue:
        def __init__(self) -> None:
            self._queue: "queue.Queue[Tuple[Any, Any, Any]]" = queue.Queue()

        def put(self, item: Tuple[Any, Any, Any]) -> None:
            self._queue.put(item)

        def get(self, timeout: float | None = None) -> Tuple[Any, Any, Any]:
            return self._queue.get(timeout=timeout)

        def get_nowait(self) -> Tuple[Any, Any, Any]:
            return self._queue.get_nowait()

    fake_queue = FakeQueue()
    stop_event, thread = runner._start_queue_pump(fake_queue, capture)
    fake_queue.put(("assistant_message", {"message": {"content": "stream"}}, 5))
    fake_queue.put((None, None, None))
    stop_event.set()
    thread.join(timeout=1)
    runner._drain_event_queue(fake_queue, capture)

    assert captured
    assert captured[0][0] == "assistant_message"
    assert captured[0][1]["message"]["content"] == "stream"


def test_remote_observation_sink_failure_prevents_task_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from ray.util import queue as ray_queue

    attempted = threading.Event()

    class FailingSink:
        def __init__(self) -> None:
            self.calls = 0

        def append(self, _event: object) -> None:
            self.calls += 1
            if self.calls == 2:
                attempted.set()
                raise OSError("observation sink unavailable")

    class FakeQueue:
        def __init__(self) -> None:
            self.queue: "queue.Queue[Tuple[Any, Any, Any]]" = queue.Queue()

        def put(self, item: Tuple[Any, Any, Any]) -> None:
            self.queue.put(item)

        def get(self, timeout: float | None = None) -> Tuple[Any, Any, Any]:
            return self.queue.get(timeout=timeout)

        def get_nowait(self) -> Tuple[Any, Any, Any]:
            return self.queue.get_nowait()

    class RemoteAgent:
        _local_mode = False
        config: Dict[str, Any] = {"modes": [{"tools_enabled": ["list_dir"]}]}
        _active_tool_names = ["list_dir"]

        def run_task(self, _task_text: str, **kwargs: Any) -> Dict[str, Any]:
            kwargs["event_queue"].put(
                (
                    "assistant_message",
                    {"message": {"role": "assistant", "content": "working"}},
                    1,
                )
            )
            assert attempted.wait(2)
            return {
                "completion_summary": {"completed": True},
                "reward_metrics_payload": {},
                "messages": [],
                "logging_dir": None,
            }

    sink = FailingSink()
    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "task",
        session_id="sink-failure",
        sink=sink,
    )
    record = SessionRecord(
        session_id="sink-failure",
        status=SessionStatus.RUNNING,
    )
    record.product_session = product_session
    turn = TurnRecord(
        input_id="input-sink-failure",
        turn_id="turn-sink-failure",
        client_message_id="message-sink-failure",
        content="task",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(
            config_path="cfg.yaml",
            task="task",
            stream=True,
        ),
    )
    runner._agent = RemoteAgent()
    monkeypatch.setattr(ray_queue, "Queue", FakeQueue)
    monkeypatch.setenv("BREADBOARD_ENABLE_REMOTE_STREAM", "1")

    with pytest.raises(
        RuntimeError,
        match="runtime event persistence failed",
    ) as failure:
        runner._execute_task("task", input_id=turn.input_id, turn_id=turn.turn_id)
    assert isinstance(failure.value.__cause__, OSError)
    assert product_session.read_model.status == "running"
    assert product_session.read_model.event_count == 1


@pytest.mark.asyncio
async def test_session_service_event_stream_yields_ordered_events() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="sess-stream", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(
            SessionEvent(EventType.TURN_START, "sess-stream", {"turn": 1}, turn=1)
        )
        await record.event_queue.put(
            SessionEvent(
                EventType.ASSISTANT_MESSAGE, "sess-stream", {"text": "hi"}, turn=1
            )
        )

    task = asyncio.create_task(producer())
    stream = service.event_stream("sess-stream")
    first = await stream.__anext__()
    second = await stream.__anext__()
    await stream.aclose()
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task
    await task

    assert first.type is EventType.TURN_START
    assert second.type is EventType.ASSISTANT_MESSAGE
    assert second.payload["text"] == "hi"
    assert first.seq == 1
    assert second.seq == 2


@pytest.mark.asyncio
async def test_session_service_event_stream_handles_completion() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="sess-complete", status=SessionStatus.RUNNING)
    await registry.create(record)

    async def producer() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(
            SessionEvent(
                EventType.COMPLETION, "sess-complete", {"summary": {"completed": True}}
            )
        )

    task = asyncio.create_task(producer())
    stream = service.event_stream("sess-complete")
    completion = await stream.__anext__()
    await stream.aclose()
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task
    await task

    assert completion.type is EventType.COMPLETION
    assert completion.payload["summary"]["completed"]


@pytest.mark.asyncio
async def test_session_service_injects_todo_snapshot_on_connect() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(
        session_id="sess-todo-connect",
        status=SessionStatus.RUNNING,
        metadata={
            "todo_last_update": {
                "op": "replace",
                "revision": 3,
                "scopeKey": "main",
                "items": [],
            }
        },
    )
    await registry.create(record)

    stream = service.event_stream("sess-todo-connect")
    injected = await stream.__anext__()
    await stream.aclose()
    if record.dispatcher_task:
        await record.event_queue.put(None)
        await record.dispatcher_task

    assert injected.type is EventType.TOOL_RESULT
    assert injected.payload["todo"]["revision"] == 3


def test_session_state_kernel_v2_preserves_admitted_ids_without_numeric_synthesis() -> (
    None
):
    state = SessionState("ws", "image", {})
    state.set_provider_metadata("session_id", "session-1")
    state.set_turn_context(input_id="input-1", turn_id="turn-1", turn_index=7)

    record = state.build_kernel_event_v2_record(
        "assistant_message", {"text": "hello"}, turn=7, seq=3
    )

    assert record["input_id"] == "input-1"
    assert record["turn_id"] == "turn-1"
    assert record["turn_id"] != "turn:7"


def test_session_runner_unknown_runtime_event_fails_closed_and_strips_sentinel() -> (
    None
):
    runner = SessionRunner(
        session=SessionRecord(session_id="session-1", status=SessionStatus.STARTING),
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="cfg.yaml", task="work"),
    )

    with pytest.raises(RuntimeProtocolError):
        runner._translate_runtime_event("unknown.normative.event", {}, turn=3)

    translated = runner._translate_runtime_event(
        "assistant_message",
        {"message": {"role": "assistant", "content": "answer\n\n>>>>>> END RESPONSE"}},
        turn=3,
    )
    assert translated is not None
    assert translated[1]["text"] == "answer"
    assert translated[1]["message"]["content"] == "answer"

    session_scoped = runner._translate_runtime_event(
        "stream.gap", {"reason": "overflow"}, turn=3
    )
    assert session_scoped is not None
    assert session_scoped[2] is None


@pytest.mark.asyncio
async def test_session_runner_crash_terminalizes_active_and_queued_turns_once(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="session-crash", status=SessionStatus.RUNNING)
    active = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="message-active",
        content="active",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    queued = TurnRecord(
        input_id="input-queued",
        turn_id="turn-queued",
        client_message_id="message-queued",
        content="queued",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id.update({active.turn_id: active, queued.turn_id: queued})
    record.active_turn_id = active.turn_id
    record.queued_turn_ids.append(queued.turn_id)
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.create(record)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="active"),
    )

    await runner._terminalize_admitted_turns(
        outcome="failed", reason="worker_crash", error_code="worker_crash"
    )
    await runner._terminalize_admitted_turns(
        outcome="failed", reason="worker_crash", error_code="worker_crash"
    )

    assert [active.terminal_outcome, queued.terminal_outcome] == ["failed", "failed"]
    assert active.terminal_resolution_committed
    assert queued.terminal_resolution_committed
    assert record.active_turn_id is None
    assert list(record.queued_turn_ids) == []
    assert [item["turn_id"] for item in record.terminal_event_envelopes] == [
        "turn-active",
        "turn-queued",
    ]

    await runner._publish_session_failure("worker_crash")
    await runner._publish_session_failure("raw provider exception")
    failure_events = []
    while not record.event_queue.empty():
        failure_events.append(record.event_queue.get_nowait())
    assert len(failure_events) == 1
    assert failure_events[0].type is EventType.ERROR
    assert failure_events[0].payload == {"code": "worker_crash"}


@pytest.mark.asyncio
@pytest.mark.parametrize("prestart", [True, False])
async def test_runner_stop_terminalizes_active_and_queued_turns_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prestart: bool,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id=f"session-stop-{prestart}",
        status=SessionStatus.STARTING,
    )
    active = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="message-active",
        content="active",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    queued = TurnRecord(
        input_id="input-queued",
        turn_id="turn-queued",
        client_message_id="message-queued",
        content="queued",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id.update({active.turn_id: active, queued.turn_id: queued})
    record.active_turn_id = active.turn_id
    record.queued_turn_ids.append(queued.turn_id)
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.create(record)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(
            config_path="cfg.yaml",
            task="active" if prestart else "",
        ),
    )
    monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {})
    runner.schedule_start()
    if not prestart:
        runner.authorize_start()
        await asyncio.sleep(0)

    await runner.stop()
    await runner.stop()

    assert [active.terminal_outcome, queued.terminal_outcome] == [
        "cancelled",
        "cancelled",
    ]
    assert active.terminal_resolution_committed
    assert queued.terminal_resolution_committed
    assert len(record.terminal_event_envelopes) == 2
    assert record.active_turn_id is None
    assert list(record.queued_turn_ids) == []
    assert record.status is SessionStatus.STOPPED
