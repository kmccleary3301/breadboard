from __future__ import annotations

import json
import pytest
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
    state = PiSemanticsState(task="read", model_id="model-a", provider="openai")
    result = admit_and_execute(state, response("read", '{"path":123}', content=None, fragments=fragments), tmp_path)
    assert result.results[0].is_error
    assert "ENOENT" in result.results[0].content
    assert result.assistant["content"][0]["text"] == "Streaming "


def test_native_edit_prepare_and_parallel_order(tmp_path: Path):
    (tmp_path / "seed.txt").write_text("old\n")
    edited = execute_pi_tool("edit", {"path": "seed.txt", "oldText": "old", "newText": "new"}, tmp_path)
    assert not edited.is_error
    assert (tmp_path / "seed.txt").read_text() == "new\n"
    state = PiSemanticsState(task="parallel", model_id="model-a", provider="openai")
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
    state = PiSemanticsState(task="cap", model_id="model-a", provider="openai")
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
    assert parse_streaming_json("[]") == []
    assert parse_streaming_json("42") == 42
    assert parse_streaming_json("null") is None
    assert parse_streaming_json('{"outer":{"command":"echo "oops""},"other":1}') == {
        "outer": {"command": "echo "}
    }
    assert parse_streaming_json('{"n":0.') == {}
    assert parse_streaming_json('"recovered\\') == "recovered"


def test_tool_execution_preserves_non_object_arguments(tmp_path: Path):
    r1 = execute_pi_tool("read", [], tmp_path)
    assert r1.is_error
    assert 'Validation failed for tool "read":' in r1.content
    assert "- root: must be object" in r1.content
    assert "Received arguments:\n[]" in r1.content

    r2 = execute_pi_tool("bash", 42, tmp_path)
    assert r2.is_error
    assert 'Validation failed for tool "bash":' in r2.content

    r3 = execute_pi_tool("write", None, tmp_path)
    assert r3.is_error
    assert 'Validation failed for tool "write":' in r3.content
def test_receiver_argument_with_unescaped_shell_quotes_matches_pinned_partial_json() -> None:
    receiver_arguments = (
        r'''{"command":"printf 'cwd='; pwd; printf 'state=%s\n' "${PI_CAPTURE_STATE-unset}""}'''
    )
    assert parse_streaming_json(receiver_arguments) == {
        "command": "printf 'cwd='; pwd; printf 'state=%s\n' ",
    }


