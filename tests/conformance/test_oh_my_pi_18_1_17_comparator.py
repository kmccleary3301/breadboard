from __future__ import annotations

import json

import pytest

from conformance.comparators.oh_my_pi_18_1_17 import (
    OhMyPi18Comparator,
    project_bb_trace,
    project_supplier_case,
)


def _request(messages: list[dict[str, object]]) -> dict[str, object]:
    return {
        "model": "capture",
        "messages": messages,
        "tools": [],
        "max_completion_tokens": 2048,
        "stream": True,
    }


def test_supplier_and_bb_use_one_wire_projection_and_dedupe_cumulative_snapshots(tmp_path) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "bash", "arguments": "{\"command\":\"printf x\"}"},
    }
    result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "bash",
        "content": "x",
    }
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
    ]
    requests = [
        {"index": 0, "body": _request(messages)},
        {"index": 1, "body": _request([*messages, result])},
        {"index": 2, "body": _request([*messages, result])},
    ]
    supplier = tmp_path / "supplier"
    receiver = supplier / "receiver"
    receiver.mkdir(parents=True)
    (supplier / "trace.json").write_text(
        json.dumps({
            "case_id": "case",
            "effects": {"marker.txt": "sha256:" + "a" * 64},
            "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
        }),
        encoding="utf-8",
    )
    (receiver / "http-transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in requests),
        encoding="utf-8",
    )
    bb_trace = {
        "case_id": "case",
        "requests": requests,
        "runtime_inputs": {"cwd": "/workspace", "home": "/home/capture", "current_date": "2026-09-23", "package_dir": "/packages"},
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }

    supplier_episode = project_supplier_case(supplier)
    bb_episode = project_bb_trace(bb_trace)
    assert supplier_episode == bb_episode
    assert len(bb_episode["tool_calls"]) == 1
    assert len(bb_episode["results"]) == 1
    assert OhMyPi18Comparator()({"capture": supplier, "replay": bb_trace})["ok"] is True


def test_comparator_rejects_nonidentical_cumulative_tool_call(tmp_path) -> None:
    call = {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    changed = {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    trace = {
        "case_id": "case",
        "requests": [{"body": _request([{"role": "assistant", "tool_calls": [call]}])}, {"body": _request([{"role": "assistant", "tool_calls": [changed]}])}],
        "effects": {},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }
    with pytest.raises(ValueError, match="non-identical wire payload"):
        project_bb_trace(trace)
