"""Behavioral wire parity regressions shared by the stock compaction lanes."""
import hashlib
import json

import pytest
from breadboard_engine.compaction_lanes.request_bytes import (
    project_guarded_values, raw_request_differences,
)
from breadboard_engine.compaction_lanes import (
    claude_lane, codex_lane, opencode_comparison, omp18_lane, openclaw_lane, pi_requests,
)


def record(raw):
    return {"method": "POST", "path": "/v1/chat/completions",
            "raw_body_bytes": raw.hex(), "json": json.loads(raw)}


def differences(lane, stock, bb):
    if lane == "claude":
        return claude_lane.compare_requests(stock, bb)
    if lane == "codex":
        return codex_lane.compare_requests(stock, bb)
    if lane == "opencode":
        return opencode_comparison.compare_records(stock, bb)
    if lane == "omp18":
        return omp18_lane.compare_requests(stock, bb, [], [])
    if lane == "openclaw":
        return openclaw_lane.compare(stock, bb)["diffs"]
    return pi_requests.compare_recorded_requests(stock, bb, [], target_id="pi-r4@0.57.1")[1]


@pytest.mark.parametrize("lane", ["claude", "codex", "opencode", "omp18", "openclaw", "pi"])
def test_wire_whitespace_cannot_pass_by_parsed_equality(lane):
    raw = b'{"model":"m","input":[],"messages":[],"tools":[],"max_tokens":2048}'
    # OpenClaw's actual main tool overlay is not part of this representation test.
    if lane == "openclaw":
        raw = b'{"model":"m","messages":[]}'
    stock = record(raw)
    bb = record(b" " + raw)
    assert differences(lane, [stock], [bb])
    assert not differences(lane, [stock], [stock])


@pytest.mark.parametrize("left,right,target", [
    (b'{"keep":1,"remove":2}', b'{ "keep":1,"remove":3}', {"keep":1}),
    (b'{"keep":1,"remove":2}', b'{"keep":1,"remove":3 }', {"keep":1}),
    (b'{"a":1,"b":2,"remove":3}', b'{"a":1, "b":2,"remove":4}', {"a":1,"b":2}),
])
def test_guarded_deletion_preserves_untouched_wire_format(left, right, target):
    assert project_guarded_values(left, target) != project_guarded_values(right, target)


def test_guarded_value_change_does_not_rewrite_other_json_values():
    raw = b'{ "metadata":12, "text":"\\u00e9", "items":[1, 2] }'
    body = json.loads(raw)
    body["metadata"] = 13
    assert project_guarded_values(raw, body) == raw.replace(b"12", b"13")


def test_raw_report_keeps_every_mismatch_and_missing_request():
    stock = [record(b'{"v":1}'), record(b'{"v":2}'), record(b'{"v":3}')]
    bb = [record(b'{ "v":1}'), record(b'{ "v":2}')]
    evidence = raw_request_differences(stock, bb)
    assert [item["index"] for item in evidence] == [0, 1, 2]
    assert evidence[0]["stock_sha256"] == hashlib.sha256(b'{"v":1}').hexdigest()
    assert evidence[0]["bb_sha256"] == hashlib.sha256(b'{ "v":1}').hexdigest()
    assert evidence[-1]["bb_sha256"] is None


@pytest.mark.parametrize("label", ["Current date: ", "Current date and time: ",
                                  "Current working directory: ", "Wall time: "])
def test_pi_environment_text_is_not_an_undeclared_deviation(label):
    left = {"model":"m", "messages":[{"role":"system", "content":label+"A"}],
            "tools":[], "max_tokens":2048}
    right = json.loads(json.dumps(left))
    right["messages"][0]["content"] = label+"B"
    stock = record(json.dumps(left, separators=(",", ":")).encode())
    bb = record(json.dumps(right, separators=(",", ":")).encode())
    passed, failures = pi_requests.compare_recorded_requests([stock], [bb], [], target_id="pi-r4@0.57.1")
    assert not passed
    assert failures[0]["type"] == "body_diff"


@pytest.mark.parametrize("tool_name,correlated", [("read", True), ("bash", False)])
def test_elapsed_header_in_other_tool_output_is_not_admitted(tool_name, correlated):
    call = {"role": "assistant", "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": tool_name, "arguments": "{}"}}]}
    def body(seconds):
        return {"tools": [], "messages": [call, {
            "role": "tool", "tool_call_id": "call_1" if correlated else "unknown",
            "content": "User file contents\nWall time: " + seconds + " seconds"}]}
    stock = record(json.dumps(body("0.12"), separators=(",", ":")).encode())
    bb = record(json.dumps(body("1.23"), separators=(",", ":")).encode())
    evidence = []
    assert omp18_lane.compare_requests([stock], [bb], [], [], timing_substitutions=evidence)
    assert evidence[0]["stock"] == evidence[0]["bb"] == 0


def test_guarded_string_projection_reuses_wire_encoder_without_changing_other_bytes():
    assert project_guarded_values(rb'{ "id": "old", "body":"\u00e9" }',
                                  {"id": "new", "body": "é"}) == rb'{ "id": "new", "body":"\u00e9" }'
