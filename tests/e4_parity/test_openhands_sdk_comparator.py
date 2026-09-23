from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from conformance.comparators.openhands_sdk import compare_cases, project_bb_trace, project_supplier_case

FIXTURES = Path(__file__).parent / "fixtures" / "openhands_sdk"
CASES = tuple(sorted(path for path in FIXTURES.iterdir() if path.is_dir()))


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_supplier_projection_self_replays(case_dir: Path) -> None:
    supplier = project_supplier_case(case_dir)
    replay = project_bb_trace(deepcopy(supplier))
    report = compare_cases(case_dir, replay)
    assert report["ok"] is True
    assert report["failed"] == 0


def _replay(case: str) -> tuple[Path, dict]:
    path = FIXTURES / case
    supplier = project_supplier_case(path)
    return path, deepcopy(supplier)


def _drop_invalid_followup(trace: dict) -> None:
    trace["tool_calls"] = [call for call in trace["tool_calls"] if call["arguments"].get("command") != "create" or call["arguments"].get("path") != "after.txt"]


def _execute_cutoff_call(trace: dict) -> None:
    trace["file_effects"]["must-not-exist.txt"] = "sha256:" + "a" * 64


def _drop_corrective_nudge(trace: dict) -> None:
    trace["requests"] = trace["requests"][:1]
    trace["request_count"] = 1


def _add_ninth_request(trace: dict) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = 8
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _change_tool_order(trace: dict) -> None:
    trace["tool_calls"][0], trace["tool_calls"][1] = trace["tool_calls"][1], trace["tool_calls"][0]


def _change_security_risk_placement(trace: dict) -> None:
    trace["tool_calls"][0]["security_risk"] = "LOW"


def _change_file_effect(trace: dict) -> None:
    path = next(iter(trace["file_effects"]))
    if trace["file_effects"][path] is not None:
        trace["file_effects"][path] = "sha256:" + "b" * 64


def _change_termination(trace: dict) -> None:
    trace["termination"]["kind"] = "finished"


@pytest.mark.parametrize(
    ("case", "mutation"),
    [
        ("OH-02-invalid-call-continues", _drop_invalid_followup),
        ("OH-03-finish-cutoff", _execute_cutoff_call),
        ("OH-06-response-classification", _drop_corrective_nudge),
        ("OH-01-normal-file-effect", _add_ninth_request),
        ("OH-04-persistent-pty-reset", _change_tool_order),
        ("OH-01-normal-file-effect", _change_security_risk_placement),
        ("OH-01-normal-file-effect", _change_file_effect),
        ("OH-05-iteration-budget", _change_termination),
    ],
    ids=["per_call_validation", "finish_cutoff", "corrective_nudge", "extra_request", "tool_order", "security_risk", "file_effect", "termination"],
)
def test_negative_mutations_fail(case: str, mutation) -> None:
    path, replay = _replay(case)
    mutation(replay)
    report = compare_cases(path, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any("first difference" in item["detail"] for item in report["assertions"] if item["status"] == "failed")


def test_undeclared_placeholder_is_rejected() -> None:
    path, replay = _replay("OH-01-normal-file-effect")
    replay["requests"][0]["body"]["messages"][0]["content"] = "<TIMESTAMP>"
    replay["normalizations"] = []
    report = compare_cases(path, replay)
    assert report["ok"] is False
    assert "normalization" in report["errors"][0]


def _volatile_trace() -> dict:
    return {
        "schema_version": "bb.e4.openhands-sdk-trace.v1",
        "case_id": "volatile-case",
        "requests": [{
            "index": 0,
            "body": {
                "id": "oh-capture-response-abc",
                "call_id": "call-abc",
                "event_id": "123e4567-e89b-12d3-a456-426614174000",
                "timestamp": "2026-09-23T12:34:56Z",
            },
        }],
        "tool_calls": [{
            "tool_name": "terminal",
            "arguments": {"id": "call-abc"},
            "security_risk": "LOW",
        }],
        "observations": [{
            "event_kind": "ObservationEvent",
            "tool_name": "terminal",
            "is_error": False,
            "result": {"timestamp": "2026-09-23T12:34:56Z"},
        }],
        "file_effects": {},
        "termination": {"kind": "finished", "native_stop_reason": "stop"},
        "request_count": 1,
    }


def test_raw_volatile_trace_derives_all_static_normalizations() -> None:
    projected = project_bb_trace(_volatile_trace())
    assert projected["normalizations"] == [
        "call_id:<CALL_ID>",
        "event_uuid:<EVENT_UUID>",
        "response_id:<RESPONSE_ID>",
        "timestamp:<TIMESTAMP>",
    ]


def test_undeclared_literal_placeholder_remains_rejected() -> None:
    trace = _volatile_trace()
    trace["requests"][0]["body"]["id"] = "<RESPONSE_ID>"
    with pytest.raises(ValueError, match="normalization"):
        project_bb_trace(trace)
