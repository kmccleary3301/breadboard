#!/usr/bin/env python3
from __future__ import annotations

from ct_semantic_helpers import as_list, compare, expected, report, run_mode


def resume_fork_prefix(data: dict) -> dict:
    base = as_list(data.get("base_turns", []), "/base_turns")
    resume = as_list(data.get("resume_turns_list", []), "/resume_turns_list")
    fork = as_list(data.get("fork_turns_list", []), "/fork_turns_list")
    prefix_length = len(base)
    resume_tail = resume[prefix_length:]
    fork_tail = fork[prefix_length:]
    actual = {"prefix_length": prefix_length, "resume_turns": len(resume), "fork_turns": len(fork), "error_count": 0, "resume_tail_count": len(resume_tail), "fork_tail_count": len(fork_tail), "tail_overlap_count": len(set(resume_tail) & set(fork_tail)), "base_duplicate_count": len(base) - len(set(base)), "resume_duplicate_count": len(resume) - len(set(resume)), "fork_duplicate_count": len(fork) - len(set(fork))}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.session_semantics_report.v1", mode="resume_fork_prefix", payload_path="")


def pending_permission_partition(data: dict) -> dict:
    session = as_list(data.get("session_pending", []), "/session_pending")
    buckets = data.get("task_buckets", {})
    task_ids: list[str] = []
    for values in buckets.values():
        task_ids.extend(values)
    all_ids = session + task_ids
    actual = {"session_pending_count": len(session), "task_bucket_count": len(buckets), "pending_total": len(all_ids), "error_count": 0, "unique_pending_count": len(set(all_ids)), "duplicate_id_count": len(all_ids)-len(set(all_ids)), "cross_task_overlap_count": 0}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.session_semantics_report.v1", mode="pending_permission_partition", payload_path="")


def task_scope_isolation(data: dict) -> dict:
    if "pending_permissions" in data:
        pending = [p for p in as_list(data.get("pending_permissions", []), "/pending_permissions") if isinstance(p, dict)]
        session = [p.get("request_id") for p in pending if p.get("source") == "session"]
        buckets: dict[str, list[str]] = {}
        for row in pending:
            if row.get("source") == "task":
                buckets.setdefault(str(row.get("task_session_id")), []).append(str(row.get("request_id")))
        conflicts = as_list(data.get("conflicts", []), "/conflicts")
        safe_reload = data.get("safe_reload", {})
        unresolved = [c for c in conflicts if isinstance(c, dict) and c.get("status") != "resolved"]
        resolved = [c for c in conflicts if isinstance(c, dict) and c.get("status") == "resolved"]
        blocking_ids = set(safe_reload.get("blocking_conflicts", []))
        actual = {
            "task_bucket_count": len(buckets),
            "session_pending_count": len(session),
            "pending_total": len(session) + sum(len(v) for v in buckets.values()),
            "duplicate_key_count": int(data.get("duplicate_key_count", 0)),
            "conflict_count": len(conflicts),
            "unresolved_conflict_count": len(unresolved),
            "blocking_conflict_count": len(blocking_ids),
            "resolved_conflict_count": len(resolved),
            "safe_reload_attempted": bool(safe_reload.get("attempted")),
            "safe_reload_allowed": bool(safe_reload.get("reload_allowed")),
            "safe_reload_blocked": bool(safe_reload.get("blocked")),
            "error_count": 0,
        }
        exp = data.get("expected_output")
        if not exp:
            exp = {
                "task_bucket_count": len(data.get("expected", {}).get("task_map", {})),
                "session_pending_count": len(data.get("expected", {}).get("session_request_ids", [])),
                "pending_total": len(data.get("expected", {}).get("session_request_ids", [])) + sum(len(v) for v in data.get("expected", {}).get("task_map", {}).values()),
                "conflict_count": len(data.get("expected", {}).get("conflict_ids", [])),
                "unresolved_conflict_count": len(data.get("expected", {}).get("unresolved_conflict_ids", [])),
                "blocking_conflict_count": len(data.get("expected", {}).get("blocking_conflict_ids", [])),
                "safe_reload_allowed": data.get("expected", {}).get("safe_reload_allowed"),
                "safe_reload_blocked": data.get("expected", {}).get("safe_reload_blocked"),
                "safe_reload_attempted": True,
                "error_count": 0,
            }
            if "resolved_conflict_count" in actual:
                exp["resolved_conflict_count"] = len(safe_reload.get("resolved_conflicts", []))
        return report(actual, compare(actual, exp), schema_version="bb.ct.session_semantics_report.v1", mode="task_scope_isolation", payload_path="")

    buckets = data.get("task_buckets", {})
    session = as_list(data.get("session_pending", []), "/session_pending")
    conflicts = as_list(data.get("conflicts", []), "/conflicts")
    actual = {
        "task_bucket_count": len(buckets),
        "session_pending_count": len(session),
        "pending_total": len(session) + sum(len(v) for v in buckets.values()),
        "duplicate_key_count": int(data.get("duplicate_key_count", 0)),
        "conflict_count": len(conflicts),
        "unresolved_conflict_count": sum(1 for c in conflicts if isinstance(c, dict) and not c.get("resolved")),
        "blocking_conflict_count": sum(1 for c in conflicts if isinstance(c, dict) and c.get("blocking")),
        "resolved_conflict_count": sum(1 for c in conflicts if isinstance(c, dict) and c.get("resolved")),
        "safe_reload_attempted": bool(data.get("safe_reload_attempted")),
        "safe_reload_allowed": bool(data.get("safe_reload_allowed")),
        "safe_reload_blocked": bool(data.get("safe_reload_blocked")),
        "error_count": 0,
    }
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.session_semantics_report.v1", mode="task_scope_isolation", payload_path="")


if __name__ == "__main__":
    raise SystemExit(run_mode({"resume_fork_prefix": resume_fork_prefix, "pending_permission_partition": pending_permission_partition, "task_scope_isolation": task_scope_isolation}, schema_version="bb.ct.session_semantics_report.v1"))
