#!/usr/bin/env python3
from __future__ import annotations

from ct_semantic_helpers import as_dict, as_list, compare, expected, report, run_mode

PHASE_ORDER = {"before": 0, "install_pre": 0, "install_post": 1, "update_pre": 2, "conflict_pre": 3, "after": 4}


def _phase_key(value: object) -> tuple[int, int, str]:
    if not isinstance(value, dict):
        return (99, 0, "")
    return (PHASE_ORDER.get(str(value.get("phase")), 99), -int(value.get("priority", 0)), str(value.get("id")))


def phase_priority_order(data: dict) -> dict:
    specs = as_list(data.get("specs", []), "/specs")
    ordered = sorted(specs, key=_phase_key)
    actual = {"spec_count": len(specs), "actual_order": [s.get("id") for s in ordered if isinstance(s, dict)], "error_count": 0}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.extension_semantics_report.v1", mode="phase_priority_order", payload_path="")


def dependency_order(data: dict) -> dict:
    specs = [as_dict(s, "/specs/*") for s in as_list(data.get("specs", []), "/specs")]
    by_id = {s["id"]: s for s in specs}
    ordered: list[str] = []
    visiting: set[str] = set()
    def visit(name: str) -> None:
        if name in ordered:
            return
        if name in visiting:
            raise ValueError("dependency cycle")
        visiting.add(name)
        for dep in by_id[name].get("after", []):
            if dep in by_id:
                visit(dep)
        visiting.remove(name)
        ordered.append(name)
    for spec in specs:
        visit(spec["id"])
    actual = {"spec_count": len(specs), "actual_order": ordered, "error_count": 0}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.extension_semantics_report.v1", mode="dependency_order", payload_path="")


def runtime_hook_execution(data: dict) -> dict:
    events = as_list(data.get("runtime_events", []), "/runtime_events")
    required = as_list(data.get("expected_required_phases", []), "/expected_required_phases")
    statuses = [e.get("status") for e in events if isinstance(e, dict)]
    observed_phases = {e.get("phase") for e in events if isinstance(e, dict)}
    isolated_failures = [e for e in events if isinstance(e, dict) and e.get("isolated") and e.get("status") in {"timed_out", "failed", "failed_isolated"}]
    fatal = any(e.get("status") == "fatal" for e in events if isinstance(e, dict))
    actual = {
        "timed_out_count": statuses.count("timed_out"),
        "event_count": len(events),
        "isolated_failure_count": len(isolated_failures),
        "required_phase_count": len(required),
        "observed_phase_count": len(observed_phases),
        "continued_after_timeout": bool(data.get("continued_after_timeout", not fatal and "timed_out" in statuses)),
        "error_count": 0,
    }
    exp = expected(data)
    if not exp:
        exp = data.get("expected_output", {})
    return report(actual, compare(actual, exp), schema_version="bb.ct.extension_semantics_report.v1", mode="runtime_hook_execution", payload_path="")


if __name__ == "__main__":
    raise SystemExit(run_mode({"phase_priority_order": phase_priority_order, "dependency_order": dependency_order, "runtime_hook_execution": runtime_hook_execution}, schema_version="bb.ct.extension_semantics_report.v1"))
