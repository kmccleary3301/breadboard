"""Project genuine stock JS serialization only into dated request DTOs."""
import copy
import json
from types import SimpleNamespace

import pytest

from breadboard_engine.compilation.context_builders.opencode_stock import (
    StockSummaryProjection, project_inflight_tool_arguments, project_inflight_tool_outputs,
)
from breadboard_engine.compaction.methods import SummaryRequest
from breadboard_engine.execution.opencode_tools import StockExecutionError
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
from tests.compaction.test_opencode_stock_tools import binary, direct_stock, plain, response


@pytest.mark.parametrize("raw", [
    ' { "description" : "Check argument order", "command" : "printf parity" } ',
    '{"description":"Check integral float","timeout":1.0,"command":"printf parity"}',
    '{"description":"Check exponent","timeout":1e3,"command":"printf parity"}',
])
def test_native_argument_projection_matches_direct_stock_and_preserves_history(plain, binary, tmp_path, raw):
    call_id = "call_arguments"
    payload = response(plain, [(call_id, "bash", json.loads(raw))])
    payload["output"][0]["arguments"] = raw
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(payload)
    native = plain.pending["body"]
    replay = {"type": "provider_replay", "provider_id": "openai",
              "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
              "payload": {"type": "function_call", "call_id": call_id, "name": "bash", "arguments": raw}}
    messages = [{"role": "assistant", "content": [replay]}]
    canonical = copy.deepcopy(messages)
    projected = project_inflight_tool_arguments(native, messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_preserve_tool_call_items": True}}})
    actual = runtime._convert_messages_to_input(projected, context=context)
    assert messages == canonical
    assert runtime._convert_messages_to_input(messages, context=context)[0]["arguments"] == raw
    assert actual[0]["arguments"] != raw
    assert list(json.loads(actual[0]["arguments"])) == list(json.loads(raw))
    plain.submit(response(plain))
    requests = []
    direct_stock(binary, plain.workspace, tmp_path / "oracle", [(call_id, "bash", json.loads(raw))],
                 captured_requests=requests, raw_arguments={call_id: raw})
    expected = next(item for item in requests[-1]["json"]["input"]
                    if item.get("type") == "function_call" and item.get("call_id") == call_id)
    assert actual == [expected]
    assert actual[0]["arguments"].encode() == expected["arguments"].encode()
    assert messages == canonical
    request = SummaryRequest(system=None, messages=(*messages, {"role": "user", "content": "Summarize."}),
                             max_tokens=None, purpose="summary", context_reason="manual")
    summary = StockSummaryProjection(SimpleNamespace(_opencode_tool_session=plain)).prepare(request)
    summary_calls = runtime._convert_messages_to_input(list(summary.messages), context=context)
    assert summary_calls[0] == expected
    assert request.messages[0] == canonical[0]
    plain.compact("Arguments remained source-serialized.", auto=False)


def test_missing_native_call_fails_without_raw_argument_fallback():
    messages = [{"role": "assistant", "content": [{"type": "provider_replay",
        "schema_version": "openai.responses.tools.v1",
        "payload": {"type": "function_call", "call_id": "call_missing", "arguments": "{}"}}]}]
    with pytest.raises(StockExecutionError, match="omitted arguments.*call_missing"):
        project_inflight_tool_arguments({"input": []}, messages)


@pytest.mark.parametrize("name,raw,repaired", [
    ("BASH", '{"command":"printf repaired","description":"Repair uppercase name"}', "bash"),
    ("bash", "{}", "bash"),
    ("bash", '{"command":', "invalid"),
])
def test_stock_repairs_calls_and_continues_with_exact_native_result(plain, binary, tmp_path, name, raw, repaired):
    call_id = "call_repair"
    call = (call_id, name, {})
    payload = response(plain, [call])
    payload["output"][0]["arguments"] = raw
    plain.user([{"type": "text", "text": "Execute the approved operation."}])
    plain.submit(payload)
    actual_result = plain.result(call_id)["__mvi_text_output"]
    native = plain.pending["body"]
    replay = {"type": "provider_replay", "provider_id": "openai",
              "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
              "payload": {"type": "function_call", "call_id": call_id, "name": name, "arguments": raw}}
    messages = [{"role": "assistant", "content": [replay]}]
    canonical = copy.deepcopy(messages)
    projected = project_inflight_tool_arguments(native, messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_preserve_tool_call_items": True}}})
    compiled = runtime._convert_messages_to_input(projected, context=context)
    assert compiled[0]["name"] == repaired
    assert messages == canonical
    plain.submit(response(plain, text="Repaired call complete."))
    requests = []
    expected = direct_stock(binary, plain.workspace, tmp_path / "oracle", [call],
                            captured_requests=requests, raw_arguments={call_id: raw})
    assert actual_result == expected[call_id]
    source_call = next(item for item in requests[-1]["json"]["input"]
                       if item.get("type") == "function_call" and item.get("call_id") == call_id)
    assert compiled == [source_call]
    assert messages == canonical


@pytest.mark.parametrize("shape", ["scalar", "block", "replay"])
def test_native_output_projection_preserves_canonical_history_and_unowned_outputs(shape):
    native = {"input": [
        {"type": "function_call_output", "call_id": "owned", "output": "[Old tool result content cleared]"},
        {"type": "function_call_output", "call_id": "native_only", "output": "Never import native history"},
        {"role": "user", "content": "Summarize the source-owned checkpoint."},
    ]}
    if shape == "scalar":
        owned = {"role": "tool", "tool_call_id": "owned", "content": "Canonical original"}
    elif shape == "block":
        owned = {"role": "tool_result", "content": [
            {"type": "tool_result", "call_id": "owned", "content": "Canonical original"}]}
    else:
        owned = {"role": "tool", "content": [{"type": "provider_replay", "provider_id": "openai",
            "schema_version": "openai.responses.tool_output.v1", "replay_scope": "same_provider",
            "payload": {"type": "function_call_output", "call_id": "owned", "output": "Canonical original"}}]}
    messages = [owned, {"role": "tool", "tool_call_id": "bb_only", "content": "BB-only result"}]
    canonical = copy.deepcopy(messages)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_tool_output_items": True}}})
    projected = project_inflight_tool_outputs(native, messages)
    assert runtime._convert_messages_to_input(projected, context=context) == [
        native["input"][0], {"type": "function_call_output", "call_id": "bb_only", "output": "BB-only result"}]
    assert messages == canonical
    assert projected[1] is messages[1]
    session = SimpleNamespace(compaction_prompt=lambda **kwargs: native)
    request = SummaryRequest(system=None, messages=(*messages, {"role": "user", "content": "Summarize."}),
                             max_tokens=None, purpose="summary", context_reason="threshold")
    summary = StockSummaryProjection(SimpleNamespace(_opencode_tool_session=session)).prepare(request)
    assert runtime._convert_messages_to_input(list(summary.messages[:-1]), context=context) == (
        runtime._convert_messages_to_input(projected, context=context))
    assert messages == canonical and request.messages[0] == canonical[0]
