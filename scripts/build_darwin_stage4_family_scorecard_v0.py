from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, write_json, write_text  # noqa: E402


REGISTRY = OUT_DIR / "family_registry_v0.json"
TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_JSON = OUT_DIR / "family_scorecard_v0.json"
OUT_MD = OUT_DIR / "family_scorecard_v0.md"


def build_stage4_family_scorecard() -> dict[str, str | int]:
    registry = load_json(REGISTRY)
    transfer = load_json(TRANSFER)
    transfer_rows = transfer.get("rows") or []
    failed_counts = Counter(row["family_id"] for row in transfer_rows if row["transfer_status"] != "retained")
    retained_lookup = {row["family_id"]: row["transfer_status"] for row in transfer_rows if row["transfer_status"] == "retained"}
    rows = []
    lines = ["# Stage-4 Family Scorecard", ""]
    for row in registry.get("rows") or []:
        score_row = {
            "family_id": row["family_id"],
            "lane_id": row["lane_id"],
            "family_kind": row["family_kind"],
            "lifecycle_status": row["lifecycle_status"],
            "promotion_class": row["promotion_class"],
            "replay_status": row["replay_status"],
            "retained_transfer_status": retained_lookup.get(row["family_id"], "none"),
            "failed_transfer_count": failed_counts.get(row["family_id"], 0),
            "transfer_eligibility": row["transfer_eligibility"],
        }
        rows.append(score_row)
        lines.append(
            f"- `{score_row['lane_id']}` / `{score_row['family_id']}`: lifecycle=`{score_row['lifecycle_status']}`, "
            f"retained_transfer=`{score_row['retained_transfer_status']}`, failed_transfer_count=`{score_row['failed_transfer_count']}`"
        )
    payload = {
        "schema": "breadboard.darwin.stage4.family_scorecard.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
