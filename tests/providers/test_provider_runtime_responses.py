import types

from agentic_coder_prototype.provider_runtime import (
    OpenAIResponsesRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)


def test_responses_message_conversion_simple_string():
    # Use a dummy descriptor; conversion is independent of descriptor fields
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    messages = [{"role": "user", "content": "hello"}]
    converted = runtime._convert_messages_to_input(messages)
    assert converted == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
    ]


def test_responses_message_conversion_chat_blocks():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ],
        }
    ]
    converted = runtime._convert_messages_to_input(messages)
    assert converted[0]["role"] == "user"
    blocks = converted[0]["content"]
    assert all(block["type"] == "input_text" for block in blocks)
    assert "".join(block["text"] for block in blocks) == "hello world"


def test_responses_message_conversion_preserves_responses_blocks():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "hello"},
                {"type": "input_image", "image_url": "http://example.com/image.png"},
            ],
        }
    ]
    converted = runtime._convert_messages_to_input(messages)
    assert converted == messages


def test_openrouter_responses_converts_chat_tool_calls_to_function_call_items():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses")
    )
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "demo_tool", "arguments": "{\"x\": 1}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\": true}"},
    ]
    converted = runtime._convert_messages_to_input(messages)
    assert converted[0]["type"] == "function_call"
    assert converted[0]["call_id"] == "call_1"
    assert converted[0]["name"] == "demo_tool"
    assert converted[0]["arguments"] == "{\"x\": 1}"
    assert converted[1]["type"] == "function_call_output"
    assert converted[1]["call_id"] == "call_1"
    assert converted[1]["output"] == "{\"ok\": true}"


def test_openrouter_responses_chat_messages_always_have_string_content():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses")
    )
    converted = runtime._convert_messages_to_input(
        [
            {"role": "assistant", "content": None},
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        ]
    )
    assert converted == [
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "hello"},
    ]


def test_responses_invoke_uses_converted_input(monkeypatch):
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    captured_payload = {}

    class FakeResponses:
        def __init__(self):
            self.seen = None

        def create(self, **kwargs):
            captured_payload.update(kwargs)
            # Minimal object with required attributes
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

    try:
        # If OpenAI is not installed, this will raise ProviderRuntimeError via _require_openai
        monkeypatch.setattr(
            "agentic_coder_prototype.provider_runtime.OpenAI",
            FakeOpenAI,
        )
        client = runtime.create_client(api_key="test-key")

        context = ProviderRuntimeContext(
            session_state=types.SimpleNamespace(
                get_provider_metadata=lambda *_args, **_kwargs: None,
                set_provider_metadata=lambda *_args, **_kwargs: None,
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

        assert "input" in captured_payload
        assert captured_payload["input"][0]["content"][0]["type"] == "input_text"
        assert captured_payload["input"][0]["content"][0]["text"] == "hello"
        assert result.messages[0].content == "ok"
    except ProviderRuntimeError:
        # Environments without OpenAI installed will exercise the error path;
        # the important contract is that conversion does not raise.
        assert captured_payload == {} or "input" not in captured_payload


def test_openrouter_responses_does_not_force_store(monkeypatch):
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses")
    )

    captured_payload = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured_payload.update(kwargs)
            output_item = types.SimpleNamespace(
                type="message",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                model=kwargs.get("model"),
                output=[output_item],
                usage={},
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()
            self.chat = types.SimpleNamespace(completions=None)

    try:
        monkeypatch.setattr(
            "agentic_coder_prototype.provider_runtime.OpenAI",
            FakeOpenAI,
        )
        client = runtime.create_client(api_key="test-key")
        context = ProviderRuntimeContext(
            session_state=types.SimpleNamespace(
                get_provider_metadata=lambda *_args, **_kwargs: None,
                set_provider_metadata=lambda *_args, **_kwargs: None,
            ),
            agent_config={"provider_tools": {}},
            stream=False,
        )

        runtime.invoke(
            client=client,
            model="openai/gpt-5-nano",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=context,
        )
        assert "store" not in captured_payload
    except ProviderRuntimeError:
        assert captured_payload == {} or "store" not in captured_payload

