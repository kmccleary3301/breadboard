from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "deep_live_search"
SUMMARY_PATH = OUT_DIR / "deep_live_search_v0.json"
ROUNDS_PATH = OUT_DIR / "campaign_rounds_v0.json"
POLICIES_PATH = OUT_DIR / "policy_snapshots_v0.json"
TELEMETRY_PATH = OUT_DIR / "provider_telemetry_v0.json"
COMPARISONS_PATH = OUT_DIR / "matched_budget_comparisons_v0.json"
ROUTE_MIX_PATH = OUT_DIR / "route_mix_v0.json"
INVALIDITY_PATH = OUT_DIR / "invalidity_summary_v0.json"
OPERATOR_ROUND_PATH = OUT_DIR / "operator_ev_by_round_v0.json"
TOPOLOGY_ROUND_PATH = OUT_DIR / "topology_ev_by_round_v0.json"
PRIOR_STABILITY_PATH = OUT_DIR / "family_prior_stability_v0.json"
STRONGEST_FAMILIES_PATH = OUT_DIR / "strongest_families_v0.json"
VERIFY_BUNDLE_PATH = OUT_DIR / "verification_bundle_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_route_mix(rows: list[dict]) -> dict:
    buckets: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in rows:
        buckets[(str(row["lane_id"]), str(row["route_id"]))]["count"] += 1
        buckets[(str(row["lane_id"]), str(row["route_id"]))][str(row["cost_source"])] += 1
    payload_rows = []
    for (lane_id, route_id), counter in sorted(buckets.items()):
        payload_rows.append(
            {
                "lane_id": lane_id,
                "route_id": route_id,
                "row_count": int(counter["count"]),
                "cost_source_counts": {k: v for k, v in counter.items() if k != "count"},
            }
        )
    return {"schema": "breadboard.darwin.stage4.route_mix.v0", "row_count": len(payload_rows), "rows": payload_rows}


def _build_invalidity_summary(rows: list[dict]) -> dict:
    buckets: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in rows:
        lane_id = str(row["lane_id"])
        round_id = str(row["campaign_round_id"])
        buckets[(lane_id, round_id)][str(row.get("invalid_reason") or "valid")] += 1
    payload_rows = []
    for (lane_id, round_id), counter in sorted(buckets.items()):
        total = sum(counter.values())
        valid = int(counter.get("valid", 0))
        payload_rows.append(
            {
                "lane_id": lane_id,
                "campaign_round_id": round_id,
                "comparison_count": total,
                "valid_count": valid,
                "invalidity_rate": round((total - valid) / total, 6) if total else 0.0,
                "reason_counts": dict(counter),
            }
        )
    return {"schema": "breadboard.darwin.stage4.invalidity_summary.v0", "row_count": len(payload_rows), "rows": payload_rows}


def _build_round_ev(rows: list[dict], key_field: str) -> dict:
    buckets: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        buckets[(str(row["lane_id"]), str(row["campaign_round_id"]), str(row[key_field]))].append(row)
    payload_rows = []
    for (lane_id, round_id, key_value), entries in sorted(buckets.items()):
        valid = [row for row in entries if row["comparison_valid"]]
        positive = [row for row in valid if row["positive_power_signal"]]
        payload_rows.append(
            {
                "lane_id": lane_id,
                "campaign_round_id": round_id,
                key_field: key_value,
                "trial_count": len(entries),
                "valid_comparison_count": len(valid),
                "positive_power_signal_count": len(positive),
                "average_delta_score": round(sum(float(row["delta_score"]) for row in valid) / len(valid), 6) if valid else 0.0,
                "average_delta_runtime_ms": round(sum(int(row["delta_runtime_ms"]) for row in valid) / len(valid), 3) if valid else 0.0,
                "average_delta_cost_usd": round(sum(float(row["delta_cost_usd"]) for row in valid) / len(valid), 8) if valid else 0.0,
            }
        )
    schema = "breadboard.darwin.stage4.operator_ev_by_round.v0" if key_field == "operator_id" else "breadboard.darwin.stage4.topology_ev_by_round.v0"
    return {"schema": schema, "row_count": len(payload_rows), "rows": payload_rows}


