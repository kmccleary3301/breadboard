from __future__ import annotations

import base64
import types
from pathlib import Path
import pytest

from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard_engine.provider.contracts import ProviderContractError
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import (
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
                id="chatcmpl-1",
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
        "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
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


def test_openai_chat_runtime_converts_null_content_to_empty_string() -> None:
    descriptor, _model = provider_router.get_runtime_descriptor("openai/gpt-4o-mini")
    runtime = provider_registry.create_runtime(descriptor)
    converted = runtime._convert_messages_to_chat(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "tool_a", "arguments": "{}"},
                    }
                ],
            }
        ]
    )
    assert isinstance(converted, list) and converted
    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"] == ""



def test_image_media_reaches_each_supported_provider_input(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifact = ArtifactStore(workspace / ".breadboard" / "artifacts").put(
        b"\x89PNG\r\n\x1a\nprovider-input",
        media_type="image/png",
    )
    uri = f"attachment://{artifact.digest}"
    metadata = {
        "attachment_capabilities": {uri: artifact.as_dict()},
    }
    session_state = types.SimpleNamespace(
        workspace=str(workspace),
        get_provider_metadata=lambda key, default=None: metadata.get(key, default),
        set_provider_metadata=lambda *_args, **_kwargs: None,
    )
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=False,
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {
                    "type": "media",
                    "kind": "image",
                    "uri": uri,
                    "mime": "image/png",
                },
            ],
        }
    ]

    chat_descriptor, _ = provider_router.get_runtime_descriptor(
        "openai/gpt-4o-mini"
    )
    chat = provider_registry.create_runtime(chat_descriptor)
    chat_content = chat._convert_messages_to_chat(
        messages, context=context
    )[0]["content"]
    chat_url = chat_content[1]["image_url"]["url"]
    assert chat_content[0] == {"type": "text", "text": "describe"}
    assert base64.b64decode(chat_url.partition(",")[2]) == b"\x89PNG\r\n\x1a\nprovider-input"

    responses = OpenAIResponsesRuntime(
        types.SimpleNamespace(
            provider_id="openai", runtime_id="openai_responses"
        )
    )
    responses_content = responses._convert_messages_to_input(
        messages, context=context
    )[0]["content"]
    assert responses_content[1]["type"] == "input_image"
    assert (
        base64.b64decode(
            responses_content[1]["image_url"].partition(",")[2]
        )
        == b"\x89PNG\r\n\x1a\nprovider-input"
    )

    anthropic_descriptor, _ = provider_router.get_runtime_descriptor(
        "anthropic/claude-sonnet-4-6"
    )
    anthropic = provider_registry.create_runtime(anthropic_descriptor)
    _, anthropic_messages = anthropic._convert_messages(
        messages, context=context
    )
    anthropic_source = anthropic_messages[0]["content"][1]["source"]
    assert anthropic_source["media_type"] == "image/png"
    assert (
        base64.b64decode(anthropic_source["data"])
        == b"\x89PNG\r\n\x1a\nprovider-input"
    )

    codex_descriptor, _ = provider_router.get_runtime_descriptor(
        "codex/gpt-5.4-mini"
    )
    codex = provider_registry.create_runtime(codex_descriptor)
    codex_input = codex._extract_latest_user_input(
        messages, context=context
    )
    assert codex_input[0] == {"type": "text", "text": "describe"}
    assert codex_input[1]["type"] == "image"
    assert (
        base64.b64decode(codex_input[1]["url"].partition(",")[2])
        == b"\x89PNG\r\n\x1a\nprovider-input"
    )


def test_image_media_requires_turn_scoped_attachment_capability() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor(
        "openai/gpt-4o-mini"
    )
    runtime = provider_registry.create_runtime(descriptor)
    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(
            workspace=".",
            get_provider_metadata=lambda _key, default=None: default,
        ),
        agent_config={},
        stream=False,
    )

    with pytest.raises(
        ProviderContractError, match="not authorized for this turn"
    ):
        runtime._convert_messages_to_chat(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "media",
                            "kind": "image",
                            "uri": "attachment://sha256:" + "a" * 64,
                            "mime": "image/png",
                        }
                    ],
                }
            ],
            context=context,
        )

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
                id="message-1",
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                status="completed",
                model="gpt-4.1-mini",
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
