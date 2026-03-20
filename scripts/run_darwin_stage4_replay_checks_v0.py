from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
DEEP_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "deep_live_search"
STRONGEST_PATH = DEEP_DIR / "strongest_families_v0.json"
OUT_JSON = DEEP_DIR / "replay_checks_v0.json"
OUT_MD = DEEP_DIR / "replay_checks_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _campaign_lookup() -> dict[str, dict]:
    manifest = _load_json(BOOTSTRAP_MANIFEST)
    rows = {}
    for row in manifest.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def _operator_defaults(operator_id: str) -> tuple[str, str]:
    if operator_id == "mut.topology.single_to_pev_v1":
        return "policy.topology.pev_v0", "policy.topology.pev_v0"
    return "policy.topology.pev_v0", "policy.topology.pev_v0"


def run_stage4_replay_checks(out_dir: Path = DEEP_DIR / "replay_runs") -> dict[str, object]:
    campaigns = _campaign_lookup()
    strongest = _load_json(STRONGEST_PATH)
    target_rows = []
    seen_lanes = set()
    for row in strongest.get("rows") or []:
        if row["lane_id"] in seen_lanes:
            continue
        target_rows.append(row)
        seen_lanes.add(row["lane_id"])
    rows = []
    lines = ["# Stage-4 Replay Checks", ""]
    for row in target_rows:
        topology_id, policy_bundle_id = _operator_defaults(str(row["operator_id"]))
        lane_run = run_named_lane(
            str(row["lane_id"]),
            campaigns[str(row["lane_id"])],
            out_dir,
            candidate_id=f"cand.stage4.replay.{row['lane_id']}.{row['operator_id']}.v0",
            mutation_operator=str(row["operator_id"]),
            topology_id=topology_id,
            policy_bundle_id=policy_bundle_id,
            budget_class="class_a",
            perturbation_group="replay",
            task_id=f"task.stage4.replay.{row['lane_id']}",
            trial_label=f"replay_{str(row['operator_id']).split('.')[-2]}",
        )
        replay_row = {
            "lane_id": str(row["lane_id"]),
            "operator_id": str(row["operator_id"]),
            "primary_score": float(lane_run["primary_score"]),
            "verifier_status": str(lane_run["verifier_status"]),
            "replay_supported": bool(lane_run["verifier_status"] == "passed" and float(lane_run["primary_score"]) >= 1.0),
        }
        rows.append(replay_row)
        lines.append(f"- `{replay_row['lane_id']}` / `{replay_row['operator_id']}`: replay_supported=`{replay_row['replay_supported']}`")
    payload = {"schema": "breadboard.darwin.stage4.replay_checks.v0", "row_count": len(rows), "rows": rows}
    _write_json(OUT_JSON, payload)
    _write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded replay checks for strongest Stage-4 deep-search families.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage4_replay_checks()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_replay_checks={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
