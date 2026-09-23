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



def test_packet_640_fixture_round_trips_and_rejects_tampered_request() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    raw_requests = [
        json.loads(line)["body"]
        for line in (fixture / "receiver" / "http-transcript.jsonl").read_text().splitlines()
        if "body" in json.loads(line)
    ]
    assert raw_requests and "model" in raw_requests[0]
    observed = project_bb_trace({
        "requests": raw_requests,
        "effects": expected["effects"],
        "termination": expected["termination"],
        "request_count": len(raw_requests),
    })
    report = compare({"capture": {"trace": expected}, "replay": observed, "scope": {}})
    assert report["ok"] is True
    assert expected["request_count"] == 3
    assert [call["name"] for call in expected["tool_calls"]] == ["write", "read"]
    assert expected["effects"]["marker.txt"] == (
        "sha256:f7a2b67b1ea18fb2bed758b564bb874610c2d875b07b08e0300d59f70a7bd958"
    )

    tampered = json.loads(json.dumps(raw_requests))
    tampered[0]["messages"][0]["content"] = "tampered"
    rejected = compare({
        "capture": {"trace": expected},
        "replay": {
            "requests": tampered,
            "effects": expected["effects"],
            "termination": expected["termination"],
            "request_count": len(tampered),
        },
        "scope": {},
    })
    assert rejected["ok"] is False
    assert any(
        assertion["assertion_id"] == "episode_equal" and assertion["status"] == "failed"
        for assertion in rejected["assertions"]
    )


def test_comparator_rejects_non_identical_repeated_tool_snapshots() -> None:
    first = {
        "model": "gpt",
        "messages": [{"role": "assistant", "tool_calls": [{
            "id": "call-1", "function": {"name": "read", "arguments": "{\"path\":\"a\"}"}
        }]}],
        "tools": [],
    }
    second = json.loads(json.dumps(first))
    second["messages"][0]["tool_calls"][0]["function"]["arguments"] = "{\"path\":\"b\"}"
    with pytest.raises(ComparatorError, match="repeated tool call call-1"):
        project_bb_trace({
            "requests": [first, second],
            "effects": {},
            "termination": {"kind": "stop", "native_stop_reason": "stop"},
            "request_count": 2,
        })


def test_comparator_rejects_non_identical_repeated_result_snapshots() -> None:
    first = {
        "model": "gpt",
        "messages": [
            {"role": "assistant", "tool_calls": [{
                "id": "call-1", "function": {"name": "read", "arguments": "{\"path\":\"a\"}"}
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "first"},
        ],
        "tools": [],
    }
    second = json.loads(json.dumps(first))
    second["messages"][1]["content"] = "second"
    with pytest.raises(ComparatorError, match="repeated tool result call-1"):
        project_bb_trace({
            "requests": [first, second],
            "effects": {},
            "termination": {"kind": "stop", "native_stop_reason": "stop"},
            "request_count": 2,
        })

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
