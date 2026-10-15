from __future__ import annotations

import pytest

from breadboard_engine.compaction import (
    CompactionContext,
    CompactionState,
    ProjectionTarget,
    settings_from_config,
)
from breadboard_engine.compaction.methods import MethodUnavailable
from breadboard_engine.compaction.remote.openai import (
    CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE,
    OpenAIResponsesCompactionPort,
    trim_remote_compaction_input_to_context_window,
)


TARGET = ProjectionTarget("openai", "responses", "gpt-5")


def _context(messages, *, state=None, context_window=800, v2=False, custom_instructions=None):
    return CompactionContext(
        messages=messages,
        state=state if state is not None else CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": v2}),
        reason="threshold",
        target=TARGET,
        context_window=context_window,
        tokens_before=100,
        custom_instructions=custom_instructions,
    )


@pytest.mark.parametrize(
    "item_type", ["compaction", "compaction_summary", "reasoning", "context_compaction"]
)
def test_trim_accounts_for_large_encrypted_native_items_without_mutating_them(item_type):
    item = {"type": item_type, "encrypted_content": "E" * 20_000}

    result = trim_remote_compaction_input_to_context_window([item], context_window=1_000)

    assert result["fits"] is False
    assert result["estimated_tokens_before"] > 1_000
    assert result["input"] == [item]


def test_second_compaction_rejects_oversized_prior_native_boundary_before_posting():
    encrypted = "E" * 20_000
    posted = []

    def http_poster(url, payload, headers):
        posted.append({"url": url, "payload": payload})
        return {"output": [{"type": "compaction", "encrypted_content": encrypted}]}

    messages = [
        {"role": "system", "content": "System policy"},
        {"role": "user", "content": "First turn"},
        {"role": "assistant", "content": "First answer"},
    ]
    state = CompactionState()
    port = OpenAIResponsesCompactionPort(http_poster=http_poster)
    first = port.compact(_context(messages, state=state, context_window=8_000))
    state.append(first, messages)

    second_messages = [
        *messages,
        {"role": "user", "content": "Second turn"},
        {"role": "assistant", "content": "Second answer"},
    ]
    with pytest.raises(MethodUnavailable):
        port.compact(_context(second_messages, state=state, context_window=1_000))

    assert len(posted) == 1
    assert first.native is not None
    assert first.native.items == ({"type": "compaction", "encrypted_content": encrypted},)


@pytest.mark.parametrize("v2", [False, True], ids=["v1", "v2"])
@pytest.mark.parametrize("instruction_source", ["active_policy", "custom_instructions"])
def test_compaction_reserves_instructions_and_trims_tool_output(v2, instruction_source):
    policy = "P" * 2_000
    history = [
        {"role": "user", "content": "Question"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_tool",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_tool", "content": "x" * 600},
    ]
    custom_instructions = policy if instruction_source == "custom_instructions" else None
    messages = history if custom_instructions else [{"role": "system", "content": policy}, *history]
    posted = []

    def http_poster(url, payload, headers):
        posted.append({"url": url, "payload": payload})
        item = {"type": "compaction", "encrypted_content": "result"}
        if v2:
            return [
                {"type": "response.output_item.done", "item": item},
                {"type": "response.completed", "response": {"usage": {"input_tokens": 1}}},
            ]
        return {"output": [item]}

    OpenAIResponsesCompactionPort(http_poster=http_poster).compact(
        _context(
            messages,
            context_window=800,
            v2=v2,
            custom_instructions=custom_instructions,
        )
    )

    assert len(posted) == 1
    payload = posted[0]["payload"]
    assert payload["instructions"] == policy
    input_items = [item for item in payload["input"] if item.get("type") != "compaction_trigger"]
    assert [item["type"] for item in input_items] == [
        "message",
        "function_call",
        "function_call_output",
    ]
    assert input_items[1] == {
        "type": "function_call",
        "call_id": "call_tool",
        "name": "read",
        "arguments": "{}",
    }
    assert input_items[2]["call_id"] == "call_tool"
    assert input_items[2]["output"] == CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE
    assert messages[-1]["content"] == "x" * 600


@pytest.mark.parametrize("v2", [False, True], ids=["v1", "v2"])
def test_instruction_budget_exhaustion_rejects_without_http(v2):
    posted = []

    def http_poster(url, payload, headers):
        posted.append({"url": url, "payload": payload})
        return {"output": [{"type": "compaction", "encrypted_content": "result"}]}

    with pytest.raises(MethodUnavailable):
        OpenAIResponsesCompactionPort(http_poster=http_poster).compact(
            _context(
                [{"role": "user", "content": "Question"}],
                context_window=800,
                v2=v2,
                custom_instructions="P" * 3_200,
            )
        )

    assert posted == []


@pytest.mark.parametrize("context_window", [None, 0], ids=["unknown", "unbounded"])
def test_unknown_or_unbounded_context_does_not_consume_instruction_budget(context_window):
    posted = []
    output = "x" * 2_400
    policy = "P" * 3_200
    messages = [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "tool_calls": [
            {"id": "call1", "function": {"name": "read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call1", "content": output},
    ]

    def http_poster(url, payload, headers):
        posted.append(payload)
        return {"output": [{"type": "compaction", "encrypted_content": "result"}]}

    OpenAIResponsesCompactionPort(http_poster=http_poster).compact(
        _context(messages, context_window=context_window, custom_instructions=policy)
    )

    assert len(posted) == 1
    assert posted[0]["instructions"] == policy
    assert posted[0]["input"][-1] == {
        "type": "function_call_output", "call_id": "call1", "output": output,
    }
