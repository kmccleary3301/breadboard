import base64
import json
import traceback
import types

import pytest

from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import (
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)
from breadboard_engine.security import redaction
from breadboard_engine.provider.contracts import (
    ProviderCorrelation,
    ProviderDone,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderRequest,
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

    with provider_router.execution_client_config(
        "openrouter/openai/gpt-4o-mini"
    ) as client_config:
        client = runtime.create_client(
            client_config["api_key"],
            base_url=client_config.get("base_url"),
            default_headers=client_config.get("default_headers"),
        )

        assert captured["api_key"] == "test-key"
        assert captured["base_url"] == "https://openrouter.ai/api/v1"
        assert captured["default_headers"]["HTTP-Referer"] == "https://example.com"
        assert (
            captured["default_headers"]["Accept"] == "application/json; charset=utf-8"
        )
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
                id="message-1",
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                status="completed",
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
            return types.SimpleNamespace(
                id="chatcmpl-1", choices=[choice], usage={}, model=model
            )

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
                b'data: {"id":"cmpl-1","choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
                b'data: {"id":"cmpl-1","choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n'
                b'data: {"id":"cmpl-1","choices":[{"index":0,"delta":{"content":" world"}}]}\n\n'
                b'data: {"id":"cmpl-1","choices":[{"index":0,"finish_reason":"stop"}]}\n\n'
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

    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1",
            input_id="input-1",
            turn_id="turn-1",
        ),
        provider=ProviderIdentity(
            provider_id=descriptor.provider_id,
            runtime_id=descriptor.runtime_id,
            route_id="openrouter/openai/gpt-4o-mini",
            model=model,
        ),
        request=ProviderRequest(
            stream=False,
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        ),
    )
    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
        exchange_recorder=recorder,
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
    assert [event.kind for event in recorder.events] == [
        "response_start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
    ]
    recorder.build(
        ProviderDone(
            output_emitted=True,
            finish_reason="stop",
            assistant_messages=[message.as_dict() for message in result.messages],
        )
    )


def test_openrouter_event_stream_requires_done_sentinel():
    descriptor, _ = provider_router.get_runtime_descriptor(
        "openrouter/openai/gpt-4o-mini"
    )
    runtime = provider_registry.create_runtime(descriptor)
    assert (
        runtime._aggregate_sse_events(
            [
                json.dumps(
                    {
                        "id": "cmpl-1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "partial",
                                },
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "id": "cmpl-1",
                        "choices": [
                            {"index": 0, "finish_reason": "stop"}
                        ],
                    }
                ),
            ]
        )
        is None
    )


def test_openrouter_runtime_event_stream_parse_failure_omits_base64(monkeypatch):
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
            self.headers = {"Content-Type": "text/event-stream", "OpenRouter-Request-Id": "req-123",
            }
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
    assert "raw_body_b64" not in details


def test_openrouter_runtime_html_error_omits_base64(monkeypatch):
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
    assert "raw_body_b64" not in details

def test_openrouter_final_parse_failure_scrubs_response_body(monkeypatch):
    canary = "e3-final-response-canary"
    monkeypatch.setattr(
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.sleep",
        lambda _: None,
    )

    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/openai/gpt-4o-mini"
    )
    runtime = provider_registry.create_runtime(descriptor)

    class FakeRawResponse:
        def __init__(self, content, parse_error):
            self.headers = {"Content-Type": "application/json"}
            self.status_code = 502
            self.content = content
            self._parse_error = parse_error

        def parse(self):
            raise self._parse_error

    first = FakeRawResponse(
        b"<html><body>retry</body></html>",
        json.JSONDecodeError("not json", "<html>", 0),
    )
    second = FakeRawResponse(
        canary.encode(),
        RuntimeError(f"parse failed with {canary}"),
    )

    class FakeWithRawResponse:
        def __init__(self):
            self._responses = iter((first, second))

        def create(self, **_kwargs):
            return next(self._responses)

    monkeypatch.setattr(
        runtime,
        "_decode_body_text",
        lambda raw: None if raw is first else canary,
    )
    collection = types.SimpleNamespace(with_raw_response=FakeWithRawResponse())
    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(),
        agent_config={},
        stream=False,
    )

    with redaction.secret_value_scope(canary):
        with pytest.raises(ProviderRuntimeError) as exc_info:
            runtime._call_with_raw_response(
                collection,
                error_context="test",
                context=context,
                model=model,
                messages=[],
            )

    rendered = "".join(
        traceback.format_exception(
            exc_info.type,
            exc_info.value,
            exc_info.tb,
        )
    )
    serialized = json.dumps(exc_info.value.details)
    assert canary not in rendered
    assert canary not in serialized
    assert base64.b64encode(canary.encode()).decode() not in serialized
    assert "raw_body_b64" not in exc_info.value.details



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
        id="chatcmpl-1",
        choices=[choice],
        usage={},
        model="deepseek/deepseek-v4-flash-0731",
    )


