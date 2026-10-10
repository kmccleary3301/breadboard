from __future__ import annotations

import pytest

from breadboard.rl.harness.runners.openclaw_semantics import (
    OpenClawSemanticsState,
    finalize_native_chat_response,
)


def test_stream_fragments_finalize_before_dispatch() -> None:
    result = finalize_native_chat_response(
        {
            "stream_fragments": [
                {"kind": "toolcall_start", "call_id": "c1", "name": "write"},
                {"kind": "toolcall_delta", "call_id": "c1", "text": '{"path":"x",'},
                {"kind": "toolcall_delta", "call_id": "c1", "text": '"content":"ok"}'},
                {"kind": "text_delta", "text": "ignored until terminal"},
            ],
            "finish_reason": "tool_calls",
        }
    )
    assert result.tool_batch.dispatchable is True
    assert result.tool_batch.calls[0].name == "write"
    assert result.tool_batch.calls[0].arguments == {"path": "x", "content": "ok"}


def test_argument_shape_is_deferred_to_native_worker() -> None:
    result = finalize_native_chat_response(
        {
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "missing-path",
                    "name": "read",
                    "arguments": {"unexpected": "value"},
                }
            ],
        }
    )
    assert result.tool_batch.dispatchable is True
    assert result.tool_batch.calls[0].arguments == {"unexpected": "value"}

def test_incomplete_or_malformed_terminal_call_has_no_dispatch() -> None:
    result = finalize_native_chat_response(
        {"finish_reason": "tool_calls", "tool_calls": [{"id": "bad", "name": "write", "arguments": '{"path":'}]}
    )
    assert result.tool_batch.dispatchable is False
    assert result.tool_batch.tool_calls == ()
    assert result.recovery is not None
    assert result.recovery.trigger == "malformed-tool-call"

def test_malformed_terminal_call_retains_text_and_source_error_without_replay_call() -> None:
    state = OpenClawSemanticsState()
    state.begin_request()
    parsed = state.consume_native_response({
        "finish_reason": "tool_calls",
        "content": "malformed tool-call rejected",
        "tool_calls": [{"id": "bad", "name": "write", "arguments": '{"path":"malformed-marker.txt","content":'}],
    })
    assert parsed.tool_batch.dispatchable is False
    assert parsed.assistant_message["content"] == [{"type": "text", "text": "malformed tool-call rejected"}]
    assert parsed.assistant_message["tool_calls"] == []
    assert parsed.assistant_message["stop_reason"] == "error"
    assert state.prepare_request_history()[-1]["tool_calls"] == []
    assert state.request_count == 1



def test_provider_error_stops_before_retry_fallback_or_compaction() -> None:
    state = OpenClawSemanticsState()
    state.begin_request()
    parsed = state.consume_native_response({"finish_reason": "error", "native_stop_reason": "server_error"})
    assert parsed.recovery is not None
    assert parsed.recovery.stop is True
    assert parsed.recovery.retry is False
    assert parsed.recovery.fallback is False
    assert parsed.recovery.compaction is False
    assert state.request_count == 1


def test_tool_admission_budget_keeps_started_history() -> None:
    state = OpenClawSemanticsState(max_tool_admissions=1)
    state.begin_request()
    state.consume_native_response({"finish_reason": "tool_calls", "tool_calls": [{"id": "one", "name": "ls", "arguments": {"path": "."}}]})
    state.commit_tool_results([{
        "role": "toolResult", "toolCallId": "one", "toolName": "ls", "isError": False,
        "content": [{"type": "text", "text": "ok"}],
    }])
    state.begin_request()
    with pytest.raises(Exception, match="tool admission cap"):
        state.consume_native_response({"finish_reason": "tool_calls", "tool_calls": [{"id": "two", "name": "ls", "arguments": {"path": "."}}]})
    assert any(message.get("toolCallId") == "one" for message in state.history)


def test_generic_stream_consumer_returns_batch_and_history() -> None:
    state = OpenClawSemanticsState()
    state.begin_request()
    result = state.consume_native_response({"content": "done", "finish_reason": "stop", "usage": {"total_tokens": 3}})
    assert result.tool_batch.tool_calls == ()
    assert result.history_mutations[0]["content"] == [{"type": "text", "text": "done"}]
    assert result.history_mutations[0]["providerUsage"] == {"total_tokens": 3}
    assert state.prepare_request_history()[0]["role"] == "assistant"



def test_to_trace_preserves_raw_requests_inputs_and_effects() -> None:
    state = OpenClawSemanticsState()
    body = {
        "model": "openclaw-model",
        "messages": [{"role": "user", "content": "task"}],
        "tools": [{"type": "function", "function": {"name": "read"}}],
        "stream": True,
    }
    trace = state.to_trace(
        requests=[body],
        runtime_inputs={"cwd": "/workspace", "current_date": "2026-09-23"},
        effects={"marker.txt": "sha256:abc"},
    )
    assert trace["requests"] == [body]
    assert trace["runtime_inputs"] == {
        "cwd": "/workspace",
        "current_date": "2026-09-23",
    }
    assert trace["effects"] == {"marker.txt": "sha256:abc"}
    assert trace["request_count"] == 1
    assert trace["termination"] == {"kind": "running", "native_stop_reason": None}

