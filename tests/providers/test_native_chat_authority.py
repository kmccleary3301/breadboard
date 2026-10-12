"""Native authority compares JSON values and preserves non-overlaid wire bytes."""
import json

import pytest

from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
from breadboard_engine.provider.profiles import (
    OpenAICompletionsProviderProfile, OpenAICompletionsRequestPolicy,
)
from breadboard_engine.provider.runtimes.openai.native_chat_wire import (
    NativeRequestAuthorityError, admitted_native_request, create_chat_client, native_chat_wire_scope,
)

openai = pytest.importorskip("openai")


def _request(*, temperature=1.0, tools=False, cap_field="max_tokens"):
    profile = OpenAICompletionsProviderProfile(
        model="native", base_url="http://127.0.0.1:8111/v1", scoped_credential="test",
        context_window=8192, max_output_tokens=128, sampling={"temperature": temperature},
        request_policy=OpenAICompletionsRequestPolicy(max_token_field=cap_field),
    )
    expected = profile.chat_request([{"role": "user", "content": "café/"}], [
        {"type": "function", "function": {"name": "echo", "parameters": {
            "type": "object", "properties": {"value": {"const": 1}},
        }}},
    ] if tools else None)
    return profile, expected


def _source(expected, overrides=None, order=None):
    overrides = overrides or {}
    pairs = [json.dumps(key) + ": " + overrides.get(key, json.dumps(
        expected[key], ensure_ascii=False, separators=(",", ":"),
    )) for key in (order if order is not None else reversed(expected))]
    return " \n{ " + ", \n ".join(pairs) + " }\t\n"


def _context(raw, summary=False):
    return ProviderRuntimeContext(None, {}, native_request_body_json=raw,
                                  extra={"compaction_summary": summary})


