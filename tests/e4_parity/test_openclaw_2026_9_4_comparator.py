from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.comparators.openclaw_2026_9_4 import (
    ComparatorError,
    _apply_supplier_overlay,
    compare,
    project_bb_trace,
    project_supplier_case,
)
from conformance.comparators import openclaw_2026_9_4 as comparator



def test_packet_640_fixture_rejects_tampered_request() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    receipt = json.loads((fixture / "case-receipt.json").read_text())
    assert expected["request_count"] == receipt["receiver_request_count"] == 3
    assert receipt["effects"]["marker.txt"]["sha256"] == expected["effects"]["marker.txt"]
    raw_requests = [
        json.loads(line)["body"]
        for line in (fixture / "receiver" / "http-transcript.jsonl").read_text().splitlines()
        if "body" in json.loads(line)
    ]
    assert raw_requests and "model" in raw_requests[0]
    overlaid_requests, _ = _apply_supplier_overlay(raw_requests)

    tampered = json.loads(json.dumps(overlaid_requests))
    tampered[0]["messages"][0]["content"] = "tampered"
    rejected = compare({
        "capture": str(fixture),
        "replay": {**expected, "requests": tampered},
        "scope": {},
    })
    assert rejected["ok"] is False
    assert any(
        assertion["assertion_id"] == "episode_equal" and assertion["status"] == "failed"
        for assertion in rejected["assertions"]
    )


@pytest.mark.parametrize("field,wrong", [
    ("model", "WRONG-MODEL"),
    ("max_completion_tokens", 99999),
    ("store", True),
    ("stream", False),
])
def test_packet_wire_authority_changes_fail_comparison(field: str, wrong: object) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    raw = [
        json.loads(line)["body"]
        for line in (fixture / "receiver" / "http-transcript.jsonl").read_text().splitlines()
    ]
    requests, _ = _apply_supplier_overlay(raw)
    expected = project_supplier_case(fixture)
    for request in requests:
        request[field] = wrong
    observed = {**expected, "requests": requests}
    report = compare({"capture": str(fixture), "replay": observed, "scope": {}})
    assert not report["ok"], f"wire field {field} was ignored"


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


def test_packet_exec_overlay_is_supplier_only_and_fails_closed(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    raw = [
        json.loads(line)["body"]
        for line in (fixture / "receiver" / "http-transcript.jsonl").read_text().splitlines()
    ]
    expected = project_supplier_case(fixture)
    assert expected["requests"][0]["tools"][1]["function"]["description"] != (
        raw[0]["tools"][1]["function"]["description"]
    )
    native_candidate = {
        "requests": raw,
        "effects": expected["effects"],
        "termination": expected["termination"],
        "request_count": len(raw),
    }
    rejected = compare({"capture": str(fixture), "replay": native_candidate, "scope": {}})
    assert rejected["ok"] is False
    assert rejected["overlay"]["native_sha256"].endswith("20973")
    import shutil
    tampered = tmp_path / "packet"
    shutil.copytree(fixture, tampered)
    first = tampered / "receiver" / "http-transcript.jsonl"
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    rows[0]["body"]["tools"][1]["function"]["description"] = "wrong"
    first.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ComparatorError, match="native_sha256"):
        project_supplier_case(tampered)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "pre_overlaid", "native_sha", "overlay_description"])
def test_real_packet_exec_overlay_rejects_counterfeits(mutation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    packet = Path("/tmp/e4-sol-review-w2-2-packet/packet/cases/normal_multiturn_write_read")
    if not packet.is_dir():
        pytest.skip("independent admitted packet is not extracted")
    requests = [
        json.loads(line)["body"]
        for line in (packet / "receiver" / "http-transcript.jsonl").read_text().splitlines()
    ]
    if mutation == "missing":
        for request in requests:
            request["tools"] = [tool for tool in request["tools"] if tool["function"]["name"] != "exec"]
    elif mutation == "duplicate":
        for request in requests:
            request["tools"].append(next(tool for tool in request["tools"] if tool["function"]["name"] == "exec").copy())
    elif mutation == "pre_overlaid":
        for request in requests:
            next(tool for tool in request["tools"] if tool["function"]["name"] == "exec")["function"]["description"] = comparator._admitted_overlay()["description"]
    else:
        original = comparator._load_json
        def altered(path: Path, default: object = None) -> object:
            config = original(path, default)
            if str(path).endswith("/openclaw/2026.9.4/native-config.json"):
                config["advertisement"]["tools"]["exec"]["native_sha256" if mutation == "native_sha" else "description"] = (
                    "sha256:" + "0" * 64 if mutation == "native_sha" else "WRONG OVERLAY"
                )
            return config
        monkeypatch.setattr(comparator, "_load_json", altered)
        if mutation == "native_sha":
            for request in requests:
                next(tool for tool in request["tools"] if tool["function"]["name"] == "exec")["function"]["description"] = "WRONG NATIVE EXEC DESCRIPTION"
            config = comparator._load_json(Path("config/e4_targets/openclaw/2026.9.4/native-config.json"))
            config["advertisement"]["tools"]["exec"]["native_sha256"] = comparator._text_sha256("WRONG NATIVE EXEC DESCRIPTION")
            monkeypatch.setattr(comparator, "_load_json", lambda path, default=None: config)
    with pytest.raises(ComparatorError):
        _apply_supplier_overlay(requests)
