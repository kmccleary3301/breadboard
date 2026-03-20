from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from breadboard_ext.darwin.stage3 import build_stage3_mutation_canary  # noqa: E402
from breadboard_ext.darwin.stage3_inference import (  # noqa: E402
    build_stage3_campaign_arm,
    build_stage3_proposal_prompt,
    build_stage3_usage_telemetry,
    resolve_stage3_route,
    validate_matched_budget_pair,
)
from breadboard_ext.darwin.stage4 import (  # noqa: E402
    build_stage4_support_envelope_digest,
    stage4_evaluator_pack_version,
    validate_stage4_claim_eligibility,
)
from run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _campaign_lookup() -> dict[str, dict]:
    manifest = _load_json(BOOTSTRAP_MANIFEST)
    rows = {}
    for row in manifest.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def _candidate_rows_for_campaign() -> list[dict]:
    return [
        {
            "campaign_arm_id": "arm.repo_swe.control.v0",
            "lane_id": "lane.repo_swe",
            "operator_id": "baseline_seed",
            "topology_id": "policy.topology.single_v0",
            "policy_bundle_id": "policy.topology.single_v0",
            "budget_class": "class_a",
            "control_tag": "control",
            "task_class": "repo_patch_workspace",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.repo_swe.topology.v0",
            "lane_id": "lane.repo_swe",
            "operator_id": "mut.topology.single_to_pev_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "control_tag": "mutation",
            "task_class": "repo_patch_workspace",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.repo_swe.toolscope.v0",
            "lane_id": "lane.repo_swe",
            "operator_id": "mut.tool_scope.add_git_diff_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "control_tag": "mutation",
            "task_class": "repo_patch_workspace",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.repo_swe.policy.v0",
            "lane_id": "lane.repo_swe",
            "operator_id": "mut.policy.shadow_memory_enable_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "control_tag": "mutation",
            "task_class": "repo_patch_workspace",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.systems.control.v0",
            "lane_id": "lane.systems",
            "operator_id": "baseline_seed",
            "topology_id": "policy.topology.single_v0",
            "policy_bundle_id": "policy.topology.single_v0",
            "budget_class": "class_a",
            "control_tag": "control",
            "task_class": "systems_reward_runtime",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.systems.topology.v0",
            "lane_id": "lane.systems",
            "operator_id": "mut.topology.single_to_pev_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "control_tag": "mutation",
            "task_class": "systems_reward_runtime",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.systems.policy.v0",
            "lane_id": "lane.systems",
            "operator_id": "mut.policy.shadow_memory_enable_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "control_tag": "mutation",
            "task_class": "systems_reward_runtime",
            "repetition_count": 2,
        },
        {
            "campaign_arm_id": "arm.harness.watchdog.v0",
            "lane_id": "lane.harness",
            "operator_id": "baseline_seed",
            "topology_id": "policy.topology.single_v0",
            "policy_bundle_id": "policy.topology.single_v0",
            "budget_class": "class_a",
            "control_tag": "watchdog",
            "task_class": "objective_regression",
            "repetition_count": 2,
        },
    ]


