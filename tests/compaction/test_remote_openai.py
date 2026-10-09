from __future__ import annotations

import json
from typing import Any, Dict, List
import pytest

from breadboard_engine.compaction import (
    CompactionContext,
    CompactionRecord,
    CompactionState,
    MethodUnavailable,
    NativeCompaction,
    ProjectionTarget,
    estimate_messages_tokens,
    settings_from_config,
)
from .support import run_pipeline
from breadboard_engine.compaction.remote import RemoteCompaction
from breadboard_engine.compaction.remote.openai import (
    COMPACTION_TRIGGER_ITEM,
    CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE,
    OpenAIResponsesCompactionPort,
    build_compaction_v2_replacement_history,
    build_openai_native_history,
    parse_sse_events,
    trim_remote_compaction_input_to_context_window,
)
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
import types


def test_build_openai_native_history_formats():
    messages = [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Please inspect this code"},
        {
            "role": "assistant",
            "content": "Let me run bash.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "file1.py\nfile2.py"},
    ]

    items = build_openai_native_history(messages, "gpt-5")
    assert len(items) == 4
    # User message
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "user"
    assert items[0]["content"][0]["text"] == "Please inspect this code"
    # Assistant text and function call
    assert items[1]["type"] == "message"
    assert items[1]["role"] == "assistant"
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_123"
    assert items[2]["name"] == "bash"
    # Function call output
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "call_123"
    assert items[3]["output"] == "file1.py\nfile2.py"


def test_build_openai_native_history_with_previous_replacement():
    prev_items = [
        {"type": "compaction", "encrypted_content": "enc_prev_token"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier retained"}]},
    ]
    new_messages = [
        {"role": "user", "content": "Follow-up question"},
    ]
    items = build_openai_native_history(new_messages, "gpt-5", previous_replacement_history=prev_items)
    assert items[:2] == prev_items
    assert items[2]["type"] == "message"
    assert items[2]["role"] == "user"
    assert items[2]["content"][0]["text"] == "Follow-up question"


def test_trim_remote_compaction_input_to_context_window():
    items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "start"}]},
        {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "x" * 10000},
    ]
    result = trim_remote_compaction_input_to_context_window(items, context_window=500)
    assert result["fits"] is True
    assert result["rewritten_outputs"] == 1
    rewritten_item = result["input"][2]
    assert rewritten_item["output"] == CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE


def test_build_compaction_v2_replacement_history_filters_contextual():
    input_items = [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>\nrepo"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "User actual task"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "response"}]},
    ]
    compaction_item = {"type": "compaction", "encrypted_content": "enc_v2"}

    replacement, img_count = build_compaction_v2_replacement_history(
        input_items, compaction_item, retained_budget=10000
    )
    # Developer and contextual user messages are excluded from retained user history
    # Real user message is kept, followed by compaction item
    assert len(replacement) == 2
    assert replacement[0]["content"][0]["text"] == "User actual task"
    assert replacement[1] == compaction_item
    assert img_count == 0


def test_parse_sse_events():
    raw_sse = (
        "event: response.output_item.done\n"
        'data: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc_sse"}}\n\n'
        "event: response.completed\n"
        'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 450, "output_tokens": 10, "total_tokens": 460}}}\n\n'
    )
    events = parse_sse_events(raw_sse)
    assert len(events) == 2
    assert events[0]["item"]["encrypted_content"] == "enc_sse"
    assert events[1]["response"]["usage"]["input_tokens"] == 450


def test_v1_responses_compact_record_creation():
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload})
        return {
            "output": [
                {"type": "compaction", "encrypted_content": "enc_v1_token"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Step 1"},
        {"role": "assistant", "content": "Step 1 answer"},
    ]
    ctx = CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )

    record = port.compact(ctx)
    assert len(posted_calls) == 1
    assert posted_calls[0]["url"].endswith("/responses/compact")
    assert record.method == "remote"
    assert record.first_kept_index == len(messages)
    assert record.summary_messages == ()  # Non-readable OMP semantics
    assert record.native is not None
    assert record.native.provider == "openai"
    assert record.native.api == "responses"
    assert record.native.model == "gpt-5"
    assert len(record.native.items) == 1
    assert record.native.items[0]["type"] == "compaction"


def test_v2_streaming_responses_compact_record_creation():
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload})
        return (
            "event: response.output_item.done\n"
            'data: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc_v2_stream"}}\n\n'
            "event: response.completed\n"
            'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 1280, "output_tokens": 5, "total_tokens": 1285}}}\n\n'
        )

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    messages = [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": "Real user turn"},
        {"role": "assistant", "content": "Assistant answer"},
    ]
    ctx = CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": True}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )

    record = port.compact(ctx)
    assert len(posted_calls) == 1
    call = posted_calls[0]
    assert call["url"].endswith("/responses")
    # Verified compaction_trigger item was appended as last input
    assert call["payload"]["input"][-1] == COMPACTION_TRIGGER_ITEM
    assert call["payload"]["stream"] is True
    assert call["payload"]["store"] is False

    assert record.method == "remote"
    assert record.first_kept_index == len(messages)
    assert record.summary_messages == ()
    assert record.native is not None
    assert record.native.model == "gpt-5"
    items = record.native.items
    assert items[-1] == {"type": "compaction", "encrypted_content": "enc_v2_stream"}
    # Contains retained user message
    user_items = [it for it in items if it.get("role") == "user"]
    assert len(user_items) == 1
    assert user_items[0]["content"][0]["text"] == "Real user turn"


