from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.harness.pi_native_tools import dispatch_native_tools
from breadboard.rl.harness.runners.pi_semantics import (
    PiResponseResult,
    PiSemanticsState,
    execute_pi_tool,
    parse_streaming_json,
)
from breadboard_engine.provider.native_response import NativeProviderResponse, NativeStreamFragment, NativeToolCall


def response(name: str, arguments: str, *, finish: str = "tool_calls", content: str | None = None, fragments=()):
    return NativeProviderResponse("binding", "request", "response", "model", content, finish, (NativeToolCall("call", name, arguments),), stream_fragments=tuple(fragments))


def admit_and_execute(state: PiSemanticsState, native: NativeProviderResponse, cwd: Path) -> PiResponseResult:
    """Drive one turn in the Conductor's order: admit, prepare, execute, commit."""
    assert state.begin_query() is None
    prepared = state.prepare_response(native)
    raw_results = dispatch_native_tools(
        [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in prepared.calls],
        cwd=cwd,
        image_delivery=state.image_delivery,
    )
    results = state.commit_tool_results(prepared.calls, raw_results)
    return PiResponseResult(prepared.assistant, prepared.calls, results, prepared.stop_reason, prepared.quiescent)


def test_stream_fragments_reconstruct_and_native_validation(tmp_path: Path):
    fragments = (
        NativeStreamFragment("content", 0, "Streaming "),
        NativeStreamFragment("tool_arguments", 1, '{"path":', "call", "read"),
        NativeStreamFragment("tool_arguments", 2, "123}", "call", "read"),
    )
    state = PiSemanticsState(task="read")
    result = admit_and_execute(state, response("read", '{"path":123}', content=None, fragments=fragments), tmp_path)
    assert result.results[0].is_error
    assert "ENOENT" in result.results[0].content
    assert result.assistant["content"][0]["text"] == "Streaming "


def test_native_edit_prepare_and_parallel_order(tmp_path: Path):
    (tmp_path / "seed.txt").write_text("old\n")
    edited = execute_pi_tool("edit", {"path": "seed.txt", "oldText": "old", "newText": "new"}, tmp_path)
    assert not edited.is_error
    assert (tmp_path / "seed.txt").read_text() == "new\n"
    state = PiSemanticsState(task="parallel")
    result = admit_and_execute(
        state,
        NativeProviderResponse(
            "binding", "request", "response", "model", "batch", "tool_calls",
            (
                NativeToolCall("a", "write", json.dumps({"path": "a.txt", "content": "A\n"})),
                NativeToolCall("bad", "edit", json.dumps({"path": "missing", "edits": []})),
                NativeToolCall("c", "write", json.dumps({"path": "c.txt", "content": "C\n"})),
            ),
        ),
        tmp_path,
    )
    assert [item.call_id for item in result.results] == ["a", "bad", "c"]
    assert result.results[1].is_error
    assert (tmp_path / "a.txt").read_text() == "A\n"
    assert (tmp_path / "c.txt").read_text() == "C\n"


def test_bash_tail_temp_file_and_nonzero_text(tmp_path: Path):
    output = execute_pi_tool("bash", {"command": "for i in $(seq 1 2100); do echo line-$i; done"}, tmp_path)
    assert not output.is_error
    assert output.details.get("fullOutputPath")
    assert "line-2100" in output.content
    failed = execute_pi_tool("bash", {"command": "printf before; exit 7"}, tmp_path)
    assert failed.is_error
    assert "Command exited with code 7" in failed.content


def test_eighth_request_then_ninth_stream_attempt_is_local_error():
    state = PiSemanticsState(task="cap")
    for index in range(8):
        assert state.begin_query() is None
        prepared = state.prepare_response(response("bash", json.dumps({"command": f"printf cap-{index}"})))
        state.commit_tool_results(prepared.calls, [{"content": f"cap-{index}"}])
    terminal = state.begin_query()
    assert terminal is not None
    assert state.request_count == 8
    assert state.stream_fn_issued == 9
    assert [record.sent for record in state.request_records] == [True] * 8 + [False]
    assert state.exit_status == "RequestLimitExceeded"
    assert state.native_stop_reason == "error"
    assert state.messages[-1]["stopReason"] == "error"


def test_partial_json_repair_and_plain_tools():
    assert parse_streaming_json('{"path":"x') == {"path": "x"}
    assert parse_streaming_json('{"path":123}') == {"path": 123}


def test_receiver_argument_with_unescaped_shell_quotes_matches_pinned_partial_json() -> None:
    receiver_arguments = (
        r'''{"command":"printf 'cwd='; pwd; printf 'state=%s\n' "${PI_CAPTURE_STATE-unset}""}'''
    )
    assert parse_streaming_json(receiver_arguments) == {
        "command": "printf 'cwd='; pwd; printf 'state=%s\n' ",
    }
