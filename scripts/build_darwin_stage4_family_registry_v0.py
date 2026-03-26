from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, default_transfer_scope, load_json, path_ref, write_json, write_text  # noqa: E402


PROMOTION = OUT_DIR / "family_promotion_report_v0.json"
OUT_JSON = OUT_DIR / "family_registry_v0.json"
OUT_MD = OUT_DIR / "family_registry_v0.md"


def build_stage4_family_registry() -> dict[str, str | int]:
    promotion = load_json(PROMOTION)
    rows = []
    lines = ["# Stage-4 Family Registry", ""]
    for row in promotion.get("rows") or []:
        scope = default_transfer_scope(
            lane_id=row["lane_id"],
            family_kind=row["family_kind"],
            promotion_outcome=row["promotion_outcome"],
        )
        registry_row = {
            "family_id": row["family_id"],
            "lane_id": row["lane_id"],
            "family_kind": row["family_kind"],
            "family_key": row["family_key"],
            "lifecycle_status": row["promotion_outcome"],
            "promotion_class": row["promotion_class"],
            "transfer_eligibility": scope,
            "replay_status": row["replay_status"],
            "evidence_refs": row["evidence_refs"],
            "decision_ref": path_ref(PROMOTION),
        }
        rows.append(registry_row)
        lines.append(
            f"- `{registry_row['lane_id']}` / `{registry_row['family_id']}`: lifecycle=`{registry_row['lifecycle_status']}`, "
            f"allowed_targets=`{registry_row['transfer_eligibility']['allowed_target_lanes']}`"
        )
    payload = {
        "schema": "breadboard.darwin.stage4.family_registry.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 family registry.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_family_registry()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_family_registry={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
