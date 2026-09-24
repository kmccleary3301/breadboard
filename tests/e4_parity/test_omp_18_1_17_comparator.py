from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from breadboard.product.evidence.e4.run_lane import _comparator_entry

from conformance.comparators.oh_my_pi_18_1_17 import (
    OhMyPi18Comparator,
    compare,
    project_bb_trace,
    project_supplier_case,
)
from breadboard_engine.conformance.c4_chain import _load_comparator_callable


def _trace() -> dict:
    return {
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "requests": [{"body": {"messages": [{"role": "user", "content": "do"}], "tools": [{"function": {"name": "bash"}}]}}],
        "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "printf hi"}}],
        "results": [{"tool_call_id": "c1", "name": "bash", "output": "hi", "error": None}],
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "termination": "submitted",
        "stop_reason": "stop",
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}],
        "request_count": 1,
    }


def test_projection_is_one_canonical_episode() -> None:
    episode = project_bb_trace(_trace())
    assert list(episode) == ["schema_version", "requests", "tool_calls", "results", "file_effects", "termination", "request_count"]
    assert episode["tool_calls"][0]["arguments"] == {"command": "printf hi"}
    assert episode["termination"] == {"kind": "submitted", "native_stop_reason": "stop"}


def test_equal_supplier_and_bb_episodes_pass() -> None:
    trace = _trace()
    report = compare({"capture": trace, "replay": deepcopy(trace)})
    assert report["ok"] is True
    assert report["failed"] == 0


def test_negative_gate_rejects_completion_order_mutation_and_effect_mutation() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["results"][0]["output"] = "different"
    replay["effects"]["marker.txt"] = None
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 2

def test_capture_unavailable_stop_reason_is_not_self_exempt(tmp_path: Path) -> None:
    supplier_dir = tmp_path / "supplier"
    (supplier_dir / "receiver").mkdir(parents=True)
    supplier_trace = _trace()
    supplier_trace.pop("native_responses")
    supplier_trace["exit"] = {"kind": "Submitted", "native_stop_reason": None}
    (supplier_dir / "trace.json").write_text(json.dumps(supplier_trace), encoding="utf-8")
    replay = _trace()
    replay["exit"] = {"kind": "Submitted", "native_stop_reason": "stop"}
    report = compare({"capture": supplier_dir, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 1


def test_bb_trace_requires_native_responses_for_each_request() -> None:
    replay = _trace()
    replay.pop("native_responses")
    with pytest.raises(ValueError, match="native_responses"):
        project_bb_trace(replay)


def test_bb_trace_requires_declared_runtime_inputs() -> None:
    replay = _trace()
    replay.pop("runtime_inputs")
    with pytest.raises(ValueError, match="runtime_inputs"):
        project_bb_trace(replay)


def test_bb_runtime_inputs_match_supplier_declaration() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["runtime_inputs"]["cwd"] = "/different"
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "runtime_inputs" in report["errors"][0]


def test_bb_cannot_self_declare_stop_reason_provenance() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["native_stop_reason_source"] = "capture_unavailable"
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "cannot declare" in report["errors"][0]


def _request(messages: list[dict[str, object]]) -> dict[str, object]:
    return {
        "model": "capture",
        "messages": messages,
        "tools": [],
        "max_completion_tokens": 2048,
        "stream": True,
    }


def test_supplier_and_bb_dedupe_cumulative_snapshots(tmp_path: Path) -> None:
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
            "native_responses": [{"choices": [{"finish_reason": "stop"}]}],
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
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}] * 3,
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }

    supplier_episode = project_supplier_case(supplier)
    bb_episode = project_bb_trace(bb_trace)
    assert supplier_episode == bb_episode
    assert len(bb_episode["tool_calls"]) == 1
    assert len(bb_episode["results"]) == 1
    assert OhMyPi18Comparator()({"capture": supplier, "replay": bb_trace})["ok"] is True


def test_comparator_rejects_nonidentical_cumulative_tool_call() -> None:
    call = {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    changed = {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    trace = {
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "requests": [
            {"body": _request([{"role": "assistant", "tool_calls": [call]}])},
            {"body": _request([{"role": "assistant", "tool_calls": [changed]}])},
        ],
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}] * 2,
        "effects": {},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }
    with pytest.raises(ValueError, match="non-identical wire payload"):
        project_bb_trace(trace)


def test_omp_comparator_loads_through_lane_registry() -> None:
    root = Path(__file__).resolve().parents[2]
    entry = _comparator_entry(
        {
            "lane_id": "oh_my_pi_18_1_17_replay",
            "compare": {"comparator": "oh_my_pi_18_1_17_trace_v1"},
        },
        root / "conformance/comparators/registry.json",
    )
    loaded = _load_comparator_callable(entry)
    assert loaded is compare
    assert entry["comparator_id"] == "oh_my_pi_18_1_17_trace_v1"
