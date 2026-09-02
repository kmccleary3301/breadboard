import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from breadboard_engine.provider.contracts import (
    ProviderContractError,
    ProviderCorrelation,
    ProviderDone,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderProtocolError,
    ProviderRequest,
    encode_provider_exchange,
    normalize_content,
    normalize_usage,
    strip_provider_exchange_completion_sentinels,
)
from breadboard_engine.provider import normalize_provider_result
from breadboard_engine.provider.normalizer import normalized_result_replay
from breadboard_engine.provider.runtime import (
    ProviderResult, ProviderMessage, ProviderToolCall,
)


def test_normalize_provider_result_produces_events():
    result = ProviderResult(
        messages=[
            ProviderMessage(
                role="assistant",
                content="Hello",
                tool_calls=[
                    ProviderToolCall(id="1", name="run_tool", arguments="{}"),
                ],
                finish_reason="stop",
            )
        ],
        raw_response={},
        usage={"total_tokens": 10},
        metadata={"model": "test-model"},
        model="test-model",
    )

    events = normalize_provider_result(result)
    event_types = [event["type"] for event in events]
    assert "text" in event_types
    assert "tool_call" in event_types
    assert event_types[-1] == "finish"
@pytest.mark.parametrize(
    "fixture_name",
    [
        "provider_exchange_v2_done.json",
        "provider_exchange_v2_error.json",
        "provider_exchange_v2_cancelled.json",
    ],
)
def test_provider_exchange_v2_fixtures_validate_and_round_trip(fixture_name):
    root = Path(__file__).parents[1]
    schema = json.loads(
        (
            root / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
        ).read_text()
    )
    payload = json.loads(
        (root / "contracts/kernel/examples" / fixture_name).read_text()
    )

    Draft202012Validator(schema).validate(payload)
    assert encode_provider_exchange(payload) == payload


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("exchange_id",), " \t"),
        (("correlation", "session_id"), "\n"),
        (("provider", "route_id"), " route"),
        (("provider", "model"), "模型"),
        (("request", "tools", 0, "description"), " "),
        (("request", "messages", 2, "content", 1, "data"), "\t"),
        (("request", "messages", 3, "content", 1, "schema_version"), " "),
        (
            ("request", "messages", 3, "content", 1, "payload", "item_id"),
            "\n",
        ),
        (("events", 2, "delta"), " "),
        (("terminal", "raw_provider_finish"), "complete now"),
        (("terminal", "evidence_refs"), [" "]),
    ],
)
def test_provider_exchange_v2_schema_rejects_python_invalid_strings(
    path, invalid_value
):
    root = Path(__file__).parents[1]
    schema = json.loads(
        (
            root / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
        ).read_text()
    )
    payload = json.loads(
        (
            root / "contracts/kernel/examples/provider_exchange_v2_done.json"
        ).read_text()
    )
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    assert not Draft202012Validator(schema).is_valid(payload)


