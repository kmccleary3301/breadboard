"""Native OpenCode owns malformed-JSON repair; ordinary runtimes stay strict."""
import copy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
import json
from pathlib import Path

from breadboard_engine.provider.contract_messages import ProviderToolCall
from breadboard_engine.provider.contract_runtime import sanitize_provider_result
from breadboard_engine.provider.contracts import (
    ProviderContractError, ProviderCorrelation, ProviderDone, ProviderExchangeRecorder,
    ProviderExchangeV2, ProviderIdentity, ProviderRequest, ProviderRuntimeContext,
)
from breadboard_engine.provider.normalizer import normalize_provider_result, normalized_result_messages
from breadboard_engine.provider.runtime import ProviderRuntimeError
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime


def invoke(raw, semantics=None, *, stream=False, recorder=None):
    item = {"id": "fc_raw", "type": "function_call", "call_id": "call_raw",
            "name": "bash", "arguments": raw, "status": "completed"}
    payload = SimpleNamespace(id="resp_raw", status="completed", output=[item],
                              usage={"input_tokens": 1, "output_tokens": 1})
    class Stream:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def __iter__(self):
            return iter([
                SimpleNamespace(type="response.output_item.added", output_index=0, item={**item, "arguments": ""}),
                SimpleNamespace(type="response.function_call_arguments.delta", item_id="fc_raw", output_index=0, delta=raw),
                SimpleNamespace(type="response.output_item.done", output_index=0, item=item),
                SimpleNamespace(type="response.completed", response=payload),
            ])
        def get_final_response(self):
            return payload
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: payload, stream=lambda **kwargs: Stream()))
    config = {"tools": {"native_semantics": semantics}} if semantics else {}
    state = SimpleNamespace(get_provider_metadata=lambda key: None, set_provider_metadata=lambda *args: None)
    context = ProviderRuntimeContext(state, config, exchange_recorder=recorder)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    return runtime.invoke(client=client, model="gpt-5.1-codex-mini", messages=[{"role": "user", "content": "Run"}],
                          tools=None, stream=stream, context=context)


@pytest.mark.parametrize("semantics", [None, "codex_0_139", "claude_code_2_1_63"])
@pytest.mark.parametrize("stream", [False, True])
def test_other_profiles_reject_malformed_json(semantics, stream):
    with pytest.raises(ProviderRuntimeError, match="Malformed Responses"):
        invoke('{"command":', semantics, stream=stream)
    with pytest.raises(ProviderContractError, match="malformed"):
        ProviderToolCall(id="call", name="bash", arguments="{")


@pytest.mark.parametrize("semantics", ["opencode_1_2_17", "oh_my_opencode_3_10_0"])
@pytest.mark.parametrize("stream", [False, True])
def test_native_raw_arguments_roundtrip_recording_and_sanitization(semantics, stream):
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(session_id="session", input_id="input", turn_id="turn"),
        provider=ProviderIdentity(provider_id="openai", runtime_id="openai_responses",
                                  route_id="openai/gpt-5.1-codex-mini", model="gpt-5.1-codex-mini"),
        request=ProviderRequest(stream=stream, messages=[{"role": "user", "content": "Run"}], tools=[]),
    )
    raw = '{"command":'
    result = sanitize_provider_result(invoke(raw, semantics, stream=stream, recorder=recorder))
    call = result.messages[0].tool_calls[0]
    assert call.arguments == call.raw_arguments == raw
    assert call.arguments_json is None and call.parsed_arguments is None
    if not stream:
        recorder.record("response_start")
        for event in normalize_provider_result(result):
            if event["type"] in {"tool_call_start", "tool_call_delta", "tool_call_end"}:
                recorder.record(event["type"], {key: value for key, value in event["payload"].items()
                                            if key in {"content_index", "message_id", "call_id", "name",
                                                       "delta", "arguments_json", "arguments", "raw_arguments"}})
    terminal = ProviderDone(output_emitted=True, finish_reason="toolUse",
                            assistant_messages=normalized_result_messages(result))
    exchange = recorder.build(terminal)
    encoded = exchange.as_dict()
    assert ProviderExchangeV2.from_dict(encoded).as_dict() == encoded
    end = next(event for event in encoded["events"] if event["kind"] == "tool_call_end")
    assert end["raw_arguments"] == raw
    assert "arguments_json" not in end and "arguments" not in end


@pytest.mark.parametrize("raw", ["", "   ", "{", "NaN", "Infinity", "-Infinity"])
def test_raw_union_is_bounded_and_exclusive(raw):
    call = ProviderToolCall(id="call", name="bash", arguments=raw, allow_raw_arguments=True)
    block = {"type": "tool_call", **call.as_dict()}
    schema = json.loads((Path(__file__).resolve().parents[2] /
                         "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json").read_text())
    validator = Draft202012Validator({"$ref": "#/$defs/tool_call_block", "$defs": schema["$defs"]})
    validator.validate(block)
    for extra in ({"arguments": None}, {"arguments_json": "{}"}, {"raw_arguments": "x" * 65537}):
        assert list(validator.iter_errors({**block, **extra}))
    for arguments in ({"arguments": "x" * 65537}, {"raw_arguments": raw, "arguments_json": "{}"}):
        with pytest.raises(ProviderContractError):
            ProviderToolCall(id="call", name="bash", allow_raw_arguments=True, **arguments)
    copied = copy.deepcopy(block)
    assert copied == block