def test_parse_streaming_json_differential_against_pinned_node() -> None:
    import os
    import shutil
    import subprocess
    import pytest

    node_modules = os.environ.get(
        "PI_CODING_AGENT_NODE_MODULES", "/tmp/pi-node-0731/node_modules"
    )
    pi_ai_parser = Path(node_modules) / "@mariozechner/pi-ai/dist/utils/json-parse.js"
    node_bin = shutil.which("node") or (
        "/Users/kylemccleary/.nvm/versions/node/v26.3.0/bin/node"
        if Path("/Users/kylemccleary/.nvm/versions/node/v26.3.0/bin/node").exists()
        else None
    )

    if not node_bin or not Path(node_bin).exists():
        pytest.skip("Node executable is not installed or available in PATH")
    if not pi_ai_parser.is_file():
        pytest.skip(f"Pinned pi-ai parser module is absent at {pi_ai_parser}")

    packet_c18adae0 = (
        r'''{"command":"printf 'cwd='; pwd; printf 'state=%s\n' "${PI_CAPTURE_STATE-unset}""}'''
    )

    corpus: list[str] = [
        packet_c18adae0,
        # leading non-objects:
        "\"a string\"",
        "[1, 2, 3]",
        "123",
        "true",
        "false",
        "null",
        "   ",
        "",
        # nested objects and arrays:
        '{"a": [1, {"b": [2, 3, {"c": "d"}]}]}',
        '{"nested": {"inner": {"deep": true}}}',
        # unescaped inner quotes:
        '{"text": "hello "world" test"}',
        '{"cmd": "echo "foo" bar"}',
        # trailing commas:
        '{"a": 1,}',
        '{"a": 1, "b": 2,}',
        '[1, 2, 3,]',
        '{"arr": [1, 2,], "obj": {"k": "v",},}',
        # numbers cut mid-token:
        '{"num": 123.',
        '{"num": 123e',
        '{"num": 123e+',
        '{"num": -',
        '-',
        '-123',
        '{"num": -123.',
        # true/false/null cut mid-token:
        '{"val": t',
        '{"val": tr',
        '{"val": tru',
        '{"val": f',
        '{"val": fa',
        '{"val": fal',
        '{"val": fals',
        '{"val": n',
        '{"val": nu',
        '{"val": nul',
        '[t, f, n]',
        '[tr, fal, nul]',
        # unicode escapes cut mid-escape:
        '{"text": "hello \\u1"',
        '{"text": "hello \\u12"',
        '{"text": "hello \\u123"',
        '{"text": "hello \\u1234"',
        '{"text": "hello \\u12',
        '{"text": "hello \\',
        # Non-objects (arrays, numbers, null):
        "[]",
        "42",
        "null",
        # Unescaped inner quotes and partial structures:
        '{"outer":{"command":"echo "oops""},"other":1}',
        '{"n":0.',
        '"recovered\\',
    ]

    diff_json = Path("/tmp/e4-sol-review-w1-2-differential.json")
    if diff_json.is_file():
        try:
            diff_data = json.loads(diff_json.read_text(encoding="utf-8"))
            for item in diff_data.get("all_mismatches", []):
                if isinstance(item, dict) and "input" in item:
                    corpus.append(item["input"])
        except Exception:
            pass
    # Truncated prefixes of every packet argument string at every byte offset
    for i in range(len(packet_c18adae0) + 1):
        corpus.append(packet_c18adae0[:i])

    other_packet_args = [
        '{"path": "/tmp/test.txt", "content": "line1\\nline2"}',
        '{"command": "git commit -m \\"initial\\"", "options": {"timeout": 30}}',
    ]
    for s in other_packet_args:
        for i in range(len(s) + 1):
            corpus.append(s[:i])

    node_script = f"""
    const fs = require('fs');
    const {{ parseStreamingJson }} = require({json.dumps(str(pi_ai_parser))});
    const inputs = JSON.parse(fs.readFileSync(0, 'utf-8'));
    const outputs = inputs.map(input => {{
        try {{
            const res = parseStreamingJson(input);
            return {{ ok: true, val: res }};
        }} catch (e) {{
            return {{ ok: false, err: e.message }};
        }}
    }});
    fs.writeFileSync(1, JSON.stringify(outputs));
    """

    env = dict(os.environ)
    env["NODE_PATH"] = node_modules
    proc = subprocess.run(
        [node_bin, "-e", node_script],
        input=json.dumps(corpus),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    node_results = json.loads(proc.stdout)
    assert len(node_results) == len(corpus)

    from breadboard.rl.harness.pi_native_tools import parse_streaming_json_batch

    py_results = parse_streaming_json_batch(corpus)
    for idx, (candidate, node_res, py_res) in enumerate(zip(corpus, node_results, py_results)):
        assert node_res["ok"], f"Node parseStreamingJson threw: {node_res.get('err')}"
        assert py_res == node_res["val"], (
            f"Differential mismatch at index {idx} for candidate {candidate!r}: "
            f"python={py_res!r} != node={node_res['val']!r}"
        )


def test_argument_parsing_fails_closed_without_pinned_worker(monkeypatch) -> None:
    from breadboard.rl.harness.pi_native_tools import PiNativeWorkerError

    monkeypatch.setenv("PI_NODE", "/usr/bin/false")
    with pytest.raises(PiNativeWorkerError):
        parse_streaming_json('{"path":"x"}')


def test_argument_parsing_preserves_lone_surrogates() -> None:
    assert parse_streaming_json('{"a":"\ud83d"}') == {"a": "\ud83d"}
