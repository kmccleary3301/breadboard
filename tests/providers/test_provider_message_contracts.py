from __future__ import annotations

import types

from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import (
    OpenAIResponsesRuntime,
    ProviderRuntimeContext,
    provider_registry,
)


def _dummy_context() -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        session_state=types.SimpleNamespace(
            get_provider_metadata=lambda *_a, **_k: None,
            set_provider_metadata=lambda *_a, **_k: None,
        ),
        agent_config={},
        stream=False,
    )


def test_openai_chat_runtime_produces_string_content_and_tool_call_arguments(monkeypatch):
    """
    ProviderMessage invariants for OpenAIChatRuntime:
    - content is either None or a string
    - each tool_call has arguments as a JSON string
    """
    descriptor, model = provider_router.get_runtime_descriptor("openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)

    # Fake chat completions response
    def _fake_choice(role: str, content, tool_calls=None, finish="stop", idx=0):
        return types.SimpleNamespace(
            message={"role": role, "content": content, "tool_calls": tool_calls or []},
            finish_reason=finish,
            index=idx,
            error=None,
        )

    # Tool call with non-string arguments to exercise normalization
    raw_tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "my_tool",
            "arguments": {"foo": "bar"},
        },
    }

    class FakeRawResponse:
        def __init__(self):
            self.headers = {"Content-Type": "application/json"}
            self.status_code = 200
            self.content = b"{}"

        def parse(self):
            return types.SimpleNamespace(
                choices=[
                    _fake_choice(
                        "assistant",
                        [{"type": "output_text", "text": "hello"}],
                        tool_calls=[raw_tool_call],
                        idx=0,
                    )
                ],
                usage={},
                model=model,
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

        def stream(self, **kwargs):
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

    client = runtime.create_client(api_key="test-key")
    context = _dummy_context()

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "my_tool", "description": "", "parameters": {}},
            }
        ],
        stream=False,
        context=context,
    )

    assert result.messages, "Expected at least one ProviderMessage"
    msg = result.messages[0]
    # content should be a string
    assert isinstance(msg.content, str)
    # tool_calls should have JSON-string arguments
    assert msg.tool_calls, "Expected at least one tool call"
    for call in msg.tool_calls:
        assert isinstance(call.arguments, str)
        assert call.arguments.startswith("{") and call.arguments.endswith("}")


def test_responses_runtime_produces_string_content(monkeypatch):
    """
    ProviderMessage invariants for OpenAIResponsesRuntime:
    - content is either None or a string
    """
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    class FakeResponses:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            output_item = types.SimpleNamespace(
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                model="gpt-4.1-mini",
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
    client = runtime.create_client(api_key="test-key")
    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(
            get_provider_metadata=lambda *_a, **_k: None,
            set_provider_metadata=lambda *_a, **_k: None,
        ),
        agent_config={"provider_tools": {"openai": {}}},
        stream=False,
    )

    result = runtime.invoke(
        client=client,
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    assert result.messages, "Expected at least one ProviderMessage"
    msg = result.messages[0]
    assert isinstance(msg.content, str)

