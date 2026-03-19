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

from breadboard_ext.darwin.search import (  # noqa: E402
    build_archive_snapshot,
    build_promotion_decision,
    build_promotion_history,
    validate_budget_usage,
)
from breadboard_ext.darwin.stage3 import (  # noqa: E402
    build_stage3_mutation_canary,
    supports_stage3_mutation_canary,
)
from run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
SEARCH_SELECTION = ROOT / "artifacts" / "darwin" / "search" / "search_enabled_lane_selection_v1.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "search"


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


def _mutation_cycles() -> dict[str, list[list[dict]]]:
    py = sys.executable
    return {
        "lane.harness": [[
            {
                "candidate_id": "cand.lane.harness.mut.pev.v1",
                "mutation_operator": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "perturbation_group": "topology_mutation",
                "task_id": "task.darwin.harness.parity_smoke.search",
                "trial_label": "mut_pev",
            },
            {
                "candidate_id": "cand.lane.harness.mut.prompt.v1",
                "mutation_operator": "mut.prompt.tighten_acceptance_v1",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "perturbation_group": "prompt_mutation",
                "task_id": "task.darwin.harness.parity_smoke.search",
                "trial_label": "mut_prompt",
            },
        ]],
        "lane.repo_swe": [[
            {
                "candidate_id": "cand.lane.repo_swe.mut.pev.v1",
                "mutation_operator": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "perturbation_group": "topology_mutation",
                "task_id": "task.darwin.repo_swe.patch_workspace_smoke.search",
                "trial_label": "mut_pev",
            },
            {
                "candidate_id": "cand.lane.repo_swe.mut.class_b.v1",
                "mutation_operator": "mut.budget.class_a_to_class_b_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_b",
                "perturbation_group": "budget_mutation",
                "task_id": "task.darwin.repo_swe.patch_workspace_smoke.search",
                "trial_label": "mut_class_b",
            },
        ]],
        "lane.scheduling": [
            [
                {
                    "candidate_id": "cand.lane.scheduling.mut.density.v1",
                    "mutation_operator": "mut.scheduler.strategy_value_density_v1",
                    "topology_id": "policy.topology.single_v0",
                    "policy_bundle_id": "policy.topology.single_v0",
                    "budget_class": "class_a",
                    "perturbation_group": "scheduler_strategy_mutation",
                    "task_id": "task.darwin.scheduling.constraint_objective_smoke.search",
                    "trial_label": "mut_value_density",
                    "command_override": [
                        py,
                        "scripts/run_darwin_scheduling_lane_baseline_v0.py",
                        "--strategy",
                        "value_density",
                        "--out",
                        "artifacts/darwin/search/lane.scheduling/value_density.json",
                    ],
                    "result_path_override": "artifacts/darwin/search/lane.scheduling/value_density.json",
                    "kind_override": "json_overall_ok",
                },
                {
                    "candidate_id": "cand.lane.scheduling.mut.slack.v1",
                    "mutation_operator": "mut.scheduler.strategy_slack_v1",
                    "topology_id": "policy.topology.pev_v0",
                    "policy_bundle_id": "policy.topology.pev_v0",
                    "budget_class": "class_a",
                    "perturbation_group": "scheduler_strategy_mutation",
                    "task_id": "task.darwin.scheduling.constraint_objective_smoke.search",
                    "trial_label": "mut_slack",
                    "command_override": [
                        py,
                        "scripts/run_darwin_scheduling_lane_baseline_v0.py",
                        "--strategy",
                        "slack_then_value",
                        "--out",
                        "artifacts/darwin/search/lane.scheduling/slack_then_value.json",
                    ],
                    "result_path_override": "artifacts/darwin/search/lane.scheduling/slack_then_value.json",
                    "kind_override": "json_overall_ok",
                },
            ],
            [
                {
                    "candidate_id": "cand.lane.scheduling.mut.hybrid.v1",
                    "mutation_operator": "mut.scheduler.strategy_hybrid_v1",
                    "topology_id": "policy.topology.pev_v0",
                    "policy_bundle_id": "policy.topology.pev_v0",
                    "budget_class": "class_a",
                    "perturbation_group": "scheduler_strategy_mutation",
                    "task_id": "task.darwin.scheduling.constraint_objective_smoke.search.cycle2",
                    "trial_label": "mut_hybrid",
                    "command_override": [
                        py,
                        "scripts/run_darwin_scheduling_lane_baseline_v0.py",
                        "--strategy",
                        "hybrid_density_deadline",
                        "--out",
                        "artifacts/darwin/search/lane.scheduling/hybrid_density_deadline.json",
                    ],
                    "result_path_override": "artifacts/darwin/search/lane.scheduling/hybrid_density_deadline.json",
                    "kind_override": "json_overall_ok",
                },
            ],
        ],
        "lane.research": [[
            {
                "candidate_id": "cand.lane.research.mut.pev.v1",
                "mutation_operator": "mut.topology.single_to_pev_v1",
                "topology_id": "policy.topology.pev_v0",
                "policy_bundle_id": "policy.topology.pev_v0",
                "budget_class": "class_a",
                "perturbation_group": "topology_mutation",
                "task_id": "task.darwin.research.evidence_synthesis_smoke.search",
                "trial_label": "mut_pev",
                "command_override": [
                    py,
                    "scripts/run_darwin_research_lane_baseline_v0.py",
                    "--strategy",
                    "coverage_first",
                    "--out",
                    "artifacts/darwin/search/lane.research/coverage_first.json",
                ],
                "result_path_override": "artifacts/darwin/search/lane.research/coverage_first.json",
                "kind_override": "json_overall_ok",
            },
            {
                "candidate_id": "cand.lane.research.mut.prompt.v1",
                "mutation_operator": "mut.prompt.tighten_acceptance_v1",
                "topology_id": "policy.topology.single_v0",
                "policy_bundle_id": "policy.topology.single_v0",
                "budget_class": "class_a",
                "perturbation_group": "prompt_mutation",
                "task_id": "task.darwin.research.evidence_synthesis_smoke.search",
                "trial_label": "mut_prompt",
                "command_override": [
                    py,
                    "scripts/run_darwin_research_lane_baseline_v0.py",
                    "--strategy",
                    "bridge_synthesis",
                    "--out",
                    "artifacts/darwin/search/lane.research/bridge_synthesis.json",
                ],
                "result_path_override": "artifacts/darwin/search/lane.research/bridge_synthesis.json",
                "kind_override": "json_overall_ok",
                "transfer_source_lane_id": "lane.harness",
                "transfer_source_candidate_id": "cand.lane.harness.mut.prompt.v1",
            },
        ]],
    }


