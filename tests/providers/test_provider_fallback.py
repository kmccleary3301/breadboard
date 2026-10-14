import copy
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard_engine.provider.runtime import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeError,
)
from breadboard_engine.state.session_state import SessionState


@pytest.mark.parametrize("native_semantics", ["codex_0_139", "standard"])
def test_provider_fallback_builds_portable_anthropic_request_from_responses_history(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, native_semantics: str,
) -> None:
    from contextlib import nullcontext

    from breadboard_engine.compaction.controller import reproject_for_route
    from breadboard_engine.conductor.model_output import retry_with_fallback
    from breadboard_engine.provider.adapters import OpenAIAdapter
    from breadboard_engine.provider.contract_requests import ProviderRequest
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
    from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime

    monkeypatch.setattr("breadboard_engine.conductor.model_output.time.sleep", lambda _: None)
    source_descriptor = SimpleNamespace(
        provider_id="openai", runtime_id="openai_responses", default_api_variant="responses",
    )
    source_runtime = SimpleNamespace(
        descriptor=source_descriptor,
        invoke=lambda **_: (_ for _ in ()).throw(
            ProviderRuntimeError("source route failed", details={"code": "provider_error"})
        ),
    )
    image_call_id = "image-call"
    text_call_id = "text-call"
    raw_call_id = "raw-call"
    one_pixel_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5l8AAAAASUVORK5CYII="
    )
    one_pixel_data_url = f"data:image/png;base64,{one_pixel_png}"
    tool_result = OpenAIAdapter().create_tool_result_message(
        image_call_id,
        "view_image",
        {
            "__mvi_native_responses_output": True,
            "__mvi_content_output": [
                {"type": "input_text", "text": "C1 BEFORE IMAGE"},
                {"type": "input_image", "image_url": one_pixel_data_url},
                {"type": "input_text", "text": "C1 BETWEEN IMAGES"},
                {"type": "input_image", "image_url": "https://example.test/image.png"},
                {"type": "input_text", "text": "C1 AFTER IMAGES"},
            ],
        },
    )
    text_tool_result = OpenAIAdapter().create_tool_result_message(
        text_call_id,
        "read_file",
        {
            "__mvi_native_responses_output": True,
            "__mvi_content_output": [
                {"type": "input_text", "text": "C2 BEFORE IMAGE"},
                {"type": "input_image", "image_url": one_pixel_data_url},
                {"type": "input_text", "text": "C2 AFTER IMAGE"},
            ],
        },
    )
    raw_tool_result = OpenAIAdapter().create_tool_result_message(
        raw_call_id,
        "opaque_tool",
        {
            "__mvi_native_responses_output": True,
            "__mvi_content_output": [
                {"type": "input_text", "text": "RAW_BEFORE_IMAGE"},
                {"type": "input_image", "image_url": one_pixel_data_url},
                {"type": "input_text", "text": "RAW_AFTER_IMAGE"},
            ],
        },
    )
    source_history = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": image_call_id,
                    "type": "function",
                    "function": {"name": "view_image", "arguments": "{}"},
                },
                {
                    "id": text_call_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
            ],
        },
        tool_result,
        text_tool_result,
        {
            "role": "assistant",
            "tool_calls": [{
                "call_id": raw_call_id,
                "name": "opaque_tool",
                "raw_arguments": "{not valid json",
            }],
        },
        raw_tool_result,
    ]
    original_history = copy.deepcopy(source_history)
    session = SessionState(
        str(tmp_path),
        "image",
        {"tools": {"native_semantics": native_semantics}},
    )
    session.provider_messages = source_history

    same_route = reproject_for_route(
        source_history,
        session,
        source_runtime=source_runtime,
        source_model="gpt-source",
        runtime=source_runtime,
        model="gpt-source",
    )
    assert same_route == source_history
    same_api = reproject_for_route(
        source_history,
        session,
        source_runtime=source_runtime,
        source_model="gpt-source",
        runtime=SimpleNamespace(descriptor=source_descriptor),
        model="gpt-source",
    )
    assert any(
        block.get("schema_version") == "openai.responses.tool_output.v1"
        for message in same_api
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    from breadboard_engine.provider.contract_wire import ProviderContractError
    from breadboard_engine.provider.input_media import resolve_input_media
    from breadboard_engine.provider.routing import ProviderDescriptor
    from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

    chat_runtime = OpenAIChatRuntime(ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=True,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=None,
        api_key_env=None,
        default_headers={},
    ))
    chat_view = reproject_for_route(
        source_history,
        session,
        source_runtime=source_runtime,
        source_model="gpt-source",
        runtime=chat_runtime,
        model="gpt-chat",
    )
    chat_request = ProviderRequest(stream=False, messages=chat_view, tools=[])
    chat_context = ProviderRuntimeContext(
        session_state=session,
        agent_config={},
        stream=False,
        extra={},
    )
    if chat_view is not source_history:
        chat_context.extra["portable_image_urls"] = True
    chat_body = chat_runtime.project_request_body(
        model="gpt-chat",
        messages=chat_request.messages,
        tools=chat_request.tools,
        stream=False,
        context=chat_context,
    )
    chat_messages = chat_body["messages"]
    assistant_index = next(
        index for index, message in enumerate(chat_messages)
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert [
        call["id"]
        for call in chat_messages[assistant_index]["tool_calls"]
    ] == [image_call_id, text_call_id]
    chat_tool_results = [
        (index, message)
        for index, message in enumerate(chat_messages)
        if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for _, message in chat_tool_results] == [
        image_call_id, text_call_id,
    ]
    first_non_tool_after_calls = next(
        index
        for index, message in enumerate(chat_messages)
        if index > assistant_index and message["role"] != "tool"
    )
    assert all(index < first_non_tool_after_calls for index, _ in chat_tool_results)
    first_tool_text = next(
        message["content"]
        for _, message in chat_tool_results
        if message["tool_call_id"] == image_call_id
    )
    first_before = first_tool_text.index("C1 BEFORE IMAGE")
    first_reference = first_tool_text.index(image_call_id)
    middle_text = first_tool_text.index("C1 BETWEEN IMAGES")
    second_reference = first_tool_text.index(
        image_call_id, first_reference + len(image_call_id),
    )
    last_text = first_tool_text.index("C1 AFTER IMAGES")
    assert first_before < first_reference < middle_text < second_reference < last_text
    assert first_tool_text.count(image_call_id) == 2
    second_tool_text = next(
        message["content"]
        for _, message in chat_tool_results
        if message["tool_call_id"] == text_call_id
    )
    assert (
        second_tool_text.index("C2 BEFORE IMAGE")
        < second_tool_text.index(text_call_id)
        < second_tool_text.index("C2 AFTER IMAGE")
    )

    image_batch = next(
        message
        for index, message in enumerate(chat_messages)
        if index > assistant_index
        and message["role"] == "user"
        and any(
            block.get("type") == "text"
            and (image_call_id in block.get("text", "")
                 or text_call_id in block.get("text", ""))
            for block in message["content"]
        )
    )
    queued_images = []
    content_blocks = image_batch["content"]
    for index, block in enumerate(content_blocks[:-1]):
        if block.get("type") != "text":
            continue
        image_call = next(
            (
                call_id for call_id in (image_call_id, text_call_id)
                if call_id in block.get("text", "")
            ),
            None,
        )
        if image_call is not None:
            image = content_blocks[index + 1]
            assert image["type"] == "image_url"
            queued_images.append((image_call, image["image_url"]["url"]))
    assert queued_images == [
        (image_call_id, one_pixel_data_url),
        (image_call_id, "https://example.test/image.png"),
        (text_call_id, one_pixel_data_url),
    ]
    raw_assistant = next(
        message for message in chat_messages
        if message["role"] == "assistant"
        and any(
            block.get("type") == "text"
            and "RAW_BEFORE_IMAGE" in block.get("text", "")
            for block in message.get("content", [])
        )
    )
    raw_assistant_text = "".join(
        block.get("text", "")
        for block in raw_assistant["content"]
        if block.get("type") == "text"
    )
    raw_before = raw_assistant_text.index("RAW_BEFORE_IMAGE")
    raw_image_reference = raw_assistant_text.index(
        raw_call_id, raw_before + len("RAW_BEFORE_IMAGE"),
    )
    raw_after = raw_assistant_text.index("RAW_AFTER_IMAGE", raw_image_reference)
    assert raw_before < raw_image_reference < raw_after
    raw_image_batch = next(
        message for message in chat_messages
        if message["role"] == "user"
        and any(
            block.get("type") == "text"
            and raw_call_id in block.get("text", "")
            for block in message.get("content", [])
        )
    )
    raw_image = next(
        block for block in raw_image_batch["content"]
        if block.get("type") == "image_url"
    )
    assert raw_image["image_url"]["url"] == one_pixel_data_url
    assert chat_messages.index(raw_assistant) < chat_messages.index(raw_image_batch)
    assert all(
        fact not in str(image_batch["content"])
        for fact in (
            "C1 BEFORE IMAGE", "C1 BETWEEN IMAGES", "C1 AFTER IMAGES",
            "C2 BEFORE IMAGE", "C2 AFTER IMAGE",
        )
    )
    with pytest.raises(ProviderContractError, match="requires the native Codex profile"):
        resolve_input_media(
            {
                "type": "media",
                "kind": "codex_image_url",
                "uri": "https://example.test/ordinary.png",
            },
            ProviderRuntimeContext(
                session_state=SimpleNamespace(
                    config={"tools": {"native_semantics": "standard"}},
                ),
                agent_config={},
                stream=False,
                extra={},
            ),
        )

    fallback_descriptor = SimpleNamespace(
        provider_id="anthropic", runtime_id="anthropic_messages", default_api_variant="messages",
    )

    class CapturingAnthropicRuntime(AnthropicMessagesRuntime):
        def invoke(self, *, client, model, messages, tools, stream, context):
            request = ProviderRequest(
                stream=stream,
                messages=messages,
                tools=list(tools or []),
            )
            self.normalized_messages = request.messages
            self.portable_image_urls = (
                context.extra.get("portable_image_urls") is True
            )
            self.fallback_replay_enabled = (
                context.agent_config["provider_tools"]["anthropic"][
                    "native_tool_result_replay"
                ]
            )
            return super().invoke(
                client=client,
                model=model,
                messages=request.messages,
                tools=request.tools,
                stream=stream,
                context=context,
            )

        def _call_non_streaming(self, client, request):
            self.wire_messages = request["messages"]
            return None, None, {}, 200, ""

        def _normalize_response(self, response, usage_override=None):
            return ProviderResult(
                messages=[ProviderMessage(role="assistant", content="fallback answer")],
                raw_response=None,
                model="claude-test",
            )

    fallback_runtime = CapturingAnthropicRuntime(fallback_descriptor)

    class FallbackRouter:
        def get_runtime_descriptor(self, route_id):
            assert route_id == "anthropic/claude-test"
            return fallback_descriptor, "claude-test"

        def create_client_config(self, route_id):
            return {}

    router = FallbackRouter()
    registry = SimpleNamespace(create_runtime=lambda descriptor: fallback_runtime)
    conductor = SimpleNamespace(
        config={},
        logger_v2=SimpleNamespace(include_structured_requests=False, run_dir=None),
        md_writer=SimpleNamespace(system=lambda _: ""),
        provider_metrics=SimpleNamespace(add_fallback=lambda **_: None),
        route_health=SimpleNamespace(
            record_failure=lambda *_, **__: None,
            record_success=lambda *_, **__: None,
        ),
        _update_health_metadata=lambda *_: None,
        _get_model_routing_preferences=lambda _: {
            "fallback_models": ["anthropic/claude-test"],
        },
        _select_fallback_route=lambda *_, **__: ("anthropic/claude-test", {}),
        _log_routing_event=lambda *_, **__: None,
    )
    runtime_context = ProviderRuntimeContext(
        session_state=session,
        agent_config={
            "provider_tools": {
                "anthropic": {"native_tool_result_replay": False},
            },
        },
        stream=False,
        extra={"source_marker": "unchanged"},
    )
    original_extra = copy.deepcopy(runtime_context.extra)
    result = retry_with_fallback(
        conductor,
        source_runtime,
        object(),
        "gpt-source",
        source_history,
        None,
        runtime_context,
        stream_responses=False,
        session_state=session,
        markdown_logger=SimpleNamespace(log_system_message=lambda _: None),
        attempted=[],
        last_error=ProviderRuntimeError(
            "source route failed", details={"code": "provider_error"},
        ),
        provider_router_override=router,
        provider_registry_override=registry,
        client_lease=lambda *_: nullcontext(object()),
        route_id="openai/gpt-source",
    )

    assert result is not None
    assert source_history == original_history
    assert runtime_context.extra == original_extra
    assert fallback_runtime.portable_image_urls is True
    assert (
        runtime_context.agent_config["provider_tools"]["anthropic"][
            "native_tool_result_replay"
        ]
        is False
    )
    assert fallback_runtime.fallback_replay_enabled is True
    replay_blocks = [
        block
        for message in fallback_runtime.normalized_messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "provider_replay"
    ]
    assert [block["provider_id"] for block in replay_blocks] == ["anthropic"]
    assert [block["schema_version"] for block in replay_blocks] == [
        "anthropic.messages.tool_result.v1",
    ]
    wire = fallback_runtime.wire_messages
    tool_uses = [
        block
        for message in wire
        for block in message["content"]
        if block.get("type") == "tool_use"
    ]
    result_message = next(
        message for message in wire
        if any(block.get("type") == "tool_result" for block in message["content"])
    )
    tool_results = [
        block for block in result_message["content"]
        if block.get("type") == "tool_result"
    ]
    assert result_message["role"] == "user"
    assert [block["id"] for block in tool_uses] == [image_call_id, text_call_id]
    assert [block["tool_use_id"] for block in tool_results] == [
        image_call_id, text_call_id,
    ]
    assert [block["content"] for block in tool_results] == [
        [
            {"type": "text", "text": "C1 BEFORE IMAGE"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": one_pixel_png,
                },
            },
            {"type": "text", "text": "C1 BETWEEN IMAGES"},
            {
                "type": "image",
                "source": {
                    "type": "url",
                    "url": "https://example.test/image.png",
                },
            },
            {"type": "text", "text": "C1 AFTER IMAGES"},
        ],
        [
            {"type": "text", "text": "C2 BEFORE IMAGE"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": one_pixel_png,
                },
            },
            {"type": "text", "text": "C2 AFTER IMAGE"},
        ],
    ]
    assistant_observations = "\n".join(
        block["text"]
        for message in wire
        if message["role"] == "assistant"
        for block in message["content"]
        if block.get("type") == "text"
    )
    assert "{not valid json" in assistant_observations
    assert f"call ID {raw_call_id}" in assistant_observations
    assert f"[Tool result for call ID {raw_call_id}]\nRAW_BEFORE_IMAGE" in assistant_observations
    raw_before = assistant_observations.index("RAW_BEFORE_IMAGE")
    raw_image_reference = assistant_observations.index(
        raw_call_id, raw_before + len("RAW_BEFORE_IMAGE"),
    )
    raw_after = assistant_observations.index("RAW_AFTER_IMAGE", raw_image_reference)
    assert raw_before < raw_image_reference < raw_after

    raw_image_message = next(
        message for message in wire
        if any(block.get("type") == "image" for block in message["content"])
    )
    assert raw_image_message["role"] == "user"
    assert raw_image_message["content"][0]["text"] == (
        f"[Images from tool call ID {raw_call_id}]"
    )
    assert raw_image_message["content"][1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": one_pixel_png,
        },
    }
    raw_observation_index = next(
        index
        for index, message in enumerate(wire)
        if message["role"] == "assistant"
        and any(
            block.get("type") == "text"
            and "RAW_BEFORE_IMAGE" in block.get("text", "")
            for block in message["content"]
        )
    )
    assert raw_observation_index < wire.index(raw_image_message)
    assert wire.index(result_message) < wire.index(raw_image_message)
    assert not any(
        block.get("type") == "tool_result" and block.get("tool_use_id") == raw_call_id
        for message in wire
        for block in message["content"]
    )

