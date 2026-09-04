from __future__ import annotations

import asyncio
import base64
import copy
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import HTTPException

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import ReplayError, session_store
from breadboard.product.coordination.work_items import WorkItemRepository
from breadboard.product.runtime.children import DurableChildReconciler
from breadboard.product.runtime.events import Session, replay_differential
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
    identity_digest,
    submission_body_digest,
)
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.runtime_event_projector import (
    BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES,
    BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES,
    KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES,
    RuntimeEventProjector,
    RuntimeProtocolError,
    _strip_completion_sentinels,
)
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.provider.contracts import (
    strip_public_completion_sentinel_tree,
)
from breadboard_engine.conductor.context_window_guard import ContextWindowGuard
from breadboard_engine.agent_llm_openai import OpenAIConductor, _queue_event_emitter
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


def _seed_product_session_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session_id: str,
    *,
    config_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink

    event_root = tmp_path / "session-events"
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(event_root))
    retained_config = config_path or tmp_path / f"{session_id}.yaml"
    retained_config.write_text("{}\n", encoding="utf-8")
    runtime_config = {"providers": {"default_model": "test/restart"}}
    lock = SessionService(state_root=tmp_path / "seed-state")._runtime_lock(
        session_id, runtime_config, str(retained_config)
    )
    ProductSession.start(
        lock,
        "retained session",
        session_id=session_id,
        sink=JsonlEventSink(event_root / session_id / "session_events.jsonl"),
    )
    return retained_config, runtime_config


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


def test_session_state_compaction_snapshot_reconstructs_after_three_boundaries() -> None:
    state = SessionState("ws", "image", {})
    session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "long horizon",
        session_id="compaction-long-horizon",
    )
    retained: set[str] = set()
    previous_raw_fact_ids: tuple[str, ...] = ()

    for trigger in range(1, 4):
        state.add_message(
            {"role": "user", "content": f"request-{trigger}"},
            to_provider=True,
        )
        state.add_transcript_entry({"checkpoint": trigger})
        owner_bytes = json.dumps(
            state.provider_messages,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        snapshot = state.compaction_snapshot()
        retained.update(node["id"] for node in state.ctree_store.nodes)

        event = session.compact(snapshot)
        assert event.shadowed_raw_fact_ids == previous_raw_fact_ids
        previous_raw_fact_ids = event.raw_fact_ids
        restored = Session.restore(session.events)

        assert event.compaction_index == trigger
        assert restored.effective_context == owner_bytes
        assert set(restored.raw_fact_ids) == retained
        assert replay_differential(session) == {}

def test_replay_differential_detects_raw_fact_reordering() -> None:
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "first"})
    state.add_message({"role": "assistant", "content": "second"})
    session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "order-sensitive replay",
        session_id="compaction-order-differential",
    )
    session.compact(state.compaction_snapshot())
    expected = session.raw_fact_ids
    assert len(expected) == 2

    with session._transition_lock:
        session._raw_fact_ids = tuple(reversed(expected))

    assert replay_differential(session)["raw_fact_ids"] == {
        "live": list(reversed(expected)),
        "replay": list(expected),
    }


def test_session_snapshot_restores_ctree_identity_before_new_facts() -> None:
    original = SessionState("ws", "image", {})
    original.add_message({"role": "user", "content": "before restart"})
    retained = original.create_snapshot("mock")

    restarted = SessionState("ws", "image", {})
    restarted.restore_ctree_events(retained["ctree_events"])
    restarted.add_message({"role": "assistant", "content": "after restart"})

    assert [node["id"] for node in restarted.ctree_store.nodes] == [
        "ctn_000001",
        "ctn_000002",
    ]
    assert restarted.compaction_snapshot().raw_fact_ids == (
        "ctn_000001",
        "ctn_000002",
    )



def test_product_owned_fact_ids_continue_after_conductor_restart() -> None:
    original = SessionState("ws", "image", {})
    original.add_message({"role": "user", "content": "before restart"})
    retained = original.create_snapshot("mock")
    product = Session.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "long horizon",
    )
    product.compact(original.compaction_snapshot())

    restarted = SessionState("ws", "image", {})
    restarted.restore_ctree_events(retained["ctree_events"])
    assert restarted.build_task_record({})["ctree_node_id"] == "ctn_000001"
    restarted.restore_raw_fact_ids(product.raw_fact_ids)
    assert restarted.build_task_record({}) == {}
    assert (
        restarted.ctree_store.record("message", {"role": "assistant"})
        == "ctn_000002"
    )

    assert restarted.compaction_snapshot().raw_fact_ids == (
        "ctn_000001",
        "ctn_000002",
    )


def test_product_restore_preserves_post_compaction_facts() -> None:
    original = SessionState("ws", "image", {})
    original.add_message({"role": "user", "content": "before compaction"})
    product = Session.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "long horizon",
    )
    product.compact(original.compaction_snapshot())
    original.add_message({"role": "assistant", "content": "after compaction"})
    retained = original.create_snapshot("mock")

    restarted = SessionState("ws", "image", {})
    restarted.restore_ctree_events(retained["ctree_events"])
    restarted.restore_raw_fact_ids(product.raw_fact_ids)

    assert [node["id"] for node in restarted.ctree_store.nodes] == [
        "ctn_000002"
    ]
    assert (
        restarted.ctree_store.record("message", {"role": "user"})
        == "ctn_000003"
    )
    assert restarted.compaction_snapshot().raw_fact_ids == (
        "ctn_000001",
        "ctn_000002",
        "ctn_000003",
    )


def test_product_turns_retain_ctree_identity_sequence() -> None:
    conductor_type = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_type)
    conductor._retained_ctree_session_id = None
    conductor._retained_ctree_events = None
    first = SessionState("ws", "image", {})
    assert first.ctree_store.record("message", {"role": "user"}, turn=1) == (
        "ctn_000001"
    )
    conductor._retain_ctree(first, "product-session")

    second = SessionState("ws", "image", {})
    conductor._restore_retained_ctree(second, "product-session")

    assert second.ctree_store.record("message", {"role": "user"}, turn=2) == (
        "ctn_000002"
    )
    assert [node["id"] for node in second.ctree_store.nodes] == [
        "ctn_000001",
        "ctn_000002",
    ]
    unrelated = SessionState("ws", "image", {})
    conductor._restore_retained_ctree(unrelated, "other-session")
    assert unrelated.ctree_store.nodes == []


def test_product_turn_restore_merges_cached_post_compaction_facts() -> None:
    conductor_type = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_type)
    conductor._retained_ctree_session_id = None
    conductor._retained_ctree_events = None
    first = SessionState("ws", "image", {})
    first.ctree_store.record("message", {"role": "user"})
    product = Session.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "long horizon",
    )
    product.compact(first.compaction_snapshot())
    first.ctree_store.record("message", {"role": "assistant"})
    conductor._retain_ctree(first, "product-session")

    restarted = SessionState("ws", "image", {})
    conductor._restore_turn_ctree(
        restarted,
        "product-session",
        resume_ctree_events=None,
        resume_retained_raw_fact_ids=(),
        retained_raw_fact_ids=product.raw_fact_ids,
    )

    assert [node["id"] for node in restarted.ctree_store.nodes] == [
        "ctn_000002"
    ]
    assert (
        restarted.ctree_store.record("message", {"role": "user"})
        == "ctn_000003"
    )
    snapshot = restarted.create_snapshot("mock")
    assert snapshot["retained_raw_fact_ids"] == ["ctn_000001"]
    resumed = SessionState("ws", "image", {})
    conductor._restore_turn_ctree(
        resumed,
        "product-session",
        resume_ctree_events=snapshot["ctree_events"],
        resume_retained_raw_fact_ids=snapshot["retained_raw_fact_ids"],
        retained_raw_fact_ids=product.raw_fact_ids,
    )
    assert [node["id"] for node in resumed.ctree_store.nodes] == [
        "ctn_000002",
        "ctn_000003",
    ]
    assert (
        resumed.ctree_store.record("message", {"role": "assistant"})
        == "ctn_000004"
    )
    conductor._retain_ctree(restarted, "product-session")
    next_turn = SessionState("ws", "image", {})
    conductor._restore_turn_ctree(
        next_turn,
        "product-session",
        resume_ctree_events=None,
        resume_retained_raw_fact_ids=(),
        retained_raw_fact_ids=product.raw_fact_ids,
    )
    assert [node["id"] for node in next_turn.ctree_store.nodes] == [
        "ctn_000002",
        "ctn_000003",
    ]
    assert (
        next_turn.ctree_store.record("message", {"role": "assistant"})
        == "ctn_000004"
    )


def test_product_effective_context_overrides_cached_resume_messages() -> None:
    conductor_type = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_type)
    state = SessionState("ws", "image", {})
    state.messages = [{"role": "user", "content": "stale logical"}]
    state.provider_messages = [{"role": "user", "content": "stale provider"}]
    retained = [
        {"role": "system", "content": "retained system"},
        {"role": "assistant", "content": "retained answer"},
    ]

    has_system = conductor._restore_product_effective_messages(state, retained)

    assert has_system is True
    assert state.messages == retained
    assert state.provider_messages == retained
    assert state.messages is not retained
    assert state.provider_messages is not retained


