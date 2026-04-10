from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from breadboard_ext.darwin.stage6 import (  # noqa: E402
    build_stage6_activation_probe_summary,
    build_stage6_family_activation_rows,
    build_stage6_search_policy_preview,
    build_stage6_transfer_cases,
    build_stage6_compounding_cases,
    select_stage6_search_policy_arms,
    summarize_stage6_provider_segmentation,
)
from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import (  # noqa: E402
    _campaign_lookup,
    _candidate_universe,
    _run_arm,
    _write_json,
    build_stage4_live_comparisons,
)


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche1" / "broader_transfer_canary"
LANE_ORDER = ("lane.systems", "lane.repo_swe")


def run_stage6_broader_transfer_canary(*, out_dir: Path = OUT_DIR) -> dict[str, object]:
    write_bootstrap_specs()
    campaigns = _campaign_lookup()
    activation_rows = build_stage6_family_activation_rows()
    activation_by_lane = {
        lane_id: [row for row in activation_rows if str(row.get("lane_id") or "") == lane_id and bool(row.get("activation_enabled"))]
        for lane_id in LANE_ORDER
    }

    policy_rows: list[dict[str, object]] = []
    arm_rows: list[dict[str, object]] = []
    run_rows: list[dict[str, object]] = []
    telemetry_rows: list[dict[str, object]] = []
    for lane_id in LANE_ORDER:
        search_policy = build_stage6_search_policy_preview(lane_id=lane_id, budget_class="class_a")
        selected_arms = select_stage6_search_policy_arms(
            search_policy=search_policy,
            candidate_rows=_candidate_universe(lane_id=lane_id, include_watchdog=True),
        )
        policy_rows.append(search_policy)
        for arm_cfg in selected_arms:
            arm_cfg = dict(arm_cfg)
            arm_cfg["repetition_count"] = int(search_policy["repetition_count"])
            arm_cfg["campaign_class"] = search_policy["campaign_class"]
            arm, lane_run_rows, lane_telemetry = _run_arm(
                arm_cfg=arm_cfg,
                spec=campaigns[arm_cfg["lane_id"]],
                out_dir=out_dir / lane_id.replace(".", "_"),
            )
            arm_rows.append(
                {
                    "campaign_arm_id": arm["campaign_arm_id"],
                    "lane_id": arm["lane_id"],
                    "operator_id": arm["operator_id"],
                    "comparison_mode": str(arm.get("comparison_mode") or "cold_start"),
                    "family_context": dict(arm.get("family_context") or {}),
                    "selection": dict(arm.get("search_policy_selection") or {}),
                }
            )
            run_rows.extend(lane_run_rows)
            telemetry_rows.extend(lane_telemetry)

    comparison_rows = build_stage4_live_comparisons(run_rows)
    compounding_cases = build_stage6_compounding_cases(
        comparison_rows=comparison_rows,
        activation_rows=activation_rows,
    )
    transfer_cases = build_stage6_transfer_cases(
        activation_rows=activation_rows,
        compounding_cases=compounding_cases,
    )
    activation_probe_summary = build_stage6_activation_probe_summary(
        activation_rows=activation_rows,
        compounding_cases=compounding_cases,
    )
    provider_segmentation = summarize_stage6_provider_segmentation(
        telemetry_rows=telemetry_rows,
        run_rows=run_rows,
    )

    policy_path = out_dir / "search_policy_preview_v1.json"
    activation_path = out_dir / "family_activation_v1.json"
    arms_path = out_dir / "selected_arms_v0.json"
    runs_path = out_dir / "campaign_runs_v0.json"
    telemetry_path = out_dir / "provider_telemetry_v0.json"
    provider_segmentation_path = out_dir / "provider_segmentation_v1.json"
    comparisons_path = out_dir / "matched_budget_comparisons_v0.json"
    compounding_cases_path = out_dir / "compounding_cases_v2.json"
    transfer_cases_path = out_dir / "transfer_cases_v2.json"
    activation_probe_summary_path = out_dir / "activation_probe_summary_v1.json"
    summary_path = out_dir / "broader_transfer_canary_v0.json"

    _write_json(policy_path, {"schema": "breadboard.darwin.stage6.search_policy_preview_bundle.v1", "row_count": len(policy_rows), "rows": policy_rows})
    _write_json(activation_path, {"schema": "breadboard.darwin.stage6.family_activation_bundle.v1", "row_count": len(activation_rows), "rows": activation_rows})
    _write_json(arms_path, {"schema": "breadboard.darwin.stage6.selected_arms.v0", "arm_count": len(arm_rows), "arms": arm_rows})
    _write_json(runs_path, {"schema": "breadboard.darwin.stage6.campaign_runs.v0", "run_count": len(run_rows), "runs": run_rows})
    _write_json(telemetry_path, {"schema": "breadboard.darwin.stage6.provider_telemetry_bundle.v0", "row_count": len(telemetry_rows), "rows": telemetry_rows})
    _write_json(provider_segmentation_path, provider_segmentation)
    _write_json(comparisons_path, {"schema": "breadboard.darwin.stage6.matched_budget_comparisons.v0", "row_count": len(comparison_rows), "rows": comparison_rows})
    _write_json(compounding_cases_path, {"schema": "breadboard.darwin.stage6.compounding_case_bundle.v2", "row_count": len(compounding_cases), "rows": compounding_cases})
    _write_json(transfer_cases_path, {"schema": "breadboard.darwin.stage6.transfer_case_bundle.v2", "row_count": len(transfer_cases), "rows": transfer_cases})
    _write_json(activation_probe_summary_path, activation_probe_summary)

    summary = {
        "schema": "breadboard.darwin.stage6.broader_transfer_canary.v0",
        "lane_order": list(LANE_ORDER),
        "activation_row_count": len(activation_rows),
        "activation_enabled_count": sum(1 for row in activation_rows if bool(row.get("activation_enabled"))),
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
        "warm_start_comparison_count": sum(1 for row in comparison_rows if str(row.get("comparison_mode") or "") == "warm_start"),
        "family_lockout_comparison_count": sum(1 for row in comparison_rows if str(row.get("comparison_mode") or "") == "family_lockout"),
        "single_family_lockout_comparison_count": sum(1 for row in comparison_rows if str(row.get("comparison_mode") or "") == "single_family_lockout"),
        "compounding_case_count": len(compounding_cases),
        "transfer_case_count": len(transfer_cases),
        "activation_probe_count": sum(
            1
            for row in list(activation_probe_summary.get("rows") or [])
            if str(row.get("activation_probe_classification") or "") == "positive_activation_probe"
        ),
        "activation_probe_summary_ref": path_ref(activation_probe_summary_path),
        "provider_origin_counts": dict(provider_segmentation.get("provider_origin_counts") or {}),
        "requested_provider_origin_counts": dict(provider_segmentation.get("requested_provider_origin_counts") or {}),
        "route_class_counts": dict(provider_segmentation.get("route_class_counts") or {}),
        "execution_mode_counts": dict(provider_segmentation.get("execution_mode_counts") or {}),
        "cost_source_counts": dict(provider_segmentation.get("cost_source_counts") or {}),
        "fallback_reason_counts": dict(provider_segmentation.get("fallback_reason_counts") or {}),
        "provider_segmentation_status": str(provider_segmentation.get("provider_segmentation_status") or "unknown"),
        "claim_rows_have_canonical_provider_segmentation": bool(provider_segmentation.get("claim_rows_have_canonical_provider_segmentation")),
        "policy_ref": path_ref(policy_path),
        "family_activation_ref": path_ref(activation_path),
        "provider_segmentation_ref": path_ref(provider_segmentation_path),
        "comparisons_ref": path_ref(comparisons_path),
        "compounding_cases_ref": path_ref(compounding_cases_path),
        "transfer_cases_ref": path_ref(transfer_cases_path),
    }
    _write_json(summary_path, summary)
    return {"summary_path": str(summary_path), "compounding_case_count": len(compounding_cases), "transfer_case_count": len(transfer_cases)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-6 broader-transfer canary.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage6_broader_transfer_canary(out_dir=Path(args.out_dir) if args.out_dir else OUT_DIR)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_broader_transfer_canary={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
