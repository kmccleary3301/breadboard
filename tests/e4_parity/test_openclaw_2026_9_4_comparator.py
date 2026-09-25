from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

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
    assert rejected["errors"] == ["wire prompt: pinned system prompt must emit exactly one Current date line"]


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


@pytest.mark.parametrize("mutation", ["node", "arch", "runtime_boundary", "missing_date", "duplicate_date"])
def test_packet_prompt_normalization_fails_closed(mutation: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    candidate = json.loads(json.dumps(expected))
    system = candidate["requests"][0]["messages"][0]
    prompt = system["content"]
    if mutation == "node":
        system["content"] = prompt.replace("node=v", "node=changed-v")
        if system["content"] == prompt:
            system["content"] = prompt.replace("Current model identity:", "Current model identity: changed")
    elif mutation == "arch":
        system["content"] = prompt.replace("## Tooling", "arch=changed\n## Tooling")
    elif mutation == "runtime_boundary":
        system["content"] = prompt.replace("<!-- /openclaw:attempt:DYNAMIC -->", "Runtime: host=fake | os=Linux (arm64)\n<!-- /openclaw:attempt:DYNAMIC -->")
    elif mutation == "missing_date":
        system["content"] = prompt.replace("Current date: 2026-09-23", "Current day: 2026-09-23")
    else:
        system["content"] = prompt.replace("Current date: 2026-09-23", "Current date: 2026-09-23\nCurrent date: 2026-09-23")
    assert not compare({"capture": str(fixture), "replay": candidate, "scope": {}})["ok"]


def test_packet_budget_refusal_requires_supplier_and_replay_controls(tmp_path: Path) -> None:
    packet = Path("/tmp/bbe4-openclaw-packet640/packet/cases/budget_cutoff_after_prefix")
    if not packet.is_dir():
        pytest.skip("independent admitted packet not extracted")
    supplier = project_supplier_case(packet)
    assert supplier["budget"] == {"cap_triggered": True, "refused_attempts": 1}
    replay = json.loads(json.dumps(supplier))
    assert compare({"capture": str(packet), "replay": replay, "scope": {}})["ok"]
    for mutation in (
        {"cap_triggered": False, "refused_attempts": 0},
        {"cap_triggered": True, "refused_attempts": 0},
    ):
        replay["budget"] = mutation
        assert not compare({"capture": str(packet), "replay": replay, "scope": {}})["ok"]
    replay.pop("budget")
    assert not compare({"capture": str(packet), "replay": replay, "scope": {}})["ok"]
def test_packet_prompt_date_and_roots_use_declared_runtime_inputs() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    candidate = json.loads(json.dumps(expected))
    candidate["runtime_inputs"] = {
        "cwd": "/tmp/bb-workspace",
        "package_dir": "/tmp/bb-openclaw/dist",
        "home": "/tmp/bb-home",
        "current_date": "2026-09-24",
    }
    for request in candidate["requests"]:
        for message in request["messages"]:
            if isinstance(message.get("content"), str):
                message["content"] = (
                    message["content"]
                    .replace("/capture-out/normal_multiturn_write_read/workspace", "/tmp/bb-workspace")
                    .replace("/opt/openclaw", "/tmp/bb-openclaw")
                    .replace("Current date: 2026-09-23", "Current date: 2026-09-24")
                )
    assert compare({"capture": str(fixture), "replay": candidate, "scope": {}})["ok"]
    candidate["runtime_inputs"]["current_date"] = "2026-09-25"
    assert not compare({"capture": str(fixture), "replay": candidate, "scope": {}})["ok"]




def test_relocated_runtime_normalizes_only_recorded_facts() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    candidate = json.loads(json.dumps(expected))
    candidate["runtime_inputs"] = {
        "cwd": "/tmp/bb-workspace",
        "package_dir": "/tmp/bb-openclaw/dist",
        "home": "/tmp/bb-home",
        "current_date": "2026-09-24",
        "session_id": "bbe4-12345",
    }
    candidate["runtime_facts"] = {
        "host": "candidate-host",
        "os": "Linux 6.8.0-candidate",
        "arch": "x64",
        "node": "v26.3.0",
        "session_id": "bbe4-12345",
        "current_date": "2026-09-24",
    }
    for request in candidate["requests"]:
        for message in request["messages"]:
            if isinstance(message.get("content"), str):
                message["content"] = (
                    message["content"]
                    .replace("/capture-out/normal_multiturn_write_read/workspace", "/tmp/bb-workspace")
                    .replace("/opt/openclaw", "/tmp/bb-openclaw")
                    .replace("Current date: 2026-09-23", "Current date: 2026-09-24")
                    .replace("capture-c18626e33a734cd7a0613d9fa38a76b0", "bbe4-12345")
                    .replace("host=perf-eng-2", "host=candidate-host")
                    .replace("os=Linux 6.8.0-90-generic (x64)", "os=Linux 6.8.0-candidate (x64)")
                )
    assert compare({"capture": str(fixture), "replay": candidate, "scope": {}})["ok"]
    for old, new in (("node=v26.3.0", "node=v99.0.0"), ("(x64)", "(arm64)"), ("model=openai/gpt-4o-mini", "model=openai/other")):
        mutant = json.loads(json.dumps(candidate))
        for request in mutant["requests"]:
            for message in request["messages"]:
                if isinstance(message.get("content"), str):
                    message["content"] = message["content"].replace(old, new)
        assert not compare({"capture": str(fixture), "replay": mutant, "scope": {}})["ok"]
    candidate["runtime_facts"]["host"] = "invented-host"
    assert not compare({"capture": str(fixture), "replay": candidate, "scope": {}})["ok"]


def test_relocated_runtime_line_cannot_be_omitted() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    supplier = project_supplier_case(fixture)
    replay = json.loads(json.dumps(supplier["requests"]))
    for request in replay:
        for message in request["messages"]:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                message["content"] = message["content"].split("\n\nRuntime: ", 1)[0]
    receipt = json.loads((fixture / "case-receipt.json").read_text())
    with pytest.raises(ComparatorError, match="exactly one relocated Runtime line"):
        comparator._wire_prompt_facts(
            replay,
            roots=comparator._supplier_wire_roots(receipt),
            runtime_facts={"current_date": "2026-09-23", "session_id": "never-matched"},
        )


def test_literal_user_path_is_not_a_normalizable_prompt_root() -> None:
    source_root = "/tmp/independent-one"
    replay_root = "/tmp/independent-two"
    source = [{
        "messages": [
            {"role": "system", "content": f"Working directory: {source_root}\nCurrent date: 2026-09-23"},
            {"role": "user", "content": f"[Wed 2026-09-23 09:18 UTC] Literal user request: {source_root}\n\nRuntime: agent=main | session=agent:main:explicit:s | sessionId=s | host=h | os=Linux (x64) | node=v26 | model=openai/m | default_model=openai/m"},
        ],
    }]
    replay = json.loads(json.dumps(source))
    for message in replay[0]["messages"]:
        message["content"] = message["content"].replace(source_root, replay_root)
    fields = {"home": "/tmp/independent-home", "package": "/opt/openclaw"}
    facts = {"current_date": "2026-09-23", "session_id": "s", "host": "h", "os": "Linux", "arch": "x64", "node": "v26"}
    normalized_source = comparator._wire_prompt_facts(source, roots={**fields, "workspace": source_root}, runtime_facts=facts)
    normalized_replay = comparator._wire_prompt_facts(replay, roots={**fields, "workspace": replay_root}, runtime_facts=facts)
    assert normalized_source != normalized_replay



def test_user_timestamp_must_match_pinned_worker_fact() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    receipt = json.loads((fixture / "case-receipt.json").read_text())
    first = project_supplier_case(fixture)["requests"][0]
    first_user = first["messages"][1]["content"]
    timestamp = comparator._USER_TIMESTAMP.match(first_user)
    runtime = comparator._RUNTIME_LINE.search(first_user)
    assert runtime is not None
    assert timestamp is not None
    facts = {
        "current_date": "2026-09-23",
        "timestamp_prefix": timestamp.group(),
        "session_id": runtime.group("session_id"),
        **{key: runtime.group(key) for key in ("host", "os", "arch", "node")},
    }
    mutated = json.loads(json.dumps(first))
    mutated["messages"][1]["content"] = first_user.replace(timestamp.group(), "[Thu 2026-09-24 09:18 UTC] ", 1)
    with pytest.raises(ComparatorError, match="timestamp differs from pinned worker fact"):
        comparator._wire_prompt_facts(
            [mutated], roots=comparator._supplier_wire_roots(receipt), runtime_facts=facts,
        )


def test_second_stamped_user_message_is_rejected() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    receipt = json.loads((fixture / "case-receipt.json").read_text())
    request = json.loads(json.dumps(project_supplier_case(fixture)["requests"][0]))
    stamp = comparator._USER_TIMESTAMP.match(request["messages"][1]["content"])
    assert stamp is not None
    request["messages"].append({"role": "user", "content": stamp.group() + "duplicate"})
    with pytest.raises(ComparatorError, match="exactly one stamped user"):
        comparator._wire_prompt_facts([request], roots=comparator._supplier_wire_roots(receipt))


def test_malformed_packet_stops_without_followup_request_or_effect() -> None:
    packet = Path("/tmp/bbe4-openclaw-packet640/packet/cases/malformed_tool_call")
    if not packet.is_dir():
        pytest.skip("independent admitted packet not extracted")
    supplier = project_supplier_case(packet)
    assert supplier["request_count"] == 1
    assert supplier["tool_calls"] == []
    assert supplier["termination"] == {"kind": "malformed_tool_call", "native_stop_reason": "error"}
    assert supplier["effects"]["malformed-marker.txt"] is None


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
    message_timestamp_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))

    tools = OpenClawNativeTools(tmp_path, message_timestamp_ms=message_timestamp_ms)
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