def test_provider_exchange_v2_shared_cross_language_parity_cases():
    root = Path(__file__).parents[1]
    schema = json.loads(
        (
            root / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    base = json.loads(
        (
            root / "contracts/kernel/examples/provider_exchange_v2_done.json"
        ).read_text()
    )
    cases = json.loads(
        (
            root / "tests/fixtures/provider_exchange_v2_parity_cases.json"
        ).read_text()
    )

    for case in cases["argument_cases"]:
        payload = copy.deepcopy(base)
        block = payload["request"]["messages"][3]["content"][0]
        block["arguments_json"] = case["arguments_json"]
        block["arguments"] = case["arguments"]
        if case["accepted"]:
            assert encode_provider_exchange(payload) == payload, case["name"]
        else:
            with pytest.raises(ProviderContractError):
                encode_provider_exchange(payload)
        if case["name"].startswith("unsafe_integer"):
            assert not validator.is_valid(payload), case["name"]

    for case in cases["replay_cases"]:
        payload = copy.deepcopy(base)
        payload["terminal"]["provider_replay"][0]["payload"][case["field"]] = (
            case["value"]
        )
        assert validator.is_valid(payload) is case["accepted"], case["name"]
        if case["accepted"]:
            assert encode_provider_exchange(payload) == payload, case["name"]
        else:
            with pytest.raises(ProviderContractError):
                encode_provider_exchange(payload)

    for case in cases["wire_shape_cases"]:
        payload = copy.deepcopy(base)
        target = payload
        for part in case["path"][:-1]:
            target = target[part]
        field = case["path"][-1]
        if case["operation"] == "delete":
            del target[field]
        else:
            target[field] = copy.deepcopy(case["value"])
        assert validator.is_valid(payload) is case["accepted"], case["name"]
        if case["accepted"]:
            assert encode_provider_exchange(payload) == payload, case["name"]
        else:
            with pytest.raises(ProviderContractError):
                encode_provider_exchange(payload)


def test_provider_exchange_v2_rejects_unknown_content_and_sequence_gaps():
    with pytest.raises(ProviderContractError):
        normalize_content(
            [{"type": "text", "text": "visible", "unknown_semantic": True}]
        )

    root = Path(__file__).parents[1]
    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    payload["events"][1]["sequence"] = 9
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)

    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    payload["events"].pop(1)
    for sequence, event in enumerate(payload["events"]):
        event["sequence"] = sequence
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)

    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    payload["events"].pop(3)
    for sequence, event in enumerate(payload["events"]):
        event["sequence"] = sequence
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)

    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    payload["terminal"]["assistant_messages"] = {}
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)

    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    del payload["terminal"]["assistant_messages"]
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)

    payload = json.loads(
        (root / "contracts/kernel/examples/provider_exchange_v2_done.json").read_text()
    )
    payload["terminal"]["output_emitted"] = False
    with pytest.raises(ProviderContractError):
        encode_provider_exchange(payload)


def test_provider_request_strips_only_known_transport_cache_metadata():
    request = ProviderRequest(
        stream=True,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "run",
                        "cache_control": {"type": "ephemeral", "ttl": "5m"},
                    }
                ],
            }
        ],
        tools=[],
    )

    assert request.messages == [
        {"role": "user", "content": [{"type": "text", "text": "run"}]}
    ]
    with pytest.raises(ProviderContractError):
        ProviderRequest(
            stream=True,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "run",
                            "cache_control": {
                                "type": "ephemeral",
                                "unknown": True,
                            },
                        }
                    ],
                }
            ],
            tools=[],
        )


def test_provider_request_preserves_authorized_media_reference() -> None:
    media = {
        "type": "media",
        "kind": "image",
        "uri": "attachment://sha256:" + "a" * 64,
        "mime": "image/png",
    }
    request = ProviderRequest(
        stream=True,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    media,
                ],
            }
        ],
        tools=[],
    )

    assert request.messages[0]["content"][1] == media

    root = Path(__file__).parents[1]
    schema = json.loads(
        (
            root / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
        ).read_text()
    )
    payload = json.loads(
        (
            root
            / "contracts/kernel/examples/provider_exchange_v2_done.json"
        ).read_text()
    )
    payload["request"]["messages"][1]["content"].append(media)
    Draft202012Validator(schema).validate(payload)

def test_provider_request_normalizes_anthropic_input_schema():
    description = "Read a file. " * 400
    request = ProviderRequest(
        stream=False,
        messages=[{"role": "user", "content": "run"}],
        tools=[
            {
                "name": "read",
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
    )

    assert request.tools == [
        {
            "name": "read",
            "description": description,
            "parameters": {
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "type": "object",
            },
        }
    ]

def test_provider_request_preserves_openai_strict_tool_mode():
    request = ProviderRequest(
        stream=False,
        messages=[{"role": "user", "content": "run"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "parameters": {"type": "object"},
                    "strict": True,
                },
            }
        ],
    )

    assert request.tools == [
        {
            "name": "read",
            "parameters": {"type": "object"},
            "strict": True,
        }
    ]



def test_provider_request_rejects_unknown_tool_schema_semantics():
    with pytest.raises(ProviderContractError):
        ProviderRequest(
            stream=False,
            messages=[{"role": "user", "content": "run"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "parameters": {"type": "object"},
                        "unknown_semantic": True,
                    },
                }
            ],
        )