def test_request_cap_records_refused_ninth_attempt_without_dispatch() -> None:
    state = OpenClawSemanticsState()
    for _ in range(8):
        assert state.begin_query() is None
    terminal = state.begin_query()
    assert terminal is not None
    assert terminal["status"] == 429
    assert terminal["isError"] is True
    assert terminal["error"] == "bbe4 capture request cap"
    assert state.request_count == 8
    assert state.refused_attempts == 1
    assert state.finish()["history"][-1]["isError"] is True


def test_stream_index_delta_preserves_initial_identity() -> None:
    result = finalize_native_chat_response(
        {
            "stream_fragments": [
                {
                    "kind": "tool_arguments",
                    "index": 0,
                    "tool_index": 2,
                    "id": "call_original",
                    "type": "function",
                    "name": "write",
                    "text": '{"path":"x",',
                },
                {
                    "kind": "tool_arguments",
                    "index": 1,
                    "tool_index": 2,
                    "text": '"content":"ok"}',
                },
            ],
            "finish_reason": "tool_calls",
        }
    )
    call = result.tool_batch.calls[0]
    assert call.tool_call_id == "call_original"
    assert call.call_type == "function"
    assert call.index == 2
    assert call.arguments == {"path": "x", "content": "ok"}


def test_source_tool_result_preserves_content_blocks_and_details() -> None:
    state = OpenClawSemanticsState(task="Inspect the image")
    parsed = state.prepare_response({
        "finish_reason": "tool_calls", "content": "Reading the image",
        "tool_calls": [{"id": "imagecall", "name": "read", "arguments": {"path": "x.png"}}],
    })
    raw = {
        "id": "imagecall", "completion_index": 0, "isError": False,
        "delivery_id": "adapter-only-random-receipt",
        "content": [
            {"type": "text", "text": "Image content"},
            {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
        ],
        "details": {"truncation": {"truncated": False}, "metadata": ["original", 7]},
    }
    committed = state.commit_tool_results(parsed.calls, [raw])[0]
    assert committed["role"] == "toolResult"
    assert committed["toolCallId"] == "imagecall"
    assert committed["toolName"] == "read"
    assert committed["content"] == raw["content"]
    assert committed["details"] == raw["details"]
    assert committed["isError"] is False
    assert "tool_call_id" not in committed
    assert set(committed) == {"role", "toolCallId", "toolName", "content", "details", "isError"}
    assert raw["delivery_id"] == "adapter-only-random-receipt"


def test_native_compaction_replaces_mutable_history_with_full_system_context() -> None:
    state = OpenClawSemanticsState(task="Original task", system_prompt="Pinned source prompt")
    replacement = [
        {"role": "system", "content": "Pinned source prompt"},
        {"role": "compactionSummary", "summary": "Prior work", "tokensBefore": 100, "timestamp": 0},
        {"role": "assistant", "content": [{"type": "text", "text": "Retained text"}]},
    ]
    state.messages = replacement
    assert state.prepare_request_history() == replacement
    state.messages.append({"role": "user", "content": "Continue"})
    assert state.history[-1] == {"role": "user", "content": "Continue"}


def test_declared_episode_clock_orders_source_messages_across_compaction() -> None:
    epoch = 1_728_432_000_000
    state = OpenClawSemanticsState(
        task="Inspect", bootstrap={"message_timestamp_ms": str(epoch)},
    )
    parsed = state.prepare_response({
        "finish_reason": "tool_calls",
        "tool_calls": [{"id": "read1", "name": "read", "arguments": {"path": "x"}}],
    })
    result = state.commit_tool_results(parsed.calls, [{
        "id": "read1", "completion_index": 0, "isError": False,
        "content": [{"type": "text", "text": "Native output"}],
    }])[0]
    assert [message["timestamp"] for message in state.history] == [epoch, epoch + 1, epoch + 2]
    state.messages = [{
        "role": "compactionSummary", "summary": "Inspected", "tokensBefore": 20,
        "timestamp": epoch + 3,
    }, result]
    next_response = state.prepare_response({"finish_reason": "stop", "content": "Done"})
    assert next_response.assistant_message["timestamp"] == epoch + 4


def test_length_stop_preserves_source_identity_until_native_retry_verdict() -> None:
    model = {"api": "openai-completions", "provider": "openai", "id": "model-a"}
    state = OpenClawSemanticsState(
        task="Inspect", bootstrap={"message_timestamp_ms": "1728432000000", "model_config": model},
    )
    parsed = state.prepare_response({
        "finish_reason": "length", "content": "",
        "usage": {"prompt_tokens": 131072, "completion_tokens": 0, "total_tokens": 131072},
    })
    assert state.is_exited
    assert state.native_stop_reason == "length"
    assert parsed.assistant_message["stopReason"] == "length"
    assert {key: parsed.assistant_message[key] for key in ("api", "provider", "model")} == {
        "api": model["api"], "provider": model["provider"], "model": model["id"],
    }
    state.reopen_after_overflow()
    assert not state.is_exited
    assert state.native_stop_reason is None
    assert state.stop_reason is None
    assert state.request_count == 0