def test_context_threshold_emits_exact_effective_provider_context() -> None:
    collector = EventCollector()
    state = SessionState(
        "ws",
        "image",
        {},
        event_emitter=collector,
        product_compaction_owner=True,
    )
    state.add_message({"role": "user", "content": "historical"})
    effective_messages = [
        {"role": "user", "content": "historical"},
        {"role": "system", "content": "per-turn-" + "x" * 20},
    ]

    payload = ContextWindowGuard(max_tokens=1, warn_ratio=1.0).maybe_compact(
        state,
        effective_messages,
    )

    assert payload is not None
    event = collector.of_type("conversation.compaction.end")
    assert len(event) == 1
    encoded = event[0][1]["effective_context"]
    assert json.loads(base64.b64decode(encoded)) == effective_messages
    assert json.loads(base64.b64decode(encoded)) != state.provider_messages


def test_context_compaction_holds_mutation_lock_through_persistence() -> None:
    started = threading.Event()
    finished = threading.Event()
    captured: Dict[str, Any] = {}
    writer: list[threading.Thread] = []

    def emit(
        event_type: str,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
    ) -> None:
        if event_type != "conversation.compaction.end":
            return
        captured.update(payload)

        def add_message() -> None:
            started.set()
            state.add_message({"role": "user", "content": "after-boundary"})
            finished.set()

        worker = threading.Thread(target=add_message)
        writer.append(worker)
        worker.start()
        assert started.wait(timeout=1)
        assert not finished.is_set()

    state = SessionState(
        "ws",
        "image",
        {},
        event_emitter=emit,
        product_compaction_owner=True,
    )
    effective_messages = [{"role": "user", "content": "before-boundary"}]
    ContextWindowGuard(max_tokens=1, warn_ratio=1.0).maybe_compact(
        state,
        effective_messages,
    )
    writer[0].join(timeout=1)

    assert finished.is_set()
    assert json.loads(base64.b64decode(captured["effective_context"])) == effective_messages
    emitted_ids = captured["raw_fact_ids"]
    final_ids = state.compaction_snapshot().raw_fact_ids
    assert tuple(emitted_ids) == final_ids[:-1]

def test_context_threshold_without_durable_owner_remains_warning_only() -> None:
    collector = EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)

    payload = ContextWindowGuard(max_tokens=1, warn_ratio=1.0).maybe_compact(
        state,
        [{"role": "user", "content": "x" * 20}],
    )

    assert payload is not None
    assert state.can_persist_compaction() is False
    assert collector.of_type("conversation.compaction.end") == []


def test_runtime_projector_commits_internal_compaction_without_leaking_context() -> None:
    product = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "project compaction",
        session_id="project-compaction",
    )
    holder = SimpleNamespace(product_session=product)
    projector = RuntimeEventProjector(
        holder,
        lambda: None,
        observation_tool_name=lambda _payload: None,
        product_session_lock=threading.RLock(),
        product_tool_completions={},
    )
    effective_context = b'[{"role":"user","content":"exact"}]'

    translated = projector.translate(
        "conversation.compaction.end",
        {
            "context_encoding": "base64",
            "effective_context": base64.b64encode(effective_context).decode("ascii"),
            "raw_fact_ids": ["ctn_000001"],
        },
        None,
    )

    assert translated is not None
    assert product.effective_context == effective_context
    assert product.raw_fact_ids == ("ctn_000001",)
    assert "effective_context" not in translated[1]

def test_runtime_projector_rejects_invalid_ctree_identity_before_commit() -> None:
    product = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "reject invalid compaction",
        session_id="reject-invalid-compaction",
    )
    projector = RuntimeEventProjector(
        SimpleNamespace(product_session=product),
        lambda: None,
        observation_tool_name=lambda _payload: None,
        product_session_lock=threading.RLock(),
        product_tool_completions={},
    )
    events_before = product.events
    for incomplete_payload in (
        {},
        {"raw_fact_ids": ["ctn_000001"]},
        {
            "context_encoding": "base64",
            "effective_context": base64.b64encode(b"[]").decode("ascii"),
        },
    ):
        with pytest.raises(RuntimeProtocolError, match="runtime_protocol_error"):
            projector.translate(
                "conversation.compaction.end",
                incomplete_payload,
                None,
            )


    with pytest.raises(RuntimeProtocolError, match="runtime_protocol_error"):
        projector.translate(
            "conversation.compaction.end",
            {
                "context_encoding": "base64",
                "effective_context": base64.b64encode(b"[]").decode("ascii"),
                "raw_fact_ids": ["fact-1"],
            },
            None,
        )

    assert product.events == events_before


def test_session_snapshot_rejects_non_exact_ctree_event_reconstruction() -> None:
    original = SessionState("ws", "image", {})
    original.add_message({"role": "user", "content": "retained"})
    retained = original.create_snapshot("mock")["ctree_events"]

    without_node = copy.deepcopy(retained)
    without_node[0].pop("node")
    with pytest.raises(ValueError, match="round-trip exactly"):
        SessionState("ws", "image", {}).restore_ctree_events(without_node)

    duplicate_identity = copy.deepcopy(retained)
    duplicate_identity.append(copy.deepcopy(duplicate_identity[0]))
    with pytest.raises(ValueError, match="duplicate retained identities"):
        SessionState("ws", "image", {}).restore_ctree_events(duplicate_identity)

    noncanonical_identity = copy.deepcopy(retained)
    noncanonical_identity[0]["node_id"] = "fact-1"
    noncanonical_identity[0]["node"]["id"] = "fact-1"
    with pytest.raises(ValueError, match="canonical C-Tree identities"):
        SessionState("ws", "image", {}).restore_ctree_events(
            noncanonical_identity
        )


def test_session_snapshot_preserves_distinct_provider_context() -> None:
    state = SessionState("ws", "image", {})
    generic_tool_result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "generic",
    }
    provider_tool_result = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": "provider-native",
            }
        ],
    }
    with state.context_mutation():
        state.messages.append(generic_tool_result)
        state.provider_messages.append(provider_tool_result)

    snapshot = state.create_snapshot("mock")
    generic_tool_result["content"] = "mutated"
    provider_tool_result["content"][0]["content"] = "mutated"

    assert snapshot["messages"] == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "generic",
        }
    ]
    assert snapshot["provider_messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "provider-native",
                }
            ],
        }
    ]




def test_compaction_snapshot_serializes_message_and_fact_mutation() -> None:
    state = SessionState("ws", "image", {})
    started = threading.Event()
    finished = threading.Event()

    def add_message() -> None:
        started.set()
        state.add_message({"role": "user", "content": "after-boundary"})
        finished.set()

    with state._compaction_lock:
        writer = threading.Thread(target=add_message)
        writer.start()
        assert started.wait(timeout=1)
        assert not finished.is_set()
        before = state.compaction_snapshot()
    writer.join(timeout=1)
    assert finished.is_set()
    after = state.compaction_snapshot()

    assert before.effective_context == b"[]"
    assert before.raw_fact_ids == ()
    assert json.loads(after.effective_context) == [
        {"content": "after-boundary", "role": "user"}
    ]
    assert len(after.raw_fact_ids) == 1


def test_restored_raw_fact_ids_serialize_with_context_mutation() -> None:
    state = SessionState("ws", "image", {})
    state.restore_raw_fact_ids(["ctn_000001"])
    started = threading.Event()
    finished = threading.Event()

    def restore() -> None:
        started.set()
        state.restore_raw_fact_ids(["ctn_000001", "ctn_000002"])
        finished.set()

    with state._compaction_lock:
        worker = threading.Thread(target=restore)
        worker.start()
        assert started.wait(timeout=1)
        assert not finished.is_set()
        assert state.compaction_snapshot().raw_fact_ids == ("ctn_000001",)
    worker.join(timeout=1)

    assert finished.is_set()
    assert state.compaction_snapshot().raw_fact_ids == (
        "ctn_000001",
        "ctn_000002",
    )


def test_persistent_snapshot_waits_for_message_fact_transaction() -> None:
    state = SessionState("ws", "image", {})
    record_started = threading.Event()
    allow_record = threading.Event()
    snapshot_finished = threading.Event()
    captured: dict[str, object] = {}
    original_record = state._record_ctree

    def paused_record(*args: object, **kwargs: object) -> object:
        record_started.set()
        assert allow_record.wait(timeout=1)
        return original_record(*args, **kwargs)

    state._record_ctree = paused_record  # type: ignore[method-assign]
    writer = threading.Thread(
        target=lambda: state.add_message(
            {"role": "user", "content": "transactional"}
        )
    )

    def take_snapshot() -> None:
        captured.update(state.create_snapshot("mock"))
        snapshot_finished.set()

    writer.start()
    assert record_started.wait(timeout=1)
    snapshotter = threading.Thread(target=take_snapshot)
    snapshotter.start()
    assert not snapshot_finished.wait(timeout=0.05)
    allow_record.set()
    writer.join(timeout=1)
    snapshotter.join(timeout=1)

    assert snapshot_finished.is_set()
    assert captured["messages"] == [
        {"role": "user", "content": "transactional"}
    ]
    assert len(captured["ctree_events"]) == 1


