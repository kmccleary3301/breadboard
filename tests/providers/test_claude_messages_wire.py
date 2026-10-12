"""Exercise Claude source root ordering through real SDK/HTTPX encoding."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace


import pytest

from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.anthropic import AnthropicMessagesRuntime
from breadboard_engine.provider.runtimes.claude_messages_wire import (
    claude_wire_scope, create_claude_client,
)

anthropic = pytest.importorskip("anthropic")



def context(native):
    return ProviderRuntimeContext(SimpleNamespace(),
        {"tools": {"native_semantics": "claude_code_2_1_63"}} if native else {})


def payload(sampling="thinking", model="claude-test"):
    value = {
        "model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": "café"}]}],
        "system": [{"type": "text", "text": "Keep nested order."}],
        "tools": [], "metadata": {"user_id": "user"}, "max_tokens": 1024,
    }
    value[sampling] = {"type": "enabled", "budget_tokens": 1024} if sampling == "thinking" else 0.1
    return value


def expected(arguments, stream):
    return {**arguments, "stream": stream}


def acknowledged(request):
    body = json.loads(request.content)
    message = {"id": "msg-test", "type": "message", "role": "assistant", "model": body["model"],
        "content": [], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}
    if body.get("stream"):
        initial = {**message, "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}
        events = [
            {"type": "message_start", "message": initial},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        data = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events)
        return (200, "text/event-stream", (data).encode())
    return (200, "application/json", json.dumps(message).encode())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("sampling", ["thinking", "temperature"])
def test_actual_claude_main_and_summary_wire_and_scope_reset(stream, sampling, sdk_wire_server):
    captured = []
    def handle(request):
        assert request.path.split("?", 1)[0].endswith("/messages")
        captured.append(request.content)
        return acknowledged(request)
    base_url = sdk_wire_server(handle)
    with create_claude_client(anthropic.Anthropic, api_key="test", base_url=base_url) as client:
        arguments = payload(sampling)
        with claude_wire_scope(context(True)):
            if stream:
                with client.messages.stream(**AnthropicMessagesRuntime._sdk_request_options(client.messages.stream, arguments), extra_query={"beta": "true"}) as events:
                    list(events)
            else:
                client.messages.create(**AnthropicMessagesRuntime._sdk_request_options(client.messages.create, arguments), stream=False, extra_query={"beta": "true"})
        client.messages.create(**AnthropicMessagesRuntime._sdk_request_options(client.messages.create, arguments), stream=False, extra_query={"beta": "true"})
    assert captured[0] == json.dumps(expected(arguments, stream), ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()
    generic = json.loads(captured[1])
    assert list(generic)[:3] == ["max_tokens", "messages", "model"]
    assert generic == expected(arguments, False)


def test_concurrent_generic_and_claude_calls_are_isolated(sdk_wire_server):
    barrier = threading.Barrier(2)
    captured = {}
    def handle(request):
        body = json.loads(request.content)
        captured[body["model"]] = request.content
        barrier.wait(timeout=5)
        return acknowledged(request)
    base_url = sdk_wire_server(handle)
    with create_claude_client(anthropic.Anthropic, api_key="test", base_url=base_url) as client:
        def send(native):
            with claude_wire_scope(context(native)):
                client.messages.create(**payload(model="native" if native else "generic"), stream=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(send, [True, False]))
    assert captured["native"] == json.dumps(expected(payload(model="native"), False),
        ensure_ascii=False, separators=(",", ":")).encode()
    assert list(json.loads(captured["generic"]))[:3] == ["max_tokens", "messages", "model"]


def test_claude_retry_and_exception_keep_scope_bounded(sdk_wire_server):
    captured = []
    def handle(request):
        captured.append(request.content)
        if len(captured) == 1:
            return (500, "application/json", json.dumps({"type": "error", "error": {"type": "api_error", "message": "retry"}}).encode())
        return acknowledged(request)
    base_url = sdk_wire_server(handle)
    with create_claude_client(anthropic.Anthropic, api_key="test", base_url=base_url, max_retries=1) as client:
        with pytest.raises(RuntimeError, match="after send"):
            with claude_wire_scope(context(True)):
                client.messages.create(**payload(), stream=False)
                raise RuntimeError("after send")
        client.messages.create(**payload(), stream=False)
    assert captured[0] == captured[1]
    assert captured[0] == json.dumps(expected(payload(), False), ensure_ascii=False,
        separators=(",", ":")).encode()
    assert list(json.loads(captured[2]))[:3] == ["max_tokens", "messages", "model"]
