from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import validate_evaluator_pack  # noqa: E402
from breadboard_ext.darwin.phase2 import build_evaluator_pack  # noqa: E402
from breadboard_ext.darwin.stage3 import build_stage3_budget_envelope, build_stage3_mutation_canary  # noqa: E402
from run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "systems_canary"


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


def _validate_pack(payload: dict) -> None:
    issues = validate_evaluator_pack(payload)
    if issues:
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))


def run_systems_mutation_canary(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    spec = campaigns["lane.systems"]
    baseline = run_named_lane("lane.systems", spec, out_dir / "runs", trial_label="baseline")
    mutation_cfgs = [
        {
            "candidate_id": "cand.lane.systems.mut.pev.v1",
            "mutation_operator": "mut.topology.single_to_pev_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "perturbation_group": "topology_mutation",
            "task_id": "task.darwin.systems.reward_smoke.stage3",
            "trial_label": "mut_pev",
        },
        {
            "candidate_id": "cand.lane.systems.mut.shadow_memory.v1",
            "mutation_operator": "mut.policy.shadow_memory_enable_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "perturbation_group": "policy_bundle_mutation",
            "task_id": "task.darwin.systems.reward_smoke.stage3",
            "trial_label": "mut_shadow_memory",
        },
    ]

    rows: list[dict] = []
    for cfg in mutation_cfgs:
        row = run_named_lane(
            "lane.systems",
            spec,
            out_dir / "runs",
            candidate_id=cfg["candidate_id"],
            mutation_operator=cfg["mutation_operator"],
            topology_id=cfg["topology_id"],
            policy_bundle_id=cfg["policy_bundle_id"],
            budget_class=cfg["budget_class"],
            perturbation_group=cfg["perturbation_group"],
            task_id=cfg["task_id"],
            trial_label=cfg["trial_label"],
        )
        canary = build_stage3_mutation_canary(
            lane_id="lane.systems",
            spec=spec,
            parent_candidate_id=baseline["candidate_id"],
            parent_candidate_ref=baseline["candidate_ref"],
            mutation_cfg=cfg,
            candidate_ref=row["candidate_ref"],
            evaluation_ref=row["evaluation_ref"],
            task_id=cfg["task_id"],
        )
        substrate_dir = out_dir / "substrate"
        target_path = substrate_dir / f"{cfg['trial_label']}_optimization_target_v1.json"
        candidate_bundle_path = substrate_dir / f"{cfg['trial_label']}_candidate_bundle_v1.json"
        materialized_path = substrate_dir / f"{cfg['trial_label']}_materialized_candidate_v1.json"
        _write_json(target_path, canary["target"])
        _write_json(candidate_bundle_path, canary["candidate_bundle"])
        _write_json(materialized_path, canary["materialized_candidate"])

        evaluator_pack = build_evaluator_pack(
            spec=spec,
            lane_id="lane.systems",
            candidate_id=row["candidate_id"],
            trial_label=cfg["trial_label"],
            task_id=cfg["task_id"],
            budget_class=cfg["budget_class"],
        )
        evaluator_pack["budget_envelope"] = build_stage3_budget_envelope(
            budget_class=cfg["budget_class"],
            wall_clock_ms=row["wall_clock_ms"],
            token_counts={},
            cost_estimate=0.0,
            comparison_class="stage3_canary",
        )
        evaluator_pack_path = out_dir / "runs" / "lane.systems" / f"{cfg['trial_label']}_evaluator_pack_v0.json"
        _write_json(evaluator_pack_path, evaluator_pack)
        _validate_pack(evaluator_pack)

        rows.append(
            {
                "lane_id": "lane.systems",
                "candidate_id": row["candidate_id"],
                "candidate_ref": row["candidate_ref"],
                "evaluation_ref": row["evaluation_ref"],
                "primary_score": row["primary_score"],
                "verifier_status": row["verifier_status"],
                "stage3_substrate": {
                    "selected_locus_id": canary["selected_locus_id"],
                    "blast_radius": canary["blast_radius"],
                    "artifact_refs": {
                        "optimization_target": str(target_path.relative_to(ROOT)),
                        "candidate_bundle": str(candidate_bundle_path.relative_to(ROOT)),
                        "materialized_candidate": str(materialized_path.relative_to(ROOT)),
                        "evaluator_pack": str(evaluator_pack_path.relative_to(ROOT)),
                    },
                },
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage3.systems_mutation_canary.v0",
        "lane_id": "lane.systems",
        "baseline_candidate_id": baseline["candidate_id"],
        "mutation_trial_count": len(rows),
        "rows": rows,
    }
    summary_path = out_dir / "systems_mutation_canary_v0.json"
    _write_json(summary_path, payload)
    return {
        "summary_path": str(summary_path),
        "lane_id": payload["lane_id"],
        "mutation_trial_count": payload["mutation_trial_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded Stage-3 systems mutation canary.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_systems_mutation_canary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"systems_mutation_canary={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
