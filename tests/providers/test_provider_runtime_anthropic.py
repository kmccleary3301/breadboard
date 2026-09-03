import types

import pytest

from breadboard_engine.provider.contracts import (
    ProviderCorrelation,
    ProviderContractError,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderRequest,
)
from breadboard_engine.provider.normalizer import (
    normalized_result_messages,
    normalized_result_replay,
)

from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import (
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)


class _DummySessionState:
    def __init__(self):
        self._metadata = {}
        self.workspace = "/tmp/test-workspace"

    def get_provider_metadata(self, key: str, default=None):
        return self._metadata.get(key, default)

    def set_provider_metadata(self, key: str, value):
        self._metadata[key] = value


def _anthropic_tool_schema():
    return [
        {
            "name": "fetch_data",
            "description": "Fetch data",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }
    ]


def test_anthropic_converts_canonical_tool_result_and_call() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor(
        "anthropic/claude-3-opus"
    )
    runtime = provider_registry.create_runtime(descriptor)

    system, converted = runtime._convert_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "call_id": "call-1",
                        "name": "fetch_data",
                        "arguments_json": '{"key":"value"}',
                        "arguments": {"key": "value"},
                    }
                ],
            },
            {
                "role": "tool_result",
                "content": [
                    {
                        "type": "tool_result",
                        "call_id": "call-1",
                        "content": '{"ok":true}',
                        "is_error": False,
                    }
                ],
            },
        ]
    )

    assert system is None
    assert converted == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "fetch_data",
                    "input": {"key": "value"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": '{"ok":true}',
                    "is_error": False,
                }
            ],
        },
    ]


def test_anthropic_rejects_malformed_tool_arguments() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor(
        "anthropic/claude-3-opus"
    )
    runtime = provider_registry.create_runtime(descriptor)

    with pytest.raises(ProviderContractError):
        runtime._convert_messages(
            [
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "fetch_data",
                                "arguments": "{",
                            },
                        }
                    ],
                }
            ]
        )


