import types

import pytest

from breadboard_engine.provider_routing import provider_router
from breadboard_engine.provider_runtime import (
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)


class _FakeCompletions:
    def create(self, **kwargs):
        return types.SimpleNamespace(choices=[], model=kwargs.get("model"))

    def stream(self, **kwargs):  # pragma: no cover - not used in test
        raise RuntimeError("streaming not required for test")


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


def test_openrouter_runtime_uses_openai_client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {
            "HTTP-Referer": "https://example.com",
            "X-Title": "BreadBoard",
            "Accept": "application/json; charset=utf-8",
            "Accept-Encoding": "identity",
        },
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = _FakeChat()
            self.responses = types.SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("not used")),
                stream=lambda **_: (_ for _ in ()).throw(RuntimeError("not used")),
            )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-4o-mini")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["default_headers"]["HTTP-Referer"] == "https://example.com"
    assert captured["default_headers"]["Accept"] == "application/json; charset=utf-8"
    assert captured["default_headers"]["Accept-Encoding"] == "identity"
    # Ensure the fake client exposes chat completions entrypoint
    assert hasattr(client.chat, "completions")
    assert client.chat.completions.create(model=model, messages=[]) is not None


def test_openrouter_gpt5_routes_through_responses_runtime_descriptor() -> None:
    descriptor, _model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-5-nano")
    assert descriptor.provider_id == "openrouter"
    assert descriptor.runtime_id == "openai_responses"


def test_openrouter_gpt5_responses_injects_provider_routing_preferences(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {},
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-5-nano")
    runtime = provider_registry.create_runtime(descriptor)

    captured_kwargs = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            output_item = types.SimpleNamespace(
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                model=model,
                output=[output_item],
                usage={},
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()
            self.chat = types.SimpleNamespace(completions=None)

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-5-nano")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(
            get_provider_metadata=lambda *_args, **_kwargs: None,
            set_provider_metadata=lambda *_args, **_kwargs: None,
        ),
        agent_config={},
        stream=False,
    )

    runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    provider_cfg = (captured_kwargs.get("extra_body") or {}).get("provider") or {}
    assert provider_cfg.get("order") == ["openai"]
    assert provider_cfg.get("allow_fallbacks") is False


def test_openrouter_runtime_injects_accept_headers_on_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {},
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRawResponse:
        def __init__(self):
            self.headers = {"Content-Type": "application/json"}
            self.status_code = 200
            self.content = b'{"choices":[]}'

        def parse(self):
            choice = types.SimpleNamespace(
                message={"role": "assistant", "content": "ok"},
                finish_reason="stop",
                index=0,
                error=None,
                tool_calls=None,
            )
            return types.SimpleNamespace(choices=[choice], usage={}, model=model)

    class FakeWithRawResponse:
        def __init__(self):
            self.seen_kwargs = None

        def create(self, **kwargs):
            self.seen_kwargs = kwargs
            return FakeRawResponse()

    raw_wrapper = FakeWithRawResponse()

    class FakeCompletions:
        def __init__(self):
            self.with_raw_response = raw_wrapper

        def create(self, **kwargs):
            raise AssertionError("raw response path should be used")

        def stream(self, **kwargs):  # pragma: no cover - not exercised here
            raise AssertionError("stream not expected")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())
            self.responses = types.SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
                stream=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
            )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-4o-mini")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
    )

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    extra_headers = raw_wrapper.seen_kwargs["extra_headers"]
    assert extra_headers["Accept"] == "application/json; charset=utf-8"
    assert extra_headers["Accept-Encoding"] == "identity"
    assert result.messages[0].content == "ok"


def test_openrouter_runtime_parses_event_stream_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {},
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRawResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/event-stream"}
            self.status_code = 200
            self.content = (
                b"data: {\"id\":\"cmpl-1\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"}}]}\n\n"
                b"data: {\"id\":\"cmpl-1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hello\"}}]}\n\n"
                b"data: {\"id\":\"cmpl-1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\" world\"}}]}\n\n"
                b"data: {\"id\":\"cmpl-1\",\"choices\":[{\"index\":0,\"finish_reason\":\"stop\"}]}\n\n"
                b"data: [DONE]\n\n"
            )

        def parse(self):
            raise AssertionError("parse should not be invoked when SSE is parsed manually")

    class FakeWithRawResponse:
        def __init__(self):
            self.seen_kwargs = None

        def create(self, **kwargs):
            self.seen_kwargs = kwargs
            return FakeRawResponse()

    raw_wrapper = FakeWithRawResponse()

    class FakeCompletions:
        def __init__(self):
            self.with_raw_response = raw_wrapper

        def create(self, **kwargs):
            raise AssertionError("raw response path should be used")

        def stream(self, **kwargs):  # pragma: no cover - not exercised here
            raise AssertionError("stream not expected")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())
            self.responses = types.SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
                stream=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
            )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-4o-mini")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
    )

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    extra_headers = raw_wrapper.seen_kwargs["extra_headers"]
    assert extra_headers["Accept"] == "application/json; charset=utf-8"
    assert extra_headers["Accept-Encoding"] == "identity"
    assert result.messages[0].content == "Hello world"
    assert result.messages[0].finish_reason == "stop"


