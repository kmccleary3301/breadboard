#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATES = {"queued", "running", "completed", "failed", "cancelled"}
ALLOWED_EDGES = {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "failed": set(),
    "completed": set(),
    "cancelled": set(),
}
TERMINAL = {"completed", "failed", "cancelled"}


def _root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_dict(value: Any, pointer: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{pointer} must be an object")
    return value


def _as_list(value: Any, pointer: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{pointer} must be an array")
    return value


def _transition_error(calls: dict[str, str], operation: dict[str, Any]) -> tuple[str | None, str]:
    op = operation.get("op")
    call_id = str(operation.get("call_id", ""))
    if not call_id:
        return "invalid_call_id", ""
    if op == "start":
        if call_id in calls:
            return "duplicate_start", calls[call_id]
        calls[call_id] = "queued"
        return None, "queued"
    if op != "transition":
        return "unknown_operation", calls.get(call_id, "unknown")
    if call_id not in calls:
        return "unknown_call", "unknown"
    from_state = calls[call_id]
    to_state = str(operation.get("to_state", ""))
    if to_state not in ALLOWED_STATES:
        return "unknown_target_state", from_state
    if from_state in TERMINAL:
        return "terminal_state_transition", from_state
    if to_state not in ALLOWED_EDGES[from_state]:
        return "invalid_transition_edge", from_state
    calls[call_id] = to_state
    return None, to_state


def _run_operations(operations: list[Any]) -> tuple[str | None, str]:
    calls: dict[str, str] = {}
    last_state = ""
    for raw in operations:
        op = _as_dict(raw, "/operations/*")
        error, state = _transition_error(calls, op)
        last_state = state
        if error is not None:
            return error, state
    return None, last_state


def check_one_bash_first_allowed(payload: dict[str, Any]) -> dict[str, Any]:
    calls = _as_list(payload.get("tool_calls"), "/tool_calls")
    violations: list[str] = []
    if not calls:
        violations.append("tool_calls must not be empty")
    first = _as_dict(calls[0], "/tool_calls/0") if calls else {}
    if first.get("tool") != "bash":
        violations.append("first tool call must be bash")
    if first.get("decision") != "allow":
        violations.append("first bash decision must be allow")
    return {"ok": not violations, "violation_count": len(violations), "violations": violations}


def check_disabled_edit_mask(payload: dict[str, Any]) -> dict[str, Any]:
    disabled = [str(item) for item in _as_list(payload.get("disabled_tools"), "/disabled_tools")]
    required = set(payload.get("required_disabled_tools") or [])
    missing = sorted(required - set(disabled))
    violations = [f"missing disabled tool {tool}" for tool in missing]
    return {"ok": not violations, "disabled_count": len(disabled), "violation_count": len(violations), "violations": violations}


def check_shell_deny_mask(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = _as_list(payload.get("decisions"), "/decisions")
    violations: list[str] = []
    for index, raw in enumerate(decisions):
        row = _as_dict(raw, f"/decisions/{index}")
        command = str(row.get("command", ""))
        expected = row.get("expected_decision")
        observed = row.get("observed_decision")
        if expected != observed:
            violations.append(f"/decisions/{index} decision mismatch")
        if expected == "deny" and row.get("reason") is None:
            violations.append(f"/decisions/{index} deny requires reason")
        if not command:
            violations.append(f"/decisions/{index} command required")
    return {"ok": not violations, "violation_count": len(violations), "violations": violations}


def check_runtime_transition_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    legal_paths = _as_list(payload.get("legal_paths"), "/legal_paths")
    invalid_cases = _as_list(payload.get("invalid_cases"), "/invalid_cases")
    violations: list[str] = []
    legal_pass = 0
    invalid_pass = 0
    for index, raw in enumerate(legal_paths):
        path = _as_dict(raw, f"/legal_paths/{index}")
        error, _state = _run_operations(_as_list(path.get("operations"), f"/legal_paths/{index}/operations"))
        if error is None:
            legal_pass += 1
        else:
            violations.append(f"/legal_paths/{index} unexpected {error}")
    for index, raw in enumerate(invalid_cases):
        case = _as_dict(raw, f"/invalid_cases/{index}")
        error, _state = _run_operations(_as_list(case.get("operations"), f"/invalid_cases/{index}/operations"))
        if error == case.get("expected_code"):
            invalid_pass += 1
        else:
            violations.append(f"/invalid_cases/{index} expected {case.get('expected_code')}, got {error}")
    return {
        "ok": not violations,
        "legal_path_count": len(legal_paths),
        "legal_path_pass_count": legal_pass,
        "invalid_case_count": len(invalid_cases),
        "invalid_case_pass_count": invalid_pass,
        "violation_count": len(violations),
        "violations": violations,
    }


def check_runtime_error_taxonomy(payload: dict[str, Any]) -> dict[str, Any]:
    cases = _as_list(payload.get("cases"), "/cases")
    violations: list[str] = []
    codes: set[str] = set()
    for index, raw in enumerate(cases):
        case = _as_dict(raw, f"/cases/{index}")
        error, from_state = _run_operations(_as_list(case.get("operations"), f"/cases/{index}/operations"))
        codes.add(str(case.get("expected_code")))
        if error != case.get("expected_code"):
            violations.append(f"/cases/{index} expected {case.get('expected_code')}, got {error}")
        if str(case.get("expected_from_state", from_state)) != from_state:
            violations.append(f"/cases/{index} expected from_state {case.get('expected_from_state')}, got {from_state}")
    return {"ok": not violations, "case_count": len(cases), "unique_code_count": len(codes), "violation_count": len(violations), "violations": violations}


def check_runtime_correction_loop_coupling(payload: dict[str, Any]) -> dict[str, Any]:
    attempts = _as_list(payload.get("attempts"), "/attempts")
    expected = _as_dict(payload.get("expected"), "/expected")
    violations: list[str] = []
    statuses = [str(row.get("status")) for row in attempts if isinstance(row, dict)]
    ids = [str(row.get("tool_call_id")) for row in attempts if isinstance(row, dict)]
    failed_count = statuses.count("failed")
    completed_count = statuses.count("completed")
    lineage_edges = 0
    for index, raw in enumerate(attempts):
        row = _as_dict(raw, f"/attempts/{index}")
        if row.get("operation_id") != expected.get("operation_id"):
            violations.append(f"/attempts/{index}/operation_id mismatch")
        if row.get("thread_id") != expected.get("thread_id"):
            violations.append(f"/attempts/{index}/thread_id mismatch")
        if expected.get("require_strict_attempt_increment") and row.get("attempt") != index + 1:
            violations.append(f"/attempts/{index}/attempt not strict")
        parent = row.get("retries_from_tool_call_id")
        if index == 0 and parent is not None:
            violations.append("first attempt must not retry from another call")
        if index > 0:
            if parent != ids[index - 1]:
                violations.append(f"/attempts/{index}/retries_from_tool_call_id must point at previous attempt")
            else:
                lineage_edges += 1
    if expected.get("min_attempts") is not None and len(attempts) < int(expected["min_attempts"]):
        violations.append("too few attempts")
    if expected.get("max_attempts") is not None and len(attempts) > int(expected["max_attempts"]):
        violations.append("too many attempts")
    if expected.get("require_final_completed") and statuses[-1:] != ["completed"]:
        violations.append("final attempt must be completed")
    if expected.get("require_failed_before_completed") and completed_count and any(status != "failed" for status in statuses[:-1]):
        violations.append("all attempts before final completion must be failed")
    return {
        "ok": not violations,
        "attempt_count": len(attempts),
        "failed_count": failed_count,
        "completed_count": completed_count,
        "lineage_edge_count": lineage_edges,
        "violation_count": len(violations),
        "violations": violations,
    }


CHECKS = {
    "one_bash_first_allowed": check_one_bash_first_allowed,
    "disabled_edit_mask": check_disabled_edit_mask,
    "shell_deny_mask": check_shell_deny_mask,
    "runtime_transition_matrix": check_runtime_transition_matrix,
    "runtime_error_taxonomy": check_runtime_error_taxonomy,
    "runtime_correction_loop_coupling": check_runtime_correction_loop_coupling,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tool policy and runtime transition semantics.")
    parser.add_argument("--mode", required=True, choices=sorted(CHECKS))
    parser.add_argument("--payload", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()
    out = _root_path(args.json_out)
    errors: list[str] = []
    try:
        payload = _as_dict(_load_json(_root_path(args.payload)), "/")
        report = CHECKS[args.mode](payload)
    except Exception as exc:
        report = {"ok": False, "violation_count": 1, "violations": [str(exc)]}
        errors.append(str(exc))
    report.setdefault("ok", False)
    report.update({"schema_version": "bb.ct.tool_policy_semantics_report.v1", "mode": args.mode, "payload_path": args.payload, "errors": errors})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
