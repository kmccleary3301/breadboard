from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest

from conformance.comparators.hermes_agent import (
    TRACE_SCHEMA_VERSION,
    compare_cases,
    project_bb_trace,
    project_supplier_case,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hermes_agent"
CASES = tuple(sorted(path for path in FIXTURES.iterdir() if path.is_dir()))


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_supplier_projection_self_comparison(case_dir: Path) -> None:
    supplier = project_supplier_case(case_dir)
    replay = deepcopy(supplier)
    assert replay["schema_version"] == TRACE_SCHEMA_VERSION
    assert project_bb_trace(replay)["role"] == "breadboard"
    report = compare_cases(case_dir, replay)
    assert report["ok"] is True
    assert report["failed"] == 0


def _replay(case_name: str) -> tuple[Path, dict[str, Any]]:
    case_dir = FIXTURES / case_name
    return case_dir, deepcopy(project_supplier_case(case_dir))


def _extra_tool_advertised(trace: dict[str, Any]) -> None:
    trace["requests"][0]["body"]["tools"].append(
        {"type": "function", "function": {"name": "unexpected_tool"}}
    )


def _changed_tool_order(trace: dict[str, Any]) -> None:
    tools = trace["requests"][0]["body"]["tools"]
    tools[0], tools[1] = tools[1], tools[0]


def _streaming_true(trace: dict[str, Any]) -> None:
    trace["controls"]["streaming"] = True
    trace["requests"][0]["body"]["stream"] = True


def _hidden_retry(trace: dict[str, Any]) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = trace["request_count"]
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _missing_agents_context(trace: dict[str, Any]) -> None:
    trace["context"]["agents_md"] = []


def _name_repair_skipped(trace: dict[str, Any]) -> None:
    trace["tool_calls"][0]["repaired_name"] = trace["tool_calls"][0]["raw_tool_name"]
    trace["tool_calls"][0]["tool_name"] = trace["tool_calls"][0]["raw_tool_name"]


def _duplicate_executed_twice(trace: dict[str, Any]) -> None:
    trace["tool_results"].append(deepcopy(trace["tool_results"][0]))


def _visible_nudge_missing(trace: dict[str, Any]) -> None:
    trace["visible_corrections"] = []


def _ninth_request(trace: dict[str, Any]) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = trace["request_count"]
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _changed_effect(trace: dict[str, Any]) -> None:
    path = next(iter(trace["file_effects"]))
    trace["file_effects"][path] = "sha256:" + "b" * 64


def _changed_termination(trace: dict[str, Any]) -> None:
    trace["termination"]["kind"] = "finished"


@pytest.mark.parametrize(
    ("case_name", "mutation"),
    [
        ("H-01-normal-memory-skill-write", _extra_tool_advertised),
        ("H-01-normal-memory-skill-write", _changed_tool_order),
        ("H-01-normal-memory-skill-write", _streaming_true),
        ("H-01-normal-memory-skill-write", _hidden_retry),
        ("H-01-normal-memory-skill-write", _missing_agents_context),
        ("H-04-name-repair-duplicate", _name_repair_skipped),
        ("H-04-name-repair-duplicate", _duplicate_executed_twice),
        ("H-03-visible-empty-recovery", _visible_nudge_missing),
        ("H-06-request-budget-stop", _ninth_request),
        ("H-01-normal-memory-skill-write", _changed_effect),
        ("H-05-terminal-lifecycle", _changed_termination),
    ],
    ids=[
        "extra_tool_advertised",
        "changed_tool_order",
        "streaming_true",
        "hidden_retry",
        "missing_agents_context",
        "name_repair_skipped",
        "duplicate_executed_twice",
        "visible_nudge_missing",
        "ninth_request",
        "changed_effect",
        "changed_termination",
    ],
)
def test_negative_gate_fails_comparison(
    case_name: str, mutation: Callable[[dict[str, Any]], None]
) -> None:
    case_dir, replay = _replay(case_name)
    mutation(replay)
    report = compare_cases(case_dir, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any(
        "first difference" in assertion["detail"]
        for assertion in report["assertions"]
        if assertion["status"] == "failed"
    )


def test_h02_preserves_invalid_name_error_and_h04_repairs_and_deduplicates() -> None:
    invalid = project_supplier_case(FIXTURES / "H-02-mixed-invalid-name")
    assert invalid["tool_results"][0]["is_error"] is True
    assert "NOT_A_TOOL" in invalid["tool_results"][0]["result"]

    repaired = project_supplier_case(FIXTURES / "H-04-name-repair-duplicate")
    assert len(repaired["tool_calls"]) == 1
    assert repaired["tool_calls"][0]["raw_sample"]["name"] == "WriteFile"
    assert repaired["tool_calls"][0]["raw_sample"]["arguments"] == (
        '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}'
    )
    assert repaired["tool_calls"][0]["raw_tool_name"] == "WriteFile"
    assert repaired["tool_calls"][0]["repaired_name"] == "write_file"
    assert repaired["tool_calls"][0]["raw_arguments"] == (
        '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}'
    )

    assert len(repaired["tool_results"]) == 1

def test_h06_stops_at_eight_requests_without_ninth() -> None:
    trace = project_supplier_case(FIXTURES / "H-06-request-budget-stop")
    assert trace["request_count"] == 8
    assert len(trace["requests"]) == 8
    assert trace["termination"] == {
        "kind": "stopped",
        "native_stop_reason": "tool_calls",
    }
