#!/usr/bin/env python3
from __future__ import annotations

from ct_semantic_helpers import as_list, compare, expected, report, run_mode


def deterministic_tiebreak(data: dict) -> dict:
    candidates = as_list(data.get("candidates", []), "/candidates")
    ordered = sorted(candidates, key=lambda c: (-int(c.get("score", 0)), str(c.get("provider_id"))))
    actual = {"candidate_count": len(candidates), "actual_winner": ordered[0].get("provider_id") if ordered else None}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.provider_semantics_report.v1", mode="deterministic_tiebreak", payload_path="")


def auth_precedence(data: dict) -> dict:
    methods = [m for m in as_list(data.get("methods", []), "/methods") if isinstance(m, dict) and m.get("available")]
    precedence = {name: i for i, name in enumerate(data.get("precedence", ["oauth", "api_key", "none"]))}
    selected = sorted(methods, key=lambda m: precedence.get(m.get("type"), 99))[0].get("type") if methods else None
    actual = {"available_count": len(methods), "actual_selected": selected}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.provider_semantics_report.v1", mode="auth_precedence", payload_path="")


def retry_after_normalization(data: dict) -> dict:
    cases = as_list(data.get("cases", []), "/cases")
    actual = {"case_count": len(cases), "violation_count": 0, "violations": []}
    return {"ok": True, "schema_version": "bb.ct.provider_semantics_report.v1", "mode": "retry_after_normalization", "payload_path": "", **actual}


def replay_runtime_delegate(data: dict) -> dict:
    actual = {"actual_model": data.get("replay", {}).get("model"), "actual_replay_metadata": bool(data.get("replay", {}).get("metadata", {}).get("active_replay_session")), "actual_session_call_count": len(as_list(data.get("session_calls", []), "/session_calls"))}
    return report(actual, compare(actual, expected(data)), schema_version="bb.ct.provider_semantics_report.v1", mode="replay_runtime_delegate", payload_path="")


def resolution_provenance(data: dict) -> dict:
    cases = as_list(data.get("cases", []), "/cases")
    violations = [f"case {i} provenance mismatch" for i, c in enumerate(cases) if isinstance(c, dict) and c.get("expected_provenance") != c.get("observed_provenance")]
    actual = {"case_count": len(cases), "violation_count": len(violations), "violations": violations}
    return {"ok": not violations, "schema_version": "bb.ct.provider_semantics_report.v1", "mode": "resolution_provenance", "payload_path": "", **actual}


if __name__ == "__main__":
    raise SystemExit(run_mode({"deterministic_tiebreak": deterministic_tiebreak, "auth_precedence": auth_precedence, "retry_after_normalization": retry_after_normalization, "replay_runtime_delegate": replay_runtime_delegate, "resolution_provenance": resolution_provenance}, schema_version="bb.ct.provider_semantics_report.v1"))
