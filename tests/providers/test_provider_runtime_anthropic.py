import types

import pytest

from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import (
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


def test_anthropic_runtime_stream_success(monkeypatch):
    descriptor, model = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)

    final_message = types.SimpleNamespace(
        content=[
            {"type": "text", "text": "Hello"},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "fetch_data",
                "input": {"foo": "bar"},
            },
            {"type": "thinking", "text": "analysis"},
        ],
        stop_reason="end_turn",
        model=model,
    )

    final_usage = {"input_tokens": 12, "output_tokens": 34}

    class FakeStream:
        def __iter__(self):
            return iter(())

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
        "agentic_coder_prototype.provider_runtime.Anthropic",
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
        "agentic_coder_prototype.provider_runtime.Anthropic",
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
        "agentic_coder_prototype.provider_runtime.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "agentic_coder_prototype.provider_runtime.AnthropicOverloadedError",
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