def test_session_state_owns_nested_provider_message_values() -> None:
    state = SessionState("ws", "image", {})
    message = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_call",
                "arguments": {"path": "before.py"},
            }
        ],
    }
    state.add_message(message)
    admitted = state.compaction_snapshot()

    message["content"][0]["arguments"]["path"] = "caller-mutated.py"
    state.messages[-1]["content"][0]["arguments"]["path"] = "history-mutated.py"

    assert state.compaction_snapshot() == admitted
    assert json.loads(admitted.effective_context)[0]["content"][0]["arguments"] == {
        "path": "before.py"
    }


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
async def test_session_runner_can_defer_execution_until_after_admission_response() -> None:
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-deferred-execution", status=SessionStatus.RUNNING)
    turn = TurnRecord(
        input_id="input-deferred",
        turn_id="turn-deferred",
        client_message_id="client-deferred",
        content="continue",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    product_inputs: list[tuple[str, list[Any]]] = []
    record.product_session = SimpleNamespace(
        input=lambda content, artifacts: product_inputs.append((content, artifacts)),
        read_model=SimpleNamespace(as_dict=lambda: {"status": "running"}),
    )
    deferred: list[Any] = []
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )

    accepted = await runner.enqueue_input(
        "continue",
        input_id=turn.input_id,
        turn_id=turn.turn_id,
        defer_execution=deferred.append,
    )

    assert accepted == "continue"
    assert runner._input_queue.empty()
    assert product_inputs == [("continue", [])]
    assert len(deferred) == 1
    await deferred[0]()
    assert product_inputs == [("continue", [])]
    assert await runner._input_queue.get() == {
        "content": "continue",
        "attachments": [],
        "input_id": turn.input_id,
        "turn_id": turn.turn_id,
    }


@pytest.mark.asyncio
async def test_deferred_input_does_not_enqueue_after_parent_cancellation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "registry"
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id="sess-deferred-cancel",
        status=SessionStatus.RUNNING,
        metadata={"workspace": str(tmp_path / "workspace")},
    )

    class Runner:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        def prepare_input_content(self, content: str) -> str:
            return content

        def validate_input_admission(self, *_args, **_kwargs) -> None:
            return None

        async def enqueue_input(
            self,
            content: str,
            attachments: list[str],
            *,
            defer_execution: Any,
            **_kwargs,
        ) -> str:
            async def execute() -> None:
                self.inputs.append(content)

            defer_execution(execute)
            return content

    runner = Runner()
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)
    deferred: list[Any] = []
    receipt = await service.send_input(
        record.session_id,
        SessionInputRequest(content="must not execute"),
        defer_execution=deferred.append,
    )

    await registry.close_admission_for_parent_cancellation(
        record.session_id,
        work_item_id="work-parent",
        reason="operator stop",
        child_recovery_refs=[],
    )
    await deferred[0]()

    cancelled = await registry.get(record.session_id)
    assert cancelled is not None and cancelled.admission_closed is True
    assert runner.inputs == []


@pytest.mark.asyncio
async def test_stale_registry_cannot_recreate_cross_process_deleted_session(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "registry"
    owner = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id="sess-cross-process-delete",
        status=SessionStatus.RUNNING,
    )
    await owner.create(record)
    deleter = SessionRegistry(state_root=state_root)
    assert await deleter.get(record.session_id) is not None
    await deleter.delete(record.session_id)

    with pytest.raises(RuntimeError, match="deleted before persistence"):
        await owner.persist(record)
    with pytest.raises(RuntimeError, match="permanently deleted"):
        await owner.create(
            SessionRecord(
                session_id=record.session_id,
                status=SessionStatus.RUNNING,
            )
        )
    assert await SessionRegistry(state_root=state_root).get(record.session_id) is None


@pytest.mark.asyncio
async def test_record_lock_wait_does_not_block_event_loop(tmp_path: Path) -> None:
    state_root = tmp_path / "registry"
    owner = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id="sess-record-lock-wait",
        status=SessionStatus.RUNNING,
    )
    await owner.create(record)
    waiter = SessionRegistry(state_root=state_root)

    async with owner._record_file_lock(record.session_id):
        waiting = asyncio.create_task(waiter.get(record.session_id))
        await asyncio.wait_for(asyncio.sleep(0.05), timeout=0.5)
        assert not waiting.done()
    assert await asyncio.wait_for(waiting, timeout=2) is not None


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

        def prepare_input_content(self, content: str) -> str:
            return content
        def validate_input_admission(
            self,
            _content: str,
            _attachments: tuple[str, ...],
            *,
            input_id: str,
            turn_id: str,
        ) -> None:
            assert input_id
            assert turn_id

        async def enqueue_input(
            self,
            content: str,
            attachments: list[str],
            *,
            input_id: str | None = None,
            turn_id: str | None = None,
            defer_execution: Any = None,
        ) -> str:
            async def execute() -> None:
                self.inputs.append(
                    (content, attachments, input_id, turn_id, record.active_turn_id)
                )

            if defer_execution is None:
                await execute()
            else:
                defer_execution(execute)
            return content

    runner = Runner()
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)
    request = SessionInputRequest(content="continue", client_message_id="client-1")

    first = await service.send_input(record.session_id, request)
    duplicate = await service.send_input(record.session_id, request)
    with pytest.raises(HTTPException) as conflict:
        await service.send_input(
            record.session_id,
            SessionInputRequest(
                content="different",
                client_message_id="client-1",
            ),
        )

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
    assert conflict.value.status_code == 409
    assert conflict.value.detail == {
        "code": "input_idempotency_conflict",
        "turn_id": first.turn_id,
    }
    assert runner.inputs == [
        ("continue", [], first.input_id, first.turn_id, first.turn_id),
    ]
    assert record.active_turn_id == first.turn_id


@pytest.mark.asyncio
async def test_parallel_sends_remain_separate_in_durable_admission_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink

    registry = SessionRegistry(state_root=tmp_path / "registry")
    record = SessionRecord(
        session_id="sess-parallel-admission",
        status=SessionStatus.RUNNING,
    )
    journal = tmp_path / "events" / record.session_id / "session_events.jsonl"
    record.product_session = ProductSession.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "parallel admission",
        session_id=record.session_id,
        sink=JsonlEventSink(journal),
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    record.runner = runner
    await registry.create(record)
    original_persist = registry.persist
    first_persist_started = asyncio.Event()
    release_first_persist = asyncio.Event()
    persist_count = 0

    async def controlled_persist(
        persisted_record: SessionRecord,
        **kwargs: Any,
    ) -> None:
        nonlocal persist_count
        persist_count += 1
        if persist_count == 1:
            first_persist_started.set()
            await release_first_persist.wait()
        await original_persist(persisted_record, **kwargs)

    monkeypatch.setattr(registry, "persist", controlled_persist)
    service = SessionService(registry=registry)
    first_task = asyncio.create_task(
        service.send_input(
            record.session_id,
            SessionInputRequest(content="first", client_message_id="parallel-1"),
        )
    )
    await asyncio.wait_for(first_persist_started.wait(), timeout=2)
    second_task = asyncio.create_task(
        service.send_input(
            record.session_id,
            SessionInputRequest(content="second", client_message_id="parallel-2"),
        )
    )
    await asyncio.sleep(0)
    assert second_task.done() is False
    release_first_persist.set()

    first, second = await asyncio.wait_for(
        asyncio.gather(first_task, second_task),
        timeout=2,
    )
    restored = await SessionRegistry(state_root=tmp_path / "registry").get(
        record.session_id
    )

    assert [first.disposition, second.disposition] == ["started", "queued"]
    assert restored is not None
    assert [
        (turn.original_disposition, turn.body_digest)
        for turn in restored.turns_by_id.values()
    ] == [
        ("started", submission_body_digest("first", ())),
        ("queued", submission_body_digest("second", ())),
    ]
    accepted_events = [
        event
        for event in map(json.loads, journal.read_text(encoding="utf-8").splitlines())
        if event["kind"] == "input.accepted"
    ]
    assert [event["payload"]["content_hash"] for event in accepted_events] == [
        "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        for content in ("first", "second")
    ]
    assert [
        runner._input_queue.get_nowait()["content"],
        runner._input_queue.get_nowait()["content"],
    ] == ["first", "second"]


@pytest.mark.asyncio
async def test_session_input_is_durable_before_deferred_execution(tmp_path: Path) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(session_id="sess-durable-admission", status=SessionStatus.RUNNING)
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)
    deferred: list[Any] = []

    receipt = await service.send_input(
        record.session_id,
        SessionInputRequest(content="continue", client_message_id="client-durable"),
        defer_execution=deferred.append,
    )

    assert runner._input_queue.empty()
    assert len(deferred) == 1
    restored = await SessionRegistry(state_root=tmp_path).get(record.session_id)
    assert restored is not None
    assert restored.turns_by_id[receipt.turn_id].input_id == receipt.input_id
    assert restored.turns_by_id[receipt.turn_id].state == "active"
    await deferred[0]()
    queued = await runner._input_queue.get()
    assert queued["input_id"] == receipt.input_id
    assert queued["turn_id"] == receipt.turn_id