def test_provider_usage_preserves_zero_absence_and_extensions():
    assert normalize_usage({}) == {}
    assert normalize_usage(
        {
            "input_tokens": 0,
            "output_tokens": 3,
            "provider_bucket": "standard",
        }
    ) == {
        "inputTokens": 0,
        "outputTokens": 3,
        "extensions": {"provider_bucket": "standard"},
    }

def test_provider_usage_extensions_are_bounded():
    with pytest.raises(ProviderContractError):
        normalize_usage(
            {
                "extensions": {
                    f"key-{index}": index
                    for index in range(33)
                }
            }
        )
    with pytest.raises(ProviderContractError):
        normalize_usage({"extensions": {"oversized": "x" * 4097}})
    assert normalize_usage({"extensions": {}}) == {"extensions": {}}
    with pytest.raises(ProviderContractError):
        normalize_usage({"extensions": None})
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ProviderContractError):
        normalize_usage({"extensions": {"cyclic": cyclic}})


def test_provider_reasoning_and_tool_results_are_not_dropped():
    message = ProviderMessage(
        role="assistant",
        content="answer",
        reasoning="plan",
        annotations={"reasoning_content": "plan"},
    )
    assert message.as_dict()["content"] == [
        {"type": "thinking", "text": "plan"},
        {"type": "text", "text": "answer"},
    ]
    request = ProviderRequest(
        stream=False,
        messages=[
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "plan",
            }
        ],
        tools=[],
    )
    assert request.messages[0]["content"] == [
        {"type": "thinking", "text": "plan"},
        {"type": "text", "text": "answer"},
    ]
    assert normalize_content(
        [
            {
                "type": "tool_result",
                "call_id": "call-error",
                "content": "failed safely",
                "is_error": True,
            }
        ]
    ) == [
        {
            "type": "tool_result",
            "call_id": "call-error",
            "content": "failed safely",
            "is_error": True,
        }
    ]
    with pytest.raises(ProviderContractError):
        normalize_content(
            [{"type": "tool_call", "call_id": "call-1", "name": "read"}]
        )
    assert normalize_content(
        [
            {
                "type": "tool_call",
                "call_id": "call-null",
                "name": "read",
                "arguments": None,
            }
        ]
    ) == [
        {
            "type": "tool_call",
            "call_id": "call-null",
            "name": "read",
            "arguments_json": "null",
            "arguments": None,
        }
    ]
    with pytest.raises(ProviderContractError):
        ProviderRequest(
            stream=False,
            messages=[
                {
                    "role": "assistant",
                    "content": "answer",
                    "unknown_semantic": True,
                }
            ],
            tools=[],
        )

    result = ProviderResult(
        messages=[
            ProviderMessage(
                role="assistant",
                content=[
                    {
                        "type": "tool_result",
                        "call_id": "call-1",
                        "content": "done",
                    }
                ],
            ),
            ProviderMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-2", name="read", arguments="{}"
                    )
                ],
            ),
        ],
        raw_response={},
    )
    events = normalize_provider_result(result)
    tool_result = next(event for event in events if event["type"] == "tool_result")
    tool_start = next(event for event in events if event["type"] == "tool_call_start")
    assert tool_result["payload"]["content"] == "done"
    assert tool_start["payload"]["message_id"] == "message_1"