@pytest.mark.parametrize("block_type", ["tool_call", "tool_result"])
def test_anthropic_user_content_rejects_tool_protocol_blocks(
    block_type: str,
) -> None:
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
    from breadboard_engine.provider.contract_wire import ProviderContractError
    from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime

    descriptor = SimpleNamespace(
        provider_id="anthropic",
        runtime_id="anthropic_messages",
        default_api_variant="messages",
    )
    runtime = AnthropicMessagesRuntime(descriptor)
    block = (
        {"type": "tool_call", "call_id": "c1", "name": "f", "arguments": "{}"}
        if block_type == "tool_call"
        else {"type": "tool_result", "call_id": "c1", "content": "observed"}
    )

    with pytest.raises(ProviderContractError, match="user content"):
        runtime._convert_messages(
            [{"role": "user", "content": [block]}],
            context=ProviderRuntimeContext(
                session_state=None,
                agent_config={},
                stream=False,
            ),
        )


def test_anthropic_continuation_preserves_results_and_ordered_side_observations() -> None:
    from breadboard_engine.compaction.controller import reproject_for_route
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.contract_requests import ProviderRequest
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
    from breadboard_engine.provider.routing import ProviderDescriptor
    from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime

    image_data = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5l8AAAAASUVORK5CYII="
    )
    image_url = f"data:image/png;base64,{image_data}"
    session = SimpleNamespace(
        config={"tools": {"native_semantics": "standard"}},
        get_provider_metadata=lambda _: None,
    )
    source_runtime = SimpleNamespace(descriptor=SimpleNamespace(
        provider_id="anthropic",
        runtime_id="anthropic_messages",
        default_api_variant="messages",
    ))
    source_history = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
                for call_id in ("c1", "c2")
            ],
        },
        AnthropicAdapter().create_tool_result_batch_message([{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "RESULT_FACT"},
                {"type": "tool_result", "tool_use_id": "c2", "content": "SECOND_RESULT"},
                {
                    "type": "text",
                    "text": "SIDEFACT_FROM_TOOL_CONTINUATION",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_data,
                    },
                },
                {"type": "text", "text": "SIDEFACT_AFTER_IMAGE"},
            ],
        }]),
    ]
    source_request = ProviderRequest(
        stream=False,
        messages=copy.deepcopy(source_history),
        tools=[],
    )
    original = copy.deepcopy(source_request.messages)
    same_route = reproject_for_route(
        source_request.messages,
        session,
        source_runtime=source_runtime,
        source_model="claude",
        runtime=source_runtime,
        model="claude",
    )
    assert same_route == source_request.messages
    assert "cache_control" in str(same_route)

    responses_runtime = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai",
        runtime_id="openai_responses",
        default_api_variant="responses",
    ))
    responses_view = reproject_for_route(
        source_request.messages,
        session,
        source_runtime=source_runtime,
        source_model="claude",
        runtime=responses_runtime,
        model="gpt-responses",
    )
    response_side_view = next(
        message
        for message in responses_view
        if message["role"] == "user"
        and any(
            block.get("type") == "text"
            and block.get("text") == "SIDEFACT_FROM_TOOL_CONTINUATION"
            for block in message.get("content", [])
        )
    )
    assert [block["type"] for block in response_side_view["content"]] == [
        "text", "media", "text",
    ]
    assert response_side_view["content"][0]["text"] == "SIDEFACT_FROM_TOOL_CONTINUATION"
    assert response_side_view["content"][2]["text"] == "SIDEFACT_AFTER_IMAGE"
    assert all("cache_control" not in block for block in response_side_view["content"])
    responses_request = ProviderRequest(
        stream=False,
        messages=responses_view,
        tools=[],
    )
    context = ProviderRuntimeContext(
        session_state=session,
        agent_config={},
        stream=False,
        extra={"portable_image_urls": True},
    )
    responses_body = responses_runtime.project_request_body(
        model="gpt-responses",
        messages=responses_request.messages,
        tools=responses_request.tools,
        stream=False,
        context=context,
    )
    response_input = responses_body["input"]
    function_outputs = [
        (index, item)
        for index, item in enumerate(response_input)
        if item.get("type") == "function_call_output"
    ]
    assert [
        (item["call_id"], item["output"])
        for _, item in function_outputs
    ] == [("c1", "RESULT_FACT"), ("c2", "SECOND_RESULT")]
    function_calls = [
        (index, item)
        for index, item in enumerate(response_input)
        if item.get("type") == "function_call"
    ]
    assert [item["call_id"] for _, item in function_calls] == ["c1", "c2"]
    assert all(
        call_index < result_index
        for (call_index, _), (result_index, _) in zip(function_calls, function_outputs)
    )
    response_side_index, response_side = next(
        (index, item)
        for index, item in enumerate(response_input)
        if item.get("role") == "user"
        and any(
            block.get("type") == "input_image"
            for block in item.get("content", [])
        )
    )
    assert all(index < response_side_index for index, _ in function_outputs)
    response_side_blocks = response_side["content"]
    assert [block["type"] for block in response_side_blocks] == [
        "input_text", "input_image", "input_text",
    ]
    assert response_side_blocks[0]["text"] == "SIDEFACT_FROM_TOOL_CONTINUATION"
    assert response_side_blocks[1]["image_url"] == image_url
    assert response_side_blocks[2]["text"] == "SIDEFACT_AFTER_IMAGE"

    chat_runtime = OpenAIChatRuntime(ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=True,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=None,
        api_key_env=None,
        default_headers={},
    ))
    chat_view = reproject_for_route(
        source_request.messages,
        session,
        source_runtime=source_runtime,
        source_model="claude",
        runtime=chat_runtime,
        model="gpt-chat",
    )
    chat_request = ProviderRequest(stream=False, messages=chat_view, tools=[])
    chat_body = chat_runtime.project_request_body(
        model="gpt-chat",
        messages=chat_request.messages,
        tools=chat_request.tools,
        stream=False,
        context=context,
    )
    chat_messages = chat_body["messages"]
    assistant_index = next(
        index for index, message in enumerate(chat_messages)
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    chat_results = [
        (index, message)
        for index, message in enumerate(chat_messages)
        if message["role"] == "tool"
    ]
    assert [
        (message["tool_call_id"], message["content"])
        for _, message in chat_results
    ] == [("c1", "RESULT_FACT"), ("c2", "SECOND_RESULT")]
    assert assistant_index < min(index for index, _ in chat_results)
    chat_side_index, chat_side = next(
        (index, message)
        for index, message in enumerate(chat_messages)
        if index > assistant_index
        and message["role"] == "user"
        and any(
            block.get("type") == "image_url"
            for block in message.get("content", [])
        )
    )
    assert all(index < chat_side_index for index, _ in chat_results)
    assert [block["type"] for block in chat_side["content"]] == [
        "text", "image_url", "text",
    ]
    assert chat_side["content"][0]["text"] == "SIDEFACT_FROM_TOOL_CONTINUATION"
    assert chat_side["content"][1]["image_url"]["url"] == image_url
    assert chat_side["content"][2]["text"] == "SIDEFACT_AFTER_IMAGE"
    assert source_request.messages == original


def test_portable_images_retain_detail_through_provider_consumers() -> None:
    from breadboard_engine.provider.history_projection import portable_provider_messages
    from breadboard_engine.provider.contract_requests import ProviderRequest
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
    from breadboard_engine.provider.routing import ProviderDescriptor
    from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
    image_data = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5l8AAAAASUVORK5CYII="
    )
    image_url = f"data:image/png;base64,{image_data}"
    cases = (
        (
            {"type": "media", "kind": "codex_image_url", "uri": image_url, "detail": "low"},
            "low",
        ),
        (
            {"type": "input_image", "image_url": image_url, "detail": "original"},
            "original",
        ),
        (
            {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
            "low",
        ),
    )
    session = SimpleNamespace(
        config={"tools": {"native_semantics": "standard"}},
        get_provider_metadata=lambda _: None,
    )
    context = ProviderRuntimeContext(
        session_state=session,
        agent_config={},
        stream=False,
        extra={"portable_image_urls": True},
    )
    runtime = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai",
        runtime_id="openai_responses",
        default_api_variant="responses",
    ))
    chat_runtime = OpenAIChatRuntime(ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=True,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=None,
        api_key_env=None,
        default_headers={},
    ))
    for block, expected_detail in cases:
        projected = portable_provider_messages(
            [{"role": "user", "content": [block]}],
            source_provider="anthropic",
            source_api="messages",
            target_provider="openai",
            target_api="responses",
            session_state=session,
        )
        request = ProviderRequest(stream=False, messages=projected, tools=[])
        body = runtime.project_request_body(
            model="gpt-responses",
            messages=request.messages,
            tools=request.tools,
            stream=False,
            context=context,
        )
        image = next(
            block
            for message in body["input"]
            if message.get("role") == "user"
            for block in message["content"]
            if block.get("type") == "input_image"
        )
        assert image["detail"] == expected_detail
        assert image["image_url"] == image_url
        chat_body = chat_runtime.project_request_body(
            model="gpt-chat",
            messages=request.messages,
            tools=request.tools,
            stream=False,
            context=context,
        )
        chat_image = next(
            block
            for message in chat_body["messages"]
            if message.get("role") == "user"
            for block in message["content"]
            if block.get("type") == "image_url"
        )
        assert chat_image["image_url"]["url"] == image_url
        assert chat_image["image_url"]["detail"] == expected_detail

