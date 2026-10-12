from copy import deepcopy
from breadboard_engine.provider.contract_wire import canonical_json as wire_json
import json
from breadboard_engine.compaction_lanes.request_bytes import project_guarded_values
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from breadboard_engine.compaction_lanes.omp18_lane import SCENARIOS, guarded_summary_fields, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.skipif(not os.environ.get("BB_OMP_TEST_SOURCE_ROOT"), reason="pinned OMP source is unavailable")
def test_stock_sdk_compiled_conductor_parity(tmp_path: Path, scenario: str) -> None:
    report = run_scenario(tmp_path, scenario)
    assert report["diffs"] == []
    assert report["request_counts"]["stock"] == report["request_counts"]["bb"]
    minimum = 2 if scenario == "long_session" else 1
    assert report["compaction_counts"]["stock"] >= minimum
    assert report["compaction_counts"]["stock"] == report["compaction_counts"]["bb"]
    finalized = [p for p in report["bb"]["phases"] if p["result"].get("kind") == "compaction_finalized"]
    assert finalized
    initialized = next(p for p in report["bb"]["phases"] if p["operation"] == "initialize")
    assert initialized["payload"]["compaction"] is True
    assert initialized["payload"]["model_config"]["contextWindow"] == 65536
    assert all(p["payload"]["context_window"] == 65536 for p in report["bb"]["phases"] if p["operation"] == "prepare_compaction")
    if scenario == "final_over_threshold":
        prepared = [p for p in report["bb"]["phases"] if p["result"].get("kind") == "compaction_prepared"]
        assert prepared[0]["payload"]["checkpoint"] == "agent_end"
        assert prepared[0]["result"]["preparation"]["method"] == "handoff"
        assert "methodOrder" not in prepared[0]["payload"].get("settings", {})
    if scenario == "large_tool_batch":
        assert any(p["payload"].get("checkpoint") == "before_request" and p["result"].get("kind") == "compaction_prepared" for p in report["bb"]["phases"])
    if scenario == "overflow":
        assert any(p["payload"].get("reason") == "overflow" for p in report["bb"]["phases"])
        failures = [p for p in report["bb"]["phases"] if p["operation"] == "project_provider_failure"]
        assert failures
        assert all(p["payload"]["provider_request_duration_ms"] >= 0 for p in failures)
        assert all(p["result"]["message"]["duration"] == p["payload"]["provider_request_duration_ms"]
                   for p in failures)
    if scenario == "long_session":
        prepared = [p for p in report["bb"]["phases"] if p["result"].get("kind") == "compaction_prepared"]
        committed_history = finalized[0]["result"]["messages"]
        assert prepared[1]["payload"]["messages"][:len(committed_history)] == committed_history
        assert len(prepared[1]["payload"]["messages"]) > len(committed_history)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("side,field,value", [
    ("stock", "tools", []), ("bb", "tools", [{"name": "wrong"}]),
    ("stock", "max_completion_tokens", 1), ("bb", "max_completion_tokens", 1),
    ("stock", "tool_choice", "auto"), ("bb", "tool_choice", "auto"),
])
def test_summary_deviation_guard_rejects_wrong_values(side: str, field: str, value: object) -> None:
    main = {"tools": [{"name": "read"}], "max_completion_tokens": 2048}
    stock = {"max_completion_tokens": 13107}
    bb = deepcopy(main)
    deviations = ["compaction_summary_episode_tools", "compaction_summary_max_tokens"]
    assert guarded_summary_fields(stock, bb, main, 13107, deviations) == {"tools", "max_completion_tokens"}
    (stock if side == "stock" else bb)[field] = value
    with pytest.raises(ValueError):
        guarded_summary_fields(stock, bb, main, 13107, deviations)


def test_raw_field_removal_preserves_other_wire_bytes() -> None:
    raw = b'{"model":"m", "tools":[{"name":"read"}],"tool_choice":"none","max_completion_tokens":2048,"messages":[{"content":"a,b"}]}'
    assert project_guarded_values(raw, {key: value for key, value in json.loads(raw).items() if key not in {"tools", "max_completion_tokens"}}) == b'{"model":"m","tool_choice":"none","messages":[{"content":"a,b"}]}'
    assert project_guarded_values(raw, {key: value for key, value in json.loads(raw).items() if key not in set()}) == raw


