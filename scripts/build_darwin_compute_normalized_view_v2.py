from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
ARCHIVE = ROOT / "artifacts" / "darwin" / "search" / "archive_snapshot_v1.json"
INVALID_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "scorecards"
NORMALIZATION_POLICY_REF = "docs/contracts/darwin/DARWIN_COMPUTE_NORMALIZED_SCORECARD_V2.md"
COST_POLICY_REF = "docs/contracts/darwin/DARWIN_COST_ACCOUNTING_CLASSIFICATION_V0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_per_second(score: float, wall_clock_ms: int) -> float:
    seconds = max(wall_clock_ms / 1000.0, 0.001)
    return round(score / seconds, 6)


def _classify_local_cost(value: object) -> str:
    if value is None:
        return "unavailable"
    cost = float(value)
    if cost == 0.0:
        return "exact_local_zero"
    if cost > 0.0:
        return "exact_local_nonzero"
    return "estimated_local"


def _score_per_local_cost(score: float, value: object) -> float | None:
    if value is None:
        return None
    cost = float(value)
    if cost <= 0.0:
        return None
    return round(score / cost, 6)


def _lane_note(lane_id: str) -> str:
    notes = {
        "lane.harness": "Parity-style test lanes saturate quickly; runtime deltas are often more informative than raw score deltas.",
        "lane.repo_swe": "Workspace-sensitive lanes may carry invalid trial exclusions; compare only against the retained valid active candidate.",
        "lane.scheduling": "Objective-bearing scheduling lanes can show meaningful score and runtime movement together.",
        "lane.atp": "ATP remains an audit lane here; normalized fields are descriptive only and not the proving center for this tranche.",
        "lane.systems": "Systems remains out of primary proving scope for this tranche; normalized fields are supporting context only.",
        "lane.research": "Research remains out of primary proving scope for this tranche; transfer-heavy interpretation remains deferred.",
    }
    return notes.get(lane_id, "Internal-only descriptive normalization surface.")


