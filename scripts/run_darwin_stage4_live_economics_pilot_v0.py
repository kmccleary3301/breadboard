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
from breadboard_ext.darwin.stage3_inference import build_stage3_proposal_prompt  # noqa: E402
from breadboard_ext.darwin.stage4 import (  # noqa: E402
    build_stage4_comparison_envelope_digest,
    build_stage4_search_policy_v1,
    build_stage4_support_envelope_digest,
    classify_stage4_power_signal,
    execute_stage4_provider_prompt,
    select_stage4_search_policy_arms,
    stage4_evaluator_pack_version,
    validate_stage4_claim_eligibility,
    validate_stage4_matched_budget_pair,
)
from run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics"


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


def _candidate_universe(*, lane_id: str, include_watchdog: bool = True) -> list[dict]:
    if lane_id == "lane.repo_swe":
        rows = [
            {
                "campaign_arm_id": "arm.repo_swe.control.stage4.v0",
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
                "campaign_arm_id": "arm.repo_swe.topology.stage4.v0",
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
                "campaign_arm_id": "arm.repo_swe.toolscope.stage4.v0",
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
                "campaign_arm_id": "arm.repo_swe.budget.stage4.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.budget.class_a_to_class_b_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_b",
                "control_tag": "mutation",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.repo_swe.policy.stage4.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "repo_patch_workspace",
                "repetition_count": 2,
            },
        ]
    elif lane_id == "lane.systems":
        rows = [
            {
                "campaign_arm_id": "arm.systems.control.stage4.v0",
                "lane_id": "lane.systems",
                "operator_id": "baseline_seed",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "control_tag": "control",
                "task_class": "systems_reward_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.systems.topology.stage4.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "systems_reward_workspace",
                "repetition_count": 2,
            },
            {
                "campaign_arm_id": "arm.systems.policy.stage4.v0",
                "lane_id": "lane.systems",
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "control_tag": "mutation",
                "task_class": "systems_reward_workspace",
                "repetition_count": 2,
            },
        ]
    else:
        raise KeyError(f"unsupported stage4 live pilot lane: {lane_id}")
    if include_watchdog:
        rows.append(
            {
                "campaign_arm_id": "arm.harness.watchdog.stage4.v0",
                "lane_id": "lane.harness",
                "operator_id": "baseline_seed",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "control_tag": "watchdog",
                "task_class": "objective_regression",
                "repetition_count": 2,
            }
        )
    return rows