def _run_arm(
    *,
    arm_cfg: dict,
    spec: dict,
    out_dir: Path,
) -> tuple[dict, list[dict], list[dict]]:
    worker_route = resolve_stage3_route(task_class=arm_cfg["task_class"], role="worker")
    filter_route = resolve_stage3_route(task_class=arm_cfg["task_class"], role="filter")
    arm = build_stage3_campaign_arm(
        arm_id=arm_cfg["campaign_arm_id"],
        lane_id=arm_cfg["lane_id"],
        operator_id=arm_cfg["operator_id"],
        topology_id=arm_cfg["topology_id"],
        budget_class=arm_cfg["budget_class"],
        route=worker_route,
        repetition_count=arm_cfg["repetition_count"],
        control_tag=arm_cfg["control_tag"],
        task_class=arm_cfg["task_class"],
    )
    arm["filter_route_id"] = filter_route["route_id"]
    arm["filter_provider_model"] = filter_route["provider_model"]
    rows: list[dict] = []
    telemetry_rows: list[dict] = []
    baseline_ref = None
    if arm_cfg["operator_id"] != "baseline_seed":
        baseline_ref = f"artifacts/darwin/candidates/cand.{arm_cfg['lane_id']}.baseline.v1.json"
    for repetition_index in range(1, arm_cfg["repetition_count"] + 1):
        trial_label = f"{arm_cfg['campaign_arm_id'].split('.')[-2]}_r{repetition_index}"
        candidate_id = f"cand.{arm_cfg['lane_id']}.{trial_label}.v1"
        task_id = f"task.stage3.{arm_cfg['lane_id']}.{arm_cfg['campaign_arm_id']}"
        support_envelope_digest = build_stage4_support_envelope_digest(
            lane_id=arm_cfg["lane_id"],
            task_id=task_id,
            topology_id=arm_cfg["topology_id"],
            policy_bundle_id=arm_cfg["policy_bundle_id"],
            budget_class=arm_cfg["budget_class"],
            allowed_tools=list(spec.get("allowed_tools") or []),
            environment_digest=str(spec.get("environment_digest") or "unknown-environment"),
            claim_target=str(spec.get("claim_target") or "internal"),
        )
        evaluator_pack_version = stage4_evaluator_pack_version(lane_id=arm_cfg["lane_id"], task_id=task_id)
        row = run_named_lane(
            arm_cfg["lane_id"],
            spec,
            out_dir / "runs",
            candidate_id=candidate_id,
            mutation_operator=arm_cfg["operator_id"],
            topology_id=arm_cfg["topology_id"],
            policy_bundle_id=arm_cfg["policy_bundle_id"],
            budget_class=arm_cfg["budget_class"],
            perturbation_group=arm_cfg["control_tag"],
            task_id=task_id,
            trial_label=trial_label,
        )
        proposal_prompt = build_stage3_proposal_prompt(
            lane_id=arm_cfg["lane_id"],
            operator_id=arm_cfg["operator_id"],
            topology_id=arm_cfg["topology_id"],
            budget_class=arm_cfg["budget_class"],
            target_id=f"stage3.target.{arm_cfg['lane_id']}.tranche2.v0",
        )
        telemetry_rows.append(
            build_stage3_usage_telemetry(
                lane_id=arm_cfg["lane_id"],
                operator_id=arm_cfg["operator_id"],
                route=filter_route,
                proposal_prompt=proposal_prompt,
                campaign_arm_id=arm_cfg["campaign_arm_id"],
                repetition_index=repetition_index,
                control_tag=f"{arm_cfg['control_tag']}_filter",
                wall_clock_ms=0,
            )
        )
        telemetry_rows.append(
            build_stage3_usage_telemetry(
                lane_id=arm_cfg["lane_id"],
                operator_id=arm_cfg["operator_id"],
                route=worker_route,
                proposal_prompt=proposal_prompt,
                campaign_arm_id=arm_cfg["campaign_arm_id"],
                repetition_index=repetition_index,
                control_tag=arm_cfg["control_tag"],
                wall_clock_ms=row["wall_clock_ms"],
            )
        )
        stage3_substrate = None
        if arm_cfg["operator_id"] != "baseline_seed" and arm_cfg["lane_id"] in {"lane.repo_swe", "lane.systems"}:
            canary = build_stage3_mutation_canary(
                lane_id=arm_cfg["lane_id"],
                spec=spec,
                parent_candidate_id=f"cand.{arm_cfg['lane_id']}.baseline.v1",
                parent_candidate_ref=baseline_ref or row["candidate_ref"],
                mutation_cfg={
                    "candidate_id": candidate_id,
                    "mutation_operator": arm_cfg["operator_id"],
                    "topology_id": arm_cfg["topology_id"],
                    "policy_bundle_id": arm_cfg["policy_bundle_id"],
                    "budget_class": arm_cfg["budget_class"],
                    "trial_label": trial_label,
                },
                candidate_ref=row["candidate_ref"],
                evaluation_ref=row["evaluation_ref"],
                task_id=task_id,
            )
            substrate_dir = out_dir / "substrate" / arm_cfg["lane_id"]
            target_path = substrate_dir / f"{trial_label}_optimization_target_v1.json"
            candidate_path = substrate_dir / f"{trial_label}_candidate_bundle_v1.json"
            materialized_path = substrate_dir / f"{trial_label}_materialized_candidate_v1.json"
            _write_json(target_path, canary["target"])
            _write_json(candidate_path, canary["candidate_bundle"])
            _write_json(materialized_path, canary["materialized_candidate"])
            stage3_substrate = {
                "selected_locus_id": canary["selected_locus_id"],
                "blast_radius": canary["blast_radius"],
                "artifact_refs": {
                    "optimization_target": str(target_path.relative_to(ROOT)),
                    "candidate_bundle": str(candidate_path.relative_to(ROOT)),
                    "materialized_candidate": str(materialized_path.relative_to(ROOT)),
                },
            }
        rows.append(
            {
                "campaign_arm_id": arm_cfg["campaign_arm_id"],
                "lane_id": arm_cfg["lane_id"],
                "operator_id": arm_cfg["operator_id"],
                "topology_id": arm_cfg["topology_id"],
                "budget_class": arm_cfg["budget_class"],
                "comparison_class": "stage3_bounded_real_inference",
                "task_id": task_id,
                "control_tag": arm_cfg["control_tag"],
                "repetition_index": repetition_index,
                "candidate_id": row["candidate_id"],
                "candidate_ref": row["candidate_ref"],
                "evaluation_ref": row["evaluation_ref"],
                "primary_score": row["primary_score"],
                "wall_clock_ms": row["wall_clock_ms"],
                "verifier_status": row["verifier_status"],
                "route_id": worker_route["route_id"],
                "route_class": worker_route["route_class"],
                "provider_model": worker_route["provider_model"],
                "execution_mode": worker_route["execution_mode"],
                "evaluator_pack_version": evaluator_pack_version,
                "support_envelope_digest": support_envelope_digest,
                "control_reserve_policy": "replication=0.20;control=0.10",
                "cost_source": "estimated_from_pricing_table" if worker_route["execution_mode"] == "live" else "scaffold_placeholder",
                "proposal_prompt": proposal_prompt,
                "stage3_substrate": stage3_substrate,
            }
        )
    return arm, rows, telemetry_rows


