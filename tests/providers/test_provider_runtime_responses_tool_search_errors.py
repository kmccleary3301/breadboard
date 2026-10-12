import json
from types import SimpleNamespace

import pytest

from breadboard_engine.provider.adapters import OpenAIAdapter
from breadboard_engine.provider.contract_requests import normalize_request_messages
from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext


def search_history(result, native_replay, canonical):
    call = {"type": "tool_search_call", "call_id": "search-denied",
            "execution": "client", "arguments": {"query": "spawn agent"}}
    assistant = {"role": "assistant", "content": [], "tool_calls": [{
        "id": call["call_id"], "type": "function", "function": {
            "name": "tool_search", "arguments": json.dumps(call["arguments"]),
        },
    }]}
    if native_replay:
        assistant["content"].append({
            "type": "provider_replay", "provider_id": "openai",
            "schema_version": "openai.responses.tools.v1",
            "replay_scope": "same_provider", "payload": call,
        })
    history = [assistant, OpenAIAdapter().create_tool_result_message(
        call["call_id"], "tool_search", result,
    )]
    return normalize_request_messages(history) if canonical else history


@pytest.fixture
def search_runtime():
    runtime = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai", runtime_id="openai_responses",
    ))
    context = ProviderRuntimeContext(
        SimpleNamespace(get_provider_metadata=lambda _key: None),
        {"provider_tools": {"openai": {
            "responses_tool_search": True, "responses_stateful": False,
        }}},
    )
    return runtime, context


@pytest.mark.parametrize("native_replay", [False, True])
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("reason", ["blocked_by_hook", "hook_tool_permission_denied", "denied: café"])
def test_hook_denied_search_replays_correlated_tool_error(
    search_runtime, native_replay, canonical, reason,
):
    runtime, context = search_runtime
    result = {"error": reason}
    payload = runtime._request_payload(
        model="test", messages=search_history(result, native_replay, canonical),
        tools=None, context=context,
    )
    assert payload["input"] == [
        {"type": "tool_search_call", "call_id": "search-denied",
         "execution": "client", "arguments": {"query": "spawn agent"}},
        {"type": "function_call_output", "call_id": "search-denied",
         "output": json.dumps(result, indent=2)},
    ]


@pytest.mark.parametrize("native_replay", [False, True])
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("tools", [[], [{
    "type": "namespace", "name": "multi_agent_v1", "tools": [{
        "type": "function", "name": "spawn_agent", "description": "Spawn an agent",
        "parameters": {"type": "object", "properties": {}}, "defer_loading": True,
    }],
}]])
@pytest.mark.parametrize("stock_error", [None, "failed to parse tool_search arguments"])
def test_search_carrier_keeps_native_wire(
    search_runtime, native_replay, canonical, tools, stock_error,
):
    runtime, context = search_runtime
    output = {"status": "completed", "execution": "client", "tools": tools}
    result = {"__mvi_text_output": json.dumps(output, separators=(",", ":"))}
    if stock_error is not None:
        result["error"] = stock_error
    payload = runtime._request_payload(
        model="test", messages=search_history(result, native_replay, canonical),
        tools=None, context=context,
    )
    assert payload["input"] == [
        {"type": "tool_search_call", "call_id": "search-denied",
         "execution": "client", "arguments": {"query": "spawn agent"}},
        {"type": "tool_search_output", "call_id": "search-denied", **output},
    ]
