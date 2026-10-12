"""Stock custom/search call fields survive unary, stream, and replay boundaries."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from breadboard_engine.provider.adapters import OpenAIAdapter
from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
from breadboard_engine.provider.contracts import ProviderRuntimeError
from breadboard_engine.state.session_state import SessionState


@pytest.mark.parametrize("kind", ["custom_tool_call", "tool_search_call"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("item_id", [None, "special-item"])
@pytest.mark.parametrize("status", [None, "completed"])
def test_special_call_input_output_exact_stock_projection(tmp_path, kind, stream, item_id, status):
    # protocol/src/models.rs:807-859: IDs are skipped, status is optional, custom
    # input is raw text, and tool-search arguments remain a JSON value.
    config = {"providers": {"default_model": "gpt-5.5", "models": [{"id": "gpt-5.5", "adapter": "openai"}]},
              "provider_tools": {"openai": {"responses_custom_tools": ["apply_patch"], "responses_tool_search": True}}}
    state = SessionState(str(tmp_path), "special", config)
    context = ProviderRuntimeContext(state, config)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    item = {"type": kind, "id": item_id, "call_id": "special-call"}
    if status is not None:
        item["status"] = status
    if kind == "custom_tool_call":
        item.update(name="apply_patch", input="*** Begin Patch\n*** Add File: café.txt\n+héllo\n*** End Patch\n")
        name, arguments = "apply_patch", {"input": item["input"]}
        output = "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nA café.txt\n"
        expected_output = {"type": "custom_tool_call_output", "call_id": "special-call", "output": output}
    else:
        item.update(execution="client", arguments={"query": "  spawn agent  ", "limit": 2})
        name, arguments = "tool_search", item["arguments"]
        output = json.dumps({"status": "completed", "execution": "client", "tools": []})
        expected_output = {"type": "tool_search_output", "call_id": "special-call",
                           "status": "completed", "execution": "client", "tools": []}
    response = SimpleNamespace(id="response-1", status="completed", output=[item], usage=None)

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __iter__(self):
            yield SimpleNamespace(type="response.output_item.added", item=item, output_index=0)
            if kind == "custom_tool_call":
                identifier = item_id or item["call_id"]
                yield SimpleNamespace(type="response.custom_tool_call_input.delta", item_id=identifier,
                                      output_index=0, delta=item["input"])
                yield SimpleNamespace(type="response.custom_tool_call_input.done", item_id=identifier,
                                      output_index=0, input=item["input"])
            yield SimpleNamespace(type="response.output_item.done", item=item, output_index=0)
            yield SimpleNamespace(type="response.completed", response=response)

        def get_final_response(self):
            return response

    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response, stream=lambda **_: Stream()))
    result = runtime.invoke(client=client, model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                            tools=None, stream=stream, context=context)
    assert result.messages[0].tool_calls[0].name == name
    assert result.messages[0].tool_calls[0].parsed_arguments == arguments
    messages = [result.messages[0].as_dict(), OpenAIAdapter().create_tool_result_message(
        "special-call", name, {"ok": True, "__mvi_text_output": output})]
    expected_call = {key: value for key, value in item.items() if key != "id"}
    assert runtime._convert_messages_to_input(messages, include_tool_calls=True, context=context) == [expected_call, expected_output]
    ordinary = ProviderRuntimeContext(state, {"providers": config["providers"]})
    with pytest.raises(ProviderRuntimeError, match="Unknown Responses output item"):
        runtime.invoke(client=client, model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                       tools=None, stream=False, context=ordinary)


def test_deferred_namespace_filter_is_explicitly_gated():
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    tools = [{"type": "namespace", "name": "multi_agent_v1", "tools": []}, {"type": "web_search"}]
    native = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_deferred_tools": ["multi_agent_v1"]}}})
    assert runtime._convert_tools_to_responses(tools, native) == [{"type": "web_search"}]
    assert runtime._convert_tools_to_responses(tools) == tools


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("namespace", [None, "multi_agent_v1"])
def test_function_arguments_preserve_original_spacing_order_and_unicode(tmp_path, stream, namespace):
    config = {"providers": {"default_model": "gpt-5.5", "models": [{"id": "gpt-5.5", "adapter": "openai"}]},
              "provider_tools": {"openai": {"responses_preserve_tool_call_items": True, "responses_namespaces": True}}}
    state = SessionState(str(tmp_path), "function-replay", config)
    context = ProviderRuntimeContext(state, config)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    arguments = '{\n  "z": "☃", "a" : [1, 2], "message": "hello" \n}\n'
    item = {"type": "function_call", "id": "function-item", "call_id": "function-call",
            "status": "completed", "name": "spawn_agent" if namespace else "exec_command", "arguments": arguments}
    if namespace:
        item["namespace"] = namespace
    response = SimpleNamespace(id="response-1", status="completed", output=[item], usage=None)

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __iter__(self):
            yield SimpleNamespace(type="response.output_item.added", item=item, output_index=0)
            yield SimpleNamespace(type="response.function_call_arguments.delta", item_id=item["id"],
                                  output_index=0, delta=arguments)
            yield SimpleNamespace(type="response.function_call_arguments.done", item_id=item["id"],
                                  output_index=0, arguments=arguments)
            yield SimpleNamespace(type="response.output_item.done", item=item, output_index=0)
            yield SimpleNamespace(type="response.completed", response=response)

        def get_final_response(self):
            return response

    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response, stream=lambda **_: Stream()))
    result = runtime.invoke(client=client, model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                            tools=None, stream=stream, context=context)
    messages = [result.messages[0].as_dict()]
    expected = {key: value for key, value in item.items() if key not in {"id", "status"}}
    assert runtime._convert_messages_to_input(messages, include_tool_calls=True, context=context) == [expected]
    ordinary = ProviderRuntimeContext(state, {"providers": config["providers"],
                                              "provider_tools": {"openai": {"responses_namespaces": True}}})
    ordinary_result = runtime.invoke(client=client, model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                                     tools=None, stream=stream, context=ordinary)
    ordinary_input = runtime._convert_messages_to_input([ordinary_result.messages[0].as_dict()],
                                                       include_tool_calls=True, context=ordinary)
    assert ordinary_input[0]["arguments"] != arguments


@pytest.mark.parametrize("original", [False, True])
@pytest.mark.parametrize("web_type", ["text", "text_and_image"])
def test_selected_model_capabilities_project_image_and_web_schemas(original, web_type):
    from copy import deepcopy

    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    tools = [{"type": "web_search", "external_web_access": True, "search_content_types": ["text", "image"]},
             {"type": "function", "name": "view_image", "parameters": {
                 "type": "object", "properties": {"path": {"type": "string"}, "detail": {"type": "string"}},
                 "required": ["path"], "additionalProperties": False}}]
    before = deepcopy(tools)
    context = ProviderRuntimeContext(None, {"providers": {"default_model": "selected", "models": [
        {"id": "parent", "supports_image_detail_original": not original,
         "web_search_tool_type": "text_and_image" if web_type == "text" else "text"},
        {"id": "selected", "supports_image_detail_original": original, "web_search_tool_type": web_type},
    ]}})
    actual = runtime._convert_tools_to_responses(tools, context)
    assert ("detail" in actual[1]["parameters"]["properties"]) is original
    assert actual[0].get("search_content_types") == (["text", "image"] if web_type == "text_and_image" else None)
    assert tools == before
    assert runtime._convert_tools_to_responses(tools) == before
