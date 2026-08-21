#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.longrun.flags import resolve_episode_max_steps, resolve_longrun_policy_profile



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


def _expected(payload: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(payload.get("expected"), "/expected")


def _compare(actual: dict[str, Any], expected: dict[str, Any], keys: list[str]) -> list[str]:
    violations: list[str] = []
    for key in keys:
        if actual.get(key) != expected.get(key):
            violations.append(f"{key} expected {expected.get(key)!r}, got {actual.get(key)!r}")
    return violations


def _report(actual: dict[str, Any], violations: list[str]) -> dict[str, Any]:
    out = dict(actual)
    out["violation_count"] = len(violations)
    out["violations"] = violations
    out["ok"] = not violations
    return out


def check_episode_max_steps_enabled_override(payload: dict[str, Any]) -> dict[str, Any]:
    actual = resolve_episode_max_steps(payload.get("config"), int(payload.get("default_max_steps", 1)))
    expected = _expected(payload)
    violations = [] if actual == expected.get("actual") else [f"actual expected {expected.get('actual')!r}, got {actual!r}"]
    return _report({"actual": actual}, violations)


def check_episode_max_steps_disabled_default(payload: dict[str, Any]) -> dict[str, Any]:
    return check_episode_max_steps_enabled_override(payload)


def check_policy_profile_env_override(payload: dict[str, Any]) -> dict[str, Any]:
    env = _as_dict(payload.get("env"), "/env")
    old = os.environ.get("BREADBOARD_LONGRUN_POLICY_PROFILE")
    try:
        if env.get("BREADBOARD_LONGRUN_POLICY_PROFILE") is None:
            os.environ.pop("BREADBOARD_LONGRUN_POLICY_PROFILE", None)
        else:
            os.environ["BREADBOARD_LONGRUN_POLICY_PROFILE"] = str(env["BREADBOARD_LONGRUN_POLICY_PROFILE"])
        actual = resolve_longrun_policy_profile(payload.get("config"), str(payload.get("default_profile", "balanced")))
    finally:
        if old is None:
            os.environ.pop("BREADBOARD_LONGRUN_POLICY_PROFILE", None)
        else:
            os.environ["BREADBOARD_LONGRUN_POLICY_PROFILE"] = old
    expected = _expected(payload)
    violations = [] if actual == expected.get("actual") else [f"actual expected {expected.get('actual')!r}, got {actual!r}"]
    return _report({"actual": actual}, violations)


def check_single_rollback_then_stop(payload: dict[str, Any]) -> dict[str, Any]:
    run = _as_dict(payload.get("run"), "/run")
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": run.get("stop_reason"),
        "actual_episodes_run": run.get("episodes_run"),
        "actual_rollback_used": bool(run.get("rollback_used")),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_convergence_stage_bounded(payload: dict[str, Any]) -> dict[str, Any]:
    run = _as_dict(payload.get("run"), "/run")
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": run.get("stop_reason"),
        "actual_episodes_run": run.get("episodes_run"),
        "convergence_bounded": bool(run.get("convergence_bounded")),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_queue_ordering_visibility(payload: dict[str, Any]) -> dict[str, Any]:
    queue = _as_list(payload.get("queue"), "/queue")
    expected = _expected(payload)
    open_items = [item for item in queue if isinstance(item, dict) and item.get("status") == "open"]
    visible_claim_order = [item.get("claim_id") for item in queue if isinstance(item, dict) and item.get("claim_visible") is True]
    actual = {
        "actual_stop_reason": payload.get("stop_reason"),
        "actual_open_count": len(open_items),
        "actual_claim_order": visible_claim_order,
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_global_token_budget_stop(payload: dict[str, Any]) -> dict[str, Any]:
    usage = _as_dict(payload.get("usage"), "/usage")
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": payload.get("stop_reason"),
        "actual_episodes_run": payload.get("episodes_run"),
        "actual_total_tokens_used": usage.get("total_tokens_used"),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_policy_boundary_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    cases = _as_list(payload.get("cases"), "/cases")
    violations: list[str] = []
    for index, case in enumerate(cases):
        row = _as_dict(case, f"/cases/{index}")
        if row.get("expected_decision") != row.get("observed_decision"):
            violations.append(f"/cases/{index} decision mismatch")
    return _report({"case_count": len(cases)}, violations)


def check_queue_pause_resume_durability(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(payload)
    actual = {
        "actual_run1_stop_reason": payload.get("run1", {}).get("stop_reason"),
        "actual_run2_stop_reason": payload.get("run2", {}).get("stop_reason"),
        "actual_resumed_from_state": bool(payload.get("run2", {}).get("resumed_from_state")),
        "actual_open_count": len([item for item in _as_list(payload.get("queue"), "/queue") if isinstance(item, dict) and item.get("status") == "open"]),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_global_subcall_budget_stop(payload: dict[str, Any]) -> dict[str, Any]:
    usage = _as_dict(payload.get("usage"), "/usage")
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": payload.get("stop_reason"),
        "actual_episodes_run": payload.get("episodes_run"),
        "actual_total_subcalls_used": usage.get("total_subcalls_used"),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_subcall_budget_resume_precheck(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": payload.get("stop_reason"),
        "actual_resumed_from_state": bool(payload.get("resumed_from_state")),
        "actual_runner_calls": payload.get("runner_calls"),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_provider_coupled_budget_trace(payload: dict[str, Any]) -> dict[str, Any]:
    events = _as_list(payload.get("events"), "/events")
    expected = _expected(payload)
    actual = {
        "actual_stop_reason": payload.get("stop_reason"),
        "actual_provider_error_count": len([e for e in events if isinstance(e, dict) and e.get("type") == "provider_error"]),
        "actual_backoff_count": len([e for e in events if isinstance(e, dict) and e.get("type") == "backoff"]),
        "actual_total_subcalls_used": payload.get("usage", {}).get("total_subcalls_used"),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_queue_multi_resume_durability(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(payload)
    queue = _as_list(payload.get("queue"), "/queue")
    actual = {
        "actual_run1_stop_reason": payload.get("run1", {}).get("stop_reason"),
        "actual_run2_stop_reason": payload.get("run2", {}).get("stop_reason"),
        "actual_run3_stop_reason": payload.get("run3", {}).get("stop_reason"),
        "actual_run2_resumed_from_state": bool(payload.get("run2", {}).get("resumed_from_state")),
        "actual_run3_resumed_from_state": bool(payload.get("run3", {}).get("resumed_from_state")),
        "actual_open_count": len([item for item in queue if isinstance(item, dict) and item.get("status") == "open"]),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_queue_multi_resume_persisted_backend(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(payload)
    queue = _as_list(payload.get("queue"), "/queue")
    actual = {
        "actual_run1_stop_reason": payload.get("run1", {}).get("stop_reason"),
        "actual_run2_stop_reason": payload.get("run2", {}).get("stop_reason"),
        "actual_run3_stop_reason": payload.get("run3", {}).get("stop_reason"),
        "actual_backend": payload.get("backend"),
        "actual_open_count": len([item for item in queue if isinstance(item, dict) and item.get("status") == "open"]),
    }
    return _report(actual, _compare(actual, expected, list(actual)))


def check_queue_backend_trace_coherence(payload: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(payload)
    trace = _as_dict(payload.get("trace"), "/trace")
    queue = _as_list(payload.get("queue"), "/queue")
    actual = {
        "actual_run3_stop_reason": payload.get("run3", {}).get("stop_reason"),
        "actual_backend": payload.get("backend"),
        "actual_open_count": len([item for item in queue if isinstance(item, dict) and item.get("status") == "open"]),
        "actual_episode_file_count": len(_as_list(trace.get("episode_files"), "/trace/episode_files")),
        "actual_summary_queue_matches_sidecar": bool(trace.get("summary_queue_matches_sidecar")),
        "actual_state_queue_matches_sidecar": bool(trace.get("state_queue_matches_sidecar")),
        "actual_summary_state_stop_reason_consistent": bool(trace.get("summary_state_stop_reason_consistent")),
    }
    if "external_trace_dir_used" in expected:
        actual["external_trace_dir_used"] = bool(trace.get("external_trace_dir_used"))
    return _report(actual, _compare(actual, expected, list(expected)))


CHECKS = {
    "episode_max_steps_enabled_override": check_episode_max_steps_enabled_override,
    "episode_max_steps_disabled_default": check_episode_max_steps_disabled_default,
    "policy_profile_env_override": check_policy_profile_env_override,
    "single_rollback_then_stop": check_single_rollback_then_stop,
    "convergence_stage_bounded": check_convergence_stage_bounded,
    "queue_ordering_visibility": check_queue_ordering_visibility,
    "global_token_budget_stop": check_global_token_budget_stop,
    "policy_boundary_matrix": check_policy_boundary_matrix,
    "queue_pause_resume_durability": check_queue_pause_resume_durability,
    "global_subcall_budget_stop": check_global_subcall_budget_stop,
    "subcall_budget_resume_precheck": check_subcall_budget_resume_precheck,
    "provider_coupled_budget_trace": check_provider_coupled_budget_trace,
    "queue_multi_resume_durability": check_queue_multi_resume_durability,
    "queue_multi_resume_persisted_backend": check_queue_multi_resume_persisted_backend,
    "queue_backend_trace_coherence": check_queue_backend_trace_coherence,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate recursion and long-run conformance semantics.")
    parser.add_argument("--mode", required=True, choices=sorted(CHECKS))
    parser.add_argument("--payload", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()

    out_path = _root_path(args.json_out)
    errors: list[str] = []
    try:
        payload = _as_dict(_load_json(_root_path(args.payload)), "/")
        report = CHECKS[args.mode](payload)
    except Exception as exc:
        report = {"ok": False, "violation_count": 1, "violations": [str(exc)]}
        errors.append(str(exc))
    report.setdefault("ok", False)
    report.update({"schema_version": "bb.ct.longrun_semantics_report.v1", "mode": args.mode, "payload_path": args.payload, "errors": errors})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
