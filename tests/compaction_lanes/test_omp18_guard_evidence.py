"""Behavioral locks for unique Bash results and retained original wire spans."""
from copy import deepcopy
import hashlib
import json

import pytest

from breadboard_engine.compaction_lanes.omp18_lane import compare_requests
from breadboard_engine.compaction_lanes.request_bytes import project_guarded_values


def _record(times, *, trailer=False):
    call = {"role": "assistant", "tool_calls": [{"id": "bash-1", "type": "function",
            "function": {"name": "bash", "arguments": '{"timeout":300}'}}]}
    messages = [call]
    for index, elapsed in enumerate(times):
        content = f"Result {index}: é\n\nWall time: {elapsed} seconds"
        if trailer:
            content += "\nTimeout clamped to 30s (requested 300s; global tools.maxTimeout ceiling 30s)."
        messages.append({"role": "tool", "tool_call_id": "bash-1", "content": content})
    body = {"prefix": "π🌿", "tools": [], "messages": messages, "untouched": "é"}
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    raw = b" \n" + raw.replace(b'"untouched":"', b'"untouched" : "').replace(
        b'"untouched" : "\xc3\xa9"', b'"untouched" : "\\u00e9"') + b"\n"
    return {"path": "/v1/chat/completions", "method": "POST", "json": body,
            "raw_body_bytes": raw.hex()}


def test_duplicate_results_for_one_bash_call_are_not_elapsed_deviations():
    stock, bb = _record(["0.01", "0.02"]), _record(["1.01", "1.02"])
    evidence = []
    differences = compare_requests([stock], [bb], [], [], timing_substitutions=evidence)
    assert differences
    assert evidence[0]["stock"] == evidence[0]["bb"] == 0
    assert "omp_native_bash_elapsed_time" not in evidence[0]["names"]
    assert evidence[0]["raw_difference"]["index"] == 0


@pytest.mark.parametrize("trailer", [False, True])
def test_elapsed_evidence_reconstructs_only_original_utf8_guarded_spans(trailer):
    stock, bb = _record(["0.01"], trailer=trailer), _record(["1.01"], trailer=trailer)
    evidence = []
    assert compare_requests([stock], [bb], [], [], timing_substitutions=evidence) == []
    record = evidence[0]
    assert record["source_commit"] == "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
    assert record["index"] == 0
    projected = []
    for side, row in (("stock", stock), ("bb", bb)):
        raw = bytes.fromhex(row["raw_body_bytes"])
        changes = record["changes"][side]
        assert len(changes) == 1
        edit = changes[0]
        assert edit["path"] == "$/messages/1/content"
        start, end = edit["start"], edit["end"]
        original = bytes.fromhex(edit["original_bytes"])
        replacement = bytes.fromhex(edit["projected_bytes"])
        assert raw[start:end] == original
        assert original == json.dumps(edit["original"], ensure_ascii=False, separators=(",", ":")).encode()
        assert replacement == json.dumps(edit["projected"], ensure_ascii=False, separators=(",", ":")).encode()
        assert record["raw_difference"][side + "_sha256"] == hashlib.sha256(raw).hexdigest()
        reconstructed = raw[:start] + replacement + raw[end:]
        target = deepcopy(row["json"])
        target["messages"][1]["content"] = edit["projected"]
        assert reconstructed == project_guarded_values(raw, target)
        assert reconstructed[:start] == raw[:start]
        assert reconstructed[start + len(replacement):] == raw[end:]
        assert b'"untouched" : "\\u00e9"' in reconstructed
        projected.append(reconstructed)
    assert projected[0] == projected[1]