def test_anthropic_runtime_stream_success(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)

    final_message = types.SimpleNamespace(
        id="message-1",
        content=[
            {"type": "text", "text": "Hello"},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "fetch_data",
                "input": {"foo": "bar"},
            },
            {"type": "thinking", "text": "analysis", "signature": "signed-analysis"},
            {"type": "redacted_thinking", "data": "encrypted-redacted"},
        ],
        stop_reason="end_turn",
        model=model,
    )

    final_usage = {"input_tokens": 12, "output_tokens": 34}

    class FakeStream:
        def __iter__(self):
            return iter(
                [
                    types.SimpleNamespace(
                        type="message_start",
                        message=types.SimpleNamespace(id="message-1"),
                    ),
                    types.SimpleNamespace(
                        type="content_block_start",
                        index=0,
                        content_block={"type": "text"},
                    ),
                    types.SimpleNamespace(
                        type="content_block_delta",
                        index=0,
                        delta={"type": "text_delta", "text": "Hello"},
                    ),
                    types.SimpleNamespace(type="content_block_stop", index=0),
                    types.SimpleNamespace(
                        type="content_block_start",
                        index=1,
                        content_block={
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "fetch_data",
                            "input": {},
                        },
                    ),
                    types.SimpleNamespace(
                        type="content_block_delta",
                        index=1,
                        delta={
                            "type": "input_json_delta",
                            "partial_json": '{"foo":"bar"}',
                        },
                    ),
                    types.SimpleNamespace(type="content_block_stop", index=1),
                    types.SimpleNamespace(
                        type="content_block_start",
                        index=2,
                        content_block={"type": "thinking"},
                    ),
                    types.SimpleNamespace(
                        type="content_block_delta",
                        index=2,
                        delta={
                            "type": "thinking_delta",
                            "thinking": "analysis",
                        },
                    ),
                    types.SimpleNamespace(
                        type="content_block_delta",
                        index=2,
                        delta={
                            "type": "signature_delta",
                            "signature": "signed-analysis",
                        },
                    ),
                    types.SimpleNamespace(type="content_block_stop", index=2),
                    types.SimpleNamespace(
                        type="content_block_start",
                        index=3,
                        content_block={"type": "redacted_thinking"},
                    ),
                    types.SimpleNamespace(type="content_block_stop", index=3),
                    types.SimpleNamespace(
                        type="message_delta",
                        delta={"stop_reason": "end_turn"},
                        usage=final_usage,
                    ),
                    types.SimpleNamespace(type="message_stop"),
                ]
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_final_message(self):
            return final_message

        def get_final_usage(self):
            return final_usage

    class FakeMessages:
        def stream(self, **kwargs):
            assert kwargs["model"] == model
            return FakeStream()

        def create(self, **kwargs):
            raise AssertionError("create should not be called when streaming succeeds")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.anthropic",
        FakeAnthropic,
    )

    client = runtime.create_client("fake-key")
    context = ProviderRuntimeContext(
        session_state=_DummySessionState(),
        agent_config={},
        stream=True,
    )

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
        tools=_anthropic_tool_schema(),
        stream=True,
        context=context,
    )

    assert result.messages[0].content == "Hello"
    assert result.messages[0].tool_calls[0].name == "fetch_data"
    assert result.reasoning_summaries == ["analysis"]
    assert result.usage == {"input_tokens": 12, "output_tokens": 34}
    assert result.metadata["usage"]["output_tokens"] == 34
    assert normalized_result_messages(result)[1]["content"] == [
        {"type": "thinking", "text": "analysis"},
        {
            "type": "provider_replay",
            "provider_id": "anthropic",
            "schema_version": "anthropic.messages.v1",
            "replay_scope": "same_provider",
            "payload": {"signature": "signed-analysis"},
        },
        {"type": "redacted_thinking", "data": "encrypted-redacted"},
        {
            "type": "provider_replay",
            "provider_id": "anthropic",
            "schema_version": "anthropic.messages.v1",
            "replay_scope": "same_provider",
            "payload": {"redacted_data": "encrypted-redacted"},
        },
    ]
    assert normalized_result_replay(result, provider_id="anthropic") == [
        {
            "provider_id": "anthropic",
            "schema_version": "anthropic.messages.v1",
            "replay_scope": "same_provider",
            "payload": {"signature": "signed-analysis"},
        },
        {
            "provider_id": "anthropic",
            "schema_version": "anthropic.messages.v1",
            "replay_scope": "same_provider",
            "payload": {"redacted_data": "encrypted-redacted"},
        },
    ]


