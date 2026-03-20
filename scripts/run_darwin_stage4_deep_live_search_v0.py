from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4 import advance_stage4_search_policy_v1, build_stage4_campaign_round_record, build_stage4_search_policy_v1  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_round  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "deep_live_search"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_lane_deep_search(*, lane_id: str, include_watchdog: bool) -> dict[str, object]:
    lane_dir = OUT_DIR / lane_id.replace(".", "_")
    policies = [build_stage4_search_policy_v1(lane_id=lane_id, budget_class="class_a")]
    rounds: list[dict] = []
    selected_arms: list[dict] = []
    run_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    comparison_rows: list[dict] = []
    for round_index in range(1, 3):
        search_policy = policies[-1]
        round_id = f"round.{lane_id}.r{round_index}"
        round_payload = run_stage4_live_round(
            lane_id=lane_id,
            search_policy=search_policy,
            out_dir=lane_dir / round_id,
            round_id=round_id,
            include_watchdog=include_watchdog if round_index == 1 else False,
        )
        selected_arms.extend(round_payload["selected_arms"])
        run_rows.extend(round_payload["run_rows"])
        telemetry_rows.extend(round_payload["telemetry_rows"])
        comparison_rows.extend(round_payload["comparison_rows"])
        rounds.append(
            build_stage4_campaign_round_record(
                round_id=round_id,
                lane_id=lane_id,
                search_policy=search_policy,
                selected_arms=round_payload["selected_arms"],
            )
        )
        policies.append(
            advance_stage4_search_policy_v1(
                search_policy=search_policy,
                comparison_rows=round_payload["comparison_rows"],
            )
        )
    payload = {
        "lane_id": lane_id,
        "round_count": len(rounds),
        "rounds": rounds,
        "policy_snapshots": policies,
        "selected_arms": selected_arms,
        "run_rows": run_rows,
        "telemetry_rows": telemetry_rows,
        "comparison_rows": comparison_rows,
    }
    return payload


def run_stage4_deep_live_search(out_dir: Path = OUT_DIR) -> dict[str, object]:
    lane_payloads = [
        _run_lane_deep_search(lane_id="lane.repo_swe", include_watchdog=True),
        _run_lane_deep_search(lane_id="lane.systems", include_watchdog=False),
    ]
    round_rows = []
    policy_snapshots = []
    arm_rows = []
    run_rows = []
    telemetry_rows = []
    comparison_rows = []
    for payload in lane_payloads:
        round_rows.extend(payload["rounds"])
        policy_snapshots.extend(payload["policy_snapshots"])
        arm_rows.extend(payload["selected_arms"])
        run_rows.extend(payload["run_rows"])
        telemetry_rows.extend(payload["telemetry_rows"])
        comparison_rows.extend(payload["comparison_rows"])

    rounds_path = out_dir / "campaign_rounds_v0.json"
    policies_path = out_dir / "policy_snapshots_v0.json"
    arms_path = out_dir / "selected_arms_v0.json"
    runs_path = out_dir / "campaign_runs_v0.json"
    telemetry_path = out_dir / "provider_telemetry_v0.json"
    comparisons_path = out_dir / "matched_budget_comparisons_v0.json"
    summary_path = out_dir / "deep_live_search_v0.json"

    _write_json(rounds_path, {"schema": "breadboard.darwin.stage4.campaign_round_bundle.v0", "round_count": len(round_rows), "rounds": round_rows})
    _write_json(policies_path, {"schema": "breadboard.darwin.stage4.policy_snapshot_bundle.v0", "row_count": len(policy_snapshots), "rows": policy_snapshots})
    _write_json(arms_path, {"schema": "breadboard.darwin.stage4.selected_arms.v0", "arm_count": len(arm_rows), "arms": arm_rows})
    _write_json(runs_path, {"schema": "breadboard.darwin.stage4.campaign_runs.v0", "run_count": len(run_rows), "runs": run_rows})
    _write_json(telemetry_path, {"schema": "breadboard.darwin.stage4.provider_telemetry_bundle.v0", "row_count": len(telemetry_rows), "rows": telemetry_rows})
    _write_json(comparisons_path, {"schema": "breadboard.darwin.stage4.matched_budget_comparisons.v0", "row_count": len(comparison_rows), "rows": comparison_rows})

    payload = {
        "schema": "breadboard.darwin.stage4.deep_live_search.v0",
        "lane_count": len({row["lane_id"] for row in run_rows}),
        "primary_lane_count": len({row["lane_id"] for row in run_rows if row["lane_id"] in {"lane.repo_swe", "lane.systems"}}),
        "round_count": len(round_rows),
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
        "claim_eligible_comparison_count": sum(1 for row in comparison_rows if row["claim_eligible"]),
        "positive_power_signal_count": sum(1 for row in comparison_rows if row["positive_power_signal"]),
        "rounds_ref": str(rounds_path.relative_to(ROOT)),
        "policies_ref": str(policies_path.relative_to(ROOT)),
        "selected_arms_ref": str(arms_path.relative_to(ROOT)),
        "runs_ref": str(runs_path.relative_to(ROOT)),
        "telemetry_ref": str(telemetry_path.relative_to(ROOT)),
        "comparisons_ref": str(comparisons_path.relative_to(ROOT)),
    }
    _write_json(summary_path, payload)
    return {"summary_path": str(summary_path), "round_count": len(round_rows), "run_count": len(run_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-4 deep bounded live-search tranche.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage4_deep_live_search()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_deep_live_search={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