def _build_prior_stability(policy_rows: list[dict]) -> dict:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for snapshot in policy_rows:
        lane_id = str(snapshot["lane_id"])
        for operator in snapshot.get("operator_priors") or []:
            buckets[(lane_id, str(operator["operator_id"]))].append(operator)
    payload_rows = []
    for (lane_id, operator_id), entries in sorted(buckets.items()):
        priorities = [float(row["priority"]) for row in entries]
        payload_rows.append(
            {
                "lane_id": lane_id,
                "operator_id": operator_id,
                "snapshot_count": len(entries),
                "min_priority": min(priorities),
                "max_priority": max(priorities),
                "priority_span": round(max(priorities) - min(priorities), 6),
                "latest_priority": priorities[-1],
            }
        )
    return {"schema": "breadboard.darwin.stage4.family_prior_stability.v0", "row_count": len(payload_rows), "rows": payload_rows}


def _build_strongest_families(comparison_rows: list[dict]) -> dict:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in comparison_rows:
        buckets[(str(row["lane_id"]), str(row["operator_id"]))].append(row)
    payload_rows = []
    for (lane_id, operator_id), entries in sorted(buckets.items()):
        valid = [row for row in entries if row["comparison_valid"]]
        positive = [row for row in valid if row["positive_power_signal"]]
        signal_rate = (len(positive) / len(valid)) if valid else 0.0
        payload_rows.append(
            {
                "lane_id": lane_id,
                "operator_id": operator_id,
                "valid_comparison_count": len(valid),
                "positive_power_signal_count": len(positive),
                "signal_rate": round(signal_rate, 6),
                "promotion_readiness": "worth_promotion_review" if len(positive) >= 2 else "provisional_only",
            }
        )
    payload_rows.sort(key=lambda row: (-float(row["signal_rate"]), -int(row["positive_power_signal_count"]), row["lane_id"], row["operator_id"]))
    return {"schema": "breadboard.darwin.stage4.strongest_families.v0", "row_count": len(payload_rows), "rows": payload_rows}


def build_stage4_deep_live_reports() -> dict[str, object]:
    summary = _load_json(SUMMARY_PATH)
    rounds = _load_json(ROUNDS_PATH)
    policies = _load_json(POLICIES_PATH)
    telemetry = _load_json(TELEMETRY_PATH)
    comparisons = _load_json(COMPARISONS_PATH)

    route_mix = _build_route_mix(list(telemetry.get("rows") or []))
    invalidity = _build_invalidity_summary(list(comparisons.get("rows") or []))
    operator_round = _build_round_ev(list(comparisons.get("rows") or []), "operator_id")
    topology_round = _build_round_ev(list(comparisons.get("rows") or []), "topology_id")
    prior_stability = _build_prior_stability(list(policies.get("rows") or []))
    strongest = _build_strongest_families(list(comparisons.get("rows") or []))

    _write_json(ROUTE_MIX_PATH, route_mix)
    _write_json(INVALIDITY_PATH, invalidity)
    _write_json(OPERATOR_ROUND_PATH, operator_round)
    _write_json(TOPOLOGY_ROUND_PATH, topology_round)
    _write_json(PRIOR_STABILITY_PATH, prior_stability)
    _write_json(STRONGEST_FAMILIES_PATH, strongest)

    verify_bundle = {
        "schema": "breadboard.darwin.stage4.deep_live_verification_bundle.v0",
        "summary_ref": str(SUMMARY_PATH.relative_to(ROOT)),
        "rounds_ref": str(ROUNDS_PATH.relative_to(ROOT)),
        "route_mix_ref": str(ROUTE_MIX_PATH.relative_to(ROOT)),
        "invalidity_ref": str(INVALIDITY_PATH.relative_to(ROOT)),
        "operator_round_ref": str(OPERATOR_ROUND_PATH.relative_to(ROOT)),
        "topology_round_ref": str(TOPOLOGY_ROUND_PATH.relative_to(ROOT)),
        "prior_stability_ref": str(PRIOR_STABILITY_PATH.relative_to(ROOT)),
        "strongest_families_ref": str(STRONGEST_FAMILIES_PATH.relative_to(ROOT)),
        "lane_count": int(summary["lane_count"]),
        "round_count": int(summary["round_count"]),
        "positive_power_signal_count": int(summary["positive_power_signal_count"]),
    }
    _write_json(VERIFY_BUNDLE_PATH, verify_bundle)
    return {
        "summary_path": str(SUMMARY_PATH),
        "route_mix_path": str(ROUTE_MIX_PATH),
        "invalidity_path": str(INVALIDITY_PATH),
        "operator_round_row_count": operator_round["row_count"],
        "topology_round_row_count": topology_round["row_count"],
        "strongest_family_count": strongest["row_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 deep-live-search tranche reports.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_deep_live_reports()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_deep_live_reports={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