def test_bb_trace_with_envelope_and_classification_equals_supplier_and_records_gap() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    assert "classification" not in expected
    assert "envelope" not in expected
    assert "final_envelope" not in expected

    candidate = json.loads(json.dumps(expected))
    candidate["classification"] = {"verdict": "success", "category": "normal"}
    candidate["envelope"] = {"turn_count": 3, "status": "completed"}
    candidate["final_envelope"] = {"turn_count": 3, "status": "completed"}

    report = compare({"capture": str(fixture), "replay": candidate, "scope": {}})
    assert report["ok"] is True
    assert report["errors"] == []
    assert any(
        assertion["assertion_id"] == "episode_equal" and assertion["status"] == "passed"
        for assertion in report["assertions"]
    )
    assert any(
        gap.get("gap_id") == "openclaw-supplier-envelope-unrecorded"
        for gap in report.get("declared_gaps", [])
    )
    gap = next(
        gap for gap in report["declared_gaps"]
        if gap.get("gap_id") == "openclaw-supplier-envelope-unrecorded"
    )
    assert "envelope" in gap["replay_keys_present"]
    assert "classification" in gap["replay_keys_present"]
    assert "openclaw-supplier-envelope-unrecorded" in report.get("gaps", [])


@pytest.mark.parametrize("key", ["classification", "envelope", "final_envelope"])
def test_supplier_receipt_with_unrecorded_keys_fails_closed(tmp_path: Path, key: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    case_dir = tmp_path / f"case_{key}"
    shutil.copytree(fixture, case_dir)
    receipt_path = case_dir / "case-receipt.json"
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_data[key] = {"unexpected": True}
    receipt_path.write_text(json.dumps(receipt_data), encoding="utf-8")

    with pytest.raises(ComparatorError, match="undeclared envelope/classification keys"):
        project_supplier_case(case_dir)

    replay = project_supplier_case(fixture)
    report = compare({"capture": str(case_dir), "replay": replay, "scope": {}})
    assert report["ok"] is False
    assert any("undeclared envelope/classification keys" in err for err in report.get("errors", []))


@pytest.mark.parametrize(
    "mutation_type",
    ["effects", "termination_kind", "request_count"],
)
def test_real_episode_difference_in_retained_key_still_fails(mutation_type: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    candidate = json.loads(json.dumps(expected))
@pytest.mark.parametrize(
    "mutation_type",
    ["effects", "termination_kind", "termination_stop_reason"],
)
def test_real_episode_difference_in_retained_key_still_fails(mutation_type: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    candidate = json.loads(json.dumps(expected))
    candidate["envelope"] = {"turn_count": 3, "status": "completed"}

    if mutation_type == "effects":
        candidate["effects"]["marker.txt"] = "sha256:" + "0" * 64
    elif mutation_type == "termination_kind":
        candidate["termination"]["kind"] = "failed"
    elif mutation_type == "termination_stop_reason":
        candidate["termination"]["native_stop_reason"] = "different_reason"

    report = compare({"capture": str(fixture), "replay": candidate, "scope": {}})
    assert report["ok"] is False
    assertion = next(a for a in report["assertions"] if a["assertion_id"] == "episode_equal")
    assert assertion["status"] == "failed"
    assert assertion["detail"]
    assert any(
        gap.get("gap_id") == "openclaw-supplier-envelope-unrecorded"
        for gap in report.get("declared_gaps", [])
    )


def test_bb_rich_record_trace_with_root_bootstrap_matches_supplier_projection() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    marker_sha = expected["effects"]["marker.txt"]

    candidate = json.loads(json.dumps(expected))
    candidate["effects"] = {
        "IDENTITY.md": {
            "exists": True,
            "bytes": 100,
            "sha256": "sha256:" + "1" * 64,
            "content_utf8": "identity content",
        },
        "SOUL.md": {
            "exists": True,
            "bytes": 50,
            "sha256": "sha256:" + "2" * 64,
            "content_utf8": "soul content",
        },
        "USER.md": {
            "exists": True,
            "bytes": 80,
            "sha256": "sha256:" + "3" * 64,
            "content_utf8": "user content",
        },
        "marker.txt": {
            "exists": True,
            "bytes": 16,
            "sha256": marker_sha,
            "content_utf8": "OPENCLAW-NORMAL\n",
        },
    }

    report = compare({"capture": str(fixture), "replay": candidate, "scope": {}})
    assert report["ok"] is True
    assert report["errors"] == []
    effects_assertion = next(a for a in report["assertions"] if a["assertion_id"] == "effects_equal")
    assert effects_assertion["status"] == "passed"
    assert effects_assertion["expected"] == {"marker.txt": marker_sha}
    assert effects_assertion["observed"] == {"marker.txt": marker_sha}


def test_nested_bootstrap_write_not_excluded_and_makes_effects_differ() -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    expected = project_supplier_case(fixture)
    marker_sha = expected["effects"]["marker.txt"]

    candidate = json.loads(json.dumps(expected))
    candidate["effects"] = {
        "sub/SOUL.md": {
            "exists": True,
            "bytes": 50,
            "sha256": "sha256:" + "4" * 64,
            "content_utf8": "nested soul content",
        },
        "marker.txt": {
            "exists": True,
            "bytes": 16,
            "sha256": marker_sha,
            "content_utf8": "OPENCLAW-NORMAL\n",
        },
    }

    report = compare({"capture": str(fixture), "replay": candidate, "scope": {}})
    assert report["ok"] is False
    effects_assertion = next(a for a in report["assertions"] if a["assertion_id"] == "effects_equal")
    assert effects_assertion["status"] == "failed"
    assert "keys differ" in effects_assertion["detail"]
    assert "sub/SOUL.md" in effects_assertion["observed"]


@pytest.mark.parametrize("malformed", [
    {"exists": True, "bytes": 10, "sha256": "sha256:" + "0" * 64, "extra_forbidden": 123},
    {"exists": True, "bytes": 10, "sha256": "not-a-sha256"},
    {"exists": True, "bytes": 10, "sha256": "sha256:tooshort"},
    {"exists": False, "extra_forbidden": True},
    {"exists": "not-a-bool", "sha256": "sha256:" + "0" * 64},
    {"exists": True, "bytes": -1, "sha256": "sha256:" + "0" * 64},
])
def test_malformed_rich_record_raises_comparator_error(malformed: dict[str, Any]) -> None:
    with pytest.raises(ComparatorError):
        comparator._project_effects({"file.txt": malformed})


@pytest.mark.parametrize("escaping_path", [
    "../escape.txt",
    "/etc/passwd",
    "sub/../../escape.txt",
    "..",
])
def test_effect_path_escaping_workspace_fails_closed(escaping_path: str) -> None:
    with pytest.raises(ComparatorError, match="escapes workspace"):
        comparator._project_effects({escaping_path: "sha256:" + "0" * 64})


def test_exists_false_probe_equals_supplier_none(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    case_dir = tmp_path / "case_with_absent_probe"
    shutil.copytree(fixture, case_dir)
    scenario_path = case_dir / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["probe_paths"].append("absent-probe.txt")
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    expected = project_supplier_case(case_dir)
    assert expected["effects"]["absent-probe.txt"] is None

    candidate = json.loads(json.dumps(expected))
    candidate["effects"]["absent-probe.txt"] = {"exists": False}

    report = compare({"capture": str(case_dir), "replay": candidate, "scope": {}})
    assert report["ok"] is True
    assert report["errors"] == []
    effects_assertion = next(a for a in report["assertions"] if a["assertion_id"] == "effects_equal")
    assert effects_assertion["status"] == "passed"
    assert effects_assertion["expected"]["absent-probe.txt"] is None
    assert effects_assertion["observed"]["absent-probe.txt"] is None

    # Also directly compare traces
    direct = comparator.OpenClawComparator().compare_traces(expected, candidate)
    assert direct["status"] == "passed"


def _case_with_absent_probe(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "openclaw_packet_640"
    case_dir = tmp_path / "case_with_absent_probe"
    shutil.copytree(fixture, case_dir)
    scenario_path = case_dir / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["probe_paths"].append("absent-probe.txt")
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    return case_dir


def test_replay_omitting_absent_declared_probe_matches_supplier(tmp_path: Path) -> None:
    case_dir = _case_with_absent_probe(tmp_path)
    expected = project_supplier_case(case_dir)
    candidate = json.loads(json.dumps(expected))
    # BreadBoard measures written files only; a declared probe it never wrote is absent.
    del candidate["effects"]["absent-probe.txt"]

    report = compare({"capture": str(case_dir), "replay": candidate, "scope": {}})

    assert report["errors"] == []
    assert report["ok"] is True
    effects_assertion = next(a for a in report["assertions"] if a["assertion_id"] == "effects_equal")
    assert effects_assertion["observed"] == expected["effects"]


def test_replay_writing_probe_the_supplier_left_absent_fails(tmp_path: Path) -> None:
    case_dir = _case_with_absent_probe(tmp_path)
    expected = project_supplier_case(case_dir)
    candidate = json.loads(json.dumps(expected))
    candidate["effects"]["absent-probe.txt"] = "sha256:" + "5" * 64

    report = compare({"capture": str(case_dir), "replay": candidate, "scope": {}})

    assert report["ok"] is False
    effects_assertion = next(a for a in report["assertions"] if a["assertion_id"] == "effects_equal")
    assert effects_assertion["status"] == "failed"
    assert effects_assertion["observed"]["absent-probe.txt"] == "sha256:" + "5" * 64
