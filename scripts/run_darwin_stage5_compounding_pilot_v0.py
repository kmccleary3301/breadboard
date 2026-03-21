from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage5 import (  # noqa: E402
    build_stage5_compounding_cases,
    build_stage5_search_policy_v2,
    load_stage5_family_registry_rows,
    select_stage5_search_policy_arms,
)
from scripts.run_darwin_stage4_live_economics_pilot_v0 import (  # noqa: E402
    _campaign_lookup,
    _candidate_universe,
    _run_arm,
    _write_json,
    build_stage4_live_comparisons,
)


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "tranche1" / "lane_repo_swe"


def _default_out_dir(lane_id: str) -> Path:
    return ROOT / "artifacts" / "darwin" / "stage5" / "tranche1" / lane_id.replace(".", "_")


def run_stage5_compounding_pilot(
    *,
    lane_id: str = "lane.repo_swe",
    out_dir: Path | None = None,
    round_index: int = 1,
) -> dict[str, object]:
    out_dir = _default_out_dir(lane_id) if out_dir is None else out_dir
    search_policy = build_stage5_search_policy_v2(
        lane_id=lane_id,
        budget_class="class_a",
        family_rows=load_stage5_family_registry_rows(),
    )
    campaigns = _campaign_lookup()
    selected_arms = select_stage5_search_policy_arms(
        search_policy=search_policy,
        candidate_rows=_candidate_universe(lane_id=lane_id, include_watchdog=True),
    )
    arm_rows: list[dict] = []
    run_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    round_id = f"round.stage5.{lane_id}.r{int(round_index)}"
    for arm_cfg in selected_arms:
        arm_cfg = dict(arm_cfg)
        arm_cfg["repetition_count"] = int(search_policy["repetition_count"])
        arm_cfg["campaign_round_id"] = round_id
        arm_cfg["campaign_class"] = search_policy["campaign_class"]
        arm, arm_run_rows, arm_telemetry = _run_arm(
            arm_cfg=arm_cfg,
            spec=campaigns[arm_cfg["lane_id"]],
            out_dir=out_dir,
        )
        arm_rows.append(
            {
                "campaign_round_id": round_id,
                "campaign_arm_id": arm["campaign_arm_id"],
                "lane_id": arm["lane_id"],
                "operator_id": arm["operator_id"],
                "budget_class": arm["budget_class"],
                "control_tag": arm["control_tag"],
                "campaign_class": search_policy["campaign_class"],
                "comparison_mode": str(arm.get("comparison_mode") or "default"),
                "family_context": dict(arm.get("family_context") or {"allowed_family_ids": [], "blocked_family_ids": []}),
                "selection": dict(arm.get("search_policy_selection") or {}),
            }
        )
        for row in arm_run_rows:
            row["campaign_round_id"] = round_id
            row["campaign_class"] = search_policy["campaign_class"]
        for row in arm_telemetry:
            row["campaign_round_id"] = round_id
            row["campaign_class"] = search_policy["campaign_class"]
        run_rows.extend(arm_run_rows)
        telemetry_rows.extend(arm_telemetry)
    comparison_rows = build_stage4_live_comparisons(run_rows)
    for row in comparison_rows:
        row["campaign_round_id"] = round_id
        row["campaign_class"] = search_policy["campaign_class"]
    compounding_cases = build_stage5_compounding_cases(comparison_rows=comparison_rows)
    comparison_mode_counts = {
        mode: sum(1 for row in comparison_rows if row.get("comparison_mode") == mode)
        for mode in ("cold_start", "warm_start", "family_lockout")
    }
    provider_origin_counts: dict[str, int] = {}
    fallback_reason_counts: dict[str, int] = {}
    for row in telemetry_rows:
        origin = str(row.get("provider_origin") or "unknown")
        provider_origin_counts[origin] = provider_origin_counts.get(origin, 0) + 1
        fallback_reason = str(row.get("fallback_reason") or "")
        if fallback_reason:
            fallback_reason_counts[fallback_reason] = fallback_reason_counts.get(fallback_reason, 0) + 1

    policy_path = out_dir / "search_policy_v2.json"
    arms_path = out_dir / "selected_arms_v0.json"
    runs_path = out_dir / "campaign_runs_v0.json"
    telemetry_path = out_dir / "provider_telemetry_v0.json"
    comparisons_path = out_dir / "matched_budget_comparisons_v0.json"
    compounding_cases_path = out_dir / "compounding_cases_v1.json"
    summary_path = out_dir / "compounding_pilot_v0.json"

    _write_json(policy_path, search_policy)
    _write_json(arms_path, {"schema": "breadboard.darwin.stage5.selected_arms.v0", "arm_count": len(arm_rows), "arms": arm_rows})
    _write_json(runs_path, {"schema": "breadboard.darwin.stage5.campaign_runs.v0", "run_count": len(run_rows), "runs": run_rows})
    _write_json(telemetry_path, {"schema": "breadboard.darwin.stage5.provider_telemetry_bundle.v0", "row_count": len(telemetry_rows), "rows": telemetry_rows})
    _write_json(comparisons_path, {"schema": "breadboard.darwin.stage5.matched_budget_comparisons.v0", "row_count": len(comparison_rows), "rows": comparison_rows})
    _write_json(compounding_cases_path, {"schema": "breadboard.darwin.stage5.compounding_case_bundle.v1", "row_count": len(compounding_cases), "rows": compounding_cases})

    summary = {
        "schema": "breadboard.darwin.stage5.compounding_pilot.v0",
        "pilot_lane_id": lane_id,
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
        "compounding_case_count": len(compounding_cases),
        "comparison_valid_count": sum(1 for row in comparison_rows if row.get("comparison_valid")),
        "claim_eligible_comparison_count": sum(1 for row in comparison_rows if row.get("claim_eligible")),
        "warm_start_comparison_count": comparison_mode_counts["warm_start"],
        "family_lockout_comparison_count": comparison_mode_counts["family_lockout"],
        "reuse_lift_count": sum(1 for row in compounding_cases if row.get("conclusion") == "reuse_lift"),
        "no_lift_count": sum(1 for row in compounding_cases if row.get("conclusion") == "no_lift"),
        "provider_origin_counts": provider_origin_counts,
        "fallback_reason_counts": fallback_reason_counts,
        "policy_ref": str(policy_path.relative_to(ROOT)),
        "selected_arms_ref": str(arms_path.relative_to(ROOT)),
        "runs_ref": str(runs_path.relative_to(ROOT)),
        "telemetry_ref": str(telemetry_path.relative_to(ROOT)),
        "comparisons_ref": str(comparisons_path.relative_to(ROOT)),
        "compounding_cases_ref": str(compounding_cases_path.relative_to(ROOT)),
    }
    _write_json(summary_path, summary)
    return {"summary_path": str(summary_path), "arm_count": len(arm_rows), "compounding_case_count": len(compounding_cases)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 tranche-1 compounding pilot.")
    parser.add_argument("--lane-id", default="lane.repo_swe")
    parser.add_argument("--round-index", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_compounding_pilot(lane_id=args.lane_id, round_index=args.round_index)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_compounding_pilot={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