@pytest.mark.parametrize(
    "side_block",
    [
        {"type": "document", "source": {"type": "text", "data": "native document"}},
        {"type": "text", "text": None},
    ],
    ids=["unsupported-document", "malformed-text"],
)
def test_native_continuation_stays_native_on_same_api_and_rejects_foreign_side_blocks(
    side_block: dict,
) -> None:
    from breadboard_engine.compaction.controller import reproject_for_route
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.contract_requests import ProviderRequest
    from breadboard_engine.provider.contract_wire import ProviderContractError
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime

    source_runtime = SimpleNamespace(descriptor=SimpleNamespace(
        provider_id="anthropic",
        runtime_id="anthropic_messages",
        default_api_variant="messages",
    ))
    source_history = [
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        },
        AnthropicAdapter().create_tool_result_batch_message([{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "RESULT"},
                side_block,
            ],
        }]),
    ]
    source_request = ProviderRequest(
        stream=False,
        messages=copy.deepcopy(source_history),
        tools=[],
    )
    session = SimpleNamespace(
        config={"tools": {"native_semantics": "standard"}},
        get_provider_metadata=lambda _: None,
    )
    same_api = reproject_for_route(
        source_request.messages,
        session,
        source_runtime=source_runtime,
        source_model="claude-old",
        runtime=source_runtime,
        model="claude-new",
    )
    assert same_api == source_request.messages

    responses_runtime = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai",
        runtime_id="openai_responses",
        default_api_variant="responses",
    ))
    with pytest.raises(ProviderContractError, match="native continuation observation"):
        reproject_for_route(
            source_request.messages,
            session,
            source_runtime=source_runtime,
            source_model="claude-old",
            runtime=responses_runtime,
            model="gpt-responses",
        )
