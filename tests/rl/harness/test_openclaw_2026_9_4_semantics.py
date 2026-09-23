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


def test_incomplete_or_malformed_terminal_call_has_no_dispatch() -> None:
    result = finalize_native_chat_response(
        {"finish_reason": "tool_calls", "tool_calls": [{"id": "bad", "name": "write", "arguments": '{"path":'}]}
    )
    assert result.tool_batch.dispatchable is False
    assert result.tool_batch.tool_calls == ()
    assert result.recovery is not None
    assert result.recovery.trigger == "malformed-tool-call"


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
    state.commit_tool_results([{"role": "tool", "tool_call_id": "one", "content": "ok"}])
    state.begin_request()
    with pytest.raises(Exception, match="tool admission cap"):
        state.consume_native_response({"finish_reason": "tool_calls", "tool_calls": [{"id": "two", "name": "ls", "arguments": {"path": "."}}]})
    assert any(message.get("tool_call_id") == "one" for message in state.history)


def test_generic_stream_consumer_returns_batch_and_history() -> None:
    state = OpenClawSemanticsState()
    state.begin_request()
    result = state.consume_native_response({"content": "done", "finish_reason": "stop", "usage": {"total_tokens": 3}})
    assert result.tool_batch.tool_calls == ()
    assert result.history_mutations[0]["content"] == "done"
    assert state.prepare_request_history()[0]["role"] == "assistant"
