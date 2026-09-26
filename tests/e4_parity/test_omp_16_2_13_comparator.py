from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conformance.comparators.oh_my_pi_16_2_13 import (
    OhMyPi16213Comparator,
    OhMyPi16213ComparatorError,
    project_bb_trace,
    project_upstream_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "e4_parity" / "fixtures" / "omp_16_2_13_supplier_cases"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
CASES = tuple(MANIFEST["cases"])
BB_RUNTIME = {
    "cwd": "/leases/omp-16213/workspace-abc",
    "home": "/leases/omp-16213/home-abc",
    "package_dir": "/srv/omp-16213/node_modules/@oh-my-pi/pi-coding-agent",
    "current_date": "2026-09-26",
}
UPSTREAM_RUNTIME = {
    "cwd": "/testbed",
    "home": "/capture/home",
    "package_dir": "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent",
    "current_date": "2026-09-26",
}
KIND_BY_STOP = {"stop": "Submitted", "error": "error", "toolUse": "running", "length": "length"}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _capture(case: str, name: str) -> Path:
    return FIXTURES / case / "capture" / name


def _rows(case: str) -> list[dict]:
    p = _capture(case, "http-transcript.jsonl")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def _events(case: str) -> list[dict]:
    p = _capture(case, "omp-events.jsonl")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def _upstream_messages(case: str) -> list[dict]:
    return [event["message"] for event in _events(case) if event.get("type") == "message_end"]


def _header_val(headers: Any, name: str) -> str:
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k.lower() == name.lower():
                return str(v)
    elif isinstance(headers, list):
        for item in headers:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                if str(item[0]).lower() == name.lower():
                    return str(item[1])
    return "0"


def _logical_bodies(case: str) -> list[dict]:
    return [
        json.loads(base64.b64decode(row["raw_body_base64"]))
        for row in _rows(case)
        if _header_val(row.get("headers"), "x-stainless-retry-count") == "0"
    ]


def _date(case: str) -> str:
    bodies = _logical_bodies(case)
    if not bodies:
        return "2026-09-26"
    system = bodies[0]["messages"][0]["content"]
    import re
    m = re.search(r"(?m)^Today is (?P<date>\d{4}-\d{2}-\d{2})", system)
    return m.group("date") if m else "2026-09-26"


def _to_bb(value, date: str, runtime: dict[str, str] = BB_RUNTIME):
    if isinstance(value, str):
        value = value.replace(
            f"Today is {date}, and the current working directory is '/testbed'.",
            f"Today is {runtime['current_date']}, and the current working directory is '{runtime['cwd']}'.",
        )
        value = value.replace("/opt/omp/node_modules/@oh-my-pi/pi-coding-agent", runtime["package_dir"])
        value = value.replace("/capture/home", runtime["home"])
        return value.replace("/testbed", runtime["cwd"])
    if isinstance(value, list):
        return [_to_bb(item, date, runtime) for item in value]
    if isinstance(value, dict):
        if value.get("role") == "user":
            return deepcopy(value)
        return {key: _to_bb(item, date, runtime) for key, item in value.items()}
    return value


def _effects(case: str) -> dict:
    pre = json.loads(_capture(case, "workspace-manifest-pre.json").read_text(encoding="utf-8"))
    post = json.loads(_capture(case, "workspace-manifest-post.json").read_text(encoding="utf-8"))
    effects = {}
    for path, value in post.items():
        if pre.get(path) != value:
            data = (FIXTURES / "blobs" / value["sha256"].removeprefix("sha256:")).read_bytes()
            effects[path] = {"exists": True, "bytes": len(data), "sha256": _sha(data), "content_utf8": data.decode("utf-8")}
    return effects


def _bb_trace(
    case: str,
    *,
    messages: list[dict] | None = None,
    requests: list[dict] | None = None,
    runtime: dict[str, str] = BB_RUNTIME,
) -> dict:
    """The trace a faithful BB clone produces for the declared case."""
    date = _date(case)
    messages = _upstream_messages(case) if messages is None else messages
    requests = _logical_bodies(case) if requests is None else requests
    assistants = [m for m in messages if m["role"] == "assistant"]
    last_stop = assistants[-1]["stopReason"]
    kind = KIND_BY_STOP[last_stop]

    if case == "declared__o9_budget_limit_stop":
        terminal_msg = {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
            "timestamp": 1234567890,
        }
        trace_messages = _to_bb(deepcopy(messages), date, runtime) + [terminal_msg]
        return {
            "schema_version": "bb.e4.omp-replay-trace.v1",
            "role": "replay",
            "profile": "omp",
            "version": "16.2.13",
            "case_id": case,
            "request_count": 8,
            "stream_fn_issued": 9,
            "messages": trace_messages,
            "effects": _effects(case),
            "termination": {"kind": "RequestLimitExceeded", "native_stop_reason": "error"},
            "requests": _to_bb(deepcopy(requests[:8]), date, runtime),
            "runtime_inputs": dict(runtime),
        }

    return {
        "schema_version": "bb.e4.omp-replay-trace.v1",
        "role": "replay",
        "profile": "omp",
        "version": "16.2.13",
        "case_id": case,
        "request_count": len(requests),
        "stream_fn_issued": len(requests),
        "messages": _to_bb(deepcopy(messages), date, runtime),
        "effects": _effects(case),
        "termination": {"kind": kind, "native_stop_reason": last_stop},
        "requests": _to_bb(deepcopy(requests), date, runtime),
        "runtime_inputs": dict(runtime),
    }


def _cancel_process() -> dict:
    return {"exit_code": 130, "terminal": {"status": "failed", "primary_failure": {"code": "CancelledError", "category": "CancelledError"}}}


def _faithful(case: str, runtime: dict[str, str] = BB_RUNTIME) -> dict:
    if case == "declared__o4_bash_timeout_nonzero_cancel":
        return {"trace": _bb_trace(case, runtime=runtime), "process": _cancel_process()}
    return {"trace": _bb_trace(case, runtime=runtime)}


def _report(case: str, replay: dict) -> dict:
    return OhMyPi16213Comparator()({"capture": {"case_dir": str(FIXTURES / case)}, "replay": replay})


def _divergence_names(report: dict) -> list[str]:
    return [item["name"] for item in report["divergences"]]


def test_fixture_manifest_binds_packet_members_and_derived_blobs() -> None:
    assert MANIFEST["source_packet"]["sha256"] == "sha256:cf93d5ac54c0271ce90a7028b0c1cde6ff022f7516cb26a838608878e5f54ec9"
    listed = {"manifest.json"}
    for entry in MANIFEST["files"]:
        path = FIXTURES / entry["path"]
        assert _sha(path.read_bytes()) == entry["sha256"], entry["path"]
        expected_member = "packet/" + entry["path"] if entry["path"] == "omp16213_capture_cases.json" else "packet/cases/" + entry["path"]
        assert entry["member_path"] == expected_member
        listed.add(entry["path"])
    for entry in MANIFEST["derived_files"]:
        assert _sha((FIXTURES / entry["path"]).read_bytes()) == entry["sha256"]
        for binding in entry["bound_by"]:
            manifest_path = FIXTURES / binding["manifest"].removeprefix("packet/cases/")
            workspace = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert workspace[binding["entry"]]["sha256"] == entry["sha256"]
        listed.add(entry["path"])
    present = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*") if path.is_file()}
    assert present == listed


@pytest.mark.parametrize("case", CASES)
def test_upstream_projection_is_consistent_with_the_capture(case: str) -> None:
    projected = project_upstream_case(FIXTURES / case)
    rows = _rows(case)
    trace = json.loads(_capture(case, "trace.json").read_text(encoding="utf-8"))
    assert len(rows) == trace["requests_total"]
    assert all("timestamp" not in message for message in projected["messages"])
    assert [call["id"] for call in projected["tool_calls"]] == [
        block["id"] for message in _upstream_messages(case) if message["role"] == "assistant"
        for block in message["content"] if block["type"] == "toolCall"
    ]


@pytest.mark.parametrize("case", CASES)
def test_upstream_self_compare_passes(case: str) -> None:
    """Upstream trace compared against itself (same roots) passes with verdict named_divergence or exact."""
    replay = _faithful(case, runtime=UPSTREAM_RUNTIME)
    report = _report(case, replay)
    assert report["passed"] is True, f"{case} failed: {report['findings']}"
    assert report["verdict"] in {"exact", "normalized", "named_divergence"}
    assert report["findings"] == []


@pytest.mark.parametrize("case", CASES)
def test_faithful_clone_trace_yields_only_named_divergences(case: str) -> None:
    report = _report(case, _faithful(case))
    assert report["findings"] == []
    assert report["verdict"] == "named_divergence"
    assert "sdk_transport_headers" in _divergence_names(report)
    assert "advertised_image_tool_unexercised" in _divergence_names(report)
    counts = {(item["side"], item["rule"]): item["count"] for item in report["normalizations"]}
    assert counts[("upstream", "workspace_root")] > 0
    assert counts[("upstream", "current_date_reminder")] > 0
    assert counts[("bb", "message_timestamp")] == len(_faithful(case)["trace"]["messages"])


def test_case_specific_divergences_are_named() -> None:
    report_o3 = _report("declared__o3_threshold_compaction", _faithful("declared__o3_threshold_compaction"))
    assert "compaction_start_then_exit" in _divergence_names(report_o3)

    report_o4 = _report("declared__o4_bash_timeout_nonzero_cancel", _faithful("declared__o4_bash_timeout_nonzero_cancel"))
    cancel = next(item for item in report_o4["divergences"] if item["name"] == "external_cancel_signal")
    assert cancel["upstream"]["exit_code"] == 0 and cancel["bb"]["exit_code"] == 130
    assert cancel["anchor_tool_call_id"] == "omp16213-call-o4_bash_timeout_nonzero_cancel-04-cancel"

    report_o9 = _report("declared__o9_budget_limit_stop", _faithful("declared__o9_budget_limit_stop"))
    assert "bounded_request_cap" in _divergence_names(report_o9)
    assert report_o9["verdict"] == "named_divergence"


_RULES_BLOCK = "\n\n<generic-rules>\nPLANTED_RULE_TOKEN\n</generic-rules>"


def _with_discovered_rules(case: str, extra: str = "") -> dict:
    replay = _faithful(case)
    for request in replay["trace"]["requests"]:
        system = request["messages"][0]
        head, marker, tail = system["content"].partition("# Skills & Rules")
        assert marker
        system["content"] = head + marker + _RULES_BLOCK + extra + tail
    return _report(case, replay)


def test_upstream_no_rules_flag_scopes_only_the_discovered_rules_block() -> None:
    report = _with_discovered_rules("declared__o6b_disabled_resources_no_rules")
    assert report["verdict"] == "named_divergence", report["findings"]
    record = next(item for item in report["divergences"] if item["name"] == "upstream_cli_no_rules")
    assert record["bb_generic_rules_blocks"] == [_RULES_BLOCK]

    near_miss = _with_discovered_rules("declared__o6b_disabled_resources_no_rules", extra="\nEXTRA")
    assert near_miss["verdict"] == "fail"
    assert "requests" in {item["field"] for item in near_miss["findings"]}

    without_flag = _with_discovered_rules("declared__o6_disabled_resources_agents_md")
    assert without_flag["verdict"] == "fail"
    assert "upstream_cli_no_rules" not in _divergence_names(without_flag)


def _without_system_line(case: str, line: str) -> dict:
    replay = _faithful(case)
    for request in replay["trace"]["requests"]:
        system = request["messages"][0]
        assert f"\n{line}\n" in system["content"]
        system["content"] = system["content"].replace(f"\n{line}\n", "\n", 1)
    return _report(case, replay)


def test_unseeded_agent_dir_scopes_only_planted_agent_dir_rule_lines() -> None:
    case = "declared__o6_disabled_resources_agents_md"
    report = _without_system_line(case, "PLANTED_AGENTDIR_RULE_TOKEN")
    assert report["verdict"] == "named_divergence", report["findings"]
    record = next(item for item in report["divergences"] if item["name"] == "controller_owned_agent_dir")
    assert record["absent_rule_lines"] == ["PLANTED_AGENTDIR_RULE_TOKEN"]
    assert record["requests_scoped"] == 1

    workspace_rule_missing = _without_system_line(case, "PLANTED_AGENTS_RULE_TOKEN")
    assert workspace_rule_missing["verdict"] == "fail"
    assert "requests" in {item["field"] for item in workspace_rule_missing["findings"]}

    assert "controller_owned_agent_dir" not in _divergence_names(_report("declared__o0_text_stop", _faithful("declared__o0_text_stop")))



def test_json_member_order_is_reported_when_bb_reorders_members() -> None:
    case = "declared__o0_text_stop"
    trace = _bb_trace(case)
    trace["requests"] = [dict(sorted(request.items())) for request in trace["requests"]]
    report = _report(case, {"trace": trace})
    assert report["verdict"] == "named_divergence"
    assert "json_member_order" in _divergence_names(report)


def _mutated(case: str, mutate) -> dict:
    replay = _faithful(case)
    mutate(replay["trace"])
    return _report(case, replay)


@pytest.mark.parametrize(
    ("case", "field", "mutate"),
    [
        (
            "declared__o1_four_tools_hashline",
            "tool_calls",
            lambda t: t["messages"].__setitem__(slice(1, 5), t["messages"][3:5] + t["messages"][1:3]),
        ),
        (
            "declared__o0_text_stop",
            "requests",
            lambda t: t["requests"][0]["messages"][0].__setitem__(
                "content",
                t["requests"][0]["messages"][0]["content"].replace("helpful assistant", "unhelpful assistant"),
            ),
        ),
        (
            "declared__o0_text_stop",
            "requests",
            lambda t: t["requests"][0]["tools"].__delitem__(0),
        ),
        (
            "declared__o0_text_stop",
            "requests",
            lambda t: t["requests"][0].__setitem__("store", False),
        ),
        (
            "declared__o5_http500_retry_disabled",
            "messages",
            lambda t: t["messages"][-1].__setitem__("errorMessage", "Different Error"),
        ),
        (
            "declared__o1_four_tools_hashline",
            "observations",
            lambda t: t["messages"][2].__setitem__("content", [{"type": "text", "text": "mutated content"}]),
        ),
        (
            "declared__o1_four_tools_hashline",
            "effects",
            lambda t: t["effects"]["written.txt"].__setitem__("sha256", "sha256:" + "0" * 64),
        ),
        (
            "declared__o6_disabled_resources_agents_md",
            "requests",
            lambda t: t["requests"][0]["messages"][0].__setitem__(
                "content",
                t["requests"][0]["messages"][0]["content"] + "\nExtra instruction line",
            ),
        ),
    ],
)
def test_consumer_visible_bugs_fail_with_specific_findings(case: str, field: str, mutate) -> None:
    report = _mutated(case, mutate)
    assert report["verdict"] == "fail"
    assert field in {item["field"] for item in report["findings"]}, report["findings"]


def test_extra_request_fails() -> None:
    def mutate(trace: dict) -> None:
        trace["requests"].append(deepcopy(trace["requests"][-1]))
        trace["request_count"] += 1
        trace["stream_fn_issued"] += 1

    report = _mutated("declared__o0_text_stop", mutate)
    assert report["verdict"] == "fail"
    assert {"requests", "request_count"} <= {item["field"] for item in report["findings"]}


def test_missing_message_timestamp_is_a_finding_not_normalized() -> None:
    report = _mutated("declared__o0_text_stop", lambda t: t["messages"][1].pop("timestamp"))
    assert report["verdict"] == "fail"
    assert any("timestamp" in item["detail"] for item in report["findings"])


def test_o4_cancel_divergence_near_miss_rejection() -> None:
    case = "declared__o4_bash_timeout_nonzero_cancel"
    replay = _faithful(case)
    replay["process"]["exit_code"] = 0
    assert _report(case, replay)["verdict"] == "fail"

    replay = _faithful(case)
    replay["process"]["terminal"]["primary_failure"]["code"] = "WrongError"
    assert _report(case, replay)["verdict"] == "fail"

    replay = _faithful(case)
    del replay["process"]
    assert _report(case, replay)["verdict"] == "fail"


def test_request_limit_without_upstream_cap_overrun_fails() -> None:
    def mutate(trace: dict) -> None:
        trace["termination"] = {"kind": "RequestLimitExceeded", "native_stop_reason": "error"}
        trace["messages"][-1] = {"role": "assistant", "content": [], "stopReason": "error"}

    report = _mutated("declared__o0_text_stop", mutate)
    assert report["verdict"] == "fail"
    assert any(item["field"] == "termination" for item in report["findings"])


def test_bb_trace_shape_fails_closed() -> None:
    trace = _bb_trace("declared__o0_text_stop")
    del trace["runtime_inputs"]["current_date"]
    with pytest.raises(OhMyPi16213ComparatorError, match="runtime_inputs_invalid"):
        project_bb_trace(trace)

    trace = _bb_trace("declared__o0_text_stop")
    trace["version"] = "18.1.17"
    with pytest.raises(OhMyPi16213ComparatorError, match="bb_trace_invalid"):
        project_bb_trace(trace)


O1_READ_CALL = "omp16213-call-o1_four_tools_hashline-01-read"


def _o1_with_read_text(text: str) -> dict:
    case = "declared__o1_four_tools_hashline"
    trace = _bb_trace(case)
    for message in trace["messages"]:
        if message.get("role") == "toolResult" and message.get("toolCallId") == O1_READ_CALL:
            message["content"][0]["text"] = text
    for request in trace["requests"]:
        for message in request["messages"]:
            if message.get("role") == "tool" and message.get("tool_call_id") == O1_READ_CALL:
                message["content"] = text
    return _report(case, {"trace": trace})


def test_files_only_seed_divergence_reports_absent_entries() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert manifest["replay_workspace"]["extra_directories"] == [".git"]


def test_files_only_seed_near_miss_unrelated_difference_fails() -> None:
    case = "declared__o1_four_tools_hashline"
    trace = _bb_trace(case)
    trace["messages"][2]["content"][0]["text"] = "unrelated file read difference\n"
    report = _report(case, {"trace": trace})
    assert report["verdict"] == "fail"
    assert "observations" in {item["field"] for item in report["findings"]}


def test_macos_workstation_fields_normalized() -> None:
    case = "declared__o0_text_stop"
    trace = _bb_trace(case)
    for msg in trace["requests"][0]["messages"]:
        if msg.get("role") == "system":
            msg["content"] = msg["content"].replace("- OS: linux 6.8.0-142-generic", "- OS: darwin 25.6.0")
            msg["content"] = msg["content"].replace("- Distro: Linux", "- Distro: Darwin")
            msg["content"] = msg["content"].replace("- Kernel: #142-Ubuntu SMP PREEMPT_DYNAMIC Wed Sep  2 14:24:27 UTC 2026", "- Kernel: Darwin Kernel Version 25.6.0")
            msg["content"] = msg["content"].replace("- Arch: x64", "- Arch: arm64")
            msg["content"] = msg["content"].replace("- CPU: INTEL(R) XEON(R) PLATINUM 8568Y+", "- CPU: Apple M3 Max")
    report = _report(case, {"trace": trace})
    assert report["passed"] is True
    assert report["verdict"] == "named_divergence"


def test_shortened_cwd_inside_home_normalized() -> None:
    case = "declared__o0_text_stop"
    runtime = dict(BB_RUNTIME)
    runtime["workspace_root"] = "/leases/pi-0571/home-abc/workspace"
    runtime["home_root"] = "/leases/pi-0571/home-abc"
    trace = _bb_trace(case, runtime=runtime)
    for msg in trace["requests"][0]["messages"]:
        if msg.get("role") == "system":
            msg["content"] = msg["content"].replace(f"'{runtime['workspace_root']}'", "'~/workspace'")
    report = _report(case, {"trace": trace})
    assert report["passed"] is True
    assert report["verdict"] == "named_divergence"


def _o4_bb_ledger_and_transcript() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    case = "declared__o4_bash_timeout_nonzero_cancel"
    date = _date(case)
    messages = _to_bb(deepcopy(_upstream_messages(case)), date)
    requests = _to_bb(deepcopy(_logical_bodies(case)), date)
    transcript = [{"body": req} for req in requests]
    consumer_id = "breadboard.oh-my-pi.v16.2.13"
    events = [
        {"sequence": 0, "phase": "initial", "source_id": consumer_id, "events": [messages[0]], "state": {"native_stop_reason": None, "public_stop": None, "request_count": 0, "stream_fn_issued": 0}},
        {"sequence": 1, "request_payload": requests[0]},
        {"sequence": 2, "turn": 1},
        {"sequence": 3, "turn": 1},
        {"sequence": 4, "response_payload": {}},
        {"sequence": 5, "phase": "assistant", "source_id": consumer_id, "events": [messages[1]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 1, "stream_fn_issued": 1}},
        {"sequence": 6, "tool_name": "bash", "call_id": "omp16213-call-o4_bash_timeout_nonzero_cancel-01-timeout"},
        {"sequence": 7, "submitted": True, "observation": {}},
        {"sequence": 8, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[2]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 1, "stream_fn_issued": 1}},
        {"sequence": 9, "request_payload": requests[1]},
        {"sequence": 10, "turn": 2},
        {"sequence": 11, "turn": 2},
        {"sequence": 12, "response_payload": {}},
        {"sequence": 13, "phase": "assistant", "source_id": consumer_id, "events": [messages[3]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 2, "stream_fn_issued": 2}},
        {"sequence": 14, "tool_name": "bash", "call_id": "omp16213-call-o4_bash_timeout_nonzero_cancel-02-exit42"},
        {"sequence": 15, "submitted": True, "observation": {}},
        {"sequence": 16, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[4]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 2, "stream_fn_issued": 2}},
        {"sequence": 17, "request_payload": requests[2]},
        {"sequence": 18, "turn": 3},
        {"sequence": 19, "turn": 3},
        {"sequence": 20, "response_payload": {}},
        {"sequence": 21, "phase": "assistant", "source_id": consumer_id, "events": [messages[5]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 3, "stream_fn_issued": 3}},
        {"sequence": 22, "tool_name": "bash", "call_id": "omp16213-call-o4_bash_timeout_nonzero_cancel-03-missing"},
        {"sequence": 23, "submitted": True, "observation": {}},
        {"sequence": 24, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[6]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 3, "stream_fn_issued": 3}},
        {"sequence": 25, "request_payload": requests[3]},
        {"sequence": 26, "turn": 4},
        {"sequence": 27, "turn": 4},
        {"sequence": 28, "response_payload": {}},
        {"sequence": 29, "phase": "assistant", "source_id": consumer_id, "events": [messages[7]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 4, "stream_fn_issued": 4}},
        {"sequence": 30, "tool_name": "bash", "call_id": "omp16213-call-o4_bash_timeout_nonzero_cancel-04-cancel"},
        {"sequence": 31, "reason": "episode close requested"},
        {"sequence": 32, "call_id": "omp16213-call-o4_bash_timeout_nonzero_cancel-04-cancel", "submitted": False, "observation": {"completion_index": 0, "content": [{"text": "(no output)", "type": "text"}], "id": "omp16213-call-o4_bash_timeout_nonzero_cancel-04-cancel", "isError": False, "terminate": False}},
        {"sequence": 33, "reason": "process exit"},
    ]
    ledger = {
        "schema_version": "bb.rl.runner-event-ledger.v2",
        "episode_id": "oh-my-pi-16213-declared--o4-bash-timeout-nonzero-cancel",
        "first_sequence": 0,
        "last_sequence": 33,
        "event_count": len(events),
        "events": events,
    }
    return ledger, transcript


