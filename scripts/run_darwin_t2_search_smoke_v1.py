from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.search import build_archive_snapshot, validate_budget_usage
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


def run_search_smoke(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    live_summary = _load_json(LIVE_SUMMARY)
    selection = _load_json(SEARCH_SELECTION)
    baseline_by_lane = {row["lane_id"]: row for row in live_summary.get("lanes") or []}

    mutation_specs = {
        "lane.harness": {
            "candidate_id": "cand.lane.harness.mut.pev.v1",
            "mutation_operator": "mut.topology.single_to_pev_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_a",
            "perturbation_group": "topology_mutation",
            "task_id": "task.darwin.harness.parity_smoke.search",
            "trial_label": "mut_pev",
        },
        "lane.repo_swe": {
            "candidate_id": "cand.lane.repo_swe.mut.pev_class_b.v1",
            "mutation_operator": "mut.budget.class_a_to_class_b_v1",
            "topology_id": "policy.topology.pev_v0",
            "policy_bundle_id": "policy.topology.pev_v0",
            "budget_class": "class_b",
            "perturbation_group": "budget_topology_mutation",
            "task_id": "task.darwin.repo_swe.patch_workspace_smoke.search",
            "trial_label": "mut_pev_class_b",
        },
    }

    mutation_rows = []
    comparative_rows = []
    for lane in selection.get("lanes") or []:
        lane_id = lane["lane_id"]
        spec = campaigns[lane_id]
        baseline_row = baseline_by_lane[lane_id]
        mutation_cfg = mutation_specs[lane_id]
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
        )
        budget_status = validate_budget_usage(
            mutation_row["budget_class"],
            mutation_row["wall_clock_ms"],
            0.0,
        )
        mutation_row["budget_status"] = budget_status
        mutation_row["parent_ids"] = [baseline_row["candidate_id"]]
        mutation_rows.append(mutation_row)
        comparative_rows.append(
            {
                "lane_id": lane_id,
                "baseline_candidate_id": baseline_row["candidate_id"],
                "baseline_primary_score": baseline_row["primary_score"],
                "best_candidate_id": mutation_row["candidate_id"],
                "best_mutated_score": mutation_row["primary_score"],
                "comparative_delta": round(mutation_row["primary_score"] - baseline_row["primary_score"], 6),
                "mutation_trial_count": 1,
                "selected_topologies": lane["allowed_topologies"],
                "preferred_operators": lane["preferred_operators"],
            }
        )

    archive_snapshot = build_archive_snapshot(
        baseline_rows=[{**baseline_by_lane[row["lane_id"]], "parent_ids": []} for row in comparative_rows],
        mutation_rows=mutation_rows,
    )
    archive_path = out_dir / "archive_snapshot_v1.json"
    _write_json(archive_path, archive_snapshot)

    payload = {
        "schema": "breadboard.darwin.search_smoke_summary.v1",
        "lane_count": len(comparative_rows),
        "mutation_trial_count": len(mutation_rows),
        "lanes": comparative_rows,
        "mutation_refs": [row["candidate_ref"] for row in mutation_rows],
        "archive_snapshot_ref": str(archive_path.relative_to(ROOT)),
        "budget_summary": {
            "class_a_usd": 0.0,
            "class_b_usd": 0.0,
            "note": "search smoke uses local evaluators only; budget tracking is wall-clock class enforcement",
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
    parser = argparse.ArgumentParser(description="Run DARWIN T2 typed-search smoke on the first two search-enabled lanes.")
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