def test_v2_fallback_to_v1_on_stream_failure():
    calls = []

    def fake_poster(url, payload, headers):
        calls.append(url)
        if url.endswith("/responses"):
            # Stream error
            return "event: response.failed\ndata: {\"error\": \"internal_error\"}\n\n"
        # V1 fallback succeeds
        return {
            "output": [
                {"type": "compaction", "encrypted_content": "enc_v1_fallback"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Turn 1"},
        {"role": "assistant", "content": "Turn 1 ans"},
    ]
    ctx = CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": True}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )

    record = port.compact(ctx)
    assert len(calls) == 2
    assert calls[0].endswith("/responses")
    assert calls[1].endswith("/responses/compact")
    assert record.native is not None
    assert record.native.items[0]["encrypted_content"] == "enc_v1_fallback"


def test_end_to_end_compaction_and_responses_runtime_replay():
    """
    Acceptance test:
    With a fake transport, Pipeline with RemoteCompaction + an OpenAI port compacts
    and the Responses runtime request body for the next turn contains the native items
    in place of the summarized history; non-matching targets get no native items;
    existing Responses tests still pass.
    """
    def fake_v1_poster(url, payload, headers):
        return {
            "output": [
                {"type": "compaction", "encrypted_content": "enc_token_turn1"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_v1_poster)
    settings = settings_from_config({"enabled": True, "method_order": ["remote", "soft"]})

    initial_messages = [
        {"role": "system", "content": "System setup"},
        {"role": "user", "content": "Old message 1" * 100},
        {"role": "assistant", "content": "Old response 1" * 100},
        {"role": "user", "content": "Old message 2" * 100},
        {"role": "assistant", "content": "Old response 2" * 100},
    ]
    state = CompactionState()
    target = ProjectionTarget("openai", "responses", "gpt-5")
    ctx = CompactionContext(
        messages=initial_messages,
        state=state,
        settings=settings,
        reason="threshold",
        target=target,
        context_window=8000,
        tokens_before=estimate_messages_tokens(initial_messages),
        remote_ports=(port,),
    )
    outcome = run_pipeline(ctx, {"remote": RemoteCompaction()})
    assert outcome.compacted
    assert len(state.records) == 1
    record = state.records[0]
    assert record.method == "remote"
    assert record.native is not None

    # Next turn: a new user turn arrives and is appended to full append-only history
    full_messages = list(initial_messages)
    full_messages.append({"role": "user", "content": "New user prompt for next turn"})

    # 1. Matching target: projected view contains native marker
    projected_matching = state.project(full_messages, target)
    assert any("bb_native_compaction" in m for m in projected_matching)

    # 2. Check Responses runtime request body generation for the next turn
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    # Simulate session state that had an old previous_response_id from the prior turn
    mock_session_state = types.SimpleNamespace(
        get_provider_metadata=lambda name: {
            "previous_response_id": "resp_stale_before_compaction",
            "conversation_id": "conv_old",
        }.get(name),
        set_provider_metadata=lambda *_args: None,
    )
    runtime_context = ProviderRuntimeContext(
        mock_session_state,
        {"provider_tools": {"openai": {"responses_stateful": True}}},
    )

    payload = runtime._request_payload(
        model="gpt-5",
        messages=projected_matching,
        tools=None,
        context=runtime_context,
    )

    # Acceptance verification:
    # A. The request body contains the native items in place of the summarized history
    input_items = payload["input"]
    assert any(it.get("type") == "compaction" and it.get("encrypted_content") == "enc_token_turn1" for it in input_items)
    # B. The new turn is appended after the native items
    assert input_items[-1]["role"] == "user"
    assert input_items[-1]["content"][0]["text"] == "New user prompt for next turn"
    # C. bb_native_compaction marker key is NEVER sent on the wire
    payload_dump = json.dumps(payload)
    assert "bb_native_compaction" not in payload_dump
    # D. Stale previous_response_id is NOT included in request payload
    assert "previous_response_id" not in payload

    # 3. Non-matching target (e.g. switched to another model):
    other_target = ProjectionTarget("openai", "responses", "gpt-other-model")
    projected_other = state.project(full_messages, other_target)
    # Does NOT carry native items of the non-matching model
    assert not any("bb_native_compaction" in m for m in projected_other)
    other_payload = runtime._request_payload(
        model="gpt-other-model",
        messages=projected_other,
        tools=None,
        context=runtime_context,
    )
    assert not any(it.get("type") == "compaction" for it in other_payload["input"])


def test_runtime_compaction_port_factory():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    fake_client = types.SimpleNamespace(api_key="sk-test", base_url="https://api.openai.com/v1")
    port = runtime.compaction_port(client=fake_client, model="gpt-5")
    assert isinstance(port, OpenAIResponsesCompactionPort)
    assert port.provider == "openai"
    assert port.api == "responses"
    assert port.supports("gpt-5")