def _replay_command_and_path(mutation_cfg: dict) -> tuple[list[str] | None, str | None]:
    replay_result_path = None
    replay_command = None
    if mutation_cfg.get("result_path_override"):
        replay_result_path = mutation_cfg["result_path_override"].replace(".json", ".replay.json")
    if mutation_cfg.get("command_override"):
        replay_command = list(mutation_cfg["command_override"])
        if replay_result_path and "--out" in replay_command:
            out_index = replay_command.index("--out") + 1
            if out_index < len(replay_command):
                replay_command[out_index] = replay_result_path
    return replay_command, replay_result_path


def _execute_mutation(
    *,
    lane_id: str,
    spec: dict,
    parent_row: dict,
    mutation_cfg: dict,
    out_dir: Path,
) -> dict:
    row = run_named_lane(
        lane_id,
        spec,
        out_dir / "runs",
        candidate_id=mutation_cfg["candidate_id"],
        mutation_operator=mutation_cfg["mutation_operator"],
        topology_id=mutation_cfg["topology_id"],
        policy_bundle_id=mutation_cfg["policy_bundle_id"],
        budget_class=mutation_cfg["budget_class"],
        perturbation_group=mutation_cfg["perturbation_group"],
        task_id=mutation_cfg["task_id"],
        trial_label=mutation_cfg["trial_label"],
        command_override=mutation_cfg.get("command_override"),
        result_path_override=mutation_cfg.get("result_path_override"),
        kind_override=mutation_cfg.get("kind_override"),
    )
    row["parent_ids"] = [parent_row["candidate_id"]]
    row["cycle_index"] = mutation_cfg.get("cycle_index", 1)
    row["budget_status"] = validate_budget_usage(
        row["budget_class"],
        row["wall_clock_ms"],
        0.0,
    )
    row["comparison_valid"] = (
        row["budget_class"] == parent_row["budget_class"]
        and row["budget_status"]["ok"]
        and row["verifier_status"] == "passed"
    )
    if mutation_cfg.get("transfer_source_lane_id"):
        row["transfer_source_lane_id"] = mutation_cfg["transfer_source_lane_id"]
        row["transfer_source_candidate_id"] = mutation_cfg["transfer_source_candidate_id"]
    if supports_stage3_mutation_canary(
        lane_id=lane_id,
        mutation_operator=mutation_cfg["mutation_operator"],
    ):
        canary = build_stage3_mutation_canary(
            lane_id=lane_id,
            spec=spec,
            parent_candidate_id=parent_row["candidate_id"],
            parent_candidate_ref=parent_row["candidate_ref"],
            mutation_cfg=mutation_cfg,
            candidate_ref=row["candidate_ref"],
            evaluation_ref=row["evaluation_ref"],
            task_id=mutation_cfg["task_id"],
        )
        substrate_dir = out_dir / "substrate" / lane_id
        substrate_dir.mkdir(parents=True, exist_ok=True)
        target_path = substrate_dir / f"{mutation_cfg['trial_label']}_optimization_target_v1.json"
        candidate_bundle_path = substrate_dir / f"{mutation_cfg['trial_label']}_candidate_bundle_v1.json"
        materialized_path = substrate_dir / f"{mutation_cfg['trial_label']}_materialized_candidate_v1.json"
        _write_json(target_path, canary["target"])
        _write_json(candidate_bundle_path, canary["candidate_bundle"])
        _write_json(materialized_path, canary["materialized_candidate"])
        row["stage3_substrate"] = {
            "selected_locus_id": canary["selected_locus_id"],
            "blast_radius": canary["blast_radius"],
            "mutation_bounds": canary["mutation_bounds"],
            "artifact_refs": {
                "optimization_target": str(target_path.relative_to(ROOT)),
                "candidate_bundle": str(candidate_bundle_path.relative_to(ROOT)),
                "materialized_candidate": str(materialized_path.relative_to(ROOT)),
            },
        }
    row["status"] = "candidate"
    row.setdefault("promotion_state", "candidate")
    return row