def test_provider_result_replay_rejects_unknown_or_defaulted_semantics():
    result = ProviderResult(
        messages=[],
        raw_response={},
        provider_replay=[
            {
                "provider_id": "openai",
                "schema_version": "openai.v1",
                "replay_scope": "same_provider",
                "payload": {
                    "signature": "kept",
                    "unknown_semantic": "must-not-disappear",
                },
            }
        ],
    )
    with pytest.raises(ProviderContractError):
        normalized_result_replay(result, provider_id="openai")

    result.provider_replay = [
        {
            "provider_id": "openai",
            "payload": {"signature": "missing-required-semantics"},
        }
    ]
    with pytest.raises(ProviderContractError):
        normalized_result_replay(result, provider_id="openai")


def test_provider_exchange_recorder_captures_every_event_family_without_loss():
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1", input_id="input-1", turn_id="turn-1"
        ),
        provider=ProviderIdentity(
            provider_id="mock",
            runtime_id="mock_chat",
            route_id="mock/dev",
            model="dev",
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "run"}],
            tools=[{"name": "read", "parameters": {"type": "object"}}],
        ),
    )
    recorder.record(
        "text_start", {"content_index": 0, "item_id": "message-1"}
    )
    recorder.record(
        "text_delta",
        {"content_index": 0, "item_id": "message-1", "delta": "x"},
    )
    recorder.record(
        "text_end", {"content_index": 0, "item_id": "message-1"}
    )
    recorder.record(
        "thinking_start", {"content_index": 1, "item_id": "thinking-1"}
    )
    recorder.record(
        "thinking_delta",
        {
            "content_index": 1,
            "item_id": "thinking-1",
            "delta": "plan",
        },
    )
    recorder.record(
        "thinking_end", {"content_index": 1, "item_id": "thinking-1"}
    )
    recorder.record(
        "tool_call_start",
        {
            "content_index": 2,
            "item_id": "message-1",
            "call_id": "call-1",
            "name": "read",
        },
    )
    recorder.record(
        "tool_call_delta",
        {
            "content_index": 2,
            "item_id": "message-1",
            "call_id": "call-1",
            "arguments_delta": "{}",
        },
    )
    recorder.record(
        "tool_call_end",
        {
            "content_index": 2,
            "item_id": "message-1",
            "call_id": "call-1",
            "arguments": {},
        },
    )
    exchange = encode_provider_exchange(
        recorder.build(
            ProviderDone(
                output_emitted=True,
                finish_reason="toolUse",
                assistant_messages=[],
            )
        )
    )
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(exchange)

    assert [event["sequence"] for event in exchange["events"]] == list(
        range(len(exchange["events"]))
    )
    assert [event["kind"] for event in exchange["events"]] == [
        "response_start",
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
    ]
    assert exchange["events"][-1]["arguments_json"] == "{}"
    assert exchange["events"][-1]["arguments"] == {}


def test_provider_exchange_sentinel_scrubbing_spans_stream_delta_boundaries():
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1",
            input_id="input-1",
            turn_id="turn-1",
        ),
        provider=ProviderIdentity(
            "mock",
            "mock_chat",
            "mock/dev",
            "dev",
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "TASK COMPLETE"}],
            tools=[],
        ),
    )
    recorder.record(
        "text_start",
        {"content_index": 0, "item_id": "message-1"},
    )
    recorder.record(
        "text_delta",
        {
            "content_index": 0,
            "item_id": "message-1",
            "delta": "answer\nTASK ",
        },
    )
    recorder.record(
        "text_delta",
        {
            "content_index": 0,
            "item_id": "message-1",
            "delta": "COMPLETE\n",
        },
    )
    recorder.record(
        "text_end",
        {"content_index": 0, "item_id": "message-1"},
    )
    exchange = strip_provider_exchange_completion_sentinels(
        recorder.build(
            ProviderDone(
                output_emitted=True,
                finish_reason="stop",
                assistant_messages=[
                    {
                        "role": "assistant",
                        "message_id": "message-1",
                        "content": [
                            {
                                "type": "text",
                                "text": "answer\nTASK COMPLETE\n",
                            }
                        ],
                    }
                ],
            )
        )
    )

    assert "".join(
        event["delta"]
        for event in exchange["events"]
        if event["kind"] == "text_delta"
    ) == "answer"
    assert exchange["terminal"]["assistant_messages"][0]["content"] == [
        {"type": "text", "text": "answer"}
    ]
    assert exchange["request"]["messages"][0]["content"] == [
        {"type": "text", "text": "TASK COMPLETE"}
    ]


