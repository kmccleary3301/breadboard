import types

import pytest

from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import (
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
        "agentic_coder_prototype.provider_runtime.OpenAI",
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