@pytest.mark.asyncio
async def test_sanitized_input_is_staged_before_registry_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession

    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-sanitized-admission",
        status=SessionStatus.RUNNING,
    )
    record.product_session = ProductSession.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "sanitized admission",
        session_id=record.session_id,
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    runner._accepted_task_texts = ["hello"]
    persisted_contents: list[str] = []
    original_persist = registry.persist

    async def capture_persist(persisted_record: SessionRecord) -> None:
        persisted_contents.extend(
            turn.content for turn in persisted_record.turns_by_id.values()
        )
        await original_persist(persisted_record)

    monkeypatch.setattr(registry, "persist", capture_persist)
    record.runner = runner
    await registry.create(record)
    deferred: list[Any] = []

    receipt = await SessionService(registry=registry).send_input(
        record.session_id,
        SessionInputRequest(
            content="hello world",
            client_message_id="client-sanitized",
        ),
        defer_execution=deferred.append,
    )

    restored = await SessionRegistry(state_root=tmp_path).get(record.session_id)
    assert restored is not None
    assert record.turns_by_id[receipt.turn_id].content == "world"
    assert persisted_contents == ["world"]
    assert restored.turns_by_id[receipt.turn_id].content == ""
    assert record.product_session.events[-1].payload["content_hash"] == (
        "sha256:" + hashlib.sha256(b"world").hexdigest()
    )
    assert len(deferred) == 1
@pytest.mark.asyncio
async def test_input_normalization_is_applied_once_for_two_prior_prefixes(
    tmp_path: Path,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession

    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-two-prefix-admission",
        status=SessionStatus.RUNNING,
    )
    record.product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "two-prefix admission",
        session_id=record.session_id,
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    runner._accepted_task_texts = ["alpha", "beta"]
    record.runner = runner
    await registry.create(record)
    deferred: list[Any] = []

    receipt = await SessionService(registry=registry).send_input(
        record.session_id,
        SessionInputRequest(
            content="alphabeta new",
            client_message_id="client-two-prefix",
        ),
        defer_execution=deferred.append,
    )

    assert record.turns_by_id[receipt.turn_id].content == "beta new"
    assert len(deferred) == 1
    await deferred[0]()
    queued = await runner._input_queue.get()
    assert queued["content"] == "beta new"

@pytest.mark.asyncio
async def test_terminal_product_session_rejects_before_durable_admission(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-terminal-input-race",
        status=SessionStatus.RUNNING,
    )

    class TerminalProductSession:
        def input(self, _content: str, _artifacts: list[Any]) -> None:
            raise RuntimeError("product session is terminal")

    record.product_session = TerminalProductSession()
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    record.runner = runner
    await registry.create(record)
    deferred: list[Any] = []

    with pytest.raises(HTTPException) as rejected:
        await SessionService(registry=registry).send_input(
            record.session_id,
            SessionInputRequest(
                content="continue",
                client_message_id="client-terminal-race",
            ),
            defer_execution=deferred.append,
        )

    retained = await SessionRegistry(state_root=tmp_path).get(record.session_id)
    assert rejected.value.status_code == 409
    assert retained is not None
    assert retained.turns_by_id == {}
    assert record.turns_by_id == {}
    assert record.active_turn_id is None
    assert deferred == []
    assert runner._input_queue.empty()

@pytest.mark.asyncio
async def test_retained_submission_digest_deduplicates_after_restart(
    tmp_path: Path,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-retained-dedupe",
        status=SessionStatus.RUNNING,
    )
    client_message_id = "client-retained"
    turn = TurnRecord(
        input_id="input-retained",
        turn_id="turn-retained",
        client_message_id=client_message_id,
        content="continue",
        attachments=(),
        original_disposition="started",
        state="active",
        body_digest=submission_body_digest("continue", ()),
    )
    record.turns_by_id[turn.turn_id] = turn
    record.submissions_by_key[client_message_id] = turn
    record.submissions_by_key_digest[identity_digest(client_message_id)] = turn
    record.active_turn_id = turn.turn_id
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.create(record)

    restarted = SessionRegistry(state_root=tmp_path)
    restored = await restarted.get(record.session_id)
    assert restored is not None
    assert restored.submissions_by_key == {}
    restored.loaded_from_retained_state = False

    class Runner:
        async def enqueue_input(self, *_args: Any, **_kwargs: Any) -> str:
            raise AssertionError("deduplicated input must not execute")

    restored.runner = Runner()
    receipt = await SessionService(registry=restarted).send_input(
        restored.session_id,
        SessionInputRequest(
            content="continue",
            client_message_id=client_message_id,
        ),
    )

    assert receipt.disposition == "deduplicated"
    assert receipt.turn_id == turn.turn_id
    assert receipt.input_id == turn.input_id


@pytest.mark.asyncio
async def test_admission_persistence_failure_precedes_logical_input_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession

    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def append(self, event: Any) -> None:
            self.events.append(event)

    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-admission-persist-failure",
        status=SessionStatus.RUNNING,
    )
    sink = RecordingSink()
    record.product_session = ProductSession.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "durable admission",
        session_id=record.session_id,
        sink=sink,
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task="", stream=False),
    )
    record.runner = runner
    await registry.create(record)
    original_persist = registry.persist

    async def fail_persist(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected admission persistence failure")

    monkeypatch.setattr(registry, "persist", fail_persist)
    deferred: list[Any] = []
    request = SessionInputRequest(
        content="continue",
        client_message_id="client-persist-failure",
    )
    with pytest.raises(OSError, match="injected admission persistence failure"):
        await SessionService(registry=registry).send_input(
            record.session_id,
            request,
            defer_execution=deferred.append,
        )

    assert [event.kind for event in sink.events] == ["session.started"]
    assert deferred == []
    assert record.turns_by_id == {}
    assert record.submissions_by_key == {}
    assert record.submissions_by_key_digest == {}
    assert record.active_turn_id is None
    assert record.turn_admission.value == "idle"

    monkeypatch.setattr(registry, "persist", original_persist)
    receipt = await SessionService(registry=registry).send_input(
        record.session_id,
        request,
        defer_execution=deferred.append,
    )

    assert receipt.disposition == "started"
    assert [event.kind for event in sink.events] == [
        "session.started",
        "input.accepted",
    ]
    assert len(deferred) == 1

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
@pytest.mark.parametrize("queued", [False, True])
async def test_cancellation_persistence_failure_rolls_back_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queued: bool,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id=f"sess-cancel-persist-failure-{queued}",
        status=SessionStatus.RUNNING,
    )
    active = TurnRecord(
        input_id="input-active",
        turn_id="turn-active",
        client_message_id="client-active",
        content="active",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    target = (
        TurnRecord(
            input_id="input-queued",
            turn_id="turn-queued",
            client_message_id="client-queued",
            content="queued",
            attachments=(),
            original_disposition="queued",
            state="queued",
        )
        if queued
        else active
    )
    record.turns_by_id[active.turn_id] = active
    record.active_turn_id = active.turn_id
    if queued:
        record.turns_by_id[target.turn_id] = target
        record.queued_turn_ids.append(target.turn_id)

    class Runner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def request_turn_cancellation(self, turn_id: str) -> bool:
            self.calls.append(turn_id)
            return True

        async def finish_queued_turn_cancellation(
            self,
            turn: TurnRecord,
            _reason: str,
        ) -> None:
            self.calls.append(turn.turn_id)

    runner = Runner()
    record.runner = runner
    await registry.create(record)
    queued_ids_before = list(record.queued_turn_ids)

    async def fail_persist(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected cancellation persistence failure")

    monkeypatch.setattr(registry, "persist", fail_persist)
    with pytest.raises(OSError, match="injected cancellation persistence failure"):
        await SessionService(registry=registry).cancel_turn(
            record.session_id,
            target.turn_id,
            SessionTurnCancelRequest(
                cancellation_request_key=f"cancel-persist-failure-{queued}",
                reason="user_requested",
            ),
        )

    assert target.cancellation_requested is False
    assert target.cancellation_reason is None
    assert record.cancellations_by_key == {}
    assert record.cancellations_by_key_digest == {}
    assert list(record.queued_turn_ids) == queued_ids_before
    assert runner.calls == []

@pytest.mark.asyncio
async def test_queued_cancellation_retry_finishes_after_terminal_persist_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="sess-queued-cancel-terminal-retry",
        status=SessionStatus.RUNNING,
    )
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
    original_persist = registry.persist
    terminal_failure_injected = False

    async def fail_first_terminal_persist(
        persisted_record: SessionRecord,
        **kwargs: Any,
    ) -> None:
        nonlocal terminal_failure_injected
        if kwargs.get("terminal_event") is not None and not terminal_failure_injected:
            terminal_failure_injected = True
            raise OSError("injected terminal persistence failure")
        await original_persist(persisted_record, **kwargs)

    monkeypatch.setattr(registry, "persist", fail_first_terminal_persist)
    request = SessionTurnCancelRequest(
        cancellation_request_key="cancel-queued-terminal-retry",
        reason="user_requested",
    )
    service = SessionService(registry=registry)

    with pytest.raises(OSError, match="injected terminal persistence failure"):
        await service.cancel_turn(record.session_id, queued.turn_id, request)

    assert queued.terminal_outcome is None
    assert list(record.queued_turn_ids) == []
    assert record.cancellations_by_key

    duplicate = await service.cancel_turn(record.session_id, queued.turn_id, request)

    assert duplicate.disposition == "deduplicated"
    assert duplicate.original_disposition == "queued_cancelled"
    assert queued.terminal_outcome == "cancelled"
    assert queued.terminal_resolution_committed is True


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

    # Retention stores only sanitized terminal envelopes plus the exact durable
    # cursor identity, never the payload behind a nonterminal replay head.
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
    await registry.persist(record, cursor_event=replay_head)
    retained_bytes = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "completion-payload-must-not-persist" not in retained_bytes
    assert "path-must-not-persist" not in retained_bytes
    retained = json.loads(retained_bytes)
    assert retained["session"]["event_seq"] == replay_head.seq
    assert retained["session"]["replay_head_sequence"] == replay_head.seq
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
    assert restored.event_seq == replay_head.seq
    assert [event.seq for event in restored.event_log] == [terminal_envelope["seq"]]
    replay_queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
    restarted_service = SessionService(registry=restarted)
    gap_queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
    with pytest.raises(HTTPException) as gap_error:
        await restarted_service._register_subscriber(
            restored,
            gap_queue,
            replay=True,
            from_id=terminal_envelope["id"],
        )
    assert gap_error.value.status_code == 409
    assert gap_error.value.detail["code"] == "resume_window_exceeded"
    await restarted_service._register_subscriber(
        restored,
        replay_queue,
        replay=True,
        from_id=replay_head.event_id,
    )
    assert replay_queue.empty()
    await restarted_service._unregister_subscriber(restored, replay_queue)
    assert restarted_service._is_retained_head_cursor(
        restored,
        str(replay_head.seq),
    )
    numeric_replay_queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
    await restarted_service._register_subscriber(
        restored,
        numeric_replay_queue,
        replay=True,
        from_id=str(replay_head.seq),
    )
    assert numeric_replay_queue.empty()
    await restarted_service._unregister_subscriber(
        restored,
        numeric_replay_queue,
    )

@pytest.mark.asyncio
async def test_dispatcher_failure_drains_queue_and_rejects_future_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-dispatcher-persist-failure",
        status=SessionStatus.RUNNING,
    )
    await registry.create(record)
    service = SessionService(registry=registry)
    await service._ensure_dispatcher(record)

    async def fail_persist(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected dispatcher persistence failure")

    monkeypatch.setattr(registry, "persist", fail_persist)
    record.event_queue.put_nowait(
        SessionEvent(EventType.TASK_EVENT, record.session_id, {"index": 1})
    )
    record.event_queue.put_nowait(
        SessionEvent(EventType.TASK_EVENT, record.session_id, {"index": 2})
    )
    assert record.dispatcher_task is not None
    await record.dispatcher_task
    await asyncio.wait_for(record.event_queue.join(), timeout=1)

    assert record.event_queue.empty()
    assert getattr(record, "_dispatcher_complete", False) is True
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="cfg.yaml", task=""),
    )
    with pytest.raises(RuntimeError, match="dispatcher is unavailable"):
        await runner._enqueue_event_async(
            SessionEvent(EventType.TASK_EVENT, record.session_id, {"index": 3})
        )


