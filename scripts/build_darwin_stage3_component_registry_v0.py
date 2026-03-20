from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import ROOT as DARWIN_ROOT
from breadboard_ext.darwin.stage3_component_transfer import load_json, policy_registry_lookup, write_json, write_text


COMPONENT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
CANDIDATES = COMPONENT_DIR / "component_candidates_v0.json"
PROMOTION = COMPONENT_DIR / "component_promotion_report_v0.json"
TRANSFER = COMPONENT_DIR / "bounded_transfer_outcomes_v0.json"
REPLAY = COMPONENT_DIR / "component_replay_v0.json"
LEDGER = COMPONENT_DIR / "stage3_decision_ledger_v1.json"
OUT_JSON = COMPONENT_DIR / "component_registry_v0.json"
OUT_MD = COMPONENT_DIR / "component_registry_v0.md"


def _registry_status(*, lane_id: str, family_id: str, promotion_rows: list[dict], transfer_rows: list[dict]) -> tuple[str, str]:
    promotion_row = next((row for row in promotion_rows if row["component_family_id"] == family_id and row["lane_id"] == lane_id), None)
    if promotion_row:
        if promotion_row["promotion_outcome"] == "promoted":
            return "promoted", "eligible_for_bounded_transfer"
        if promotion_row["promotion_outcome"] == "not_promoted":
            return "not_promoted", "not_transfer_eligible"
        return "withheld", "not_transfer_eligible"
    transfer_row = next((row for row in transfer_rows if row["component_family_id"] == family_id and row["source_lane_id"] == lane_id), None)
    if transfer_row and transfer_row["transfer_status"] == "retained":
        return "transfer_candidate", "eligible_for_bounded_transfer"
    return "transfer_candidate", "not_transfer_eligible"


def write_component_registry() -> dict[str, str | int]:
    candidates = load_json(CANDIDATES)
    promotion = load_json(PROMOTION)
    transfer = load_json(TRANSFER)
    replay = load_json(REPLAY)
    _ = load_json(LEDGER)
    policy_lookup = policy_registry_lookup()
    replay_lookup = {row["lane_id"]: row for row in replay.get("rows") or []}

    rows: list[dict] = []
    lines = ["# Stage-3 Canonical Component Registry", ""]
    for row in candidates.get("rows") or []:
        lifecycle_status, transfer_status = _registry_status(
            lane_id=row["lane_id"],
            family_id=row["component_family_id"],
            promotion_rows=promotion.get("rows") or [],
            transfer_rows=transfer.get("rows") or [],
        )
        if row["component_key"] in policy_lookup:
            allowed_target_lanes = list(policy_lookup[row["component_key"]]["applies_to_lanes"])
        else:
            allowed_target_lanes = [row["lane_id"]]
        registry_row = {
            "component_family_id": row["component_family_id"],
            "source_lane_id": row["lane_id"],
            "component_kind": row["component_kind"],
            "component_key": row["component_key"],
            "lifecycle_status": lifecycle_status,
            "transfer_eligibility": transfer_status,
            "allowed_target_lanes": allowed_target_lanes,
            "disallowed_target_lanes": sorted({"lane.repo_swe", "lane.systems", "lane.scheduling", "lane.harness", "lane.atp"} - set(allowed_target_lanes)),
            "replay_status": "supported" if replay_lookup.get(row["lane_id"], {}).get("replay_supported") else "not_recorded",
            "valid_comparison_count": row["valid_comparison_count"],
            "improvement_count": row["improvement_count"],
            "matched_budget_retention_rate": row["matched_budget_retention_rate"],
            "evidence_refs": list(row["evidence_refs"]),
            "decision_ledger_ref": str(LEDGER.relative_to(DARWIN_ROOT)),
        }
        rows.append(registry_row)
        lines.append(
            f"- `{registry_row['component_family_id']}`: lifecycle=`{registry_row['lifecycle_status']}`, "
            f"transfer=`{registry_row['transfer_eligibility']}`, replay=`{registry_row['replay_status']}`"
        )

    payload = {
        "schema": "breadboard.darwin.stage3.component_registry.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 canonical component registry.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_registry()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_registry={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