@pytest.mark.parametrize("side,field,value", [
    ("stock", "tools", []), ("bb", "tools", []),
    ("stock", "max_completion_tokens", 13107), ("bb", "max_completion_tokens", 1),
    ("stock", "tool_choice", "auto"), ("bb", "tool_choice", "auto"),
])
def test_handoff_guard_retains_equal_fields_and_rejects_changes(side, field, value) -> None:
    main = {"tools": [{"name": "read"}], "max_completion_tokens": 2048}
    stock = {**deepcopy(main), "tool_choice": "none"}
    bb = deepcopy(stock)
    deviations = ["compaction_summary_episode_tools", "compaction_summary_max_tokens"]
    assert guarded_summary_fields(stock, bb, main, None, deviations) == set()
    (stock if side == "stock" else bb)[field] = value
    with pytest.raises(ValueError):
        guarded_summary_fields(stock, bb, main, None, deviations)


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("BB_OMP_TEST_BUN"), reason="Darwin Bun is required")
@pytest.mark.parametrize("output,status", [
    ("", 0), ("malformed row", 0), ("1 0 1 Ss\\n1 0 1 Ss", 0),
    ("999999999999999999 1 1 S", 0), ("1 0 1 Ss", 1),
])
def test_darwin_process_inspection_fails_closed(output: str, status: int) -> None:
    from breadboard_engine.compaction_lanes.omp18_lane import ROOT
    script = """
    const input = JSON.parse(await Bun.stdin.text());
    const { processTable } = await import(input.module);
    Bun.spawn = (argv, options) => {
      if (JSON.stringify(argv) !== JSON.stringify(["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="]))
        throw new Error("non-fixed process argv");
      return {stdout: new Response(input.output).body, stderr: new Response("failed").body,
        exited: Promise.resolve(input.status)};
    };
    try { await processTable(); process.exit(2); }
    catch (error) { await Bun.write(Bun.stdout, String(error)); }
    """
    result = subprocess.run([os.environ["BB_OMP_TEST_BUN"], "-e", script],
        input=json.dumps({"output": output, "status": status,
                          "module": (ROOT / "breadboard/rl/harness/runners/omp_18_1_17/tool_session.ts").as_uri()}),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "process table" in result.stdout


@pytest.mark.skipif(not os.environ.get("BB_OMP_TEST_BUN"), reason="Bun is required")
def test_native_rpc_writer_preserves_large_multibyte_frames() -> None:
    from breadboard_engine.compaction_lanes.omp18_lane import ROOT
    script = """
    const input = JSON.parse(await Bun.stdin.text());
    const { writeFrame } = await import(input.module);
    await writeFrame(input.payload);
    await writeFrame({complete: true});
    """
    payload = {"text": "é—" * 65536}
    result = subprocess.run([os.environ["BB_OMP_TEST_BUN"], "-e", script],
        input=json.dumps({"payload": payload,
                          "module": (ROOT / "breadboard/rl/harness/runners/omp_18_1_17/rpc_frames.ts").as_uri()}).encode(),
        capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    length = int.from_bytes(result.stdout[:4], "big")
    assert length > 65536
    assert json.loads(result.stdout[4:4 + length]) == payload
    remaining = result.stdout[4 + length:]
    assert int.from_bytes(remaining[:4], "big") == len(remaining) - 4
    assert json.loads(remaining[4:]) == {"complete": True}


@pytest.mark.parametrize("handoff", [False, True])
def test_summary_guard_rejects_boolean_integer_schema_equivalence(handoff: bool) -> None:
    main = {"tools": [{"additionalProperties": False}], "max_completion_tokens": 2048}
    stock = {**deepcopy(main), "tool_choice": "none"} if handoff else {"max_completion_tokens": 13107}
    bb = deepcopy(stock) if handoff else deepcopy(main)
    bb["tools"] = [{"additionalProperties": 0}]
    with pytest.raises(ValueError):
        guarded_summary_fields(stock, bb, main, None if handoff else 13107,
            ["compaction_summary_episode_tools", "compaction_summary_max_tokens"])


@pytest.mark.parametrize("role", ["tool", "assistant"])
def test_elapsed_time_normalization_is_tool_content_only(role: str) -> None:
    from breadboard_engine.compaction_lanes.omp18_lane import compare_requests
    call = {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_bash", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]}
    stock = {"tools": [], "messages": [call, {"role": role, "tool_call_id": "call_bash", "content": "Record A\nWall time: 0.12 seconds"}]}
    bb = {"tools": [], "messages": [deepcopy(call), {"role": role, "tool_call_id": "call_bash", "content": "Record A\nWall time: 1.23 seconds"}]}
    def recording(body):
        return [{"json": body, "raw_body_bytes": wire_json(body).encode("utf-8").hex()}]
    substitutions = []
    diffs = compare_requests(recording(stock), recording(bb), [], [], timing_substitutions=substitutions)
    assert bool(diffs) == (role != "tool")
    assert {key: substitutions[0][key] for key in ("index", "stock", "bb")} == {
        "index": 0, "stock": int(role == "tool"), "bb": int(role == "tool")}
    assert substitutions[0]["raw_difference"]["index"] == 0
    if role == "tool":
        assert substitutions[0]["names"] == ["omp_native_bash_elapsed_time"]
        assert substitutions[0]["changes"]["stock"][0]["path"] == "$/messages/1/content"
    if role == "tool":
        bb["messages"][1]["content"] = "Record B\nWall time: 1.23 seconds"
        assert compare_requests(recording(stock), recording(bb), [], [])[0]["type"] == "byte_diff"


def _clamped_bash_record(elapsed: str, arguments: str = "{}") -> list[dict]:
    body = {"tools": [], "messages": [
        {"role": "assistant", "tool_calls": [{"id": "bash-1", "type": "function",
            "function": {"name": "bash", "arguments": arguments}}]},
        {"role": "tool", "tool_call_id": "bash-1",
         "content": "Record A\n\nWall time: " + elapsed + " seconds\n"
                    "Timeout clamped to 30s (requested 300s; global tools.maxTimeout ceiling 30s)."},
    ]}
    return [{"json": body, "raw_body_bytes": wire_json(body).encode().hex()}]


@pytest.mark.parametrize("arguments", ["{}", '{"timeout":300}'])
def test_elapsed_guard_preserves_equal_source_timeout_clamp_trailer(arguments):
    from breadboard_engine.compaction_lanes.omp18_lane import compare_requests
    stock = _clamped_bash_record("0.57", arguments)
    bb = _clamped_bash_record("0.67", arguments)
    substitutions = []
    assert compare_requests(stock, bb, [], [], timing_substitutions=substitutions) == []
    evidence = substitutions[0]
    assert evidence["raw_difference"]["index"] == 0
    assert evidence["source_commit"] == "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
    assert evidence["names"] == ["omp_native_bash_elapsed_time"]
    for side in ("stock", "bb"):
        change = evidence["changes"][side][0]
        assert change["path"] == "$/messages/1/content"
        assert change["original"].split("\n")[-1] == change["projected"].split("\n")[-1]
        assert change["projected"] == "Record A\n\nWall time: <elapsed> seconds\n" + change["original"].split("\n")[-1]


@pytest.mark.parametrize("variant", [
    "arbitrary_suffix", "duplicate_header", "embedded_header", "changed_trailer",
    "wrong_requested", "wrong_ceiling", "wrong_call_timeout", "duplicate_call",
    "wrong_tool", "ambiguous_arguments",
])
def test_elapsed_guard_rejects_ambiguous_or_unadmitted_clamp_boundaries(variant):
    from breadboard_engine.compaction_lanes.omp18_lane import compare_requests
    stock, bb = _clamped_bash_record("0.57"), _clamped_bash_record("0.67")
    for records in (stock, bb):
        body = records[0]["json"]
        content = body["messages"][1]["content"]
        if variant == "arbitrary_suffix":
            content += "\nextra"
        elif variant == "duplicate_header":
            content = "Wall time: 7.89 seconds\n" + content
        elif variant == "embedded_header":
            content = content.replace("Wall time: ", "embedded Wall time: ")
        elif variant == "wrong_requested":
            content = content.replace("requested 300s", "requested 301s")
        elif variant == "wrong_ceiling":
            content = content.replace("ceiling 30s", "ceiling 31s")
        elif variant == "wrong_call_timeout":
            body["messages"][0]["tool_calls"][0]["function"]["arguments"] = '{"timeout":29}'
        elif variant == "duplicate_call":
            body["messages"][0]["tool_calls"] *= 2
        elif variant == "wrong_tool":
            body["messages"][0]["tool_calls"][0]["function"]["name"] = "read"
        elif variant == "ambiguous_arguments":
            body["messages"][0]["tool_calls"][0]["function"]["arguments"] = '{"timeout":29,"timeout":300}'
        elif variant == "changed_trailer" and records is bb:
            content += " changed"
        body["messages"][1]["content"] = content
        records[0]["raw_body_bytes"] = wire_json(body).encode().hex()
    assert compare_requests(stock, bb, [], [])


@pytest.mark.parametrize("equal_elapsed", [False, True])
@pytest.mark.parametrize("slot", ["Record A", "Timeout clamped"])
def test_elapsed_guard_does_not_hide_other_content_or_trailer_wire_escapes(equal_elapsed, slot):
    from breadboard_engine.compaction_lanes.omp18_lane import compare_requests
    stock = _clamped_bash_record("0.57")
    bb = _clamped_bash_record("0.57" if equal_elapsed else "0.67")
    raw = bytes.fromhex(bb[0]["raw_body_bytes"]).decode()
    raw = raw.replace(slot, slot.replace(" ", "\\u0020", 1))
    bb[0]["raw_body_bytes"] = raw.encode().hex()
    assert json.loads(raw) == bb[0]["json"]
    assert stock[0]["raw_body_bytes"] != bb[0]["raw_body_bytes"]
    assert compare_requests(stock, bb, [], [])


def test_comparator_guards_stock_handoff_when_candidate_choice_is_wrong() -> None:
    from breadboard_engine.compaction_lanes.omp18_lane import compare_requests

    stock = {"tools": [{"name": "read"}], "max_completion_tokens": 2048, "tool_choice": "none"}
    bb = {**deepcopy(stock), "tool_choice": "auto"}
    def recording(body):
        return [{"json": body, "raw_body_bytes": wire_json(body).encode("utf-8").hex()}]
    assert compare_requests(recording(stock), recording(bb), [], []) == [{
        "index": 0, "type": "deviation_guard", "message": "Handoff tool_choice differs from stock",
    }]


@pytest.mark.skipif(not os.environ.get("BB_OMP_TEST_SOURCE_ROOT"), reason="pinned OMP source is unavailable")
def test_production_conductor_preserves_failed_rescue_without_counting_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard_engine.compaction_lanes import omp18_lane as lane

    monkeypatch.setattr(lane, "WINDOW", 4000)
    usage = {"input_tokens": 1000, "output_tokens": 30, "total_tokens": 1030}
    scripted = [
        {"type": "response", "tool_calls": [{
            "name": "read", "call_id": "read-0-0",
            "arguments": json.dumps({"i": "Reading numbered records", "path": "records-0-0.txt"}),
        }], "usage": usage},
        {"type": "text", "text": "Inspection complete.", "usage": usage},
    ]
    # Only the recording provider is scripted; both real harnesses retain
    # their default method order and use the same small bound model window.
    monkeypatch.setattr(lane, "scenario_script", lambda scenario: scripted)
    report = lane.run_scenario(tmp_path, "rescue_dead_end")
    rewrites = [p for p in report["bb"]["phases"] if p["result"].get("history_rewritten") is True]
    assert rewrites
    assert all(p["result"]["kind"] == "compaction_unavailable" for p in rewrites)
    assert report["request_counts"] == {"stock": 2, "bb": 2}
    assert report["compaction_counts"] == {"stock": 0, "bb": 0}
    assert report["diffs"] == []
    assert any(p["operation"] == "project_request" and
               p["payload"]["messages"] == rewrites[0]["result"]["messages"]
               for p in report["bb"]["phases"])
    original = (tmp_path / "workspace" / "records-0-0.txt").read_text()
    assert original not in json.dumps(report["requests"]["bb"][1]["json"])