@pytest.mark.asyncio
async def test_async_event_enqueue_fails_fast_when_bounded_queue_is_full() -> None:
    record = SessionRecord(
        session_id="session-bounded-event-queue",
        status=SessionStatus.RUNNING,
    )
    record.event_queue = asyncio.Queue(maxsize=1)
    record.event_queue.put_nowait(
        SessionEvent(EventType.TASK_EVENT, record.session_id, {"index": 1})
    )
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="cfg.yaml", task=""),
    )

    with pytest.raises(RuntimeError, match="event queue is full"):
        await asyncio.wait_for(
            runner._enqueue_event_async(
                SessionEvent(EventType.TASK_EVENT, record.session_id, {"index": 2})
            ),
            timeout=1,
        )


@pytest.mark.asyncio
async def test_retained_restart_terminalizes_interrupted_turn_and_resumes_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-restart-interrupted",
        status=SessionStatus.RUNNING,
        metadata={"permission_mode": "configured"},
    )
    replacement_profile, runtime_config = _seed_product_session_journal(
        monkeypatch, tmp_path, record.session_id
    )
    interrupted = TurnRecord(
        input_id="input-interrupted",
        turn_id="turn-interrupted",
        client_message_id="client-interrupted",
        content="must-not-persist",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    record.turns_by_id[interrupted.turn_id] = interrupted
    record.active_turn_id = interrupted.turn_id
    await registry.create(record)
    record.event_seq = 390
    record.replay_head_sequence = 390
    record.replay_head_event_id = "event-before-process-death"
    await registry.persist(record)

    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.service.resolve_default_profile",
        lambda: SimpleNamespace(
            source_path=replacement_profile,
            public_identity=lambda: {
                "definition_ref": "agent_configs/templates/daily_driver.v1.yaml"
            },
        ),
    )
    monkeypatch.setattr(
        SessionRunner,
        "prepare_runtime_config",
        lambda self: runtime_config,
    )

    restarted = SessionRegistry(state_root=tmp_path)
    service = SessionService(registry=restarted)
    restored = await service.ensure_session(record.session_id)
    try:
        await asyncio.sleep(0)
        assert restored.loaded_from_retained_state is False
        assert restored.runner is not None
        restored_turn = restored.turns_by_id[interrupted.turn_id]
        assert restored_turn.terminal_outcome == "failed"
        assert restored_turn.terminal_resolution_committed is True
        assert restored.active_turn_id is None
        assert restored.turn_admission.value == "idle"
        assert len(restored.terminal_event_envelopes) == 1
        terminal = restored.terminal_event_envelopes[0]
        assert terminal["seq"] == 391
        summary = restored.to_summary()
        assert summary.head_sequence == 391
        assert summary.head_event_id == terminal["id"]
        assert terminal["type"] == "turn_failed"
        assert terminal["input_id"] == interrupted.input_id
        assert terminal["turn_id"] == interrupted.turn_id
        assert terminal["payload"] == {"error": {"code": "runtime_failure"}}
        assert (await service.ensure_session(record.session_id)).runner is restored.runner
        retained = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
        assert retained["session"]["permission_mode"] == "configured"
        assert retained["turns"][0]["terminal_resolution_committed"] is True
        assert len(retained["terminal_event_envelopes"]) == 1
    finally:
        if restored.runner is not None:
            await restored.runner.stop()

@pytest.mark.asyncio
async def test_retained_restart_preserves_explicit_config_and_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_config = tmp_path / "explicit-session.yaml"
    explicit_config.write_text("{}\n", encoding="utf-8")
    explicit_workspace = tmp_path / "workspace"
    explicit_workspace.mkdir()
    registry = SessionRegistry(state_root=tmp_path / "state")
    record = SessionRecord(
        session_id="session-restart-explicit-context",
        status=SessionStatus.RUNNING,
        metadata={
            "config_path": str(explicit_config),
            "workspace": str(explicit_workspace),
            "permission_mode": "configured",
            "mode": "review",
        },
    )
    _, runtime_config = _seed_product_session_journal(
        monkeypatch,
        tmp_path,
        record.session_id,
        config_path=explicit_config,
    )
    await registry.create(record)

    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.service.resolve_default_profile",
        lambda: pytest.fail("explicit retained config resolved the default profile"),
    )
    prepared_requests: list[SessionCreateRequest] = []
    prepared_modes: list[str | None] = []

    def capture_runtime_request(runner: SessionRunner) -> dict[str, Any]:
        prepared_requests.append(runner.request)
        prepared_modes.append(runner._mode)
        return runtime_config

    monkeypatch.setattr(SessionRunner, "prepare_runtime_config", capture_runtime_request)

    restarted = SessionRegistry(state_root=tmp_path / "state")
    restored = await SessionService(registry=restarted).ensure_session(record.session_id)
    try:
        assert prepared_requests
        assert prepared_requests[0].config_path == str(explicit_config)
        assert prepared_requests[0].workspace == str(explicit_workspace)
        assert prepared_modes == ["review"]
        assert restored.metadata["config_path"] == str(explicit_config)
        assert restored.metadata["workspace"] == str(explicit_workspace)
        assert restored.metadata["mode"] == "review"
    finally:
        if restored.runner is not None:
            await restored.runner.stop()