@pytest.mark.parametrize(
    ("events", "code"),
    [
        ([], "missing_anthropic_terminal"),
        (
            [types.SimpleNamespace(type="future_semantic")],
            "unknown_anthropic_event",
        ),
    ],
)
def test_anthropic_stream_requires_known_terminal_semantics(
    events, code
) -> None:
    descriptor, _ = provider_router.get_runtime_descriptor(
        "anthropic/claude-3-opus"
    )
    runtime = provider_registry.create_runtime(descriptor)

    class FakeStream:
        def __iter__(self):
            return iter(events)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_final_message(self):
            raise AssertionError("invalid stream must fail before final message")

    client = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            stream=lambda **_kwargs: FakeStream()
        )
    )
    context = ProviderRuntimeContext(
        session_state=_DummySessionState(),
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._call_streaming(client, {}, context)

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.details["code"] == code


def test_anthropic_runtime_stream_error(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeMessages:
        def stream(self, **kwargs):
            raise RuntimeError("stream disabled")

        def create(self, **kwargs):
            raise AssertionError("create should not be reached in this test")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.anthropic",
        FakeAnthropic,
    )

    client = runtime.create_client("fake-key")
    context = ProviderRuntimeContext(
        session_state=_DummySessionState(),
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError):
        runtime.invoke(
            client=client,
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=_anthropic_tool_schema(),
            stream=True,
            context=context,
        )


def test_anthropic_runtime_preserves_classified_429_details(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor(
        "anthropic/claude-3-opus"
    )
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRateLimitError(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.status_code = 429
            self.response = types.SimpleNamespace(
                headers={"retry-after": "17"},
                text='{"error":"rate_limited"}',
            )

    class FakeMessages:
        def __init__(self):
            self.with_raw_response = self

        def create(self, **_kwargs):
            raise FakeRateLimitError()

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.anthropic_rate_limit_error",
        FakeRateLimitError,
    )
    context = ProviderRuntimeContext(
        session_state=_DummySessionState(),
        agent_config={},
        stream=False,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=types.SimpleNamespace(messages=FakeMessages()),
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            stream=False,
            context=context,
        )

    assert exc_info.value.details == {
        "classification": "rate_limited",
        "status_code": 429,
        "retry_after": "17",
    }


def test_anthropic_runtime_identifies_overload():
    descriptor, _ = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)

    exc = types.SimpleNamespace(
        status_code=529,
        body={"error": {"type": "overloaded_error"}},
        message="Overloaded",
    )

    assert runtime._is_overloaded_error(exc)  # type: ignore[attr-defined]


def test_anthropic_runtime_retries_on_overload(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeOverloadError(Exception):
        def __init__(self):
            super().__init__("Overloaded")
            self.status_code = 529
            self.body = {"error": {"type": "overloaded_error"}}
            self.response = types.SimpleNamespace(headers={}, text='{"error":"overloaded"}')

    class FakeRawResponse:
        def __init__(self):
            self.http_response = types.SimpleNamespace(
                headers={"content-type": "application/json"},
                status_code=200,
                content=b"{}",
            )

        def parse(self):
            return types.SimpleNamespace(
                content=[{"type": "text", "text": "ok"}],
                stop_reason="end_turn",
                id="message-overload-retry",
                model=model,
            )

    class FakeMessages:
        def __init__(self):
            self.calls = 0
            self.with_raw_response = self

        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise FakeOverloadError()
            return FakeRawResponse()

    fake_client = types.SimpleNamespace(messages=FakeMessages())

    session_state = _DummySessionState()
    agent_config = {
        "provider_tools": {
            "anthropic": {
                "rate_limit": {
                    "enabled": True,
                    "max_retries": 1,
                    "retry_base_seconds": 0.0,
                    "retry_max_seconds": 0.0,
                    "retry_jitter_seconds": 0.0,
                    "fallback_cooldown_seconds": 0.0,
                    "min_wait_seconds": 0.0,
                }
            }
        }
    }

    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config=agent_config,
        stream=False,
    )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.anthropic_overloaded_error",
        FakeOverloadError,
        raising=False,
    )

    result = runtime.invoke(
        client=fake_client,
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
        tools=_anthropic_tool_schema(),
        stream=False,
        context=context,
    )

    assert result.messages[0].content == "ok"
    assert fake_client.messages.calls == 2
    overload_meta = session_state.get_provider_metadata("anthropic_last_overload")
    assert overload_meta["attempt"] == 1


def test_anthropic_runtime_never_retries_after_stream_output(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor(
        "anthropic/claude-3-opus"
    )
    runtime = provider_registry.create_runtime(descriptor)

    class FakeOverloadError(Exception):
        def __init__(self):
            super().__init__("Overloaded")
            self.status_code = 529
            self.body = {"error": {"type": "overloaded_error"}}
            self.response = types.SimpleNamespace(
                headers={}, text='{"error":"overloaded"}'
            )

    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1", input_id="input-1", turn_id="turn-1"
        ),
        provider=ProviderIdentity(
            provider_id="anthropic",
            runtime_id="anthropic_messages",
            route_id="anthropic/claude-3-opus",
            model=model,
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        ),
    )
    context = ProviderRuntimeContext(
        session_state=_DummySessionState(),
        agent_config={
            "provider_tools": {
                "anthropic": {"rate_limit": {"max_retries": 1}}
            }
        },
        stream=True,
        exchange_recorder=recorder,
    )
    calls = []

    def call_streaming(_client, _request, observed_context):
        calls.append(True)
        observed_context.record_provider_event(
            "text_start", {"item_id": "message-1"}
        )
        observed_context.record_provider_event(
            "text_delta", {"item_id": "message-1", "delta": "partial"}
        )
        raise FakeOverloadError()

    monkeypatch.setattr(runtime, "_call_streaming", call_streaming)

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=object(),
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            stream=True,
            context=context,
        )

    assert calls == [True]
    assert exc_info.value.output_emitted is True
    assert exc_info.value.details == {
        "classification": "overloaded",
        "status_code": 529,
    }
