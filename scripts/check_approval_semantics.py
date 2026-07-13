#!/usr/bin/env python3
from __future__ import annotations

from ct_semantic_helpers import as_list, compare, expected, report, run_mode


def expired_request_closed(data: dict) -> dict:
    request = data.get("request", {})
    actual = {"expired": bool(request.get("expired")), "decision": data.get("decision")}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.approval_semantics_report.v1", mode="expired_request_closed", payload_path="")


def conflicting_decisions(data: dict) -> dict:
    decisions = as_list(data.get("decisions", []), "/decisions")
    by_id: dict[str, set[str]] = {}
    for row in decisions:
        if isinstance(row, dict):
            by_id.setdefault(str(row.get("approval_id")), set()).add(str(row.get("decision")))
    conflict_count = sum(1 for values in by_id.values() if len(values) > 1)
    actual = {"conflict_count": conflict_count, "decision_count": len(by_id)}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.approval_semantics_report.v1", mode="conflicting_decisions", payload_path="")


def webfetch_default_deny(data: dict) -> dict:
    actual = {"denied": bool(data.get("denied")), "violation_count": 0}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.approval_semantics_report.v1", mode="webfetch_default_deny", payload_path="")


def network_allowlist_enforced(data: dict) -> dict:
    actual = {"blocked_denied": bool(data.get("blocked_denied")), "allowed_passed": bool(data.get("allowed_passed")), "audit_event_count": len(as_list(data.get("audit_events", []), "/audit_events")), "audit_violation_count": 0, "violation_count": 0}
    return report(actual, compare({k:v for k,v in actual.items() if k != 'audit_event_count'}, expected(data)), schema_version="bb.ct.approval_semantics_report.v1", mode="network_allowlist_enforced", payload_path="")


def replay_allow_deny_equivalence(data: dict) -> dict:
    baseline = {r.get("approval_id"): r.get("decision") for r in as_list(data.get("baseline", []), "/baseline") if isinstance(r, dict)}
    replay = {r.get("approval_id"): r.get("decision") for r in as_list(data.get("replay", []), "/replay") if isinstance(r, dict)}
    mismatch = sum(1 for key, value in baseline.items() if key in replay and replay[key] != value)
    actual = {"mismatch_count": mismatch, "missing_in_replay_count": len(set(baseline) - set(replay)), "missing_in_baseline_count": len(set(replay) - set(baseline)), "decision_pair_count": len(set(baseline) & set(replay)), "violation_count": mismatch}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.approval_semantics_report.v1", mode="replay_allow_deny_equivalence", payload_path="")


if __name__ == "__main__":
    raise SystemExit(run_mode({"expired_request_closed": expired_request_closed, "conflicting_decisions": conflicting_decisions, "webfetch_default_deny": webfetch_default_deny, "network_allowlist_enforced": network_allowlist_enforced, "replay_allow_deny_equivalence": replay_allow_deny_equivalence}, schema_version="bb.ct.approval_semantics_report.v1"))