def run_bounded_inference_campaign(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    arms = _candidate_rows_for_campaign()
    arm_rows: list[dict] = []
    run_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    for arm_cfg in arms:
        arm, arm_run_rows, arm_telemetry = _run_arm(
            arm_cfg=arm_cfg,
            spec=campaigns[arm_cfg["lane_id"]],
            out_dir=out_dir,
        )
        arm_rows.append(arm)
        run_rows.extend(arm_run_rows)
        telemetry_rows.extend(arm_telemetry)

    control_lookup: dict[str, dict] = {}
    for row in run_rows:
        if row["control_tag"] in {"control", "watchdog"} and row["repetition_index"] == 1:
            control_lookup[row["lane_id"]] = row

    comparison_rows: list[dict] = []
    for row in run_rows:
        if row["control_tag"] in {"control", "watchdog"}:
            continue
        baseline = control_lookup[row["lane_id"]]
        check = validate_matched_budget_pair(baseline=baseline, candidate=row)
        claim_check = validate_stage4_claim_eligibility(
            {
                "execution_mode": row["execution_mode"],
                "cost_source": row["cost_source"],
            }
        )
        delta_score = round(float(row["primary_score"]) - float(baseline["primary_score"]), 6)
        delta_runtime_ms = int(row["wall_clock_ms"]) - int(baseline["wall_clock_ms"])
        positive_signal = delta_score > 0 or (delta_score == 0.0 and delta_runtime_ms < 0 and row["verifier_status"] == "passed")
        comparison_rows.append(
            {
                "lane_id": row["lane_id"],
                "campaign_arm_id": row["campaign_arm_id"],
                "baseline_campaign_arm_id": baseline["campaign_arm_id"],
                "operator_id": row["operator_id"],
                "topology_id": row["topology_id"],
                "budget_class": row["budget_class"],
                "comparison_class": row["comparison_class"],
                "control_tag": row["control_tag"],
                "comparison_valid": check.ok and row["verifier_status"] == "passed",
                "invalid_reason": None if check.ok else check.reason,
                "claim_eligible": claim_check.ok,
                "claim_ineligible_reason": claim_check.reason,
                "delta_score": delta_score,
                "delta_runtime_ms": delta_runtime_ms,
                "positive_signal": positive_signal and check.ok,
                "power_claim_eligible_signal": positive_signal and check.ok and claim_check.ok,
            }
        )

    arms_path = out_dir / "campaign_arms_v0.json"
    runs_path = out_dir / "campaign_runs_v0.json"
    telemetry_path = out_dir / "usage_telemetry_v0.json"
    comparisons_path = out_dir / "matched_budget_comparisons_v0.json"
    summary_path = out_dir / "bounded_inference_campaign_v0.json"
    _write_json(arms_path, {"schema": "breadboard.darwin.stage3.campaign_arms.v0", "arm_count": len(arm_rows), "arms": arm_rows})
    _write_json(runs_path, {"schema": "breadboard.darwin.stage3.campaign_runs.v0", "run_count": len(run_rows), "runs": run_rows})
    _write_json(telemetry_path, {"schema": "breadboard.darwin.stage3.usage_telemetry_bundle.v0", "row_count": len(telemetry_rows), "rows": telemetry_rows})
    _write_json(comparisons_path, {"schema": "breadboard.darwin.stage3.matched_budget_comparisons.v0", "row_count": len(comparison_rows), "rows": comparison_rows})
    summary = {
        "schema": "breadboard.darwin.stage3.bounded_inference_campaign.v0",
        "lane_count": len({row["lane_id"] for row in run_rows}),
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
        "positive_signal_count": sum(1 for row in comparison_rows if row["positive_signal"]),
        "claim_eligible_comparison_count": sum(1 for row in comparison_rows if row["claim_eligible"]),
        "power_claim_eligible_signal_count": sum(1 for row in comparison_rows if row["power_claim_eligible_signal"]),
        "execution_modes": sorted({row["execution_mode"] for row in run_rows}),
        "arms_ref": str(arms_path.relative_to(ROOT)),
        "runs_ref": str(runs_path.relative_to(ROOT)),
        "telemetry_ref": str(telemetry_path.relative_to(ROOT)),
        "comparisons_ref": str(comparisons_path.relative_to(ROOT)),
    }
    _write_json(summary_path, summary)
    return {
        "summary_path": str(summary_path),
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded Stage-3 real-inference entry campaign.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_bounded_inference_campaign()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"bounded_inference_campaign={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