def test_o4_ledger_input_positive_from_fixture_capture(tmp_path: Path) -> None:
    case = "declared__o4_bash_timeout_nonzero_cancel"
    ledger, transcript = _o4_bb_ledger_and_transcript()
    canonical_bytes = json.dumps(ledger).encode("utf-8")
    digest = _sha(canonical_bytes)
    ledger_file = tmp_path / "events.jsonl"
    ledger_file.write_bytes(canonical_bytes)

    rep = _report(case, {"ledger": ledger_file, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})
    assert rep["verdict"] == "named_divergence", rep["findings"]
    assert rep["findings"] == []
    assert rep["passed"] is True
    [cancel] = [d for d in rep["divergences"] if d["name"] == "external_cancel_signal"]
    assert cancel["anchor_tool_call_id"] == "omp16213-call-o4_bash_timeout_nonzero_cancel-04-cancel"
    assert cancel["bb"]["exit_code"] == 130
    assert cancel["workspace_state"]["proven"] is False
    assert cancel["anchor_unsubmitted_observation"]["content"][0]["text"] == "(no output)"
    assert cancel["runtime_inputs_source"] == "derived from sent system prompt"


def test_o4_ledger_negative_cases(tmp_path: Path) -> None:
    case = "declared__o4_bash_timeout_nonzero_cancel"
    ledger, transcript = _o4_bb_ledger_and_transcript()
    canonical_bytes = json.dumps(ledger).encode("utf-8")
    digest = _sha(canonical_bytes)
    ledger_file = tmp_path / "events.jsonl"
    ledger_file.write_bytes(canonical_bytes)

    # 1. ledger on a non-cancel case
    with pytest.raises(OhMyPi16213ComparatorError, match="ledger replay is admitted only for a signal-cancel case"):
        _report("declared__o0_text_stop", {"ledger": ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})

    # 2. missing process
    with pytest.raises(OhMyPi16213ComparatorError, match="ledger replay requires replay.process"):
        _report(case, {"ledger": ledger, "ledger_digest": digest, "transcript": transcript})

    # 3. exit code != 130
    proc = _cancel_process()
    proc["exit_code"] = 1
    rep = _report(case, {"ledger": ledger, "ledger_digest": digest, "transcript": transcript, "process": proc})
    assert rep["verdict"] == "fail"
    assert any(item["field"] == "process" for item in rep["findings"])

    # 4. digest mismatch
    with pytest.raises(OhMyPi16213ComparatorError, match="ledger digest mismatch"):
        _report(case, {"ledger": ledger_file, "ledger_digest": "sha256:" + "0" * 64, "transcript": transcript, "process": _cancel_process()})

    # 5. non-contiguous sequence
    bad_ledger = deepcopy(ledger)
    bad_ledger["events"][5]["sequence"] = 99
    with pytest.raises(OhMyPi16213ComparatorError, match="non-contiguous ledger sequence"):
        _report(case, {"ledger": bad_ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})

    # 6. message mismatch before the anchor
    bad_ledger = deepcopy(ledger)
    bad_ledger["events"][0]["events"][0]["content"][0]["text"] += " mutated"
    rep = _report(case, {"ledger": bad_ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})
    assert rep["verdict"] == "fail"
    assert any(item["field"] == "messages" for item in rep["findings"])

    # 7. tool-arg mismatch
    bad_ledger = deepcopy(ledger)
    bad_ledger["events"][5]["events"][0]["content"][1]["arguments"]["timeout"] = 999
    rep = _report(case, {"ledger": bad_ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})
    assert rep["verdict"] == "fail"
    assert any(item["field"] == "tool_calls" for item in rep["findings"])