@pytest.mark.asyncio
async def test_retained_restart_without_logical_journal_fails_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT",
        str(tmp_path / "session-events"),
    )
    state_root = tmp_path / "state"
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id="session-missing-logical-journal",
        status=SessionStatus.RUNNING,
    )
    await registry.create(record)

    service = SessionService(registry=SessionRegistry(state_root=state_root))
    with pytest.raises(ReplayError) as captured:
        await service.ensure_session(record.session_id)

    assert captured.value.code == "missing_event_stream"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.STOPPED,
    ],
)
async def test_retained_terminal_session_is_not_resurrected(
    tmp_path: Path,
    terminal_status: SessionStatus,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id=f"session-retained-{terminal_status.value}",
        status=terminal_status,
    )
    await registry.create(record)

    restarted = SessionRegistry(state_root=tmp_path)
    service = SessionService(registry=restarted)
    restored = await service.ensure_session(record.session_id)

    assert restored.status is terminal_status
    assert restored.runner is None
    assert restored.loaded_from_retained_state is False
    assert (await service.ensure_session(record.session_id)) is restored

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "delete"])
async def test_locked_operation_resumes_retained_session_without_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    config_path = tmp_path / "retained-config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    state_root = tmp_path / "state"
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id=f"session-retained-{operation}",
        status=SessionStatus.RUNNING,
        metadata={
            "config_path": str(config_path),
            "permission_mode": "configured",
        },
    )
    _, runtime_config = _seed_product_session_journal(
        monkeypatch,
        tmp_path,
        record.session_id,
        config_path=config_path,
    )
    await registry.create(record)
    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.service.resolve_default_profile",
        lambda: SimpleNamespace(
            source_path=config_path,
            public_identity=lambda: {
                "definition_ref": "agent_configs/templates/daily_driver.v1.yaml"
            },
        ),
    )
    monkeypatch.setattr(
        SessionRunner,
        "prepare_runtime_config",
        lambda self: runtime_config,
    )
    service = SessionService(registry=SessionRegistry(state_root=state_root))

    if operation == "stop":
        await asyncio.wait_for(service.stop_session(record.session_id), timeout=2)
        stopped = await service.registry.get(record.session_id)
        assert stopped is not None
        assert stopped.status is SessionStatus.STOPPED
    else:
        await asyncio.wait_for(service.delete_session(record.session_id), timeout=2)
        assert await service.registry.get(record.session_id) is None


@pytest.mark.asyncio
async def test_session_creation_persists_absolute_explicit_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "relative-config.yaml"
    config_path.write_text("profile: {name: relative-test}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        str(tmp_path / "runtime-records"),
    )
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT",
        str(tmp_path / "session-events"),
    )
    monkeypatch.setattr(SessionRunner, "schedule_start", lambda self: None)
    monkeypatch.setattr(SessionRunner, "authorize_start", lambda self: None)
    service = SessionService(state_root=tmp_path / "state")

    response = await service.create_session(
        SessionCreateRequest(
            config_path=config_path.name,
            task="",
            stream=False,
        )
    )
    created = await service.ensure_session(response.session_id)

    assert created.metadata["config_path"] == str(config_path)
    assert created.runner is not None
    assert created.runner.request.config_path == str(config_path)
    await service.delete_session(response.session_id)



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


def test_remote_nonstreaming_compaction_reaches_product_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.product.runtime import Session as ProductSession
    from ray.util import queue as ray_queue

    effective_context = b'[{"content":"exact remote context","role":"user"}]'

    class FakeQueue:
        def __init__(self) -> None:
            self.queue: "queue.Queue[Tuple[Any, ...]]" = queue.Queue()

        def put(self, item: Tuple[Any, ...]) -> None:
            self.queue.put(item)

        def get(self, timeout: float | None = None) -> Tuple[Any, ...]:
            return self.queue.get(timeout=timeout)

        def get_nowait(self) -> Tuple[Any, ...]:
            return self.queue.get_nowait()

    class RemoteAgent:
        _local_mode = False
        config: Dict[str, Any] = {"modes": [{"tools_enabled": []}]}
        _active_tool_names: list[str] = []

        def run_task(self, _task_text: str, **kwargs: Any) -> Dict[str, Any]:
            assert kwargs["event_emitter"] is None
            assert kwargs["context"]["retained_raw_fact_ids"] == ["ctn_000001"]
            assert kwargs["context"]["_product_compaction_owner"] is True
            assert kwargs["context"]["retained_effective_messages"] == [
                {"content": "retained remote context", "role": "user"}
            ]
            emit = _queue_event_emitter(
                kwargs["event_queue"],
                kwargs["event_ack_queue"],
            )
            emit(
                "assistant_message",
                {"message": {"role": "assistant", "content": "must stay private"}},
                1,
            )
            emit(
                "conversation.compaction.end",
                {
                    "context_encoding": "base64",
                    "effective_context": base64.b64encode(
                        effective_context
                    ).decode("ascii"),
                    "raw_fact_ids": ["ctn_000001", "ctn_000002"],
                },
                1,
            )
            assert product_session.effective_context == effective_context
            return {
                "completion_summary": {"completed": True},
                "reward_metrics_payload": {},
                "messages": [],
                "logging_dir": None,
            }

    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "task",
        session_id="remote-compaction",
    )
    retained_state = SessionState("ws", "image", {})
    retained_state.ctree_store.record("message", {"role": "user"})
    retained_state.provider_messages = [
        {"role": "user", "content": "retained remote context"}
    ]
    product_session.compact(retained_state.compaction_snapshot())
    record = SessionRecord(
        session_id="remote-compaction",
        status=SessionStatus.RUNNING,
    )
    record.product_session = product_session
    turn = TurnRecord(
        input_id="input-remote-compaction",
        turn_id="turn-remote-compaction",
        client_message_id="message-remote-compaction",
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
            stream=False,
        ),
    )
    runner._agent = RemoteAgent()
    monkeypatch.setattr(ray_queue, "Queue", FakeQueue)
    monkeypatch.delenv("BREADBOARD_ENABLE_REMOTE_STREAM", raising=False)

    runner._execute_task("task", input_id=turn.input_id, turn_id=turn.turn_id)

    assert product_session.effective_context == effective_context
    assert product_session.raw_fact_ids == ("ctn_000001", "ctn_000002")
    assert all(event.kind != "message.assistant" for event in product_session.events)
    assert runner._published_events == 1


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


def _lifecycle_runner_with_product_session(
    registry: SessionRegistry,
    record: SessionRecord,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[bool],
) -> tuple[SessionRunner, Any]:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession

    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "active",
        session_id=record.session_id,
    )
    record.product_session = product_session
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(
            config_path="cfg.yaml",
            task="active",
            stream=False,
        ),
    )
    record.runner = runner
    monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {})

    async def initialized() -> None:
        return None

    monkeypatch.setattr(runner, "_ensure_agent_initialized", initialized)

    def execute_task(
        _task: str,
        *,
        input_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        completed = outcomes.pop(0)
        return {
            "completion_summary": {
                "completed": completed,
                "reason": "completed" if completed else "stopped_by_user",
            },
            "reward_metrics": None,
            "logging_dir": None,
            "_terminal_events": [],
            "_turn_completion_payload": {},
        }
    monkeypatch.setattr(runner._task_execution, "execute_task", execute_task)
    return runner, product_session


async def _wait_for_terminal_turn(turn: TurnRecord) -> None:
    for _ in range(200):
        if turn.terminal_resolution_committed:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"turn {turn.turn_id} did not become terminal")


@pytest.mark.asyncio
async def test_active_turn_cancellation_keeps_interactive_session_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-active-cancel-continues",
        status=SessionStatus.STARTING,
    )
    runner, product_session = _lifecycle_runner_with_product_session(
        registry,
        record,
        monkeypatch,
        [False, True],
    )
    await registry.create(record)
    await runner.prepare_start()
    active = record.turns_by_id[record.active_turn_id or ""]
    active.cancellation_requested = True
    active.cancellation_reason = "user_requested"
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.persist(record)
    runner.schedule_start()
    runner.authorize_start()

    await _wait_for_terminal_turn(active)
    assert active.terminal_outcome == "cancelled", record.terminal_event_envelopes
    assert product_session.read_model.status == "running"
    assert record.status is SessionStatus.RUNNING

    receipt = await SessionService(registry=registry).send_input(
        record.session_id,
        SessionInputRequest(
            content="continue",
            client_message_id="message-continue",
        ),
    )
    continued = record.turns_by_id[receipt.turn_id]
    await _wait_for_terminal_turn(continued)

    assert continued.terminal_outcome == "completed"
    assert product_session.read_model.status == "running"
    assert record.status is SessionStatus.RUNNING
    await runner.stop()