def test_provider_exchange_sentinel_only_output_becomes_control_only():
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1",
            input_id="input-1",
            turn_id="turn-1",
        ),
        provider=ProviderIdentity(
            "mock",
            "mock_chat",
            "mock/dev",
            "dev",
        ),
        request=ProviderRequest(stream=True, messages=[], tools=[]),
    )
    recorder.record(
        "text_start",
        {"content_index": 0, "item_id": "message-1"},
    )
    recorder.record(
        "text_delta",
        {
            "content_index": 0,
            "item_id": "message-1",
            "delta": "TASK COMPLETE\n",
        },
    )
    recorder.record(
        "text_end",
        {"content_index": 0, "item_id": "message-1"},
    )
    exchange = strip_provider_exchange_completion_sentinels(
        recorder.build(
            ProviderDone(
                output_emitted=True,
                finish_reason="stop",
                assistant_messages=[
                    {
                        "role": "assistant",
                        "message_id": "message-1",
                        "content": [
                            {"type": "text", "text": "TASK COMPLETE"}
                        ],
                    }
                ],
            )
        )
    )

    assert [
        event["kind"] for event in exchange["events"]
    ] == ["response_start", "text_start", "text_end"]
    assert exchange["terminal"]["assistant_messages"] == []
    assert exchange["terminal"]["output_emitted"] is False


def test_provider_exchange_recorder_and_replay_fail_closed():
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-1", input_id="input-1", turn_id="turn-1"
        ),
        provider=ProviderIdentity("mock", "mock_chat", "mock/dev", "dev"),
        request=ProviderRequest(stream=True, messages=[], tools=[]),
    )
    with pytest.raises(ProviderProtocolError):
        recorder.record("provider_private_event", {"delta": "must not disappear"})
    with pytest.raises(ProviderProtocolError):
        recorder.record(
            "text_delta",
            {
                "item_id": "message-1",
                "delta": "visible",
                "unknown_semantic": True,
            },
        )
    with pytest.raises(ProviderProtocolError):
        recorder.record("text_start", {"content_index": 0})
    assert [event.kind for event in recorder.events] == ["response_start"]
    with pytest.raises(ProviderContractError):
        normalize_content(
            [
                {
                    "type": "provider_replay",
                    "provider_id": "mock",
                    "schema_version": "mock.v1",
                    "replay_scope": "same_provider",
                    "payload": {"unknown_secret": "not-allowlisted"},
                }
            ]
        )
    with pytest.raises(ProviderContractError):
        normalize_content(
            [
                {
                    "type": "provider_replay",
                    "provider_id": "mock",
                    "schema_version": "mock.v1",
                    "replay_scope": "same_provider",
                    "payload": {"signature": "x" * 4097},
                }
            ]
        )


def test_provider_done_accepts_only_bounded_protocol_finish_tokens():
    terminal = ProviderDone(output_emitted=False, raw_provider_finish="tool_calls")
    assert terminal.as_dict()["raw_provider_finish"] == "tool_calls"

    with pytest.raises(ProviderContractError):
        ProviderDone(
            output_emitted=False,
            raw_provider_finish="provider returned sensitive prose",
        ).as_dict()


def test_provider_identity_rejects_noncanonical_route_text():
    with pytest.raises(ProviderContractError):
        ProviderIdentity(
            provider_id="mock",
            runtime_id="mock_chat",
            route_id="mock/private provider response",
            model="dev",
        )
