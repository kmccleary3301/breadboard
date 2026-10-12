"""Dynamic stock root order reaches the actual Responses SDK encoder."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime

pytest.importorskip("openai")


def runtime():
    return OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))


def context(semantics, order):
    return ProviderRuntimeContext(
        SimpleNamespace(get_provider_metadata=lambda _key: None),
        {"tools": {"native_semantics": semantics}, "provider_tools": {"openai": {
            "responses_stateful": False, "include_reasoning": False,
            "request_params": {"store": False, "max_output_tokens": 17},
        }}}, native_request_field_order=order,
    )


def acknowledged(request):
    body = json.loads(request.content)
    value = {"id": "resp-test", "object": "response", "created_at": 0,
        "status": "completed", "model": body["model"], "output": [], "error": None,
        "incomplete_details": None, "instructions": None, "metadata": {},
        "parallel_tool_calls": True, "temperature": 1, "tool_choice": "auto",
        "tools": [], "top_p": 1, "usage": None}
    if body.get("stream"):
        events = [
            {"type": "response.created", "sequence_number": 0, "response": {**value, "status": "in_progress"}},
            {"type": "response.completed", "sequence_number": 1, "response": value},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return 200, "text/event-stream", data.encode()
    return 200, "application/json", json.dumps(value).encode()


@pytest.mark.parametrize("semantics", ["opencode_1_2_17", "oh_my_opencode_3_10_0"])
@pytest.mark.parametrize("stream", [False, True])
def test_source_tuple_controls_actual_root_without_a_profile_field_table(semantics, stream, sdk_wire_server):
    captured = []
    def handle(request):
        captured.append(request.content)
        return acknowledged(request)
    provider = runtime()
    # Deliberately different from both Python SDK and Codex root order.
    source_order = tuple(json.loads('{"input":[],"max_output_tokens":17,"model":"source","tools":[],"store":false,"stream":true}'))
    with provider.create_client("test", base_url=sdk_wire_server(handle) + "/v1") as client:
        provider.invoke(client=client, model="source", messages=[{"role": "user", "content": "café"}],
            tools=None, stream=stream, context=context(semantics, source_order))
    body = json.loads(captured[0])
    assert list(body) == [key for key in source_order if key in body]
    assert body["model"] == "source"
    assert body["max_output_tokens"] == 17
    assert body["store"] is False
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "café"}]
    assert "tools" not in body


def test_source_and_generic_contexts_do_not_leak_across_concurrent_calls(sdk_wire_server):
    captured = {}
    barrier = threading.Barrier(2)
    def handle(request):
        body = json.loads(request.content)
        captured[body["model"]] = request.content
        barrier.wait(timeout=5)
        return acknowledged(request)
    provider = runtime()
    source_order = ("input", "max_output_tokens", "model", "store")
    with provider.create_client("test", base_url=sdk_wire_server(handle) + "/v1") as client:
        def send(source):
            provider.invoke(client=client, model="source" if source else "generic",
                messages=[{"role": "user", "content": "hi"}], tools=None, stream=False,
                context=context("opencode_1_2_17", source_order) if source else context("generic", None))
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(send, [True, False]))
    assert list(json.loads(captured["source"])) == list(source_order)
    assert list(json.loads(captured["generic"]))[0] == "model"