@pytest.mark.asyncio
async def test_interactive_failure_terminalizes_remaining_admitted_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-failure-drains-admitted",
        status=SessionStatus.STARTING,
    )
    runner, product_session = _lifecycle_runner_with_product_session(
        registry,
        record,
        monkeypatch,
        [False],
    )
    await registry.create(record)
    await runner.prepare_start()
    active = record.turns_by_id[record.active_turn_id or ""]
    queued = TurnRecord(
        input_id="input-queued",
        turn_id="turn-queued",
        client_message_id="message-queued",
        content="queued",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id[queued.turn_id] = queued
    record.queued_turn_ids.append(queued.turn_id)
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.persist(record)
    runner.schedule_start()
    runner.authorize_start()

    await _wait_for_terminal_turn(queued)

    assert active.terminal_outcome == "failed"
    assert queued.terminal_outcome == "failed"
    assert active.terminal_resolution_committed is True
    assert queued.terminal_resolution_committed is True
    assert record.active_turn_id is None
    assert list(record.queued_turn_ids) == []
    assert product_session.read_model.status == "failed"
    assert record.status is SessionStatus.FAILED
    await runner.stop()


@pytest.mark.asyncio
async def test_dispatcher_persistence_failure_still_fails_session_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-dispatcher-failure-lifecycle",
        status=SessionStatus.STARTING,
    )
    runner, product_session = _lifecycle_runner_with_product_session(
        registry,
        record,
        monkeypatch,
        [False],
    )
    await registry.create(record)
    await runner.prepare_start()
    service = SessionService(registry=registry)
    await service._ensure_dispatcher(record)

    async def fail_event_persist(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected event persistence failure")

    monkeypatch.setattr(registry, "persist", fail_event_persist)
    runner.schedule_start()
    runner.authorize_start()
    assert runner._task is not None
    await asyncio.wait_for(runner._task, timeout=2)

    assert getattr(record, "_dispatcher_complete", False) is True
    assert product_session.read_model.status == "failed"
    assert record.status is SessionStatus.FAILED
    assert runner._closed is True


@pytest.mark.asyncio
async def test_productless_interactive_failure_marks_session_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-productless-failure",
        status=SessionStatus.RUNNING,
    )
    runner, _ = _lifecycle_runner_with_product_session(
        registry,
        record,
        monkeypatch,
        [False],
    )
    record.product_session = None
    await registry.create(record)
    await runner.prepare_start()
    active = record.turns_by_id[record.active_turn_id or ""]
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.persist(record)
    runner.schedule_start()
    runner.authorize_start()
    assert runner._task is not None
    await runner._task

    assert active.terminal_outcome == "failed"
    assert active.terminal_resolution_committed is True
    assert record.status is SessionStatus.FAILED


@pytest.mark.asyncio
async def test_interactive_stop_cancels_remaining_admitted_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id="session-stop-cancels-admitted",
        status=SessionStatus.STARTING,
    )
    runner, product_session = _lifecycle_runner_with_product_session(
        registry,
        record,
        monkeypatch,
        [],
    )
    entered = threading.Event()
    release = threading.Event()

    def execute_task(
        _task: str,
        *,
        input_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        entered.set()
        assert release.wait(2)
        return {
            "completion_summary": {
                "completed": False,
                "reason": "stopped_by_user",
            },
            "reward_metrics": None,
            "logging_dir": None,
            "_terminal_events": [],
            "_turn_completion_payload": {},
        }

    monkeypatch.setattr(runner._task_execution, "execute_task", execute_task)
    await registry.create(record)
    await runner.prepare_start()
    active = record.turns_by_id[record.active_turn_id or ""]
    queued = TurnRecord(
        input_id="input-queued",
        turn_id="turn-queued",
        client_message_id="message-queued",
        content="queued",
        attachments=(),
        original_disposition="queued",
        state="queued",
    )
    record.turns_by_id[queued.turn_id] = queued
    record.queued_turn_ids.append(queued.turn_id)
    record.turn_admission = record.turn_admission.__class__.ACTIVE
    await registry.persist(record)
    runner.schedule_start()
    runner.authorize_start()
    assert await asyncio.to_thread(entered.wait, 2)

    stop_task = asyncio.create_task(runner.stop())
    for _ in range(200):
        if runner._stop_event.is_set():
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("runner stop did not signal execution")
    release.set()
    await stop_task

    assert active.terminal_outcome == "cancelled"
    assert queued.terminal_outcome == "cancelled"
    assert active.terminal_resolution_committed is True
    assert queued.terminal_resolution_committed is True
    assert record.active_turn_id is None
    assert list(record.queued_turn_ids) == []
    assert product_session.read_model.status == "canceled"
    assert record.status is SessionStatus.STOPPED


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

@pytest.mark.asyncio
async def test_retained_registry_first_input_reconciles_journal_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    state_root = tmp_path / "state"
    session_id = "session-restart-admission-gap"
    event_root = tmp_path / "session-events"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    _, runtime_config = _seed_product_session_journal(
        monkeypatch,
        tmp_path,
        session_id,
        config_path=config_path,
    )

    from breadboard_engine.api.cli_bridge.session_artifacts import SessionArtifactStore

    class Upload:
        filename = "proof.txt"
        content_type = "text/plain"

        def __init__(self) -> None:
            self.data = b"proof"

        async def read(self, size: int = -1) -> bytes:
            data = self.data if size < 0 else self.data[:size]
            self.data = self.data[len(data) :]
            return data

    metadata = {
        "config_path": str(config_path),
        "permission_mode": "configured",
        "session_event_root": str(event_root),
        "workspace": str(workspace),
    }
    artifacts = SessionArtifactStore(session_id=session_id, metadata=metadata)
    uploaded = await artifacts.upload([Upload()], workspace_dir=workspace)
    attachment_id = uploaded.attachments[0].id
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata=metadata,
    )
    turn = TurnRecord(
        input_id="input-admission-gap",
        turn_id="turn-admission-gap",
        client_message_id="client-admission-gap",
        content="recover this input",
        attachments=(attachment_id,),
        original_disposition="started",
        state="active",
        body_digest=submission_body_digest(
            "recover this input", (attachment_id,)
        ),
        logical_event_count_before_admission=1,
        logical_input_content_hash=(
            "sha256:"
            + hashlib.sha256("recover this input".encode("utf-8")).hexdigest()
        ),
    )
    record.turns_by_id[turn.turn_id] = turn
    record.submissions_by_key[turn.client_message_id] = turn
    record.submissions_by_key_digest[identity_digest(turn.client_message_id)] = turn
    record.active_turn_id = turn.turn_id
    await registry.create(record)
    state_path = registry._state_path(session_id)
    assert state_path is not None
    retained_state = json.loads(state_path.read_text(encoding="utf-8"))
    retained_turn_state = retained_state["turns"][0]
    assert "recover this input" not in state_path.read_text(encoding="utf-8")
    assert "content" not in retained_turn_state
    assert retained_turn_state["content_hash"] == turn.logical_input_content_hash
    assert retained_turn_state["attachments"] == [attachment_id]

    monkeypatch.setattr(
        SessionRunner,
        "prepare_runtime_config",
        lambda self: runtime_config,
    )
    restarted = SessionRegistry(state_root=state_root)
    service = SessionService(registry=restarted)
    restored = await service.ensure_session(session_id)
    try:
        assert restored.runner is not None
        retained_turn = restored.turns_by_id[turn.turn_id]
        assert retained_turn.content == ""
        assert retained_turn.logical_input_content_hash == (
            turn.logical_input_content_hash
        )
        assert retained_turn.attachments == (attachment_id,)
        accepted_events = [
            event
            for event in restored.product_session.events
            if event.kind == "input.accepted"
        ]
        assert len(accepted_events) == 1
        assert dict(accepted_events[0].payload) == {
            "content_hash": turn.logical_input_content_hash,
            "attachments": (
                restored.runner.artifacts.selected_artifacts((attachment_id,))[0].as_dict(),
            ),
        }

        deferred: list[Any] = []
        duplicate = await service.send_input(
            session_id,
            SessionInputRequest(
                content=turn.content,
                attachments=[attachment_id],
                client_message_id=turn.client_message_id,
            ),
            defer_execution=deferred.append,
        )
        assert duplicate.disposition == "deduplicated"
        assert deferred == []
        assert len(
            [
                event
                for event in restored.product_session.events
                if event.kind == "input.accepted"
            ]
        ) == 1
    finally:
        if restored.runner is not None:
            await restored.runner.stop()


@pytest.mark.asyncio
async def test_legacy_workspace_journal_binds_before_terminal_publication(
    tmp_path: Path,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink

    workspace = tmp_path / "workspace"
    session_id = "session-legacy-workspace-binding"
    event_path = session_store.session_event_path(workspace, session_id)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "b" * 64}),
        "legacy workspace session",
        session_id=session_id,
        sink=JsonlEventSink(event_path),
    )
    product_session.complete()

    state_root = tmp_path / "state"
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={"workspace": str(workspace)},
    )
    await registry.create(record)

    restarted = SessionRegistry(state_root=state_root)
    service = SessionService(registry=restarted)
    restored = await service.ensure_session(session_id)

    assert restored.metadata["durable_product_workspace"] == str(workspace)
    projection, _ = session_store.load_session(workspace, session_id)
    assert projection.read_model.status == "completed"
    assert projection.read_model.session_id == session_id


