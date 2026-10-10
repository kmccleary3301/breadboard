from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.compaction_lanes.omp18_lane import SCENARIOS, guarded_summary_fields, remove_fields, run_scenario


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
    assert remove_fields(raw, {"tools", "max_completion_tokens"}) == b'{"model":"m","tool_choice":"none","messages":[{"content":"a,b"}]}'
    assert remove_fields(raw, set()) == raw


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
    from scripts.compaction_lanes.omp18_lane import ROOT
    worker = (ROOT / "breadboard/rl/harness/runners/omp_native_tool_worker.ts").read_text()
    function = worker.split("async function processTable()", 1)[1].split("async function descendantHandles", 1)[0]
    # Execute the production function, injecting only the external process result.
    script = "async function processTable()" + function + """
    const input = JSON.parse(await Bun.stdin.text());
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
        input=json.dumps({"output": output, "status": status}), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "process table" in result.stdout


@pytest.mark.skipif(not os.environ.get("BB_OMP_TEST_BUN"), reason="Bun is required")
def test_native_rpc_writer_preserves_large_multibyte_frames() -> None:
    from scripts.compaction_lanes.omp18_lane import ROOT
    worker = (ROOT / "breadboard/rl/harness/runners/omp_native_tool_worker.ts").read_text()
    function = worker.split("async function writeFrame(", 1)[1].split("\nwhile (true)", 1)[0]
    script = "async function writeFrame(" + function + """
    const input = JSON.parse(await Bun.stdin.text());
    await writeFrame(input);
    await writeFrame({complete: true});
    """
    payload = {"text": "é—" * 65536}
    result = subprocess.run([os.environ["BB_OMP_TEST_BUN"], "-e", script],
        input=json.dumps(payload).encode(), capture_output=True, timeout=10)
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
    from scripts.compaction_lanes.omp18_lane import canonical_json, compare_requests
    stock = {"tools": [], "messages": [{"role": role, "content": "Record A\nWall time: 0.12 seconds"}]}
    bb = {"tools": [], "messages": [{"role": role, "content": "Record A\nWall time: 1.23 seconds"}]}
    def recording(body):
        return [{"json": body, "raw_body_bytes": canonical_json(body).hex()}]
    substitutions = []
    diffs = compare_requests(recording(stock), recording(bb), [], [], timing_substitutions=substitutions)
    assert bool(diffs) == (role != "tool")
    assert substitutions == [{"index": 0, "stock": int(role == "tool"), "bb": int(role == "tool")}]
    if role == "tool":
        bb["messages"][0]["content"] = "Record B\nWall time: 1.23 seconds"
        assert compare_requests(recording(stock), recording(bb), [], [])[0]["type"] == "byte_diff"


def test_comparator_guards_stock_handoff_when_candidate_choice_is_wrong() -> None:
    from scripts.compaction_lanes.omp18_lane import canonical_json, compare_requests

    stock = {"tools": [{"name": "read"}], "max_completion_tokens": 2048, "tool_choice": "none"}
    bb = {**deepcopy(stock), "tool_choice": "auto"}
    def recording(body):
        return [{"json": body, "raw_body_bytes": canonical_json(body).hex()}]
    assert compare_requests(recording(stock), recording(bb), [], []) == [{
        "index": 0, "type": "deviation_guard", "message": "Handoff tool_choice differs from stock",
    }]


@pytest.mark.skipif(not os.environ.get("BB_OMP_TEST_SOURCE_ROOT"), reason="pinned OMP source is unavailable")
def test_production_conductor_preserves_failed_rescue_without_counting_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.compaction_lanes import omp18_lane as lane

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