def _run_arm(*, arm_cfg: dict, spec: dict, out_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    run_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    for repetition_index in range(1, int(arm_cfg["repetition_count"]) + 1):
        task_id = f"task.stage4.{arm_cfg['lane_id']}.{arm_cfg['campaign_arm_id']}"
        trial_label = f"{arm_cfg['campaign_arm_id'].split('.')[-2]}_r{repetition_index}"
        proposal_prompt = build_stage3_proposal_prompt(
            lane_id=arm_cfg["lane_id"],
            operator_id=arm_cfg["operator_id"],
            topology_id=arm_cfg["topology_id"],
            budget_class=arm_cfg["budget_class"],
            target_id=f"stage4.target.{arm_cfg['lane_id']}.tranche1.v0",
        )
        provider_result = execute_stage4_provider_prompt(
            lane_id=arm_cfg["lane_id"],
            task_class=arm_cfg["task_class"],
            prompt=proposal_prompt,
            role="worker",
        )
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
        comparison_envelope_digest = build_stage4_comparison_envelope_digest(
            lane_id=arm_cfg["lane_id"],
            task_id=task_id,
            budget_class=arm_cfg["budget_class"],
            comparison_class="stage4_live_economics",
            environment_digest=str(spec.get("environment_digest") or "unknown-environment"),
            claim_target=str(spec.get("claim_target") or "internal"),
        )
        evaluator_pack_version = stage4_evaluator_pack_version(
            lane_id=arm_cfg["lane_id"],
            task_id=task_id,
        )
        candidate_id = f"cand.stage4.{arm_cfg['lane_id']}.{trial_label}.v1"
        lane_run = run_named_lane(
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
        telemetry_rows.append(
            {
                "schema": "breadboard.darwin.stage4.provider_telemetry.v0",
                "campaign_arm_id": arm_cfg["campaign_arm_id"],
                "lane_id": arm_cfg["lane_id"],
                "operator_id": arm_cfg["operator_id"],
                "task_id": task_id,
                "repetition_index": repetition_index,
                "route_id": provider_result["route_id"],
                "provider_model": provider_result["provider_model"],
                "route_class": provider_result["route_class"],
                "execution_mode": provider_result["execution_mode"],
                "usage": dict(provider_result["usage"]),
                "cost_estimate": float(provider_result["cost_estimate"]),
                "cost_source": provider_result["cost_source"],
                "response_id": provider_result.get("response_id"),
                "claim_eligible": bool(provider_result["claim_eligible"]),
                "claim_ineligible_reason": provider_result.get("claim_ineligible_reason"),
                "live_block_reason": provider_result.get("live_block_reason"),
                "support_envelope_digest": support_envelope_digest,
                "evaluator_pack_version": evaluator_pack_version,
            }
        )
        stage3_substrate = None
        if arm_cfg["operator_id"] != "baseline_seed" and arm_cfg["lane_id"] in {"lane.repo_swe", "lane.systems"}:
            canary = build_stage3_mutation_canary(
                lane_id=arm_cfg["lane_id"],
                spec=spec,
                parent_candidate_id=f"cand.{arm_cfg['lane_id']}.baseline.v1",
                parent_candidate_ref=f"artifacts/darwin/candidates/cand.{arm_cfg['lane_id']}.baseline.v1.json",
                mutation_cfg={
                    "candidate_id": candidate_id,
                    "mutation_operator": arm_cfg["operator_id"],
                    "topology_id": arm_cfg["topology_id"],
                    "policy_bundle_id": arm_cfg["policy_bundle_id"],
                    "budget_class": arm_cfg["budget_class"],
                    "trial_label": trial_label,
                },
                candidate_ref=lane_run["candidate_ref"],
                evaluation_ref=lane_run["evaluation_ref"],
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
        run_rows.append(
            {
                "campaign_arm_id": arm_cfg["campaign_arm_id"],
                "lane_id": arm_cfg["lane_id"],
                "operator_id": arm_cfg["operator_id"],
                "topology_id": arm_cfg["topology_id"],
                "budget_class": arm_cfg["budget_class"],
                "comparison_class": "stage4_live_economics",
                "task_id": task_id,
                "control_tag": arm_cfg["control_tag"],
                "repetition_index": repetition_index,
                "candidate_id": lane_run["candidate_id"],
                "candidate_ref": lane_run["candidate_ref"],
                "evaluation_ref": lane_run["evaluation_ref"],
                "primary_score": lane_run["primary_score"],
                "wall_clock_ms": lane_run["wall_clock_ms"],
                "verifier_status": lane_run["verifier_status"],
                "route_id": provider_result["route_id"],
                "route_class": provider_result["route_class"],
                "provider_model": provider_result["provider_model"],
                "execution_mode": provider_result["execution_mode"],
                "comparison_envelope_digest": comparison_envelope_digest,
                "support_envelope_digest": support_envelope_digest,
                "evaluator_pack_version": evaluator_pack_version,
                "control_reserve_policy": "replication=0.20;control=0.10",
                "cost_source": provider_result["cost_source"],
                "cost_estimate": float(provider_result["cost_estimate"]),
                "claim_eligible": bool(provider_result["claim_eligible"]),
                "claim_ineligible_reason": provider_result.get("claim_ineligible_reason"),
                "search_policy_selection": dict(arm_cfg.get("search_policy_selection") or {}),
                "stage3_substrate": stage3_substrate,
            }
        )
    return arm_cfg, run_rows, telemetry_rows


def build_stage4_live_comparisons(run_rows: list[dict]) -> list[dict]:
    control_lookup = {
        (row["lane_id"], int(row["repetition_index"])): row
        for row in run_rows
        if row["control_tag"] in {"control", "watchdog"} and row["repetition_index"] == 1
    }
    for row in run_rows:
        if row["control_tag"] in {"control", "watchdog"}:
            control_lookup[(row["lane_id"], int(row["repetition_index"]))] = row
    comparison_rows: list[dict] = []
    for row in run_rows:
        if row["control_tag"] in {"control", "watchdog"}:
            continue
        baseline = control_lookup[(row["lane_id"], int(row["repetition_index"]))]
        matched_check = validate_stage4_matched_budget_pair(baseline=baseline, candidate=row)
        claim_check = validate_stage4_claim_eligibility(
            {
                "execution_mode": row["execution_mode"],
                "cost_source": row["cost_source"],
            }
        )
        delta_score = round(float(row["primary_score"]) - float(baseline["primary_score"]), 6)
        delta_runtime_ms = int(row["wall_clock_ms"]) - int(baseline["wall_clock_ms"])
        delta_cost_usd = round(float(row["cost_estimate"]) - float(baseline["cost_estimate"]), 8)
        power_signal = classify_stage4_power_signal(
            comparison_valid=matched_check.ok and row["verifier_status"] == "passed",
            claim_eligible=claim_check.ok,
            delta_score=delta_score,
            delta_runtime_ms=delta_runtime_ms,
            delta_cost_usd=delta_cost_usd,
        )
        comparison_rows.append(
            {
                "lane_id": row["lane_id"],
                "campaign_arm_id": row["campaign_arm_id"],
                "baseline_campaign_arm_id": baseline["campaign_arm_id"],
                "repetition_index": int(row["repetition_index"]),
                "operator_id": row["operator_id"],
                "topology_id": row["topology_id"],
                "comparison_valid": matched_check.ok and row["verifier_status"] == "passed",
                "invalid_reason": None if matched_check.ok else matched_check.reason,
                "claim_eligible": claim_check.ok,
                "claim_ineligible_reason": claim_check.reason,
                "delta_score": delta_score,
                "delta_runtime_ms": delta_runtime_ms,
                "delta_cost_usd": delta_cost_usd,
                "positive_power_signal": power_signal.positive,
                "power_signal_class": power_signal.signal_class,
                "search_policy_selection": dict(row.get("search_policy_selection") or {}),
            }
        )
    return comparison_rows


def run_stage4_live_round(
    *,
    lane_id: str,
    search_policy: dict,
    out_dir: Path,
    round_id: str,
    include_watchdog: bool = True,
) -> dict[str, object]:
    campaigns = _campaign_lookup()
    selected_arms = select_stage4_search_policy_arms(
        search_policy=search_policy,
        candidate_rows=_candidate_universe(lane_id=lane_id, include_watchdog=include_watchdog),
    )
    arm_rows: list[dict] = []
    run_rows: list[dict] = []
    telemetry_rows: list[dict] = []
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
    return {
        "selected_arms": arm_rows,
        "run_rows": run_rows,
        "telemetry_rows": telemetry_rows,
        "comparison_rows": comparison_rows,
    }


def run_stage4_live_pilot(
    *,
    lane_id: str,
    out_dir: Path,
    include_watchdog: bool = True,
) -> dict:
    search_policy = build_stage4_search_policy_v1(lane_id=lane_id, budget_class="class_a")
    round_payload = run_stage4_live_round(
        lane_id=lane_id,
        search_policy=search_policy,
        out_dir=out_dir,
        round_id=f"round.{lane_id}.r1",
        include_watchdog=include_watchdog,
    )
    arm_rows = list(round_payload["selected_arms"])
    run_rows = list(round_payload["run_rows"])
    telemetry_rows = list(round_payload["telemetry_rows"])
    comparison_rows = list(round_payload["comparison_rows"])
    policy_path = out_dir / "search_policy_v1.json"
    arms_path = out_dir / "selected_arms_v0.json"
    runs_path = out_dir / "campaign_runs_v0.json"
    telemetry_path = out_dir / "provider_telemetry_v0.json"
    comparisons_path = out_dir / "matched_budget_comparisons_v0.json"
    summary_path = out_dir / "live_economics_pilot_v0.json"
    _write_json(policy_path, search_policy)
    _write_json(arms_path, {"schema": "breadboard.darwin.stage4.selected_arms.v0", "arm_count": len(arm_rows), "arms": arm_rows})
    _write_json(runs_path, {"schema": "breadboard.darwin.stage4.campaign_runs.v0", "run_count": len(run_rows), "runs": run_rows})
    _write_json(telemetry_path, {"schema": "breadboard.darwin.stage4.provider_telemetry_bundle.v0", "row_count": len(telemetry_rows), "rows": telemetry_rows})
    _write_json(comparisons_path, {"schema": "breadboard.darwin.stage4.matched_budget_comparisons.v0", "row_count": len(comparison_rows), "rows": comparison_rows})
    summary = {
        "schema": "breadboard.darwin.stage4.live_economics_pilot.v0",
        "pilot_lane_id": lane_id,
        "lane_count": len({row["lane_id"] for row in run_rows}),
        "arm_count": len(arm_rows),
        "run_count": len(run_rows),
        "comparison_count": len(comparison_rows),
        "execution_modes": sorted({row["execution_mode"] for row in run_rows}),
        "claim_eligible_comparison_count": sum(1 for row in comparison_rows if row["claim_eligible"]),
        "positive_power_signal_count": sum(1 for row in comparison_rows if row["positive_power_signal"]),
        "selected_operator_ids": [
            row["operator_id"]
            for row in arm_rows
            if row["lane_id"] == lane_id and row["control_tag"] != "control"
        ],
        "policy_ref": str(policy_path.relative_to(ROOT)),
        "selected_arms_ref": str(arms_path.relative_to(ROOT)),
        "runs_ref": str(runs_path.relative_to(ROOT)),
        "telemetry_ref": str(telemetry_path.relative_to(ROOT)),
        "comparisons_ref": str(comparisons_path.relative_to(ROOT)),
    }
    if lane_id == "lane.repo_swe":
        summary["selected_repo_swe_operator_ids"] = list(summary["selected_operator_ids"])
    elif lane_id == "lane.systems":
        summary["selected_systems_operator_ids"] = list(summary["selected_operator_ids"])
    _write_json(summary_path, summary)
    return {"summary_path": str(summary_path), "arm_count": len(arm_rows), "run_count": len(run_rows)}


def run_stage4_live_economics_pilot(out_dir: Path = OUT_DIR) -> dict:
    return run_stage4_live_pilot(lane_id="lane.repo_swe", out_dir=out_dir, include_watchdog=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-4 live-economics/SearchPolicy pilot.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage4_live_economics_pilot()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_live_economics_pilot={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
