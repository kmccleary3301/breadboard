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


def test_phase_worker_runs_real_pinned_source_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    dist = Path(os.environ.get("OPENCLAW_DIST", "/opt/openclaw/dist"))
    if not dist.is_dir():
        pytest.skip("pinned OpenClaw dist is not mounted")
    monkeypatch.setenv("OPENCLAW_DIST", str(dist))
    from breadboard.rl.harness.openclaw_native_tools import (
        OpenClawNativeTools,
        native_worker_invocation,
    )

    tools = OpenClawNativeTools(tmp_path)
    try:
        invocation = native_worker_invocation()
        assert invocation["protocol"] == "bb.openclaw-native.v1"
        assert invocation["phases"] == [
            "initialize",
            "project_request",
            "prepare_tools",
            "execute_batch",
            "ack",
            "close",
        ]
        request = tools._worker._request({"phase": "project_request", "messages": []})
        assert request["kind"] == "request"
        assert [item["function"]["name"] for item in request["tools"]] == [
            "ls",
            "read",
            "edit",
            "write",
            "exec",
            "process",
        ]
        assert tools.execute("write", {"path": "marker.txt", "content": "OK\n"})["changed"]
        assert tools.execute("read", {"path": "marker.txt"})["content"] == "OK\n"
    finally:
        tools.scope.cleanup()
