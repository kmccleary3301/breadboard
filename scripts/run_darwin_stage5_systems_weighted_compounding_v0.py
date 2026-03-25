from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _write_json  # noqa: E402
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "systems_weighted"
LANE_ORDER = ("lane.systems", "lane.repo_swe")


def run_stage5_systems_weighted_compounding(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    lane_runs: list[dict[str, object]] = []
    for round_index in range(1, int(rounds) + 1):
        round_dir = out_dir / f"round_r{round_index}"
        for lane_id in LANE_ORDER:
            lane_summary = run_stage5_compounding_pilot(
                lane_id=lane_id,
                out_dir=round_dir / lane_id.replace(".", "_"),
                round_index=round_index,
            )
            lane_runs.append(
                {
                    "round_index": round_index,
                    "lane_id": lane_id,
                    "summary_path": lane_summary["summary_path"],
                }
            )

    bundle_rows = []
    lane_totals: dict[str, dict[str, int]] = {}
    completed_row_count = 0
    for row in lane_runs:
        payload = json.loads(Path(str(row["summary_path"])).read_text(encoding="utf-8"))
        policy_ref = Path(str(payload.get("policy_ref") or ""))
        policy_payload = json.loads((ROOT / policy_ref).read_text(encoding="utf-8")) if policy_ref else {}
        round_complete = str(payload.get("run_completion_status") or "") == "complete"
        bundle_row = {
            "round_index": int(row["round_index"]),
            "lane_id": str(row["lane_id"]),
            "round_complete": round_complete,
            "live_claim_surface_status": str(payload.get("live_claim_surface_status") or "unknown"),
            "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
            "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
            "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
            "no_lift_count": int(payload.get("no_lift_count") or 0),
            "flat_count": int(payload.get("flat_count") or 0),
            "lane_weight": str(dict(policy_payload.get("cross_lane_weighting") or {}).get("lane_weight") or "unset"),
            "summary_ref": path_ref(Path(str(row["summary_path"]))),
        }
        bundle_rows.append(bundle_row)
        if round_complete:
            completed_row_count += 1
        lane_totals.setdefault(
            bundle_row["lane_id"],
            {
                "claim_eligible_comparison_count": 0,
                "comparison_valid_count": 0,
                "reuse_lift_count": 0,
                "no_lift_count": 0,
                "flat_count": 0,
            },
        )
        lane_totals[bundle_row["lane_id"]]["claim_eligible_comparison_count"] += bundle_row["claim_eligible_comparison_count"]
        lane_totals[bundle_row["lane_id"]]["comparison_valid_count"] += bundle_row["comparison_valid_count"]
        lane_totals[bundle_row["lane_id"]]["reuse_lift_count"] += bundle_row["reuse_lift_count"]
        lane_totals[bundle_row["lane_id"]]["no_lift_count"] += bundle_row["no_lift_count"]
        lane_totals[bundle_row["lane_id"]]["flat_count"] += bundle_row["flat_count"]

    bundle = {
        "schema": "breadboard.darwin.stage5.systems_weighted_compounding.v0",
        "round_count": int(rounds),
        "lane_count": len(LANE_ORDER),
        "lane_order": list(LANE_ORDER),
        "row_count": len(bundle_rows),
        "completed_row_count": completed_row_count,
        "bundle_complete": completed_row_count == len(bundle_rows),
        "rows": bundle_rows,
        "lane_totals": lane_totals,
    }
    bundle_path = out_dir / "systems_weighted_compounding_v0.json"
    _write_json(bundle_path, bundle)
    return {"summary_path": str(bundle_path), "row_count": len(bundle_rows), "round_count": int(rounds)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 systems-weighted compounding slice.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_systems_weighted_compounding(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_systems_weighted_compounding={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
