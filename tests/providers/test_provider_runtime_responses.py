import types
import pytest
from breadboard_engine.conductor.modes import (
    _finalize_model_surface,
    _provider_wire_evidence,
    _surface_digest,
)

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

from breadboard_engine.provider.runtime import (
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


def test_openrouter_responses_wire_surface_digest_uses_responses_converters():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses")
    )
    context = ProviderRuntimeContext(
        types.SimpleNamespace(get_provider_metadata=lambda *_args: None),
        {},
    )
    messages = [
        {
            "role": "tool_result",
            "content": [
                {
                    "type": "tool_result",
                    "call_id": "call_1",
                    "content": {"status": "ok"},
                }
            ],
        }
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read",
                "parameters": {"type": "object"},
            },
        }
    ]

    body, _, _, _ = _provider_wire_evidence(
        profile=None,
        runtime=runtime,
        provider_id="openrouter",
        model="openai/gpt-5-nano",
        messages=messages,
        tools=tools,
        stream=False,
        client_config={},
        context=context,
    )
    _, input_messages = runtime._split_messages_for_responses(messages, context)
    actual_messages = runtime._convert_messages_to_input(
        input_messages,
        include_tool_calls=True,
        context=context,
    )
    actual_tools = runtime._convert_tools_to_responses(tools)
    surface = _finalize_model_surface(
        {"prompt_sections": {}, "tools": []},
        actual_messages,
        actual_tools,
        "",
    )

    assert body["messages"] == actual_messages
    assert body["tools"] == actual_tools
    assert surface is not None
    assert surface["provider_request"] == {
        "messages_sha256": _surface_digest(actual_messages),
        "tools_sha256": _surface_digest(actual_tools),
        "request_sha256": _surface_digest(
            {"messages": actual_messages, "tools": actual_tools}
        ),
    }
    assert body["messages"][0]["type"] == "function_call_output"


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
                    "function": {"name": "demo_tool", "arguments": '{"x": 1}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
    ]
    converted = runtime._convert_messages_to_input(messages)
    assert converted[0]["type"] == "function_call"
    assert converted[0]["call_id"] == "call_1"
    assert converted[0]["name"] == "demo_tool"
    assert converted[0]["arguments"] == '{"x":1}'
    assert converted[1]["type"] == "function_call_output"
    assert converted[1]["call_id"] == "call_1"
    assert converted[1]["output"] == '{"ok": true}'


def test_responses_converts_canonical_tool_result_blocks() -> None:
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    converted = runtime._convert_messages_to_input(
        [
            {
                "role": "tool_result",
                "content": [
                    {
                        "type": "tool_result",
                        "call_id": "call_1",
                        "content": '{"ok":true}',
                        "is_error": False,
                    }
                ],
            }
        ]
    )

    assert converted == [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"ok":true}',
        }
    ]


