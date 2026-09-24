from __future__ import annotations

import hashlib

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from breadboard.rl.harness.hermes_tools import HermesToolRuntime, HermesToolRuntimeError

from conformance.comparators.hermes_agent import (
    TRACE_SCHEMA_VERSION,
    compare_cases,
    project_bb_trace,
    project_supplier_case,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hermes_agent"
CASES = tuple(sorted(path for path in FIXTURES.iterdir() if path.is_dir()))
PACKET_TRACE_SHA256 = {
    "H-01-normal-memory-skill-write": "fe1f1d43161e2b5df36ec9226bf2d78be8a6540493e0d20661c3d3652d42a307",
    "H-02-mixed-invalid-name": "83866a6d4e2a95470569fba1cbb8843d53471cc52e6dd97677a80260ba1fed23",
    "H-03-visible-empty-recovery": "270e7f13668ed301936e9b02800b2e810a938f99d500495900eac3606fa473be",
    "H-04-name-repair-duplicate": "aaed681f1c5f4bead25a118091197fb67120427ecefec5db0a242ea06da27c55",
    "H-05-terminal-lifecycle": "b0b801a0b7affa832aa381b40cfa140d0efb89e8e8f499a656c8c175e47bf36e",
    "H-06-request-budget-stop": "bd65f26d9003fd3bd3346ba8f198a42663b901bb0489ee75c601316fe8130c72",
}
TARGET_CONFIG = Path(__file__).resolve().parents[2] / "config/e4_targets/hermes_agent/2026.9.11/native-config.json"


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_fixture_bytes_match_original_packet(case_dir: Path) -> None:
    assert hashlib.sha256((case_dir / "trace.json").read_bytes()).hexdigest() == (
        PACKET_TRACE_SHA256[case_dir.name]
    )


def test_declared_schema_overlay_advertises_only_bounded_tools(tmp_path: Path) -> None:
    config = json.loads(TARGET_CONFIG.read_bytes())
    source = deepcopy(json.loads((FIXTURES / "H-01-normal-memory-skill-write/trace.json").read_bytes())["requests"][0]["body"]["tools"])
    state = SimpleNamespace(tools=source)
    runtime = HermesToolRuntime(
        state, workspace=tmp_path, scratch=tmp_path, hermes_home=tmp_path,
        remaining=lambda: 120, schema_overlay=config["schema_overlay"],
    )
    schemas = runtime._bounded_tool_schemas()
    by_name = {schema["function"]["name"]: schema["function"] for schema in schemas}
    assert "Documents auto-extract" not in by_name["read_file"]["description"]
    terminal = by_name["terminal"]
    assert not {"background", "pty", "notify"} & terminal["parameters"]["properties"].keys()
    assert "30" in terminal["parameters"]["properties"]["timeout"]["description"]
    assert "600" not in terminal["description"] + terminal["parameters"]["properties"]["timeout"]["description"]
    source[-2]["function"]["description"] += " unauthorized"
    with pytest.raises(HermesToolRuntimeError, match="schema"):
        runtime._bounded_tool_schemas()



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


def _changed_max_tokens(trace: dict[str, Any]) -> None:
    trace["controls"]["max_tokens"] = 1024


def _changed_provider_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["provider_deadline"] = 60
    trace["controls"]["provider_timeout"] = 60


def _changed_native_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["native_deadline"] = 10
    trace["controls"]["tool_deadline"] = 10


def _changed_watchdog_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["watchdog_deadline"] = 15
    trace["controls"]["watchdog"] = 15


def _changed_terminal_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["terminal_deadline"] = 10
    trace["controls"]["terminal_timeout"] = 10


def _changed_fallback(trace: dict[str, Any]) -> None:
    trace["controls"]["fallback"] = True


def _changed_retry(trace: dict[str, Any]) -> None:
    trace["controls"]["api_max_retries"] = 0


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
        ("H-01-normal-memory-skill-write", _changed_max_tokens),
        ("H-01-normal-memory-skill-write", _changed_provider_deadline),
        ("H-01-normal-memory-skill-write", _changed_native_deadline),
        ("H-01-normal-memory-skill-write", _changed_watchdog_deadline),
        ("H-01-normal-memory-skill-write", _changed_terminal_deadline),
        ("H-01-normal-memory-skill-write", _changed_fallback),
        ("H-01-normal-memory-skill-write", _changed_retry),
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
        "changed_max_tokens",
        "changed_provider_deadline",
        "changed_native_deadline",
        "changed_watchdog_deadline",
        "changed_terminal_deadline",
        "changed_fallback",
        "changed_retry",
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


def _replace_strings(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_strings(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, old, new) for key, item in value.items()}
    return value


def test_workspace_roots_are_typed_and_fail_closed() -> None:
    case_dir, supplier = _replay("H-01-normal-memory-skill-write")
    observed = _replace_strings(
        supplier,
        str(case_dir / "workspace"),
        "/lease/workspace-abc/repository",
    )
    observed["runtime"] = {"cwd": "/lease/workspace-abc/repository"}
    report = compare_cases(case_dir, observed)
    assert report["ok"] is True
    assert report["normalizations"] == ["workspace_root:<WORKSPACE>"]

    outside = deepcopy(observed)
    outside["requests"][0]["body"]["messages"][0]["content"] = "/outside/not-authorized"
    assert compare_cases(case_dir, outside)["ok"] is False

    relative_mismatch = deepcopy(observed)
    relative_mismatch["requests"][0]["body"]["messages"][0]["content"] = (
        "/lease/workspace-abc/repository/different.txt"
    )
    assert compare_cases(case_dir, relative_mismatch)["ok"] is False

    missing_runtime = deepcopy(observed)
    del missing_runtime["runtime"]
    report = compare_cases(case_dir, missing_runtime)
    assert report["ok"] is False
    assert "runtime.cwd" in report["errors"][0]


def test_comparator_rejects_name_only_tool_schemas() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    replay["requests"][0]["body"]["tools"] = [
        {"type": "function", "function": {"name": t["function"]["name"]}}
        for t in replay["requests"][0]["body"]["tools"]
    ]
    report = compare_cases(case_dir, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any("tools" in a.get("detail", "") for a in report["assertions"] if a["status"] == "failed")