def _reply(request):
    model = json.loads(request.content)["model"]
    if json.loads(request.content).get("stream"):
        chunk = {"id": "chat-authority", "object": "chat.completion.chunk", "created": 1,
                 "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        return 200, "text/event-stream", b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"
    body = {"id": "chat-authority", "object": "chat.completion", "created": 1,
            "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
    return 200, "application/json", json.dumps(body).encode()


def _wire(sdk_wire_server, profile, expected, raw, wanted, *, summary=False):
    captured = []

    def reply(request):
        captured.append(request.content)
        return _reply(request)

    receipt, body = admitted_native_request(_context(raw, summary), profile, expected)
    assert receipt == expected
    with create_chat_client(openai.OpenAI, api_key="test", base_url=sdk_wire_server(reply) + "/v1") as client:
        client.chat.completions.create(model="generic", messages=expected["messages"], stream=False)
        with native_chat_wire_scope(body):
            with client.chat.completions.create(model="native", messages=expected["messages"], stream=True) as stream:
                assert "".join(chunk.choices[0].delta.content or "" for chunk in stream) == "ok"
        client.chat.completions.create(model="generic", messages=expected["messages"], stream=False)
    assert captured[1] == wanted
    assert captured[0] == captured[2], "source body escaped its invocation scope"
    return receipt


@pytest.mark.parametrize("number", ["1", "1.0", "1e0"])
def test_json_numeric_value_authority_preserves_actual_sdk_wire(sdk_wire_server, number):
    profile, expected = _request()
    raw = _source(expected, {"temperature": number})
    receipt = _wire(sdk_wire_server, profile, expected, raw, raw.encode())
    assert type(receipt["temperature"]) is (int if number == "1" else float)


def test_summary_changes_only_tools_and_cap_value_spans(sdk_wire_server):
    profile, expected = _request(temperature=1e-7, tools=True)
    old_tools = '[{"original_summary_tool":true}]'
    raw = _source(expected, {
        "temperature": "1e-7", "max_tokens": "900", "tools": old_tools,
        "messages": '[{"content":"caf\\u00e9\\/","role":"user"}]',
    })
    wanted = raw.replace('"max_tokens": 900', '"max_tokens": 128').replace(
        old_tools, json.dumps(expected["tools"], ensure_ascii=False, separators=(",", ":")),
    )
    _wire(sdk_wire_server, profile, expected, raw, wanted.encode(), summary=True)


def test_summary_appends_only_missing_admitted_members(sdk_wire_server):
    profile, expected = _request(temperature=1e-7, tools=True)
    raw = _source({key: value for key, value in expected.items() if key not in {"tools", "max_tokens"}}, {
        "temperature": "1e-7", "messages": '[{"role":"user","content":"caf\\u00e9\\/"}]',
    })
    close = raw.rindex("}")
    added = ',"tools":' + json.dumps(expected["tools"], ensure_ascii=False, separators=(",", ":")) + ',"max_tokens":128'
    wanted = raw[:close] + added + raw[close:]
    _wire(sdk_wire_server, profile, expected, raw, wanted.encode(), summary=True)


@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_summary_removes_only_source_tools_member(sdk_wire_server, position):
    profile, expected = _request(temperature=1e-7)
    source = dict(expected, tools=[{"old": True}])
    order = list(expected)
    order.insert({"first": 0, "middle": len(order) // 2, "last": len(order)}[position], "tools")
    raw = _source(source, {"temperature": "1e-7"}, order)
    member = '"tools": [{"old":true}]'
    wanted = raw.replace(', \n ' + member, "", 1) if position == "last" else raw.replace(member + ",", "", 1)
    _wire(sdk_wire_server, profile, expected, raw, wanted.encode(), summary=True)


def test_summary_renames_legacy_cap_in_place(sdk_wire_server):
    profile, expected = _request(temperature=1e-7, cap_field="max_completion_tokens")
    source = dict(expected)
    source["max_tokens"] = source.pop("max_completion_tokens")
    raw = _source(source, {"temperature": "1e-7", "max_tokens": "900"})
    wanted = raw.replace('"max_tokens": 900', '"max_completion_tokens": 128')
    _wire(sdk_wire_server, profile, expected, raw, wanted.encode(), summary=True)


@pytest.mark.parametrize("changed,code", [
    ({"temperature": "true"}, "native_request_body_mismatch"),
    ({"temperature": "0.99"}, "native_request_body_mismatch"),
    ({"temperature": "1.000000000000000000001"}, "native_request_body_mismatch"),
    ({"temperature": "NaN"}, "policy_request_invalid"),
    ({"messages": '[{"role":"user","role":"user","content":"café/"}]'}, "policy_request_invalid"),
])
def test_changed_or_ambiguous_json_is_rejected_before_http(sdk_wire_server, changed, code):
    profile, expected = _request()
    raw = _source(expected, changed)
    captured = []

    def reply(request):
        captured.append(request.content)
        return _reply(request)

    with create_chat_client(openai.OpenAI, api_key="test", base_url=sdk_wire_server(reply) + "/v1") as client:
        with pytest.raises(NativeRequestAuthorityError) as caught:
            _, body = admitted_native_request(_context(raw), profile, expected)
            with native_chat_wire_scope(body):
                client.chat.completions.create(model="native", messages=expected["messages"], stream=True)
        assert caught.value.details["code"] == code
        assert captured == []


def test_duplicate_root_member_is_rejected():
    profile, expected = _request()
    raw = _source(expected).replace('"model": "native"', '"model": "native", "model": "native"')
    with pytest.raises(NativeRequestAuthorityError) as caught:
        admitted_native_request(_context(raw), profile, expected)
    assert caught.value.details["code"] == "policy_request_invalid"


def test_nested_boolean_is_not_a_json_number():
    profile, expected = _request(tools=True)
    raw = _source(expected).replace('"const":1', '"const":true')
    with pytest.raises(NativeRequestAuthorityError) as caught:
        admitted_native_request(_context(raw), profile, expected)
    assert caught.value.details["code"] == "native_request_body_mismatch"


@pytest.mark.parametrize("summary", [False, True])
@pytest.mark.parametrize("change", ["missing_model", "unknown_member", "both_cap_fields"])
def test_non_overlaid_member_authority_remains_closed(sdk_wire_server, summary, change):
    profile, expected = _request()
    source = dict(expected)
    if change == "missing_model":
        del source["model"]
    elif change == "unknown_member":
        source["unadmitted"] = True
    else:
        source["max_completion_tokens"] = 128
    raw = _source(source)
    captured = []

    def reply(request):
        captured.append(request.content)
        return _reply(request)

    with create_chat_client(openai.OpenAI, api_key="test", base_url=sdk_wire_server(reply) + "/v1") as client:
        with pytest.raises(NativeRequestAuthorityError) as caught:
            _, body = admitted_native_request(_context(raw, summary), profile, expected)
            with native_chat_wire_scope(body):
                client.chat.completions.create(model="native", messages=expected["messages"], stream=True)
        assert caught.value.details["code"] == "native_request_members_mismatch"
        assert captured == []
