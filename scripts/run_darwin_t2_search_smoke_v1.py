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

from breadboard_ext.darwin.search import (
    build_archive_snapshot,
    build_promotion_decision,
    validate_budget_usage,
)
from run_darwin_t1_live_baselines_v1 import run_named_lane


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


def _mutation_specs() -> dict[str, list[dict]]:
    return {
        "lane.harness": [
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
        ],
        "lane.repo_swe": [
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
        ],
        "lane.scheduling": [
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
                    sys.executable,
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
                    sys.executable,
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
    }


def run_search_smoke(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    live_summary = _load_json(LIVE_SUMMARY)
    selection = _load_json(SEARCH_SELECTION)
    baseline_by_lane = {row["lane_id"]: row for row in live_summary.get("lanes") or []}

    mutation_rows = []
    promotion_rows = []
    outcome_rows = []
    replay_audits = []
    mutation_specs = _mutation_specs()

    for lane in selection.get("lanes") or []:
        lane_id = lane["lane_id"]
        spec = campaigns[lane_id]
        baseline_row = dict(baseline_by_lane[lane_id])
        baseline_row["status"] = "candidate"
        baseline_row["promotion_state"] = "baseline"
        lane_mutation_rows = []
        invalid_count = 0
        improved_count = 0
        noop_count = 0
        for mutation_cfg in mutation_specs[lane_id]:
            mutation_row = run_named_lane(
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
            mutation_row["parent_ids"] = [baseline_row["candidate_id"]]
            mutation_row["budget_status"] = validate_budget_usage(
                mutation_row["budget_class"],
                mutation_row["wall_clock_ms"],
                0.0,
            )
            mutation_row["comparison_valid"] = (
                mutation_row["budget_class"] == baseline_row["budget_class"]
                and mutation_row["budget_status"]["ok"]
                and mutation_row["verifier_status"] == "passed"
            )
            if not mutation_row["comparison_valid"]:
                invalid_count += 1
            elif float(mutation_row["primary_score"]) > float(baseline_row["primary_score"]):
                improved_count += 1
            else:
                noop_count += 1
            mutation_rows.append(mutation_row)
            lane_mutation_rows.append(mutation_row)

        decision = build_promotion_decision(
            lane_id=lane_id,
            baseline_row=baseline_row,
            mutation_rows=lane_mutation_rows,
        )
        if decision["decision"] == "promote_mutation":
            for row in lane_mutation_rows:
                row["promotion_state"] = "promoted" if row["candidate_id"] == decision["winner_candidate_id"] else "rejected"
                row["status"] = "candidate"
            baseline_row["promotion_state"] = "superseded"
        else:
            for row in lane_mutation_rows:
                row["promotion_state"] = "rejected"
                row["status"] = "candidate"
            baseline_row["promotion_state"] = "retained"

        if decision["decision"] == "promote_mutation" and lane_id == "lane.scheduling":
            winner_cfg = next(cfg for cfg in mutation_specs[lane_id] if cfg["candidate_id"] == decision["winner_candidate_id"])
            replay_result_path = (winner_cfg.get("result_path_override") or "").replace(".json", ".replay.json")
            replay_command = None
            if winner_cfg.get("command_override"):
                replay_command = list(winner_cfg["command_override"])
                if "--out" in replay_command:
                    out_index = replay_command.index("--out") + 1
                    if out_index < len(replay_command):
                        replay_command[out_index] = replay_result_path
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
                task_id=winner_cfg["task_id"],
                trial_label=f"{winner_cfg['trial_label']}_replay",
                command_override=replay_command,
                result_path_override=replay_result_path,
                kind_override=winner_cfg.get("kind_override"),
            )
            replay_audit = {
                "lane_id": lane_id,
                "candidate_id": decision["winner_candidate_id"],
                "replay_candidate_id": replay_row["candidate_id"],
                "stable": replay_row["primary_score"] == decision["winner_primary_score"],
                "replay_primary_score": replay_row["primary_score"],
            }
            replay_audits.append(replay_audit)
            decision["replay_audit"] = replay_audit

        promotion_rows.append(decision)
        outcome_rows.append(
            {
                "lane_id": lane_id,
                "mutation_trial_count": len(lane_mutation_rows),
                "valid_comparison_count": len(lane_mutation_rows) - invalid_count,
                "invalid_comparison_count": invalid_count,
                "improvement_count": improved_count,
                "noop_count": noop_count,
                "best_candidate_id": decision["winner_candidate_id"],
                "best_mutated_score": decision["winner_primary_score"] if decision["decision"] == "promote_mutation" else baseline_row["primary_score"],
                "baseline_primary_score": baseline_row["primary_score"],
                "comparative_delta": decision["improvement"],
                "promotion_status": decision["decision"],
                "selected_topologies": lane["allowed_topologies"],
                "preferred_operators": lane["preferred_operators"],
                "archive_size": 1 + len(lane_mutation_rows),
            }
        )

    archive_snapshot = build_archive_snapshot(
        baseline_rows=[{**dict(baseline_by_lane[row["lane_id"]]), "parent_ids": [], "promotion_state": next(dec["decision"] == "retain_baseline" and "retained" or "superseded" for dec in promotion_rows if dec["lane_id"] == row["lane_id"]), "status": "candidate"} for row in outcome_rows],
        mutation_rows=mutation_rows,
    )
    archive_path = out_dir / "archive_snapshot_v1.json"
    _write_json(archive_path, archive_snapshot)

    promotion_path = out_dir / "promotion_decisions_v1.json"
    _write_json(
        promotion_path,
        {
            "schema": "breadboard.darwin.promotion_decisions.v1",
            "lane_count": len(promotion_rows),
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
        "lanes": outcome_rows,
        "mutation_refs": [row["candidate_ref"] for row in mutation_rows],
        "archive_snapshot_ref": str(archive_path.relative_to(ROOT)),
        "promotion_decision_ref": str(promotion_path.relative_to(ROOT)),
        "replay_audit_ref": str(replay_path.relative_to(ROOT)),
        "mutation_outcome_ref": str(outcome_path.relative_to(ROOT)),
        "budget_summary": {
            "class_a_usd": 0.0,
            "class_b_usd": 0.0,
            "note": "promotion cycles use local evaluators only; budget tracking is wall-clock class enforcement",
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
    parser = argparse.ArgumentParser(description="Run DARWIN typed-search promotion cycles on the initial search-enabled lanes.")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_search_smoke(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"search_smoke={summary['summary_path']}")
        print(f"archive_snapshot={summary['archive_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
