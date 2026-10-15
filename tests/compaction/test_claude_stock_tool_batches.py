"""Compare batched execution against an independent scripted stock exchange."""
import json
from types import SimpleNamespace

import pytest

from breadboard_engine.conductor.tool_executor import execute_agent_calls
from breadboard_engine.execution.claude_tools import ClaudeToolSession
from breadboard_engine.execution.stock_driver import resolve_stock_driver
from breadboard_engine.provider.adapters import AnthropicAdapter
from breadboard_engine.provider.anthropic_replay import replay_messages
from breadboard_engine.state.session_state import SessionState


MODEL = "claude-haiku-4-5-20251001"
USAGE = {"input_tokens": 20, "output_tokens": 3,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


class ScriptedStockReference(ClaudeToolSession):
    """Use the pinned CLI, but neither execute_batch nor capture_tool_results."""

    def __init__(self, workspace, calls):
        self.reference_calls = calls
        self.issued = False
        self.continuation = None
        super().__init__(workspace, MODEL, timeout=45, permission_mode="bypassPermissions")

    def respond(self, kind, body):
        assert kind == "main"
        if not self.issued:
            self.issued = True
            return [{"type": "tool_use", **call} for call in self.reference_calls]
        ids = {call["id"] for call in self.reference_calls}
        indices = [index for index, message in enumerate(body["messages"])
                   if message["role"] == "user" and isinstance(message["content"], list)
                   and any(part.get("type") == "tool_result" and part.get("tool_use_id") in ids
                           for part in message["content"])]
        assert indices
        self.continuation = body["messages"][min(indices):]
        return [{"type": "text", "text": "Reference complete."}]


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("second_tool", ["Read", "Edit"])
@pytest.mark.parametrize("continuous", [False, True])
def test_full_batch_matches_independent_stock(tmp_path, second_tool, continuous):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n")
    second.write_text("second\n")
    calls = [{"id": "toolu_batch_first", "name": "Read", "input": {"file_path": str(first)}}]
    if second_tool == "Read":
        calls.append({"id": "toolu_batch_second", "name": "Read", "input": {"file_path": str(second)}})
    else:
        calls.append({"id": "toolu_batch_second", "name": "Edit", "input": {
            "file_path": str(first), "old_string": "one", "new_string": "two"}})
    reference = ScriptedStockReference(str(tmp_path), calls)
    try:
        reference._run({"kind": "reference"}, reference.marker)
        expected = reference.continuation
        assert expected is not None
    finally:
        reference.close()
    first.write_text("one\n")
    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45,
                             permission_mode="bypassPermissions", mirror_auto_compaction=continuous)
    try:
        if continuous:
            stock.capture_context("Execute the admitted batch.")
            stock.stage_primary_response({"id": "msg_batch", "type": "message", "role": "assistant",
                "model": MODEL, "content": [{"type": "tool_use", **call} for call in calls],
                "stop_reason": "tool_use", "stop_sequence": None, "usage": USAGE})
        config = {"tools": {"native_semantics": "claude_code_2_1_63", "group_native_calls_by_turn": True}}
        state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config=config)
        state._claude_tool_session = stock
        state.set_provider_metadata("usage", USAGE)
        conductor = SimpleNamespace(config=config)
        conductor.stock_driver = resolve_stock_driver(conductor)
        parsed = [SimpleNamespace(function=call["name"], provider_name=call["name"],
                                  arguments=call["input"], call_id=call["id"]) for call in calls]

        def forbidden_individual_execution(payload):
            pytest.fail("Native batch was split into individual BB executions")

        executed, failed_at, error, metadata = execute_agent_calls(
            conductor, parsed, forbidden_individual_execution, state)
        assert failed_at == -1 and error is None
        assert metadata["executed_calls"] == 2
        actual = executed[0][1]["__claude_tool_result_messages"]
        # Safe Reads can finish in different orders in two genuine stock runs
        # (pl6.getCompletedResults, cli.js:3583). Compare native result bytes by
        # ID; cache_control is request decoration, not the tool's result mapper.
        def native_results(messages):
            return {part["tool_use_id"]: {key: value for key, value in part.items() if key != "cache_control"}
                    for message in messages for part in message["content"] if part["type"] == "tool_result"}

        actual_results, expected_results = native_results(actual), native_results(expected)
        assert actual_results.keys() == expected_results.keys()
        for call_id in actual_results:
            assert wire(actual_results[call_id]) == wire(expected_results[call_id])
        if second_tool == "Edit":
            # Unsafe Edit is serialized after Read, so the independent stock
            # exchange has identical order as well as identical result bytes.
            assert wire(actual) == wire(expected)
        canonical = AnthropicAdapter().create_tool_result_batch_message(actual)
        assert wire(replay_messages(canonical["content"])) == wire(actual)
        assert [block["call_id"] for block in canonical["content"] if block["type"] == "tool_result"] == [
            call.call_id for call, _ in executed]
        mains = [record["request"] for record in stock.records if record["kind"] == "main"]
        assert len(mains) == 2
        result_index = next(index for index, message in enumerate(mains[-1]["messages"])
                            if message["role"] == "user" and isinstance(message["content"], list)
                            and any(part.get("type") == "tool_result" and part.get("tool_use_id") in actual_results
                                    for part in message["content"]))
        assert wire(actual) == wire(mains[-1]["messages"][result_index:])
        assistant = next(message for message in reversed(mains[-1]["messages"]) if message["role"] == "assistant")
        assert [part["id"] for part in assistant["content"] if part["type"] == "tool_use"] == [call["id"] for call in calls]
        if continuous:
            assert stock.awaiting_main_request is not None
            assert stock.staged_primary_response is None
            assert not any(stock.marker in json.dumps(message) for message in mains[-1]["messages"])
            assert all("_provider_tool_batch" not in message for message in mains[-1]["messages"])
        if second_tool == "Edit":
            assert first.read_text() == "two\n"
        if continuous:
            stock.release_main_response({"kind": "answer"}, {
                "id": "msg_batch_final", "type": "message", "role": "assistant", "model": MODEL,
                "content": [{"type": "text", "text": "Batch complete."}],
                "stop_reason": "end_turn", "stop_sequence": None, "usage": USAGE,
            })
            stock.await_tool_result()
    finally:
        stock.close()


def test_group_replay_preserves_multiple_user_messages():
    messages = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "first"}]},
        {"role": "user", "content": [{"type": "text", "text": "attached stock message"},
         {"type": "tool_result", "tool_use_id": "b", "content": "second", "is_error": True,
          "cache_control": {"type": "ephemeral"}}]},
    ]
    canonical = AnthropicAdapter().create_tool_result_batch_message(messages)
    assert wire(replay_messages(canonical["content"])) == wire(messages)
    assert [part["call_id"] for part in canonical["content"][:-1]] == ["a", "b"]
