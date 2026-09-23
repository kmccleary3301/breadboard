from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.harness.runners.pi_semantics import (
    PiSemanticsState,
    execute_pi_tool,
    parse_streaming_json,
    prepare_edit_arguments,
)
from breadboard_engine.provider.native_response import NativeProviderResponse, NativeStreamFragment, NativeToolCall


def response(name: str, arguments: str, *, finish: str = "tool_calls", content: str | None = None, fragments=()):
    return NativeProviderResponse("binding", "request", "response", "model", content, finish, (NativeToolCall("call", name, arguments),), stream_fragments=tuple(fragments))


def test_stream_fragments_reconstruct_and_coerce_path(tmp_path: Path):
    fragments = (
        NativeStreamFragment("content", 0, "Streaming "),
        NativeStreamFragment("tool_arguments", 1, '{"path":', "call", "read"),
        NativeStreamFragment("tool_arguments", 2, "123}", "call", "read"),
    )
    state = PiSemanticsState(task="read", cwd=tmp_path)
    result = state.consume_response(response("read", "", content=None, fragments=fragments))
    assert result.calls[0].arguments == {"path": "123"}
    assert result.results[0].is_error
    assert "ENOENT" in result.results[0].content
    assert result.assistant["content"][0]["text"] == "Streaming "


def test_prepare_edit_arguments_and_parallel_order(tmp_path: Path):
    (tmp_path / "seed.txt").write_text("old\n")
    prepared = prepare_edit_arguments({"path": "seed.txt", "edits": '[{"oldText":"old","newText":"new"}]'})
    assert prepared["edits"] == [{"oldText": "old", "newText": "new"}]
    state = PiSemanticsState(task="parallel", cwd=tmp_path)
    result = state.consume_response(
        NativeProviderResponse(
            "binding", "request", "response", "model", "batch", "tool_calls",
            (
                NativeToolCall("a", "write", json.dumps({"path": "a.txt", "content": "A\n"})),
                NativeToolCall("bad", "edit", json.dumps({"path": "missing", "edits": []})),
                NativeToolCall("c", "write", json.dumps({"path": "c.txt", "content": "C\n"})),
            ),
        )
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


def test_eighth_request_then_ninth_stream_attempt_is_local_error(tmp_path: Path):
    calls = []
    responses = [response("bash", json.dumps({"command": f"printf cap-{i}"})) for i in range(8)]
    state = PiSemanticsState(task="cap", cwd=tmp_path)
    trace = state.run_episode(responses)
    assert trace["request_count"] == 8
    assert trace["stream_fn_issued"] == 9
    assert trace["termination"]["native_stop_reason"] == "error"
    assert trace["messages"][-1]["stopReason"] == "error"
    assert not calls


def test_partial_json_repair_and_plain_tools():
    assert parse_streaming_json('{"path":"x') == {"path": "x"}
    assert parse_streaming_json('{"path":123}') == {"path": 123}
