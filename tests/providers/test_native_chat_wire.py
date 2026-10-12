"""Captured source JSON is admitted by profile values and sent verbatim."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext, ProviderRuntimeError
from breadboard_engine.provider.profiles import OpenAICompletionsProviderProfile
from breadboard_engine.provider.runtimes.openai.native_chat_wire import (
    NativeRequestAuthorityError, admitted_native_request, create_chat_client, native_chat_wire_scope,
)

openai = pytest.importorskip("openai")


def admitted(model="native", summary=False):
    profile = OpenAICompletionsProviderProfile(
        base_url="http://127.0.0.1:8111/v1", model=model, scoped_credential="test",
        context_window=10000, max_output_tokens=1024,
    )
    expected = profile.chat_request([{"role": "user", "content": "café"}], [{
        "type": "function", "function": {"name": "echo", "parameters": {"type": "object"}},
    }])
    source = dict(reversed(list(expected.items())))
    source["messages"] = [{"content": "café", "role": "user"}]
    raw = json.dumps(source, ensure_ascii=False, indent=1)
    context = ProviderRuntimeContext(SimpleNamespace(), {}, native_request_body_json=raw,
        extra={"compaction_summary": summary})
    return profile, expected, context, raw


def acknowledged(request):
    body = json.loads(request.content)
    if body.get("stream"):
        chunks = [
            {"id": "chat-test", "object": "chat.completion.chunk", "created": 0,
             "model": body["model"], "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
            {"id": "chat-test", "object": "chat.completion.chunk", "created": 0,
             "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return 200, "text/event-stream", data.encode()
    response = {"id": "chat-test", "object": "chat.completion", "created": 0,
        "model": body["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    return 200, "application/json", json.dumps(response).encode()


def test_admission_retains_dynamic_order_and_rejects_changed_authority():
    profile, expected, context, raw = admitted()
    receipt, body = admitted_native_request(context, profile, expected)
    assert receipt == expected
    assert body == raw.encode()
    changed = json.loads(raw)
    changed["model"] = "other"
    context.native_request_body_json = json.dumps(changed)
    with pytest.raises(NativeRequestAuthorityError) as caught:
        admitted_native_request(context, profile, expected)
    assert caught.value.details["code"] == "native_request_body_mismatch"
    assert isinstance(caught.value, ProviderRuntimeError)
    assert caught.value.kind == "configuration"


@pytest.mark.parametrize("source_json, code", [
    ("{}", "native_request_members_mismatch"),
    ("[]", "policy_request_invalid"),
    ("{", "policy_request_invalid"),
])
def test_authority_rejection_preserves_protocol_classification(source_json, code):
    profile, expected, context, _ = admitted()
    context.native_request_body_json = source_json
    with pytest.raises(NativeRequestAuthorityError) as caught:
        admitted_native_request(context, profile, expected)
    assert caught.value.kind == "configuration"
    assert caught.value.details["code"] == code


def test_summary_overlays_only_admitted_cap_and_tools_preserving_positions():
    profile, expected, context, raw = admitted(summary=True)
    source = json.loads(raw)
    cap = profile.request_policy.max_token_field
    source[cap] = 9999
    source["tools"] = [{"source_summary_tool": True}]
    context.native_request_body_json = json.dumps(source, separators=(",", ":"))
    receipt, body = admitted_native_request(context, profile, expected)
    assert receipt == expected
    assert list(json.loads(body)) == list(source)
    assert receipt[cap] == expected[cap]


def test_source_body_survives_sdk_stream_and_scope_resets(sdk_wire_server):
    captured = []
    def handle(request):
        captured.append(request.content)
        return acknowledged(request)
    profile, expected, context, raw = admitted()
    receipt, body = admitted_native_request(context, profile, expected)
    with create_chat_client(openai.OpenAI, api_key="test", base_url=sdk_wire_server(handle) + "/v1") as client:
        with native_chat_wire_scope(body):
            with client.chat.completions.create(model="native", messages=expected["messages"], stream=True) as stream:
                list(stream)
        client.chat.completions.create(model="generic", messages=expected["messages"], stream=False)
    assert captured[0] == raw.encode()
    assert json.loads(captured[1]) == {"model": "generic", "messages": expected["messages"], "stream": False}


def test_concurrent_source_and_generic_wire_are_isolated(sdk_wire_server):
    barrier = threading.Barrier(2)
    captured = {}
    def handle(request):
        model = json.loads(request.content)["model"]
        captured[model] = request.content
        barrier.wait(timeout=5)
        return acknowledged(request)
    profile, expected, context, raw = admitted()
    _, body = admitted_native_request(context, profile, expected)
    with create_chat_client(openai.OpenAI, api_key="test", base_url=sdk_wire_server(handle) + "/v1") as client:
        def send(native):
            with native_chat_wire_scope(body if native else None):
                response = client.chat.completions.create(model="native" if native else "generic",
                    messages=expected["messages"], stream=native)
                if native:
                    with response:
                        list(response)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(send, [True, False]))
    assert captured["native"] == raw.encode()
    assert json.loads(captured["generic"])["model"] == "generic"
