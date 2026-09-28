from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conformance.comparators.pi_coding_agent_0_57_1 import (
    Pi0571ComparatorError,
    PiCodingAgent0571Comparator,
    project_bb_trace,
    project_upstream_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "e4_parity" / "fixtures" / "pi_0_57_1_supplier_cases"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
CASES = tuple(MANIFEST["cases"])
BB_RUNTIME = {
    "cwd": "/leases/pi-0571/workspace-abc",
    "home": "/leases/pi-0571/home-abc",
    "package_dir": "/srv/pi-0571/node_modules/@mariozechner/pi-coding-agent",
    "current_date_time": "Monday, April 5, 2027 at 09:07:03 PM UTC",
}
KIND_BY_STOP = {"stop": "Submitted", "error": "error", "toolUse": "running", "length": "length"}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _capture(case: str, name: str) -> Path:
    return FIXTURES / case / "capture" / name


def _rows(case: str) -> list[dict]:
    return [json.loads(line) for line in _capture(case, "http-transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def _events(case: str) -> list[dict]:
    return [json.loads(line) for line in _capture(case, "pi-events.jsonl").read_text(encoding="utf-8").splitlines()]


def _upstream_messages(case: str) -> list[dict]:
    return [event["message"] for event in _events(case) if event["type"] == "message_end"]


def _logical_bodies(case: str) -> list[dict]:
    return [
        json.loads(base64.b64decode(row["raw_body_base64"]))
        for row in _rows(case)
        if row["headers"]["X-Stainless-Retry-Count"] == "0"
    ]


def _to_bb(value, date_time: str):
    if isinstance(value, str):
        value = value.replace(f"Current date and time: {date_time}", f"Current date and time: {BB_RUNTIME['current_date_time']}")
        value = value.replace("/opt/bb-pi-runtime/pi/node_modules/@mariozechner/pi-coding-agent", BB_RUNTIME["package_dir"])
        value = value.replace("/capture/home", BB_RUNTIME["home"])
        return value.replace("/testbed", BB_RUNTIME["cwd"])
    if isinstance(value, list):
        return [_to_bb(item, date_time) for item in value]
    if isinstance(value, dict):
        if value.get("role") == "user":
            # BB receives upstream's task text verbatim, including its literal /testbed.
            return deepcopy(value)
        return {key: _to_bb(item, date_time) for key, item in value.items()}
    return value


def _date_time(case: str) -> str:
    system = _logical_bodies(case)[0]["messages"][0]["content"]
    return next(line for line in system.splitlines() if line.startswith("Current date and time: ")).split(": ", 1)[1]


def _effects(case: str) -> dict:
    pre = json.loads(_capture(case, "workspace-manifest-pre.json").read_text(encoding="utf-8"))
    post = json.loads(_capture(case, "workspace-manifest-post.json").read_text(encoding="utf-8"))
    effects = {}
    for path, value in post.items():
        if pre.get(path) != value:
            data = (FIXTURES / "blobs" / value["sha256"].removeprefix("sha256:")).read_bytes()
            effects[path] = {"exists": True, "bytes": len(data), "sha256": _sha(data), "content_utf8": data.decode("utf-8")}
    return effects


def _bb_trace(case: str, *, messages: list[dict] | None = None, requests: list[dict] | None = None) -> dict:
    """The trace a faithful BB clone produces for the declared case (one sampled request, no SDK retry)."""
    date_time = _date_time(case)
    messages = _upstream_messages(case) if messages is None else messages
    requests = _logical_bodies(case) if requests is None else requests
    last_stop = [message for message in messages if message["role"] == "assistant"][-1]["stopReason"]
    return {
        "schema_version": "bb.e4.pi-replay-trace.v1",
        "role": "replay",
        "profile": "pi",
        "version": "0.57.1",
        "case_id": case,
        "request_count": len(requests),
        "stream_fn_issued": len(requests),
        "messages": _to_bb(deepcopy(messages), date_time),
        "effects": _effects(case),
        "termination": {"kind": KIND_BY_STOP[last_stop], "native_stop_reason": last_stop},
        "requests": _to_bb(deepcopy(requests), date_time),
        "runtime_inputs": dict(BB_RUNTIME),
    }


def _p8_bb_trace() -> dict:
    case = "declared__p8_retry_sdk_recovers"
    messages = _upstream_messages(case)
    recovered = messages[-1]
    failed = _rows(case)[0]
    terminal = {
        "role": "assistant",
        "content": [],
        "api": recovered["api"],
        "provider": recovered["provider"],
        "model": recovered["model"],
        "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
                  "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}},
        "stopReason": "error",
        "errorMessage": f"{failed['status']} {failed['response']['error']['message']}",
        "timestamp": recovered["timestamp"] + 7,
    }
    return _bb_trace(case, messages=messages[:-1] + [terminal])


def _cancel_process() -> dict:
    return {"exit_code": 130, "terminal": {"status": "failed", "primary_failure": {"code": "CancelledError", "category": "CancelledError"}}}


def _faithful(case: str) -> dict:
    if case == "declared__p8_retry_sdk_recovers":
        return {"trace": _p8_bb_trace()}
    if case == "declared__p4_bash_timeout_nonzero_cancel":
        return {"trace": _bb_trace(case), "process": _cancel_process()}
    return {"trace": _bb_trace(case)}


def _report(case: str, replay: dict) -> dict:
    return PiCodingAgent0571Comparator()({"capture": {"case_dir": str(FIXTURES / case)}, "replay": replay})


def _divergence_names(report: dict) -> list[str]:
    return [item["name"] for item in report["divergences"]]


def test_fixture_manifest_binds_packet_members_and_derived_blobs() -> None:
    assert MANIFEST["source_packet"]["sha256"] == "sha256:14527fc6370808897647861cf2da09e9e7c29e48a233d517211105b8d17799a4"
    listed = {"manifest.json"}
    for entry in MANIFEST["files"]:
        path = FIXTURES / entry["path"]
        assert _sha(path.read_bytes()) == entry["sha256"], entry["path"]
        assert entry["member_path"] == "packet/cases/" + entry["path"]
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
    assert len(rows) == trace["requests_served"]
    assert projected["request_count"] == len(_logical_bodies(case)) == len([m for m in _upstream_messages(case) if m["role"] == "assistant"])
    assert all("timestamp" not in message for message in projected["messages"])
    assert [call["id"] for call in projected["tool_calls"]] == [
        block["id"] for message in _upstream_messages(case) if message["role"] == "assistant"
        for block in message["content"] if block["type"] == "toolCall"
    ]
    # Runtime-produced roots are tokenized; the task text keeps its literal /testbed.
    def without_user(value):
        if isinstance(value, dict):
            return {} if value.get("role") == "user" else {key: without_user(item) for key, item in value.items()}
        if isinstance(value, list):
            return [without_user(item) for item in value]
        return value
    assert "/testbed" not in json.dumps(without_user(projected))
    assert "/testbed" in projected["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize(
    ("case", "http_rows", "logical"),
    [
        ("declared__p5_retry_default_http500_exhausted", 4, 2),
        ("declared__p7_retry_session_recovers", 3, 1),
        ("declared__p8_retry_sdk_recovers", 2, 1),
    ],
)
def test_sdk_hidden_retry_is_reported_not_normalized(case: str, http_rows: int, logical: int) -> None:
    report = _report(case, _faithful(case))
    assert report["verdict"] == "named_divergence", report["findings"]
    record = next(item for item in report["divergences"] if item["name"] == "sdk_hidden_transport_retry")
    assert record["upstream_http_request_count"] == http_rows
    assert record["upstream_logical_request_count"] == logical
    assert record["bb_request_count"] == logical


def test_p8_bb_must_fail_with_the_sdk_derived_error_message() -> None:
    trace = _p8_bb_trace()
    trace["messages"][-1]["errorMessage"] = "500 status code (no body)"
    report = _report("declared__p8_retry_sdk_recovers", {"trace": trace})
    assert report["verdict"] == "fail"
    assert any(item["field"] == "messages" and "errorMessage" in item["detail"] for item in report["findings"])


@pytest.mark.parametrize("case", CASES)
def test_faithful_clone_trace_yields_only_named_divergences(case: str) -> None:
    report = _report(case, _faithful(case))
    assert report["findings"] == []
    assert report["verdict"] == "named_divergence"
    assert "sdk_transport_headers" in _divergence_names(report)
    counts = {(item["side"], item["rule"]): item["count"] for item in report["normalizations"]}
    assert counts[("upstream", "workspace_root")] > 0
    assert counts[("upstream", "current_date_time_system_line")] == len(_logical_bodies(case)) or case == "declared__p8_retry_sdk_recovers"
    assert counts[("bb", "message_timestamp")] == len(_faithful(case)["trace"]["messages"])


def test_case_specific_divergences_are_named() -> None:
    assert "tool_download_attempt" in _divergence_names(_report("declared__p1_all_tools_sequence", _faithful("declared__p1_all_tools_sequence")))
    assert "compaction_start_then_exit" in _divergence_names(_report("declared__p3_context_compaction", _faithful("declared__p3_context_compaction")))
    cancel = next(
        item for item in _report("declared__p4_bash_timeout_nonzero_cancel", _faithful("declared__p4_bash_timeout_nonzero_cancel"))["divergences"]
        if item["name"] == "external_cancel_signal"
    )
    assert cancel["upstream"]["exit_code"] == -15 and cancel["bb"]["exit_code"] == 130


def test_json_member_order_is_reported_when_bb_reorders_members() -> None:
    case = "declared__p0_text_stop"
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
        ("declared__p1_all_tools_sequence", "tool_calls",
         lambda t: t["messages"].__setitem__(slice(1, 5), t["messages"][3:5] + t["messages"][1:3])),
        ("declared__p0_text_stop", "requests",
         lambda t: t["requests"][0]["messages"][0].__setitem__("content", t["requests"][0]["messages"][0]["content"].replace("Be concise", "Be verbose"))),
        ("declared__p0_text_stop", "requests",
         lambda t: t["requests"][0]["tools"][0]["function"].pop("strict")),
        ("declared__p0_text_stop", "requests", lambda t: t["requests"][0].__setitem__("store", False)),
        ("declared__p5_retry_default_http500_exhausted", "messages",
         lambda t: t["messages"][-1].__setitem__("errorMessage", "500 Internal Server Error")),
        ("declared__p1_all_tools_sequence", "observations", lambda t: t["messages"][6].pop("details")),
        ("declared__p1_all_tools_sequence", "effects", lambda t: t["effects"]["p1-created.txt"].__setitem__("sha256", "sha256:" + "0" * 64)),
        ("declared__p6_disabled_resources_and_agents_md", "requests",
         lambda t: t["requests"][0]["messages"][0].__setitem__("content", t["requests"][0]["messages"][0]["content"] + "\nCurrent date and time: Monday, April 5, 2027 at 09:07:04 PM UTC")),
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

    report = _mutated("declared__p0_text_stop", mutate)
    assert report["verdict"] == "fail"
    assert {"requests", "request_count"} <= {item["field"] for item in report["findings"]}


def test_missing_message_timestamp_is_a_finding_not_normalized() -> None:
    report = _mutated("declared__p0_text_stop", lambda t: t["messages"][1].pop("timestamp"))
    assert report["verdict"] == "fail"
    assert any("timestamp" in item["detail"] for item in report["findings"])


def test_p4_extra_request_after_anchor_or_wrong_signal_fails() -> None:
    case = "declared__p4_bash_timeout_nonzero_cancel"
    replay = _faithful(case)
    replay["trace"]["requests"].append(deepcopy(replay["trace"]["requests"][-1]))
    replay["trace"]["request_count"] += 1
    assert "requests" in {item["field"] for item in _report(case, replay)["findings"]}
    replay = _faithful(case)
    replay["process"]["exit_code"] = 0
    assert _report(case, replay)["verdict"] == "fail"
    replay = _faithful(case)
    del replay["process"]
    assert _report(case, replay)["verdict"] == "fail"

def _p4_bb_ledger_and_transcript():
    case = "declared__p4_bash_timeout_nonzero_cancel"
    date_time = _date_time(case)
    messages = _to_bb(deepcopy(_upstream_messages(case)), date_time)
    requests = _to_bb(deepcopy(_logical_bodies(case)), date_time)
    transcript = [{"body": req} for req in requests]
    consumer_id = "breadboard.pi-coding-agent.v0.57.1"
    events = [
        {"sequence": 0, "phase": "initial", "source_id": consumer_id, "events": [messages[0]], "state": {"native_stop_reason": None, "public_stop": None, "request_count": 0, "stream_fn_issued": 0}},
        {"sequence": 1, "request_payload": requests[0]},
        {"sequence": 2, "turn": 1},
        {"sequence": 3, "turn": 1},
        {"sequence": 4, "response_payload": {}},
        {"sequence": 5, "phase": "assistant", "source_id": consumer_id, "events": [messages[1]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 1, "stream_fn_issued": 1}},
        {"sequence": 6, "tool_name": "bash", "call_id": "pi057-call-p4-00-timeout"},
        {"sequence": 7, "submitted": True, "observation": {}},
        {"sequence": 8, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[2]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 1, "stream_fn_issued": 1}},
        {"sequence": 9, "request_payload": requests[1]},
        {"sequence": 10, "turn": 2},
        {"sequence": 11, "turn": 2},
        {"sequence": 12, "response_payload": {}},
        {"sequence": 13, "phase": "assistant", "source_id": consumer_id, "events": [messages[3]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 2, "stream_fn_issued": 2}},
        {"sequence": 14, "tool_name": "bash", "call_id": "pi057-call-p4-01-nonzero"},
        {"sequence": 15, "submitted": True, "observation": {}},
        {"sequence": 16, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[4]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 2, "stream_fn_issued": 2}},
        {"sequence": 17, "request_payload": requests[2]},
        {"sequence": 18, "turn": 3},
        {"sequence": 19, "turn": 3},
        {"sequence": 20, "response_payload": {}},
        {"sequence": 21, "phase": "assistant", "source_id": consumer_id, "events": [messages[5]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 3, "stream_fn_issued": 3}},
        {"sequence": 22, "tool_name": "bash", "call_id": "pi057-call-p4-02-missing"},
        {"sequence": 23, "submitted": True, "observation": {}},
        {"sequence": 24, "phase": "observation_batch", "source_id": consumer_id, "events": [messages[6]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 3, "stream_fn_issued": 3}},
        {"sequence": 25, "request_payload": requests[3]},
        {"sequence": 26, "turn": 4},
        {"sequence": 27, "turn": 4},
        {"sequence": 28, "response_payload": {}},
        {"sequence": 29, "phase": "assistant", "source_id": consumer_id, "events": [messages[7]], "state": {"native_stop_reason": "toolUse", "public_stop": None, "request_count": 4, "stream_fn_issued": 4}},
        {"sequence": 30, "tool_name": "bash", "call_id": "pi057-call-p4-03-cancel"},
        {"sequence": 31, "reason": "episode close requested"},
        {"sequence": 32, "call_id": "pi057-call-p4-03-cancel", "submitted": False, "observation": {"completion_index": 0, "content": [{"text": "(no output)", "type": "text"}], "id": "pi057-call-p4-03-cancel", "isError": False, "terminate": False}},
        {"sequence": 33, "reason": "process exit"},
    ]
    ledger = {
        "schema_version": "bb.rl.runner-event-ledger.v2",
        "episode_id": "pi-coding-agent-0571-declared--p4-bash-timeout-nonzero-cancel",
        "first_sequence": 0,
        "last_sequence": 33,
        "event_count": len(events),
        "events": events,
    }
    return ledger, transcript


def test_p4_ledger_input_positive_from_fixture_capture(tmp_path: Path) -> None:
    case = "declared__p4_bash_timeout_nonzero_cancel"
    ledger, transcript = _p4_bb_ledger_and_transcript()
    canonical_bytes = json.dumps(ledger).encode("utf-8")
    digest = _sha(canonical_bytes)
    ledger_file = tmp_path / "events.jsonl"
    ledger_file.write_bytes(canonical_bytes)

    rep = _report(case, {"ledger": ledger_file, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})
    assert rep["verdict"] == "named_divergence", rep["findings"]
    assert rep["findings"] == []
    assert rep["passed"] is True
    [cancel] = [d for d in rep["divergences"] if d["name"] == "external_cancel_signal"]
    assert cancel["anchor_tool_call_id"] == "pi057-call-p4-03-cancel"
    assert cancel["bb"]["exit_code"] == 130
    assert cancel["workspace_state"]["proven"] is False
    assert cancel["anchor_unsubmitted_observation"]["content"][0]["text"] == "(no output)"
    assert cancel["runtime_inputs_source"] == "derived from sent system prompt"


def test_p4_ledger_negative_cases(tmp_path: Path) -> None:
    case = "declared__p4_bash_timeout_nonzero_cancel"
    ledger, transcript = _p4_bb_ledger_and_transcript()
    canonical_bytes = json.dumps(ledger).encode("utf-8")
    digest = _sha(canonical_bytes)
    ledger_file = tmp_path / "events.jsonl"
    ledger_file.write_bytes(canonical_bytes)

    # 1. ledger on a non-cancel case
    with pytest.raises(Pi0571ComparatorError, match="ledger replay is admitted only for a signal-cancel case"):
        _report("declared__p0_text_stop", {"ledger": ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})

    # 2. missing process
    with pytest.raises(Pi0571ComparatorError, match="ledger replay requires replay.process"):
        _report(case, {"ledger": ledger, "ledger_digest": digest, "transcript": transcript})

    # 3. exit code != 130
    proc = _cancel_process()
    proc["exit_code"] = 1
    rep = _report(case, {"ledger": ledger, "ledger_digest": digest, "transcript": transcript, "process": proc})
    assert rep["verdict"] == "fail"
    assert any(item["field"] == "process" for item in rep["findings"])

    # 4. digest mismatch
    with pytest.raises(Pi0571ComparatorError, match="ledger digest mismatch"):
        _report(case, {"ledger": ledger_file, "ledger_digest": "sha256:" + "0" * 64, "transcript": transcript, "process": _cancel_process()})

    # 5. non-contiguous sequence
    bad_ledger = deepcopy(ledger)
    bad_ledger["events"][5]["sequence"] = 99
    with pytest.raises(Pi0571ComparatorError, match="non-contiguous ledger sequence"):
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

    # 8. request-count mismatch
    bad_transcript = deepcopy(transcript)[:-1]
    rep = _report(case, {"ledger": ledger, "ledger_digest": digest, "transcript": bad_transcript, "process": _cancel_process()})
    assert rep["verdict"] == "fail"
    assert any(item["field"] in ("request_count", "requests") for item in rep["findings"])

    # 9. a home-like path left in content surfacing as a finding
    bad_ledger = deepcopy(ledger)
    bad_ledger["events"][0]["events"][0]["content"][0]["text"] += "\nrefer to /capture/home/config.json"
    rep = _report(case, {"ledger": bad_ledger, "ledger_digest": digest, "transcript": transcript, "process": _cancel_process()})
    assert rep["verdict"] == "fail"
    assert any(item["field"] == "messages" for item in rep["findings"])

def test_request_limit_without_upstream_cap_overrun_fails() -> None:
    def mutate(trace: dict) -> None:
        trace["termination"] = {"kind": "RequestLimitExceeded", "native_stop_reason": "stop"}

    report = _mutated("declared__p1_all_tools_sequence", mutate)
    assert report["verdict"] == "fail"


def test_bb_trace_shape_fails_closed() -> None:
    trace = _bb_trace("declared__p0_text_stop")
    del trace["runtime_inputs"]["current_date_time"]
    with pytest.raises(Pi0571ComparatorError, match="runtime_inputs_invalid"):
        project_bb_trace(trace)
    trace = _bb_trace("declared__p0_text_stop")
    trace["version"] = "0.73.1"
    with pytest.raises(Pi0571ComparatorError, match="bb_trace_invalid"):
        project_bb_trace(trace)



def test_task_text_literal_root_compares_as_is_on_both_sides() -> None:
    case = "declared__p0_text_stop"
    trace = _bb_trace(case)
    assert "/testbed" in trace["messages"][0]["content"][0]["text"]
    assert _report(case, {"trace": trace})["verdict"] == "named_divergence"
    rewritten = deepcopy(trace)
    rewritten["messages"][0]["content"][0]["text"] = rewritten["messages"][0]["content"][0]["text"].replace(
        "/testbed", BB_RUNTIME["cwd"])
    assert any(item["field"] == "messages" for item in _report(case, {"trace": rewritten})["findings"])


P1_LS_CALL = "pi057-call-p1-06-ls"


def _p1_with_ls_text(text: str) -> dict:
    case = "declared__p1_all_tools_sequence"
    trace = _bb_trace(case)
    for message in trace["messages"]:
        if message["role"] == "toolResult" and message["toolCallId"] == P1_LS_CALL:
            message["content"][0]["text"] = text
    for request in trace["requests"]:
        for message in request["messages"]:
            if message["role"] == "tool" and message["tool_call_id"] == P1_LS_CALL:
                message["content"] = text
    return _report(case, {"trace": trace})


def test_files_only_seed_is_a_named_divergence_listing_absent_entries() -> None:
    report = _p1_with_ls_text("binary.bin\nnested/\np1-created.txt\nREADME.txt\nscript.sh")
    assert report["verdict"] == "named_divergence", report["findings"]
    [record] = [item for item in report["divergences"] if item["name"] == "workspace_regular_files_only"]
    assert record["absent_entries"] == [".git", "symlink_to_readme.txt"]
    assert record["affected_tool_call_ids"] == [P1_LS_CALL]


def test_files_only_seed_does_not_hide_an_unrelated_difference_in_the_same_observation() -> None:
    report = _p1_with_ls_text("binary.bin\nnested/\np1-created.txt\nREADME.md\nscript.sh")
    assert report["verdict"] == "fail"
    assert "workspace_regular_files_only" not in _divergence_names(report)
    assert {"observations", "messages", "requests"} <= {item["field"] for item in report["findings"]}