def test_openrouter_chat_stream_omits_absent_tools_and_uses_chat_finalizer():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)
    captured = {}
    response = _chat_response(content="hello")
    stream = _FakeChatStream(
        [
            _chat_chunk(content="hello"),
            types.SimpleNamespace(type="content.delta"),
            types.SimpleNamespace(type="content.done"),
        ],
        response,
    )

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



def test_openrouter_chat_stream_rejects_malformed_text_delta():
    descriptor, model = provider_router.get_runtime_descriptor(
        "openrouter/deepseek/deepseek-v4-flash-0731"
    )
    runtime = provider_registry.create_runtime(descriptor)
    stream = _FakeChatStream(
        [_chat_chunk(content={"text": "must-not-disappear"})],
        _chat_response(content=None),
    )
    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(stream=lambda **_kwargs: stream)
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

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.safe_code == "invalid_chat_text_delta"

    unknown_event = _chat_chunk()
    unknown_event.chunk.choices[0].delta.unknown_semantic = "must-not-disappear"
    unknown_stream = _FakeChatStream(
        [unknown_event], _chat_response(content=None)
    )
    client.chat.completions.stream = lambda **_kwargs: unknown_stream
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
    assert exc_info.value.safe_code == "unknown_chat_delta"

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
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1",
            input_id="input-1",
            turn_id="turn-1",
        ),
        provider=ProviderIdentity(
            provider_id=descriptor.provider_id,
            runtime_id=descriptor.runtime_id,
            route_id="openrouter/deepseek/deepseek-v4-flash-0731",
            model=model,
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "inspect"}],
            tools=[
                {
                    "name": "read",
                    "parameters": {"type": "object"},
                }
            ],
        ),
    )

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
            session_state=session_state,
            agent_config={},
            stream=True,
            exchange_recorder=recorder,
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
        "assistant.reasoning.start",
        "assistant.reasoning.delta",
        "assistant.tool_call.start",
        "assistant.tool_call.delta",
        "assistant.tool_call.delta",
        "assistant.tool_call.end",
        "assistant.reasoning.end",
        "assistant.message.end",
    ]
    assert session_state.events[2][1]["text"] == "inspect "
    assert session_state.events[4][1]["arguments_delta"] == '{"path":'
    assert [event.kind for event in recorder.events] == [
        "response_start",
        "thinking_start",
        "thinking_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_end",
        "thinking_end",
    ]
    recorder.build(
        ProviderDone(
            output_emitted=True,
            finish_reason="toolUse",
            assistant_messages=[message.as_dict() for message in result.messages],
        )
    )
    assert session_state.events[6][1]["arguments"] == '{"path":"README.md"}'
    assert "text" not in session_state.events[8][1]


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
            raise TypeError("e3-crash-trace-canary")

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
    rendered = "".join(
        traceback.format_exception(
            exc_info.type,
            exc_info.value,
            exc_info.tb,
        )
    )
    assert "e3-crash-trace-canary" not in rendered
    assert "provider operation failed (TypeError)" in rendered

def test_openai_usage_extraction_preserves_nested_cached_tokens() -> None:
    descriptor, _model = provider_router.get_runtime_descriptor(
        "openrouter/openai/gpt-4o-mini"
    )
    runtime = provider_registry.create_runtime(descriptor)
    response = types.SimpleNamespace(
        usage=types.SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=7,
            prompt_tokens_details=types.SimpleNamespace(cached_tokens=5),
        )
    )

    assert runtime._extract_usage(response) == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 5},
    }