def test_retained_admission_reconciliation_accepts_interleaved_observations() -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from breadboard_engine.api.cli_bridge.session_runner import SessionRunner

    record = SessionRecord(
        session_id="session-interleaved-admission",
        status=SessionStatus.RUNNING,
    )
    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "c" * 64}),
        "interleaved admission",
        session_id=record.session_id,
    )
    record.product_session = product_session
    content_hash = "sha256:" + (
        __import__("hashlib").sha256(b"interleaved input").hexdigest()
    )
    turn = TurnRecord(
        input_id="input-interleaved-admission",
        turn_id="turn-interleaved-admission",
        client_message_id="client-interleaved-admission",
        content="interleaved input",
        attachments=(),
        original_disposition="started",
        state="active",
        logical_event_count_before_admission=1,
        logical_input_content_hash=content_hash,
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    product_session.assistant_message("observation raced with admission")
    product_session.input(turn.content, [])

    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="cfg.yaml", task=""),
    )
    runner.reconcile_retained_input_admissions()

    accepted_events = [
        event for event in product_session.events if event.kind == "input.accepted"
    ]
    assert len(accepted_events) == 1
    assert accepted_events[0].payload["content_hash"] == content_hash


@pytest.mark.asyncio
async def test_duplicate_attachment_ids_are_canonical_before_registry_persist(
    tmp_path: Path,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink
    from breadboard_engine.api.cli_bridge.session_artifacts import SessionArtifactStore

    class Upload:
        filename = "proof.txt"
        content_type = "text/plain"

        def __init__(self) -> None:
            self.data = b"proof"

        async def read(self, size: int = -1) -> bytes:
            data = self.data if size < 0 else self.data[:size]
            self.data = self.data[len(data) :]
            return data

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata = {"workspace": str(workspace)}
    session_id = "session-duplicate-attachment-ids"
    artifacts = SessionArtifactStore(session_id=session_id, metadata=metadata)
    uploaded = await artifacts.upload([Upload()], workspace_dir=workspace)
    attachment_id = uploaded.attachments[0].id
    runner_metadata = dict(metadata)
    runner_artifacts = SessionArtifactStore(
        session_id=session_id, metadata=runner_metadata
    )
    runner_artifacts.restore_manifest(workspace)

    event_root = tmp_path / "events"
    event_path = event_root / session_id / "session_events.jsonl"
    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "d" * 64}),
        "duplicate attachment IDs",
        session_id=session_id,
        sink=JsonlEventSink(event_path),
    )
    state_root = tmp_path / "state"
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={**runner_metadata, "session_event_root": str(event_root)},
    )
    record.product_session = product_session
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(
            config_path="cfg.yaml", task="", workspace=str(workspace)
        ),
    )
    runner.artifacts = runner_artifacts
    record.runner = runner
    await registry.create(record)

    deferred: list[Any] = []
    service = SessionService(registry=registry)
    receipt = await service.send_input(
        session_id,
        SessionInputRequest(
            content="use attachment",
            attachments=[attachment_id, attachment_id],
            client_message_id="client-duplicate-attachment-ids",
        ),
        defer_execution=deferred.append,
    )

    assert receipt.disposition == "started"
    turn = record.turns_by_id[receipt.turn_id]
    assert turn.attachments == (attachment_id,)
    state_path = registry._state_path(session_id)
    assert state_path is not None
    retained = json.loads(state_path.read_text(encoding="utf-8"))
    assert retained["turns"][0]["attachments"] == [attachment_id]
    assert len(
        [event for event in product_session.events if event.kind == "input.accepted"]
    ) == 1
    assert len(deferred) == 1
    with pytest.raises(HTTPException) as invalid:
        await service.send_input(
            session_id,
            SessionInputRequest(
                content="invalid attachment",
                attachments=["missing-attachment"],
                client_message_id="client-invalid-attachment",
            ),
            defer_execution=deferred.append,
        )
    assert invalid.value.status_code == 400
    assert len(record.turns_by_id) == 1
    retained_after_invalid = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(retained_after_invalid["turns"]) == 1
    assert len(deferred) == 1
    await runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["pause", "complete", "request_approval"])
async def test_product_transition_after_registry_admission_aborts_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    import hashlib

    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink

    class Crash(BaseException):
        pass

    session_id = f"session-transition-race-{transition}"
    event_root = tmp_path / "events"
    event_path = event_root / session_id / "session_events.jsonl"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    runtime_config = {"providers": {"default_model": "test/restart"}}
    registry = SessionRegistry(state_root=tmp_path / "state")
    lock = SessionService(registry=registry)._runtime_lock(
        session_id, runtime_config, str(config_path)
    )
    product_session = ProductSession.start(
        lock,
        "transition race",
        session_id=session_id,
        sink=JsonlEventSink(event_path),
    )
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={
            "config_path": str(config_path),
            "permission_mode": "configured",
            "session_event_root": str(event_root),
        },
    )
    record.product_session = product_session
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path=str(config_path), task=""),
    )
    record.runner = runner
    await registry.create(record)

    async def crash_enqueue(*_args: Any, **_kwargs: Any) -> str:
        if transition == "request_approval":
            runner.transition_product_session(
                "request_approval", "approval-race", "shell"
            )
        else:
            runner.transition_product_session(transition, f"{transition}-race")
        raise Crash()

    monkeypatch.setattr(runner, "enqueue_input", crash_enqueue)
    with pytest.raises(Crash):
        await SessionService(registry=registry).send_input(
            session_id,
            SessionInputRequest(
                content="input lost at lifecycle boundary",
                client_message_id="client-transition-race",
            ),
        )

    monkeypatch.setattr(
        SessionRunner,
        "prepare_runtime_config",
        lambda self: runtime_config,
    )
    restarted = SessionRegistry(state_root=tmp_path / "state")
    recovered_service = SessionService(registry=restarted)
    recovered = await recovered_service.ensure_session(session_id)
    try:
        accepted_events = [
            event
            for event in recovered.product_session.events
            if event.kind == "input.accepted"
        ]
        assert accepted_events == []
        recovered_turn = recovered.turns_by_id[next(iter(recovered.turns_by_id))]
        assert recovered_turn.cancellation_requested is True
        assert recovered_turn.terminal_outcome in {"cancelled", "completed"}
        if transition == "pause":
            assert recovered.product_session.read_model.status == "paused"
            assert recovered.projected_status() is SessionStatus.RUNNING
        elif transition == "request_approval":
            assert recovered.product_session.read_model.status == "running"
            assert recovered.projected_status() is SessionStatus.RUNNING
        else:
            assert recovered.product_session.read_model.status == "completed"
            assert recovered.projected_status() is SessionStatus.COMPLETED
        assert recovered_turn.logical_input_content_hash == (
            "sha256:"
            + hashlib.sha256(
                "input lost at lifecycle boundary".encode("utf-8")
            ).hexdigest()
        )
    finally:
        if recovered.runner is not None:
            await recovered.runner.stop()


@pytest.mark.parametrize("terminal_kind", ["completed", "failed"])
def test_terminal_retained_turn_is_unchanged_by_later_lifecycle_event(
    tmp_path: Path,
    terminal_kind: str,
) -> None:
    from dataclasses import asdict

    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink

    session_id = f"session-terminal-retained-{terminal_kind}"
    product_session = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "f" * 64}),
        "terminal retained turn",
        session_id=session_id,
        sink=JsonlEventSink(
            tmp_path / session_id / "session_events.jsonl"
        ),
    )
    if terminal_kind == "completed":
        product_session.complete("already completed")
        session_status = SessionStatus.COMPLETED
    else:
        product_session.fail("worker_failure", "already failed")
        session_status = SessionStatus.FAILED
    turn = TurnRecord(
        input_id="input-terminal-retained",
        turn_id="turn-terminal-retained",
        client_message_id="client-terminal-retained",
        content="",
        attachments=(),
        original_disposition="started",
        state=terminal_kind,
        terminal_outcome=terminal_kind,
        terminal_resolution_committed=True,
        logical_event_count_before_admission=1,
        logical_input_content_hash="sha256:" + "a" * 64,
    )
    record = SessionRecord(
        session_id=session_id,
        status=session_status,
    )
    record.product_session = product_session
    record.turns_by_id[turn.turn_id] = turn
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(task=""),
    )
    before = asdict(turn)

    runner.reconcile_retained_input_admissions()

    assert asdict(turn) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "cancellation_requested",
        "execution_committed",
        "terminal_resolution_committed",
    ],
)
async def test_restart_rejects_non_boolean_retained_turn_flags(
    tmp_path: Path,
    field: str,
) -> None:
    registry = SessionRegistry(state_root=tmp_path)
    record = SessionRecord(
        session_id=f"sess-invalid-{field}",
        status=SessionStatus.RUNNING,
    )
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
    await registry.create(record)
    state_path = next(tmp_path.glob("*.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["turns"][0][field] = "false"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = SessionRegistry(state_root=tmp_path)

    assert await restarted.get(record.session_id) is None


def test_default_service_wires_durable_child_recovery(tmp_path: Path) -> None:
    service = SessionService(state_root=tmp_path / "session-state")

    assert isinstance(service._durable_child_repository, WorkItemRepository)
    assert isinstance(service._durable_child_reconciler, DurableChildReconciler)
