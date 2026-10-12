"""Exercise the selected serializer through real SDK/HTTPX request encoding."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace


import pytest

from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.codex_responses_wire import (
    codex_wire_scope, create_responses_client,
)

openai = pytest.importorskip("openai")



def context(native):
    return ProviderRuntimeContext(
        SimpleNamespace(), {"tools": {"native_semantics": "codex_0_139"}} if native else {},
    )


def payload(model="gpt-test"):
    return {
        "model": model, "instructions": "Keep café unchanged.",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "café"}], "type": "message"},
            {"type": "function_call", "call_id": "call", "name": "echo", "arguments": '{"z":1,"a":2}'},
            {"type": "function_call_output", "call_id": "call", "output": "ok"},
        ],
        "tools": [], "tool_choice": "auto", "parallel_tool_calls": True,
        "reasoning": {"effort": "medium"}, "store": False,
        "include": ["reasoning.encrypted_content"], "prompt_cache_key": "key",
        "text": {"verbosity": "low"}, "extra_body": {"client_metadata": {"z": "1", "a": "2"}},
    }


def response(model):
    return {
        "id": "resp-test", "object": "response", "created_at": 0,
        "status": "completed", "model": model, "output": [], "error": None,
        "incomplete_details": None, "instructions": None, "metadata": {},
        "parallel_tool_calls": True, "temperature": 1, "tool_choice": "auto",
        "tools": [], "top_p": 1, "usage": None,
    }


def acknowledged(request):
    body = json.loads(request.content)
    value = response(body["model"])
    if body.get("stream"):
        events = [
            {"type": "response.created", "sequence_number": 0, "response": {**value, "status": "in_progress"}},
            {"type": "response.completed", "sequence_number": 1, "response": value},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return (200, "text/event-stream", data.encode())
    return (200, "application/json", json.dumps(value).encode())


def expected_native(arguments, stream):
    return {
        "model": arguments["model"], "instructions": arguments["instructions"],
        "input": [
            {"type": "message", "role": "user", "content": arguments["input"][0]["content"]},
            {"type": "function_call", "name": "echo", "arguments": '{"z":1,"a":2}', "call_id": "call"},
            arguments["input"][2],
        ],
        "tools": [], "tool_choice": "auto", "parallel_tool_calls": True,
        "reasoning": {"effort": "medium"}, "store": False, "stream": stream,
        "include": ["reasoning.encrypted_content"], "prompt_cache_key": "key",
        "text": {"verbosity": "low"}, "client_metadata": {"z": "1", "a": "2"},
    }


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_encodes_source_order_and_scope_resets(stream, sdk_wire_server):
    captured = []
    def handle(request):
        captured.append(request.content)
        return acknowledged(request)
    base_url = sdk_wire_server(handle) + "/v1"
    with create_responses_client(openai.OpenAI, api_key="test", base_url=base_url) as client:
        arguments = payload()
        with codex_wire_scope(context(True)):
            if stream:
                with client.responses.stream(**arguments) as events:
                    list(events)
            else:
                client.responses.create(**arguments, stream=False)
        client.responses.create(**arguments, stream=False)
    assert captured[0] == json.dumps(expected_native(arguments, stream), ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()
    generic = json.loads(captured[1])
    assert list(generic)[:2] == ["model", "stream"]
    assert list(generic["input"][0]) == ["role", "content", "type"]
    assert list(generic["input"][1]) == ["type", "call_id", "name", "arguments"]
    assert generic == expected_native(arguments, False)


def test_concurrent_native_and_generic_requests_are_isolated(sdk_wire_server):
    barrier = threading.Barrier(2)
    captured = {}
    def handle(request):
        body = json.loads(request.content)
        captured[body["model"]] = request.content
        barrier.wait(timeout=5)
        return acknowledged(request)
    base_url = sdk_wire_server(handle) + "/v1"
    with create_responses_client(openai.OpenAI, api_key="test", base_url=base_url) as client:
        def send(native):
            model = "native" if native else "generic"
            with codex_wire_scope(context(native)):
                client.responses.create(**payload(model), stream=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(send, [True, False]))
    assert captured["native"] == json.dumps(expected_native(payload("native"), False),
        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    assert list(json.loads(captured["generic"]))[:2] == ["model", "stream"]


def test_retry_retains_source_order_and_exception_resets_scope(sdk_wire_server):
    captured = []
    def handle(request):
        captured.append(request.content)
        if len(captured) == 1:
            return (500, "application/json", json.dumps({"error": {"message": "retry", "type": "server_error"}}).encode())
        return acknowledged(request)
    base_url = sdk_wire_server(handle) + "/v1"
    with create_responses_client(openai.OpenAI, api_key="test", base_url=base_url, max_retries=1) as client:
        with pytest.raises(RuntimeError, match="after send"):
            with codex_wire_scope(context(True)):
                client.responses.create(**payload(), stream=False)
                raise RuntimeError("after send")
        client.responses.create(**payload(), stream=False)
    assert captured[0] == captured[1]
    assert captured[0] == json.dumps(expected_native(payload(), False), ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()
    assert list(json.loads(captured[2]))[:2] == ["model", "stream"]
