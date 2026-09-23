from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.comparators.openclaw_2026_9_4 import (
    ComparatorError,
    compare,
    project_bb_trace,
    project_supplier_case,
)


def _case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    (case / "receiver").mkdir(parents=True)
    (case / "workspace").mkdir()
    (case / "workspace" / "marker.txt").write_text("OK\n")
    (case / "scenario.json").write_text(
        json.dumps(
            {
                "case_id": "synthetic",
                "probe_paths": ["marker.txt"],
                "steps": [
                    {"finish_reason": "tool_calls", "tool_calls": [{"name": "write", "arguments": {"path": "marker.txt", "content": "OK\\n"}}]},
                    {"finish_reason": "stop", "assistant_content": "done"},
                ],
            }
        )
    )
    (case / "receiver" / "http-transcript.jsonl").write_text(
        json.dumps({"body": {"messages": [{"role": "user", "content": "task"}, {"role": "assistant", "tool_calls": [{"id": "w1", "function": {"name": "write", "arguments": "{\"path\":\"marker.txt\",\"content\":\"OK\\\\n\"}"}}]}], "tools": [{"name": "write"}]}}) + "\n"
        + json.dumps({"body": {"messages": [{"role": "tool", "tool_call_id": "w1", "content": "OK"}], "tools": [{"name": "write"}]}}) + "\n"
    )
    return case


def test_supplier_projection_and_replay_projection_share_canonical_episode(tmp_path: Path) -> None:
    expected = project_supplier_case(_case(tmp_path))
    observed = project_bb_trace(expected)
    report = compare({"capture": {"trace": expected}, "replay": observed, "scope": {}})
    assert report["ok"] is True
    assert expected["request_count"] == 2
    assert expected["effects"]["marker.txt"].startswith("sha256:")


def test_comparator_negative_gate_rejects_extra_tool_and_request_count() -> None:
    trace = {
        "requests": [],
        "tool_calls": [{"id": "x", "name": "web_search", "arguments": {}}],
        "results": [],
        "effects": {},
        "termination": {"kind": "stop", "native_stop_reason": "stop"},
        "request_count": 1,
    }
    with pytest.raises(ComparatorError, match="request_count"):
        project_bb_trace(trace)


def test_comparator_negative_gate_rejects_undeclared_placeholder() -> None:
    trace = {
        "requests": [{"messages": [{"content": {"placeholder": "<UNDECLARED>"}}], "tools": []}],
        "tool_calls": [],
        "results": [],
        "effects": {},
        "termination": {"kind": "stop", "native_stop_reason": "stop"},
        "request_count": 1,
    }
    with pytest.raises(ComparatorError, match="undeclared"):
        project_bb_trace(trace)


def test_authoritative_packet_cases_and_independent_corruption_gate(tmp_path: Path) -> None:
    packet = Path(
        "/Users/kylemccleary/projects/breadboard/docs_tmp/"
        "bb_direction_assessment/engine_pr_handoff_20260827/"
        "e4_admission_20260914T221653Z/do2-20260923/openclaw/packet/"
        "openclaw-capture-proxy-merged-472-20260923T061727Z.tar.gz"
    )
    if not packet.exists():
        pytest.skip("authoritative OpenClaw packet is not mounted")
    import copy
    import tarfile
    with tarfile.open(packet) as archive:
        archive.extractall(tmp_path)
    cases = tmp_path / "packet" / "cases"
    expected_counts = {
        "normal_multiturn_write_read": 3,
        "process_exec_effect": 3,
        "streaming_fragmented_write": 2,
        "malformed_tool_call": 1,
        "provider_failure_no_retry": 1,
        "budget_cutoff_after_prefix": 8,
    }
    for name, count in expected_counts.items():
        expected = project_supplier_case(cases / name)
        assert expected["request_count"] == count
        observed = project_bb_trace(copy.deepcopy(expected))
        assert compare({"capture": {"trace": expected}, "replay": observed, "scope": {}})["ok"]
        corrupted = copy.deepcopy(observed)
        path = next(iter(corrupted["effects"]), "missing.txt")
        corrupted["effects"][path] = "sha256:independent-corruption"
        assert not compare({"capture": {"trace": expected}, "replay": corrupted, "scope": {}})["ok"]
