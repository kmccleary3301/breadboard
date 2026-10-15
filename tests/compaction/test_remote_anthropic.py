"""Tests for Anthropic on-demand compaction and native replay."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import pytest

from breadboard_engine.compaction import (
    NATIVE_MARKER_KEY,
    CompactionContext,
    CompactionState,
    MethodUnavailable,
    NativeCompactionError,
    ProjectionTarget,
    estimate_messages_tokens,
    settings_from_config,
)
from breadboard_engine.compaction.remote.anthropic import (
    AnthropicCompactionPort,
    find_anthropic_compaction_cut,
    supports_anthropic_compaction,
)
from breadboard_engine.compaction.primitives.accounting import build_estimator

from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime
from breadboard_engine.provider.contracts import (
    ProviderRuntimeContext,
)


TARGET = ProjectionTarget("anthropic", "messages", "claude-sonnet-5")


class FakeAnthropicMessages:
    def __init__(self, response: Any = None, error: Optional[Exception] = None) -> None:
        self.response = response
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeAnthropicClient:
    def __init__(self, response: Any = None, error: Optional[Exception] = None) -> None:
        self.messages = FakeAnthropicMessages(response, error)
        self.beta = SimpleNamespace(messages=self.messages)


def _context(
    messages: list[dict],
    state: Optional[CompactionState] = None,
    *,
    model: str = "claude-sonnet-5",
    keep_recent_tokens: int = 100,
    window: int = 200_000,
    custom_instructions: Optional[str] = None,
) -> CompactionContext:
    settings = settings_from_config({
        "enabled": True,
        "keep_recent_tokens": keep_recent_tokens,
        "custom_instructions": custom_instructions,
    })
    return CompactionContext(
        messages=messages,
        state=state or CompactionState(),
        settings=settings,
        reason="threshold",
        target=ProjectionTarget("anthropic", "messages", model),
        context_window=window,
        tokens_before=estimate_messages_tokens(messages),
        custom_instructions=custom_instructions,
    )


# -----------------------------------------------------------------------------
# 1. Model Support Tests
# -----------------------------------------------------------------------------

def test_supports_anthropic_compaction_supported_models() -> None:
    supported = [
        "claude-opus-4-6",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-fable-5",
        "claude-mythos-5",
        "claude-mythos-preview",
        "us.anthropic.claude-opus-5-5",
        "anthropic.claude-sonnet-4-6",
    ]
    for model_id in supported:
        assert supports_anthropic_compaction(model_id), f"Expected {model_id} to be supported"


def test_supports_anthropic_compaction_unsupported_models() -> None:
    unsupported = [
        "claude-haiku-4-5",
        "claude-sonnet-4-5",
        "claude-opus-4-5",
        "claude-opus-4-1",
        "claude-3-5-sonnet",
        "claude-3-opus",
        "gpt-4o",
    ]
    for model_id in unsupported:
        assert not supports_anthropic_compaction(model_id), f"Expected {model_id} to be unsupported"


# -----------------------------------------------------------------------------
# 3. Cut Calculation Tests
# -----------------------------------------------------------------------------

def _claude_continuation(call_id: str, text: str) -> dict:
    from breadboard_engine.provider.adapters import AnthropicAdapter

    return AnthropicAdapter().create_tool_result_message(call_id, "read", {"__claude_tool_result_messages": [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}]}]})


@pytest.mark.parametrize("result", [
    {"role": "tool", "tool_call_id": "call-1", "content": "bytes"},
    _claude_continuation("call-1", "bytes"),
])
def test_find_anthropic_compaction_cut_moves_past_tool_pairs(result) -> None:
    user = lambda text: {"role": "user", "content": text}
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
    }
    done = {"role": "assistant", "content": "done"}

    # Initial cut at 2 (before result) must advance past result to 3 (done)
    messages = [user("start"), call, result, done, user("next")]
    assert find_anthropic_compaction_cut(messages, 2) == 3


def test_find_anthropic_compaction_cut_same_role_boundaries() -> None:
    user = lambda text: {"role": "user", "content": text}
    done = {"role": "assistant", "content": "done"}

    # User followed by user cannot cut at 1 (same role), moves to 2 (done)
    messages = [user("old"), user("same"), done]
    assert find_anthropic_compaction_cut(messages, 1) == 2


def test_find_anthropic_compaction_cut_developer_system_skipped() -> None:
    user = lambda text: {"role": "user", "content": text}
    done = {"role": "assistant", "content": "done"}

    messages = [user("old"), {"role": "developer", "content": "control"}, done]
    assert find_anthropic_compaction_cut(messages, 1) == 2


def test_find_anthropic_compaction_cut_no_safe_tail_summarizes_all() -> None:
    user = lambda text: {"role": "user", "content": text}
    messages = [user("old"), user("same")]
    assert find_anthropic_compaction_cut(messages, 1) == 2


# -----------------------------------------------------------------------------
# 4. Port Execution Tests
# -----------------------------------------------------------------------------



def test_anthropic_native_compaction_counts_opaque_block_for_budget_decisions() -> None:
    summary = "Readable handoff summary."
    source_block = {
        "type": "compaction",
        "content": summary,
        "signature": "sig_state_opaque",
        "encrypted_content": "opaque-ciphertext-" + "x" * 16_000,
    }
    original_block = dict(source_block)
    fake_response = SimpleNamespace(
        stop_reason="compaction",
        model=TARGET.model,
        content=[source_block],
        usage=SimpleNamespace(input_tokens=0, output_tokens=0, iterations=[]),
    )
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "next task"},
        {"role": "assistant", "content": "done"},
    ]
    ctx = _context(messages)
    record = AnthropicCompactionPort(
        client=FakeAnthropicClient(response=fake_response),
        model=TARGET.model,
    ).compact(ctx)

    assert record.native is not None
    assert record.native.items == (source_block,)
    assert source_block == original_block
    assert record.summary == summary
    assert record.summary_messages[0]["content"] == summary

    ctx.state.append(record, messages)
    projected = ctx.state.project(messages, ctx.target)
    small_budget = 1_000
    large_budget = 4_000
    for estimate in (estimate_messages_tokens, build_estimator("bytes4")):
        projected_tokens = estimate(projected)
        assert estimate(messages) < small_budget <= projected_tokens
        assert projected_tokens < large_budget


def test_anthropic_compaction_input_carries_ledger_edits() -> None:
    from breadboard_engine.compaction.state import MessageEdit

    fake_response = SimpleNamespace(stop_reason="compaction", model="claude-sonnet-5",
                                    content=[SimpleNamespace(type="compaction", content="S", signature="sig")],
                                    usage=SimpleNamespace(input_tokens=0, output_tokens=0, iterations=[]))
    fake_client = FakeAnthropicClient(response=fake_response)
    messages = [
        {"role": "user", "content": "ORIGINAL_OUTPUT " * 50},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "next task"},
        {"role": "assistant", "content": "done"},
    ]
    ctx = _context(messages)
    ctx.state.append(ctx.new_record(method="prune", edits=(MessageEdit(0, {"role": "user", "content": "[pruned]"}),)),
                     messages)
    AnthropicCompactionPort(client=fake_client, model="claude-sonnet-5").compact(ctx)

    sent = repr(fake_client.messages.calls[0]["messages"])
    assert "[pruned]" in sent and "ORIGINAL_OUTPUT" not in sent



def test_anthropic_compaction_port_rejects_unsupported_model() -> None:
    fake_client = FakeAnthropicClient()
    port = AnthropicCompactionPort(client=fake_client, model="claude-haiku-4-5")
    ctx = _context([{"role": "user", "content": "hi"}], model="claude-haiku-4-5")
    with pytest.raises(MethodUnavailable):
        port.compact(ctx)


def test_anthropic_compaction_port_raises_on_non_compaction_stop() -> None:
    fake_response = SimpleNamespace(
        stop_reason="max_tokens",
        model="claude-sonnet-5",
        content=[SimpleNamespace(type="text", text="incomplete summary")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=100),
    )
    fake_client = FakeAnthropicClient(response=fake_response)
    port = AnthropicCompactionPort(client=fake_client, model="claude-sonnet-5")

    ctx = _context([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
    with pytest.raises(NativeCompactionError):
        port.compact(ctx)


def test_anthropic_compaction_port_rejects_unsigned_block() -> None:
    fake_response = SimpleNamespace(
        stop_reason="compaction",
        model="claude-sonnet-5",
        content=[SimpleNamespace(type="compaction", content="unsigned", signature=None)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    fake_client = FakeAnthropicClient(response=fake_response)
    port = AnthropicCompactionPort(client=fake_client, model="claude-sonnet-5")

    ctx = _context([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
    with pytest.raises(NativeCompactionError):
        port.compact(ctx)


# -----------------------------------------------------------------------------
# 5. AnthropicMessagesRuntime Replay Integration Tests
# -----------------------------------------------------------------------------

def test_runtime_has_no_compaction_port_for_unsupported_model() -> None:
    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )
    client = FakeAnthropicClient()

    unsupported_port = runtime.compaction_port(client=client, model="claude-sonnet-4-5")
    assert unsupported_port is None


def test_runtime_replays_native_marker_and_folds_assistant() -> None:
    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )
    summary_block = {"type": "compaction", "content": "Summary text", "signature": "sig_123"}
    marker_msg = {
        "role": "user",
        "content": "Summary text",
        NATIVE_MARKER_KEY: {
            "record_id": "cmp_abc",
            "provider": "anthropic",
            "api": "messages",
            "model": "claude-sonnet-5",
            "items": [summary_block],
        },
    }
    retained_assistant = {"role": "assistant", "content": "Retained answer"}
    next_user = {"role": "user", "content": "Next question"}

    messages = [marker_msg, retained_assistant, next_user]
    _, converted = runtime._convert_messages(messages)

    # 1. Native marker key is never on the wire
    for m in converted:
        assert NATIVE_MARKER_KEY not in m

    # 2. The compaction block was folded into the head of the following assistant message
    assert len(converted) == 2
    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"][0] == summary_block
    assert converted[0]["content"][1] == {"type": "text", "text": "Retained answer"}
    assert converted[1] == {"role": "user", "content": [{"type": "text", "text": "Next question"}]}


def test_runtime_replays_standalone_compaction_block_when_not_followed_by_assistant() -> None:
    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )
    summary_block = {"type": "compaction", "content": "Summary text", "signature": "sig_123"}
    marker_msg = {
        "role": "user",
        "content": "Summary text",
        NATIVE_MARKER_KEY: {
            "record_id": "cmp_abc",
            "provider": "anthropic",
            "api": "messages",
            "model": "claude-sonnet-5",
            "items": [summary_block],
        },
    }
    next_user = {"role": "user", "content": "Next question"}

    messages = [marker_msg, next_user]
    _, converted = runtime._convert_messages(messages)

    assert len(converted) == 2
    assert converted[0] == {"role": "assistant", "content": [summary_block]}
    assert converted[1] == {"role": "user", "content": [{"type": "text", "text": "Next question"}]}


def test_runtime_ignores_foreign_provider_native_marker() -> None:
    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )
    # A marker from OpenAI should NOT be replayed natively on Anthropic
    marker_msg = {
        "role": "user",
        "content": "OpenAI summary text",
        NATIVE_MARKER_KEY: {
            "record_id": "cmp_openai",
            "provider": "openai",
            "api": "responses",
            "model": "gpt-4o",
            "items": [{"id": "item_1"}],
        },
    }
    messages = [marker_msg]
    _, converted = runtime._convert_messages(messages)

    assert len(converted) == 1
    assert converted[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "OpenAI summary text"}],
    }
    assert NATIVE_MARKER_KEY not in converted[0]


class FakeAnthropicError(Exception):
    def __init__(self, message: str, status_code: int, body: dict) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_runtime_surfaces_context_overflow_details() -> None:
    from breadboard_engine.compaction.overflow import is_context_overflow
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError

    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )

    overflow_exc = FakeAnthropicError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'prompt is too long: 210000 tokens > 200000 maximum'}}",
        status_code=400,
        body={"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 210000 tokens > 200000 maximum"}},
    )

    def raise_overflow(**kwargs):
        raise overflow_exc

    fake_client = FakeAnthropicClient()
    fake_client.messages.with_raw_response = SimpleNamespace(create=raise_overflow)

    class _FakeSessionState:
        def __init__(self) -> None:
            self.metadata = {}
        def get_provider_metadata(self, key, default=None):
            return self.metadata.get(key, default)
        def set_provider_metadata(self, key, value):
            self.metadata[key] = value

    def make_ctx():
        return ProviderRuntimeContext(
            session_state=_FakeSessionState(),
            agent_config={},
        )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=fake_client,
            model="claude-sonnet-5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=make_ctx(),
        )

    err = exc_info.value
    assert err.details is not None
    assert err.details.get("code") == "context_length_exceeded"
    assert is_context_overflow(err) is True
    assert "prompt is too long" not in str(err)

    # 429 rate limit / 500 error does not classify as context overflow
    server_err = FakeAnthropicError(
        "Error code: 500 - internal server error",
        status_code=500,
        body={"type": "error", "error": {"type": "api_error", "message": "internal error"}},
    )

    def raise_500(**kwargs):
        raise server_err

    fake_client_500 = FakeAnthropicClient()
    fake_client_500.messages.with_raw_response = SimpleNamespace(create=raise_500)

    with pytest.raises(ProviderRuntimeError) as exc_info_500:
        runtime.invoke(
            client=fake_client_500,
            model="claude-sonnet-5",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=make_ctx(),
        )
    assert not is_context_overflow(exc_info_500.value)


def test_anthropic_compaction_port_converts_authorized_media(tmp_path: Any) -> None:
    from dataclasses import replace
    from breadboard.product.runtime.artifacts import put_workspace_artifact
    from breadboard_engine.state.session_state import SessionState

    content = b"fake image bytes"
    artifact = put_workspace_artifact(tmp_path, content, media_type="image/png")
    uri = f"attachment://{artifact.digest}"
    state = SessionState(workspace=str(tmp_path), image="claude-dev", config={})
    state.set_provider_metadata("attachment_capabilities", {uri: artifact.as_dict()})

    runtime = AnthropicMessagesRuntime(
        descriptor=SimpleNamespace(provider_id="anthropic", api_type="messages", config={})
    )
    fake_client = FakeAnthropicClient()
    fake_response = SimpleNamespace(
        stop_reason="compaction",
        content=[
            SimpleNamespace(type="compaction", content="## Summary\ncompacted", signature="sig")
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    fake_client.messages.create = lambda **kwargs: (fake_client.messages.calls.append(kwargs) or fake_response)
    port = runtime.compaction_port(client=fake_client, model="claude-sonnet-5")
    messages = [
        {"role": "user", "content": [
            {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"},
            {"type": "text", "text": "What is in this image?"},
        ]},
        {"role": "assistant", "content": "I see an image."},
        {"role": "user", "content": "Tell me more."},
        {"role": "assistant", "content": "Done."},
    ]
    ctx = replace(_context(messages, state=state.compaction_state), runtime_context=ProviderRuntimeContext(state, {}))
    record = port.compact(ctx)
    assert record.method == "remote"
    assert len(fake_client.messages.calls) == 1
    sent_msgs = fake_client.messages.calls[0]["messages"]
    image_block = sent_msgs[0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
