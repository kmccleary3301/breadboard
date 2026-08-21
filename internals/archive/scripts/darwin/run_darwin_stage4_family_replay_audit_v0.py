from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402
from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, write_json, write_text  # noqa: E402


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_JSON = OUT_DIR / "family_replay_audit_v0.json"
OUT_MD = OUT_DIR / "family_replay_audit_v0.md"


def _campaign_lookup() -> dict[str, dict]:
    manifest = load_json(BOOTSTRAP_MANIFEST)
    rows = {}
    for row in manifest.get("specs") or []:
        spec = load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def _topology_for_row(row: dict) -> str:
    if row["family_kind"] == "topology":
        return str(row["family_key"])
    return "policy.topology.pev_v0"


def run_stage4_family_replay_audit() -> dict[str, str | int]:
    promotion = load_json(PROMOTION)
    transfer = load_json(TRANSFER)
    campaigns = _campaign_lookup()
    promoted = [row for row in promotion.get("rows") or [] if row["promotion_outcome"] == "promoted"]
    retained_sources = {row["family_id"] for row in transfer.get("rows") or [] if row["transfer_status"] == "retained"}
    rows = []
    lines = ["# Stage-4 Family Replay Audit", ""]
    for row in promoted:
        topology_id = _topology_for_row(row)
        lane_run = run_named_lane(
            str(row["lane_id"]),
            campaigns[str(row["lane_id"])],
            OUT_DIR / "replay_runs",
            candidate_id=f"cand.stage4.family_replay.{row['lane_id']}.{row['family_kind']}.v0",
            mutation_operator=str(row["source_operator_id"]),
            topology_id=topology_id,
            policy_bundle_id=topology_id,
            budget_class="class_a",
            perturbation_group="replay",
            task_id=f"task.stage4.family_replay.{row['lane_id']}",
            trial_label=f"family_replay_{row['family_kind']}",
        )
        replay_row = {
            "family_id": str(row["family_id"]),
            "lane_id": str(row["lane_id"]),
            "family_kind": str(row["family_kind"]),
            "source_operator_id": str(row["source_operator_id"]),
            "retained_transfer_source": row["family_id"] in retained_sources,
            "primary_score": float(lane_run["primary_score"]),
            "verifier_status": str(lane_run["verifier_status"]),
            "replay_supported": bool(lane_run["verifier_status"] == "passed" and float(lane_run["primary_score"]) >= 1.0),
        }
        rows.append(replay_row)
        lines.append(f"- `{replay_row['family_id']}`: replay_supported=`{replay_row['replay_supported']}`")
    payload = {
        "schema": "breadboard.darwin.stage4.family_replay_audit.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded replay audits for promoted Stage-4 families.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage4_family_replay_audit()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_replay_audit={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