def test_openrouter_runtime_event_stream_parse_failure_records_base64(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {},
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRawResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/event-stream", "OpenRouter-Request-Id": "req-123"}
            self.status_code = 200
            self.content = b"data: not-a-json-payload\n\n"

        def parse(self):
            raise AssertionError("parse should not be invoked when SSE fails")

    class FakeWithRawResponse:
        def __init__(self):
            self.seen_kwargs = None

        def create(self, **kwargs):
            self.seen_kwargs = kwargs
            return FakeRawResponse()

    raw_wrapper = FakeWithRawResponse()

    class FakeCompletions:
        def __init__(self):
            self.with_raw_response = raw_wrapper

        def create(self, **kwargs):
            raise AssertionError("raw response path should be used")

        def stream(self, **kwargs):  # pragma: no cover - not exercised here
            raise AssertionError("stream not expected")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())
            self.responses = types.SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
                stream=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
            )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-4o-mini")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=client,
            model=model,
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=context,
        )

    details = exc_info.value.details
    assert details["classification"] == "event_stream_parse_failed"
    assert details["content_type"] == "text/event-stream"
    assert details["response_headers"]["content-type"] == "text/event-stream"
    assert details["request_id"] == "req-123"
    assert "raw_body_b64" in details and isinstance(details["raw_body_b64"], str)


def test_openrouter_runtime_html_error_includes_base64(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        provider_router.providers["openrouter"],
        "default_headers",
        {},
        raising=False,
    )

    descriptor, model = provider_router.get_runtime_descriptor("openrouter/openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRawResponse:
        def __init__(self):
            self.headers = {"Content-Type": "text/html"}
            self.status_code = 502
            self.content = b"<html><body>blocked by edge</body></html>"

        def parse(self):
            raise AssertionError("parse should not be invoked for html error")

    class FakeWithRawResponse:
        def __init__(self):
            self.seen_kwargs = None

        def create(self, **kwargs):
            self.seen_kwargs = kwargs
            return FakeRawResponse()

    raw_wrapper = FakeWithRawResponse()

    class FakeCompletions:
        def __init__(self):
            self.with_raw_response = raw_wrapper

        def create(self, **kwargs):
            raise AssertionError("raw response path should be used")

        def stream(self, **kwargs):  # pragma: no cover - not exercised here
            raise AssertionError("stream not expected")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())
            self.responses = types.SimpleNamespace(
                create=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
                stream=lambda **_: (_ for _ in ()).throw(RuntimeError("unused")),
            )

    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
        FakeOpenAI,
    )

    client_config = provider_router.create_client_config("openrouter/openai/gpt-4o-mini")
    client = runtime.create_client(
        client_config["api_key"],
        base_url=client_config.get("base_url"),
        default_headers=client_config.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=client,
            model=model,
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=context,
        )

    details = exc_info.value.details
    assert details["html_detected"] is True
    assert details["content_type"] == "text/html"
    assert "raw_body_b64" in details and isinstance(details["raw_body_b64"], str)


class _CapturingSessionState:
    def __init__(self) -> None:
        self._active_turn_index = 7
        self.events = []

    def _emit_event(self, event_type, payload, *, turn=None):
        self.events.append((event_type, payload, turn))


class _FakeChatStream:
    def __init__(self, events, final_response):
        self._events = events
        self._final_response = final_response
        self.finalized = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def __iter__(self):
        return iter(self._events)

    def get_final_completion(self):
        self.finalized = True
        return self._final_response


def _chat_chunk(*, content=None, reasoning_content=None, tool_calls=None):
    delta = types.SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        reasoning=None,
        reasoning_details=None,
        tool_calls=tool_calls,
    )
    choice = types.SimpleNamespace(index=0, delta=delta)
    chunk = types.SimpleNamespace(id="chatcmpl-1", choices=[choice])
    return types.SimpleNamespace(type="chunk", chunk=chunk)


def _chat_response(*, content="ok", tool_calls=None, reasoning_content=None):
    message = types.SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls or [],
        reasoning_content=reasoning_content,
        reasoning=None,
        reasoning_details=None,
    )
    choice = types.SimpleNamespace(
        index=0, message=message, finish_reason="tool_calls" if tool_calls else "stop"
    )
    return types.SimpleNamespace(
        choices=[choice], usage={}, model="deepseek/deepseek-v4-flash-0731"
    )


