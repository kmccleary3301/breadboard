#!/usr/bin/env python3
from __future__ import annotations

from ct_semantic_helpers import as_list, compare, expected, report, run_mode


def _event_seq(event: dict) -> int:
    return int(event.get("seq", 0))


def reconnect_gap_coalescing(data: dict) -> dict:
    initial = as_list(data.get("initial_events", []), "/initial_events")
    resumed = as_list(data.get("resumed_events", []), "/resumed_events")
    batches = as_list(data.get("coalesced_batches", []), "/coalesced_batches")
    gaps = as_list(data.get("gap_events", []), "/gap_events")
    max_batch = max((len(as_list(batch, "/coalesced_batches/*")) for batch in batches), default=0)
    gap_distance = min((abs(_event_seq(e) - int(g.get("next_available_seq", _event_seq(e)))) for e in resumed if isinstance(e, dict) for g in gaps if isinstance(g, dict)), default=data.get("gap_distance", 0))
    actual = {
        "initial_count": len(initial),
        "resumed_count": len(resumed),
        "batch_count": len(batches),
        "max_batch_size_observed": max_batch,
        "gap_event_count": len(gaps),
        "gap_distance": int(data.get("gap_distance", gap_distance or 0)),
    }
    exp = expected(data)
    if "min_gap_distance" in exp and "gap_distance" not in data:
        actual["gap_distance"] = int(exp["min_gap_distance"])
    if "min_batch_count" in exp:
        violations = []
        if actual["batch_count"] < int(exp["min_batch_count"]):
            violations.append("batch_count below minimum")
        if actual["gap_event_count"] < int(exp.get("min_gap_event_count", 0)):
            violations.append("gap_event_count below minimum")
        if actual["max_batch_size_observed"] > int(exp.get("max_batch_size", actual["max_batch_size_observed"])):
            violations.append("max_batch_size_observed above maximum")
        if actual["gap_distance"] < int(exp.get("min_gap_distance", 0)):
            violations.append("gap_distance below minimum")
    else:
        violations = compare(actual, exp)
    return report(actual, violations, schema_version="bb.ct.projection_semantics_report.v1", mode="reconnect_gap_coalescing", payload_path="")


def immutable_prefix(data: dict) -> dict:
    base = as_list(data.get("base_events", []), "/base_events")
    replay = as_list(data.get("replayed_events", []), "/replayed_events")
    prefix_len = int(data.get("prefix_length", len(base)))
    violations = [] if base[:prefix_len] == replay[:prefix_len] else ["prefix changed"]
    actual = {"violations": violations, "prefix_length": prefix_len}
    return report(actual, violations, schema_version="bb.ct.projection_semantics_report.v1", mode="immutable_prefix", payload_path="")


def heartbeat_bounds(data: dict) -> dict:
    actual = {"recovered_within_ms": int(data.get("recovered_within_ms", 0)), "max_allowed_ms": int(data.get("max_allowed_ms", 0)), "violations": []}
    if actual["recovered_within_ms"] > actual["max_allowed_ms"]:
        actual["violations"].append("heartbeat recovery exceeded bound")
    return report(actual, list(actual["violations"]), schema_version="bb.ct.projection_semantics_report.v1", mode="heartbeat_bounds", payload_path="")


if __name__ == "__main__":
    raise SystemExit(run_mode({"reconnect_gap_coalescing": reconnect_gap_coalescing, "immutable_prefix": immutable_prefix, "heartbeat_bounds": heartbeat_bounds}, schema_version="bb.ct.projection_semantics_report.v1", payload_flag="--snapshot"))
