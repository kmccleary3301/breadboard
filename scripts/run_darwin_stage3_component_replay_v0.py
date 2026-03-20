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

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs  # noqa: E402
from scripts.build_darwin_stage3_component_candidates_v0 import write_component_candidates  # noqa: E402
from run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
OUT_JSON = OUT_DIR / "component_replay_v0.json"


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


def _replay_pair(*, lane_id: str, topology_id: str, policy_bundle_id: str, out_dir: Path) -> dict:
    specs = _campaign_lookup()
    spec = specs[lane_id]
    baseline = run_named_lane(
        lane_id,
        spec,
        out_dir / lane_id,
        candidate_id=f"cand.{lane_id}.replay_control.v0",
        mutation_operator="baseline_seed",
        topology_id=spec["topology_family"],
        policy_bundle_id=spec["policy_bundle_id"],
        budget_class=spec["budget_class"],
        perturbation_group="replay_control",
        task_id=f"task.stage3.replay.{lane_id}.control",
        trial_label="replay_control",
    )
    candidate = run_named_lane(
        lane_id,
        spec,
        out_dir / lane_id,
        candidate_id=f"cand.{lane_id}.replay_candidate.v0",
        mutation_operator="mut.topology.single_to_pev_v1",
        topology_id=topology_id,
        policy_bundle_id=policy_bundle_id,
        budget_class=spec["budget_class"],
        perturbation_group="replay_candidate",
        task_id=f"task.stage3.replay.{lane_id}.candidate",
        trial_label="replay_candidate",
    )
    replay_supported = candidate["verifier_status"] == "passed" and baseline["verifier_status"] == "passed" and float(candidate["primary_score"]) >= float(baseline["primary_score"])
    return {
        "lane_id": lane_id,
        "baseline_candidate_id": baseline["candidate_id"],
        "replay_candidate_id": candidate["candidate_id"],
        "baseline_candidate_ref": baseline["candidate_ref"],
        "replay_candidate_ref": candidate["candidate_ref"],
        "baseline_evaluation_ref": baseline["evaluation_ref"],
        "replay_evaluation_ref": candidate["evaluation_ref"],
        "delta_score": round(float(candidate["primary_score"]) - float(baseline["primary_score"]), 6),
        "delta_runtime_ms": int(candidate["wall_clock_ms"]) - int(baseline["wall_clock_ms"]),
        "replay_supported": replay_supported,
    }


def run_component_replay(out_dir: Path = OUT_DIR) -> dict:
    write_bootstrap_specs()
    write_component_candidates()
    rows = [
        _replay_pair(lane_id="lane.repo_swe", topology_id="policy.topology.pev_v0", policy_bundle_id="policy.topology.pev_v0", out_dir=out_dir / "replay"),
        _replay_pair(lane_id="lane.systems", topology_id="policy.topology.pev_v0", policy_bundle_id="policy.topology.pev_v0", out_dir=out_dir / "replay"),
    ]
    payload = {
        "schema": "breadboard.darwin.stage3.component_replay.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    _write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded replay checks for Stage-3 component-promotion candidates.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_component_replay()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_replay={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
