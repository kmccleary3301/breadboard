from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import load_json, write_json, write_text


COMPONENT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
REGISTRY = COMPONENT_DIR / "component_registry_v0.json"
TRANSFER = COMPONENT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_JSON = COMPONENT_DIR / "component_scorecard_v0.json"
OUT_MD = COMPONENT_DIR / "component_scorecard_v0.md"


def write_component_scorecard() -> dict[str, str | int]:
    registry = load_json(REGISTRY)
    transfer = load_json(TRANSFER)
    retained_lookup = {row["component_family_id"]: row for row in transfer.get("rows") or [] if row["transfer_status"] == "retained"}
    failed_lookup: dict[str, int] = {}
    for row in transfer.get("rows") or []:
        if row["transfer_status"] != "retained":
            failed_lookup[row["component_family_id"]] = failed_lookup.get(row["component_family_id"], 0) + 1

    rows = []
    lines = ["# Stage-3 Component Scorecard", ""]
    for row in registry.get("rows") or []:
        retained = retained_lookup.get(row["component_family_id"])
        score_row = {
            "component_family_id": row["component_family_id"],
            "source_lane_id": row["source_lane_id"],
            "component_kind": row["component_kind"],
            "lifecycle_status": row["lifecycle_status"],
            "replay_status": row["replay_status"],
            "transfer_eligibility": row["transfer_eligibility"],
            "retained_transfer_status": retained["transfer_status"] if retained else "none_recorded",
            "retained_transfer_target": retained["target_lane_id"] if retained else None,
            "failed_transfer_count": failed_lookup.get(row["component_family_id"], 0),
            "valid_comparison_count": row["valid_comparison_count"],
            "improvement_count": row["improvement_count"],
            "interpretation_note": (
                "canonical promoted family"
                if row["lifecycle_status"] == "promoted"
                else "non-promoted bounded candidate"
            ),
        }
        rows.append(score_row)
        lines.append(
            f"- `{score_row['component_family_id']}`: lifecycle=`{score_row['lifecycle_status']}`, "
            f"retained_transfer=`{score_row['retained_transfer_status']}`, failed_transfer_count=`{score_row['failed_transfer_count']}`"
        )

    payload = {
        "schema": "breadboard.darwin.stage3.component_scorecard.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 comparative component scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_component_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"component_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