def test_openrouter_chat_stream_omits_absent_tools_and_uses_chat_finalizer():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)
    captured = {}
    response = _chat_response(content="hello")
    stream = _FakeChatStream([_chat_chunk(content="hello")], response)

    def open_stream(**kwargs):
        captured.update(kwargs)
        return stream

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(stream=open_stream)
        )
    )
    session_state = _CapturingSessionState()
    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=True,
        context=ProviderRuntimeContext(
            session_state=session_state, agent_config={}, stream=True
        ),
    )

    assert "tools" not in captured
    assert stream.finalized is True
    assert result.messages[0].content == "hello"
    assert [event[0] for event in session_state.events] == [
        "assistant.message.start",
        "assistant.message.delta",
        "assistant.message.end",
    ]


def test_openrouter_chat_stream_projects_reasoning_and_tool_deltas_into_result():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)
    first_tool_delta = types.SimpleNamespace(
        index=0,
        id="call-1",
        function=types.SimpleNamespace(name="read", arguments='{"path":'),
    )
    second_tool_delta = types.SimpleNamespace(
        index=0,
        id=None,
        function=types.SimpleNamespace(name=None, arguments='"README.md"}'),
    )
    final_tool = types.SimpleNamespace(
        id="call-1",
        type="function",
        function=types.SimpleNamespace(name="read", arguments='{"path":"README.md"}'),
    )
    response = _chat_response(content=None, tool_calls=[final_tool])
    stream = _FakeChatStream(
        [
            _chat_chunk(reasoning_content="inspect "),
            _chat_chunk(tool_calls=[first_tool_delta]),
            _chat_chunk(tool_calls=[second_tool_delta]),
        ],
        response,
    )
    captured = {}
    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                stream=lambda **kwargs: captured.update(kwargs) or stream
            )
        )
    )
    session_state = _CapturingSessionState()

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "inspect"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "read", "parameters": {"type": "object"}},
            }
        ],
        stream=True,
        context=ProviderRuntimeContext(
            session_state=session_state, agent_config={}, stream=True
        ),
    )

    assert captured["tools"][0]["function"]["name"] == "read"
    assert result.messages[0].reasoning == "inspect "
    assert result.messages[0].annotations["reasoning_content"] == "inspect "
    assert result.messages[0].tool_calls[0].id == "call-1"
    assert result.messages[0].tool_calls[0].arguments == '{"path":"README.md"}'
    event_types = [event[0] for event in session_state.events]
    assert event_types == [
        "assistant.message.start",
        "assistant.reasoning.delta",
        "assistant.tool_call.start",
        "assistant.tool_call.delta",
        "assistant.tool_call.delta",
        "assistant.tool_call.end",
        "assistant.message.end",
    ]
    assert session_state.events[1][1]["text"] == "inspect "
    assert session_state.events[3][1]["arguments_delta"] == '{"path":'
    assert session_state.events[5][1]["arguments"] == '{"path":"README.md"}'
    assert "text" not in session_state.events[6][1]


def test_openrouter_chat_replays_tool_and_reasoning_fields():
    descriptor, _ = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "read", "arguments": "{}"},
        }
    ]

    converted = runtime._convert_messages_to_chat(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
                "reasoning_content": "private chain",
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]
    )

    assert converted[0]["tool_calls"] == tool_calls
    assert converted[0]["reasoning_content"] == "private chain"
    assert converted[1]["tool_call_id"] == "call-1"


def test_openrouter_deepseek_uses_model_specific_chat_capabilities():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    capabilities = provider_router.get_capabilities(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )

    assert model == "deepseek/deepseek-v4-flash-0731"
    assert descriptor.runtime_id == "openrouter_chat"
    assert descriptor.default_api_variant == "chat"
    assert descriptor.supports_native_tools is True
    assert capabilities.tool_calls == "parallel"
    assert capabilities.streaming == "event_deltas"
    assert capabilities.reasoning == "openrouter"


def test_openrouter_chat_stream_classifies_sdk_shape_errors_as_adapter_faults():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)

    class BrokenStream:
        def __enter__(self):
            raise TypeError("local sdk shape mismatch")

        def __exit__(self, *_args):
            return None

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(stream=lambda **_kwargs: BrokenStream())
        )
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=client,
            model=model,
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=True,
            context=ProviderRuntimeContext(
                session_state=_CapturingSessionState(),
                agent_config={},
                stream=True,
            ),
        )

    assert exc_info.value.kind == "adapter"
    assert exc_info.value.replay_safe is True