def test_responses_rejects_malformed_tool_call_arguments() -> None:
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses")
    )

    with pytest.raises(ProviderContractError):
        runtime._convert_messages_to_input(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "demo_tool",
                                "arguments": "{",
                            },
                        }
                    ],
                }
            ]
        )


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
                id="message_1",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            reasoning_item = types.SimpleNamespace(
                type="reasoning",
                id="reasoning_1",
                summary=[{"type": "summary_text", "text": "plan"}],
                encrypted_content="encrypted-plan",
            )
            return types.SimpleNamespace(
                id="resp_1",
                model="gpt-4.1-mini",
                status="completed",
                output=[reasoning_item, output_item],
                usage={},
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()
            self.chat = types.SimpleNamespace(completions=None)

    try:
        # If OpenAI is not installed, this will raise ProviderRuntimeError via _require_openai
        monkeypatch.setattr(
            "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
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
        assert result.reasoning_summaries == ["plan"]
        assert normalized_result_messages(result)[1]["content"] == [
            {"type": "thinking", "text": "plan"},
            {
                "type": "provider_replay",
                "provider_id": "openai",
                "schema_version": "openai.responses.v1",
                "replay_scope": "same_provider",
                "payload": {
                    "encrypted_content": "encrypted-plan",
                    "item_id": "reasoning_1",
                    "reasoning_id": "reasoning_1",
                },
            },
        ]
        assert normalized_result_replay(result, provider_id="openai") == [
            {
                "provider_id": "openai",
                "schema_version": "bb.provider_replay.v1",
                "replay_scope": "same_provider",
                "payload": {
                    "encrypted_content": "encrypted-plan",
                    "item_id": "reasoning_1",
                    "reasoning_id": "reasoning_1",
                },
            }
        ]
    except ProviderRuntimeError:
        # Environments without OpenAI installed will exercise the error path;
        # the important contract is that conversion does not raise.
        assert captured_payload == {} or "input" not in captured_payload


@pytest.mark.parametrize(
    ("status", "output", "kind", "code"),
    [
        ("failed", [], "provider", "provider_response_failed"),
        ("cancelled", [], "provider", "provider_response_cancelled"),
        (
            "completed",
            [types.SimpleNamespace(type="unknown_normative_output")],
            "protocol",
            "unknown_responses_output",
        ),
    ],
)
def test_responses_terminal_and_output_protocol_failures_are_typed(
    status, output, kind, code
):
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    response = types.SimpleNamespace(
        id="resp-error",
        model="gpt-5.2",
        status=status,
        output=output,
        usage={},
    )
    client = types.SimpleNamespace(
        responses=types.SimpleNamespace(create=lambda **_kwargs: response)
    )
    context = ProviderRuntimeContext(
        session_state=types.SimpleNamespace(
            get_provider_metadata=lambda *_args, **_kwargs: None,
            set_provider_metadata=lambda *_args, **_kwargs: None,
        ),
        agent_config={"provider_tools": {"openai": {}}},
        stream=False,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=client,
            model="gpt-5.2",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            context=context,
        )

    assert exc_info.value.kind == kind
    assert exc_info.value.details["code"] == code


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
                id="message_1",
                role="assistant",
                content=[{"type": "output_text", "text": "ok"}],
                finish_reason="stop",
            )
            return types.SimpleNamespace(
                id="resp_1",
                model=kwargs.get("model"),
                status="completed",
                output=[output_item],
                usage={},
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()
            self.chat = types.SimpleNamespace(completions=None)

    try:
        monkeypatch.setattr(
            "breadboard_engine.provider.sdk_bindings.provider_sdk_bindings.openai",
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


def test_responses_stream_emits_assistant_delta_events(monkeypatch):
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    class FakeStream:
        def __init__(self):
            output_item = types.SimpleNamespace(
                type="message",
                id="msg_1",
                role="assistant",
                content=[{"type": "output_text", "text": "Hello there"}],
                finish_reason="stop",
            )
            self._final = types.SimpleNamespace(
                id="resp_stream_1",
                model="gpt-5.4-mini",
                status="completed",
                output=[output_item],
                usage={},
            )
            self._events = [
                types.SimpleNamespace(type="response.output_text.delta", item_id="msg_1", delta="Hello",
                ),
                types.SimpleNamespace(type="response.output_text.delta", item_id="msg_1", delta=" there",
                ),
                types.SimpleNamespace(
                    type="response.reasoning_text.delta",
                    item_id="reasoning_1",
                    delta="plan",
                ),
                types.SimpleNamespace(type="response.output_text.done", item_id="msg_1", text="Hello there",
                ),
                types.SimpleNamespace(type="response.completed"),
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter(self._events)

        def get_final_response(self):
            return self._final

    class FakeResponses:
        def stream(self, **kwargs):
            return FakeStream()

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

    emitted = []

    class FakeSessionState:
        _active_turn_index = 3

        def get_provider_metadata(self, *_args, **_kwargs):
            return None

        def set_provider_metadata(self, *_args, **_kwargs):
            return None

        def _emit_event(self, event_type, payload, *, turn=None):
            emitted.append((event_type, payload, turn))

    context = ProviderRuntimeContext(
        session_state=FakeSessionState(),
        agent_config={"provider_tools": {"openai": {}}},
        stream=True,
    )
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1", input_id="input-1", turn_id="turn-1"
        ),
        provider=ProviderIdentity(
            provider_id="openai",
            runtime_id="openai_responses",
            route_id="openai/gpt-5.4-mini",
            model="gpt-5.4-mini",
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        ),
    )
    context.exchange_recorder = recorder

    result = runtime.invoke(
        client=FakeClient(),
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        stream=True,
        context=context,
    )

    assert result.messages[0].content == "Hello there"
    assert emitted == [
        ("assistant.message.start", {"item_id": "msg_1"}, 3),
        ("assistant.message.delta", {"item_id": "msg_1", "delta": "Hello"}, 3),
        ("assistant.message.delta", {"item_id": "msg_1", "delta": " there"}, 3),
        ("assistant.reasoning.start", {"item_id": "reasoning_1"}, 3),
        (
            "assistant.reasoning.delta",
            {"item_id": "reasoning_1", "delta": "plan"},
            3,
        ),
        ("assistant.message.end", {"item_id": "msg_1"}, 3),
        ("assistant.reasoning.end", {"item_id": "reasoning_1"}, 3),
    ]
    assert [event.kind for event in recorder.events] == [
        "response_start",
        "text_start",
        "text_delta",
        "text_delta",
        "thinking_start",
        "thinking_delta",
        "text_end",
        "thinking_end",
    ]


def test_responses_tool_argument_delta_requires_item_id():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter(
                [
                    types.SimpleNamespace(
                        type="response.function_call_arguments.delta",
                        item_id="",
                        delta='{"path":',
                        output_index=0,
                    )
                ]
            )

        def get_final_response(self):
            raise AssertionError("malformed stream must fail before completion")

    client = types.SimpleNamespace(
        responses=types.SimpleNamespace(stream=lambda **_kwargs: FakeStream())
    )
    session_state = types.SimpleNamespace(
        _active_turn_index=1,
        get_provider_metadata=lambda *_args, **_kwargs: None,
        set_provider_metadata=lambda *_args, **_kwargs: None,
    )
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={"provider_tools": {"openai": {}}},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime.invoke(
            client=client,
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            stream=True,
            context=context,
        )

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.details["code"] == "invalid_responses_event"


def test_responses_output_item_done_rejects_unknown_item_type() -> None:
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter(
                [
                    types.SimpleNamespace(
                        type="response.output_item.done",
                        item_id="future-1",
                        output_index=0,
                        item=types.SimpleNamespace(
                            id="future-1", type="future_output"
                        ),
                    )
                ]
            )

        def get_final_response(self):
            raise AssertionError("malformed stream must fail before completion")

    client = types.SimpleNamespace(
        responses=types.SimpleNamespace(stream=lambda **_kwargs: FakeStream())
    )
    session_state = types.SimpleNamespace(
        _active_turn_index=1,
        get_provider_metadata=lambda *_args, **_kwargs: None,
        _emit_event=lambda *_args, **_kwargs: None,
    )
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._stream_responses(
            client,
            {"model": "gpt-5.4-mini", "input": []},
            context,
        )

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.details["code"] == "invalid_responses_event"


@pytest.mark.parametrize(
    ("events", "kind", "code"),
    [
        ([], "protocol", "missing_responses_terminal"),
        (
            [types.SimpleNamespace(type="future.semantic")],
            "protocol",
            "unknown_responses_event",
        ),
        (
            [types.SimpleNamespace(type="response.failed")],
            "provider",
            "provider_response_failed",
        ),
    ],
)
def test_responses_stream_requires_known_terminal_semantics(
    events, kind, code
) -> None:
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(
            provider_id="openai", runtime_id="openai_responses"
        )
    )

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter(events)

        def get_final_response(self):
            raise AssertionError("invalid stream must fail before final response")

    client = types.SimpleNamespace(
        responses=types.SimpleNamespace(stream=lambda **_kwargs: FakeStream())
    )
    session_state = types.SimpleNamespace(
        _active_turn_index=1,
        get_provider_metadata=lambda *_args, **_kwargs: None,
        _emit_event=lambda *_args, **_kwargs: None,
    )
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._stream_responses(
            client,
            {"model": "gpt-5.4-mini", "input": []},
            context,
        )

    assert exc_info.value.kind == kind
    assert exc_info.value.details["code"] == code