def build_compute_normalized_view_v2() -> dict:
    live = _load_json(LIVE_SUMMARY)
    search = _load_json(SEARCH_SUMMARY) if SEARCH_SUMMARY.exists() else {"lanes": []}
    archive = _load_json(ARCHIVE)
    invalid = _load_json(INVALID_LEDGER) if INVALID_LEDGER.exists() else {"rows": []}

    archive_by_candidate = {row["candidate_id"]: row for row in archive.get("rows") or []}
    search_by_lane = {row["lane_id"]: row for row in search.get("lanes") or []}
    invalid_by_candidate = {row["candidate_id"]: row["reason"] for row in invalid.get("rows") or []}

    rows = []
    improved_active_lane_count = 0
    invalid_trial_lane_count = 0
    exact_zero_local_cost_lane_count = 0

    for lane in live.get("lanes") or []:
        lane_id = lane["lane_id"]
        baseline_eval = _load_json(ROOT / lane["evaluation_ref"])
        baseline_score = float(lane["primary_score"])
        baseline_wall_clock_ms = int(baseline_eval["wall_clock_ms"])
        baseline_runtime_rate = _score_per_second(baseline_score, baseline_wall_clock_ms)
        baseline_local_cost = baseline_eval.get("cost_estimate")
        baseline_local_cost_class = _classify_local_cost(baseline_local_cost)
        baseline_local_cost_rate = _score_per_local_cost(baseline_score, baseline_local_cost)

        search_row = search_by_lane.get(lane_id)
        active_candidate_id = lane["candidate_id"]
        active_score = baseline_score
        active_eval_ref = lane["evaluation_ref"]
        active_wall_clock_ms = baseline_wall_clock_ms
        active_runtime_rate = baseline_runtime_rate
        active_local_cost = baseline_local_cost
        active_local_cost_class = baseline_local_cost_class
        active_local_cost_rate = baseline_local_cost_rate
        invalid_trial_count = int(search_row.get("invalid_comparison_count", 0)) if search_row else 0
        active_invalid_reason = None

        promoted_candidate_id = search_row.get("active_promoted_candidate_id") if search_row else None
        if promoted_candidate_id:
            promoted_row = archive_by_candidate[promoted_candidate_id]
            promoted_eval = _load_json(ROOT / promoted_row["evaluation_ref"])
            active_candidate_id = promoted_candidate_id
            active_score = float(promoted_row["primary_score"])
            active_eval_ref = promoted_row["evaluation_ref"]
            active_wall_clock_ms = int(promoted_eval["wall_clock_ms"])
            active_runtime_rate = _score_per_second(active_score, active_wall_clock_ms)
            active_local_cost = promoted_eval.get("cost_estimate")
            active_local_cost_class = _classify_local_cost(active_local_cost)
            active_local_cost_rate = _score_per_local_cost(active_score, active_local_cost)
            active_invalid_reason = invalid_by_candidate.get(promoted_candidate_id)

        score_delta = round(active_score - baseline_score, 6)
        runtime_rate_delta = round(active_runtime_rate - baseline_runtime_rate, 6)
        if score_delta > 0:
            improved_active_lane_count += 1
        if invalid_trial_count > 0:
            invalid_trial_lane_count += 1
        if baseline_local_cost_class == "exact_local_zero" and active_local_cost_class == "exact_local_zero":
            exact_zero_local_cost_lane_count += 1

        if not search_row:
            comparison_status = "baseline_only"
            comparison_valid = True
        elif active_invalid_reason:
            comparison_status = "invalid_active_candidate"
            comparison_valid = False
        elif active_candidate_id == lane["candidate_id"] and invalid_trial_count > 0:
            comparison_status = "baseline_retained_with_invalid_trials"
            comparison_valid = True
        elif active_candidate_id == lane["candidate_id"]:
            comparison_status = "baseline_retained_valid"
            comparison_valid = True
        else:
            comparison_status = "promoted_candidate_valid"
            comparison_valid = True

        interpretation_flags = [
            "internal_only",
            "not_superiority_surface",
            "external_billing_unavailable",
        ]
        if invalid_trial_count > 0:
            interpretation_flags.append("lane_contains_invalid_trials")
        if baseline_local_cost_class == "exact_local_zero" and active_local_cost_class == "exact_local_zero":
            interpretation_flags.append("local_cost_denominator_zero")
        if active_candidate_id == lane["candidate_id"]:
            interpretation_flags.append("baseline_retained")
        else:
            interpretation_flags.append("promoted_candidate_active")

        rows.append(
            {
                "lane_id": lane_id,
                "baseline_candidate_id": lane["candidate_id"],
                "baseline_score": baseline_score,
                "baseline_eval_ref": lane["evaluation_ref"],
                "baseline_wall_clock_ms": baseline_wall_clock_ms,
                "baseline_runtime_score_per_second": baseline_runtime_rate,
                "baseline_local_cost_usd": baseline_local_cost,
                "baseline_local_cost_classification": baseline_local_cost_class,
                "baseline_local_cost_score_per_usd": baseline_local_cost_rate,
                "active_candidate_id": active_candidate_id,
                "active_score": active_score,
                "active_eval_ref": active_eval_ref,
                "active_wall_clock_ms": active_wall_clock_ms,
                "active_runtime_score_per_second": active_runtime_rate,
                "active_local_cost_usd": active_local_cost,
                "active_local_cost_classification": active_local_cost_class,
                "active_local_cost_score_per_usd": active_local_cost_rate,
                "external_billing_classification": "unavailable",
                "score_delta": score_delta,
                "runtime_rate_delta": runtime_rate_delta,
                "comparison_status": comparison_status,
                "comparison_valid": comparison_valid,
                "invalid_trial_count": invalid_trial_count,
                "active_invalid_reason": active_invalid_reason,
                "interpretation_flags": interpretation_flags,
                "lane_note": _lane_note(lane_id),
            }
        )

    return {
        "schema": "breadboard.darwin.compute_normalized_view.v2",
        "normalization_policy_ref": NORMALIZATION_POLICY_REF,
        "cost_policy_ref": COST_POLICY_REF,
        "lane_count": len(rows),
        "summary": {
            "improved_active_lane_count": improved_active_lane_count,
            "invalid_trial_lane_count": invalid_trial_lane_count,
            "exact_zero_local_cost_lane_count": exact_zero_local_cost_lane_count,
            "external_billing_classification": "unavailable",
            "cost_accounting_mode": "local_evaluator_accounting_only",
        },
        "lanes": rows,
    }


def write_compute_normalized_view_v2(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_compute_normalized_view_v2()
    out_path = out_dir / "compute_normalized_view_v2.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "lane_count": payload["lane_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Phase-2 DARWIN compute-normalized comparative view.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_compute_normalized_view_v2()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"compute_normalized_view={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