def run_search_smoke(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    live_summary = _load_json(LIVE_SUMMARY)
    selection = _load_json(SEARCH_SELECTION)
    baseline_by_lane = {row["lane_id"]: row for row in live_summary.get("lanes") or []}
    cycle_plan = _mutation_cycles()

    all_rows: list[dict] = []
    mutation_rows: list[dict] = []
    promotion_rows: list[dict] = []
    outcome_rows: list[dict] = []
    replay_audits: list[dict] = []
    promotion_histories: list[dict] = []
    transfer_attempts: list[dict] = []

    for lane in selection.get("lanes") or []:
        lane_id = lane["lane_id"]
        spec = campaigns[lane_id]
        seed_row = dict(baseline_by_lane[lane_id])
        seed_row["status"] = "candidate"
        seed_row["promotion_state"] = "baseline"
        seed_row["parent_ids"] = []
        seed_row["cycle_index"] = 0
        all_rows.append(seed_row)
        active_row = seed_row
        cycle_records: list[dict] = []
        lane_mutation_rows: list[dict] = []
        invalid_count = 0
        improved_count = 0
        noop_count = 0
        replay_audit = None

        for cycle_index, cycle_mutations in enumerate(cycle_plan[lane_id], start=1):
            if lane_id == "lane.scheduling" and cycle_index > 1 and active_row["candidate_id"] == seed_row["candidate_id"]:
                continue
            cycle_rows: list[dict] = []
            for mutation_cfg in cycle_mutations:
                cfg = dict(mutation_cfg)
                cfg["cycle_index"] = cycle_index
                row = _execute_mutation(lane_id=lane_id, spec=spec, parent_row=active_row, mutation_cfg=cfg, out_dir=out_dir)
                cycle_rows.append(row)
                lane_mutation_rows.append(row)
                mutation_rows.append(row)
                all_rows.append(row)
                if not row["comparison_valid"]:
                    invalid_count += 1
                elif float(row["primary_score"]) > float(active_row["primary_score"]):
                    improved_count += 1
                else:
                    noop_count += 1

            decision = build_promotion_decision(lane_id=lane_id, baseline_row=active_row, mutation_rows=cycle_rows)
            winner_candidate_id = decision["winner_candidate_id"]
            if decision["decision"] == "promote_mutation":
                active_row["promotion_state"] = "superseded" if active_row["candidate_id"] != seed_row["candidate_id"] else "superseded"
                for row in cycle_rows:
                    row["promotion_state"] = "promoted" if row["candidate_id"] == winner_candidate_id else "rejected"
                active_row = next(row for row in cycle_rows if row["candidate_id"] == winner_candidate_id)
            else:
                if active_row["candidate_id"] == seed_row["candidate_id"]:
                    active_row["promotion_state"] = "retained"
                for row in cycle_rows:
                    row["promotion_state"] = "rejected"
            cycle_records.append(
                {
                    "cycle_index": cycle_index,
                    "baseline_candidate_id": decision["baseline_candidate_id"],
                    "winner_candidate_id": decision["winner_candidate_id"],
                    "decision": decision["decision"],
                    "improvement": decision["improvement"],
                    "rollback_candidate_id": decision["rollback_candidate_id"],
                    "candidate_ids": [row["candidate_id"] for row in cycle_rows],
                }
            )
            promotion_rows.append({**decision, "cycle_index": cycle_index})

        if lane_id in {"lane.scheduling", "lane.research"} and active_row["candidate_id"] != seed_row["candidate_id"]:
            winner_cfg = next(cfg for cycle_mutations in cycle_plan[lane_id] for cfg in cycle_mutations if cfg["candidate_id"] == active_row["candidate_id"])
            replay_command, replay_result_path = _replay_command_and_path(winner_cfg)
            replay_row = run_named_lane(
                lane_id,
                spec,
                out_dir / "replay",
                candidate_id=f"{winner_cfg['candidate_id']}.replay",
                mutation_operator=winner_cfg["mutation_operator"],
                topology_id=winner_cfg["topology_id"],
                policy_bundle_id=winner_cfg["policy_bundle_id"],
                budget_class=winner_cfg["budget_class"],
                perturbation_group="replay_audit",
                task_id=f"{winner_cfg['task_id']}.replay",
                trial_label=f"{winner_cfg['trial_label']}_replay",
                command_override=replay_command,
                result_path_override=replay_result_path,
                kind_override=winner_cfg.get("kind_override"),
            )
            replay_audit = {
                "lane_id": lane_id,
                "candidate_id": active_row["candidate_id"],
                "replay_candidate_id": replay_row["candidate_id"],
                "stable": replay_row["primary_score"] == active_row["primary_score"],
                "replay_primary_score": replay_row["primary_score"],
            }
            replay_audits.append(replay_audit)

        promotion_histories.append(
            build_promotion_history(
                lane_id=lane_id,
                baseline_candidate_id=seed_row["candidate_id"],
                cycle_records=cycle_records,
                active_candidate_id=active_row["candidate_id"],
                active_primary_score=float(active_row["primary_score"]),
            )
        )

        if lane_id == "lane.research":
            target_row = next(row for row in lane_mutation_rows if row["candidate_id"] == "cand.lane.research.mut.prompt.v1")
            transfer_attempts.append(
                {
                    "transfer_id": "transfer.harness.prompt_to_research.v1",
                    "source_lane_id": "lane.harness",
                    "source_candidate_id": "cand.lane.harness.mut.prompt.v1",
                    "target_lane_id": lane_id,
                    "target_candidate_id": target_row["candidate_id"],
                    "operator_id": "mut.prompt.tighten_acceptance_v1",
                    "budget_class": target_row["budget_class"],
                    "comparison_valid": bool(target_row["comparison_valid"]),
                    "result": "improved" if float(target_row["primary_score"]) > float(seed_row["primary_score"]) and bool(target_row["comparison_valid"]) else "no_improvement",
                    "baseline_primary_score": seed_row["primary_score"],
                    "transferred_primary_score": target_row["primary_score"],
                    "promotion_status": "promoted" if active_row["candidate_id"] == target_row["candidate_id"] else "not_promoted",
                    "replay_required": True,
                    "replay_stable": bool(replay_audit and replay_audit.get("candidate_id") == target_row["candidate_id"] and replay_audit.get("stable")),
                    "validity_reason": "shared_prompt_operator_shared_budget_shared_topology_family",
                }
            )

        outcome_rows.append(
            {
                "lane_id": lane_id,
                "mutation_trial_count": len(lane_mutation_rows),
                "valid_comparison_count": len(lane_mutation_rows) - invalid_count,
                "invalid_comparison_count": invalid_count,
                "improvement_count": improved_count,
                "noop_count": noop_count,
                "best_candidate_id": active_row["candidate_id"],
                "best_mutated_score": active_row["primary_score"],
                "baseline_primary_score": seed_row["primary_score"],
                "comparative_delta": round(float(active_row["primary_score"]) - float(seed_row["primary_score"]), 6),
                "promotion_status": "promote_mutation" if active_row["candidate_id"] != seed_row["candidate_id"] else "retain_baseline",
                "selected_topologies": lane["allowed_topologies"],
                "preferred_operators": lane["preferred_operators"],
                "archive_size": 1 + len(lane_mutation_rows),
                "promotion_cycle_count": len(cycle_records),
                "active_promoted_candidate_id": active_row["candidate_id"] if active_row["candidate_id"] != seed_row["candidate_id"] else None,
                "promotion_history_depth": sum(1 for cycle in cycle_records if cycle["decision"] == "promote_mutation"),
                "transfer_attempt_count": sum(1 for row in transfer_attempts if row["target_lane_id"] == lane_id),
                "valid_transfer_count": sum(1 for row in transfer_attempts if row["target_lane_id"] == lane_id and row["comparison_valid"]),
                "successful_transfer_count": sum(1 for row in transfer_attempts if row["target_lane_id"] == lane_id and row["result"] == "improved"),
            }
        )

    archive_snapshot = build_archive_snapshot(candidate_rows=all_rows)
    archive_path = out_dir / "archive_snapshot_v1.json"
    _write_json(archive_path, archive_snapshot)

    promotion_path = out_dir / "promotion_decisions_v1.json"
    _write_json(
        promotion_path,
        {
            "schema": "breadboard.darwin.promotion_decisions.v1",
            "lane_count": len({row["lane_id"] for row in promotion_rows}),
            "decision_count": len(promotion_rows),
            "decisions": promotion_rows,
        },
    )

    replay_path = out_dir / "promotion_replay_audit_v1.json"
    _write_json(
        replay_path,
        {
            "schema": "breadboard.darwin.promotion_replay_audit.v1",
            "audit_count": len(replay_audits),
            "audits": replay_audits,
        },
    )

    history_path = out_dir / "promotion_history_v1.json"
    _write_json(
        history_path,
        {
            "schema": "breadboard.darwin.promotion_history.v1",
            "lane_count": len(promotion_histories),
            "lanes": promotion_histories,
        },
    )

    transfer_path = out_dir / "transfer_ledger_v1.json"
    _write_json(
        transfer_path,
        {
            "schema": "breadboard.darwin.transfer_ledger.v1",
            "attempt_count": len(transfer_attempts),
            "attempts": transfer_attempts,
        },
    )

    outcome_path = out_dir / "mutation_outcome_summary_v1.json"
    _write_json(
        outcome_path,
        {
            "schema": "breadboard.darwin.mutation_outcome_summary.v1",
            "lane_count": len(outcome_rows),
            "lanes": outcome_rows,
        },
    )

    payload = {
        "schema": "breadboard.darwin.search_smoke_summary.v1",
        "lane_count": len(outcome_rows),
        "mutation_trial_count": len(mutation_rows),
        "stage3_substrate_backed_trial_count": sum(1 for row in mutation_rows if row.get("stage3_substrate")),
        "stage3_substrate_lane_ids": sorted({row["lane_id"] for row in mutation_rows if row.get("stage3_substrate")}),
        "lanes": outcome_rows,
        "mutation_refs": [row["candidate_ref"] for row in mutation_rows],
        "archive_snapshot_ref": str(archive_path.relative_to(ROOT)),
        "promotion_decision_ref": str(promotion_path.relative_to(ROOT)),
        "replay_audit_ref": str(replay_path.relative_to(ROOT)),
        "promotion_history_ref": str(history_path.relative_to(ROOT)),
        "transfer_ledger_ref": str(transfer_path.relative_to(ROOT)),
        "mutation_outcome_ref": str(outcome_path.relative_to(ROOT)),
        "budget_summary": {
            "class_a_usd": 0.0,
            "class_b_usd": 0.0,
            "note": "local DARWIN promotion cycles and transfer checks use zero-cost local evaluators; budget tracking is wall-clock class enforcement",
        },
    }
    summary_path = out_dir / "search_smoke_summary_v1.json"
    _write_json(summary_path, payload)
    return {
        "summary_path": str(summary_path),
        "archive_path": str(archive_path),
        "lane_count": payload["lane_count"],
        "mutation_trial_count": payload["mutation_trial_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DARWIN typed-search promotion cycles on the active search-enabled lanes.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_search_smoke()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"search_smoke_summary={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
