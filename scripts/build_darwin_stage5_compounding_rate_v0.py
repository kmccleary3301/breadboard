from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402


DEFAULT_SCALED = ROOT / "artifacts" / "darwin" / "stage5" / "scaled_compounding" / "scaled_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_rate"
OUT_JSON = OUT_DIR / "compounding_rate_v0.json"
OUT_MD = OUT_DIR / "compounding_rate_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _confidence_class(*, reuse_lift_count: int, no_lift_count: int, round_complete: bool) -> str:
    if not round_complete:
        return "partial_or_stale"
    if reuse_lift_count > no_lift_count:
        return "positive"
    if reuse_lift_count == no_lift_count:
        return "flat"
    return "negative"


def build_stage5_compounding_rate(
    *,
    scaled_path: Path = DEFAULT_SCALED,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    scaled_payload = _load_json(scaled_path)
    systems_weighted_path = ROOT / str(scaled_payload["systems_weighted_ref"])
    systems_weighted_payload = _load_json(systems_weighted_path)

    rows = []
    for row in list(systems_weighted_payload.get("rows") or []):
        claim_eligible = int(row.get("claim_eligible_comparison_count") or 0)
        reuse_lift = int(row.get("reuse_lift_count") or 0)
        flat = int(row.get("flat_count") or 0)
        no_lift = int(row.get("no_lift_count") or 0)
        rows.append(
            {
                "round_index": int(row.get("round_index") or 0),
                "lane_id": str(row.get("lane_id") or ""),
                "lane_weight": str(row.get("lane_weight") or "unset"),
                "round_complete": bool(row.get("round_complete")),
                "claim_eligible_comparison_count": claim_eligible,
                "reuse_lift_count": reuse_lift,
                "flat_count": flat,
                "no_lift_count": no_lift,
                "reuse_lift_rate": round(reuse_lift / claim_eligible, 4) if claim_eligible else 0.0,
                "confidence_class": _confidence_class(
                    reuse_lift_count=reuse_lift,
                    no_lift_count=no_lift,
                    round_complete=bool(row.get("round_complete")),
                ),
                "summary_ref": row.get("summary_ref"),
            }
        )

    lane_summaries = []
    lane_ids = sorted({str(row["lane_id"]) for row in rows})
    for lane_id in lane_ids:
        lane_rows = [row for row in rows if row["lane_id"] == lane_id]
        total_claim_eligible = sum(int(row["claim_eligible_comparison_count"]) for row in lane_rows)
        total_reuse = sum(int(row["reuse_lift_count"]) for row in lane_rows)
        total_flat = sum(int(row["flat_count"]) for row in lane_rows)
        total_no_lift = sum(int(row["no_lift_count"]) for row in lane_rows)
        lane_summaries.append(
            {
                "lane_id": lane_id,
                "lane_weight": str(lane_rows[0]["lane_weight"]) if lane_rows else "unset",
                "round_count": len(lane_rows),
                "total_claim_eligible_comparison_count": total_claim_eligible,
                "total_reuse_lift_count": total_reuse,
                "total_flat_count": total_flat,
                "total_no_lift_count": total_no_lift,
                "aggregate_reuse_lift_rate": round(total_reuse / total_claim_eligible, 4) if total_claim_eligible else 0.0,
                "round_trend": (
                    "improving"
                    if len(lane_rows) >= 2 and float(lane_rows[-1]["reuse_lift_rate"]) > float(lane_rows[0]["reuse_lift_rate"])
                    else "stable_or_declining"
                ),
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.compounding_rate.v0",
        "source_refs": {
            "scaled_compounding_ref": path_ref(scaled_path),
            "systems_weighted_ref": path_ref(systems_weighted_path),
        },
        "row_count": len(rows),
        "rows": rows,
        "lane_summary_count": len(lane_summaries),
        "lane_summaries": lane_summaries,
    }
    lines = ["# Stage-5 Compounding Rate", ""]
    for row in lane_summaries:
        lines.append(
            f"- `{row['lane_id']}`: rounds=`{row['round_count']}`, reuse_rate=`{row['aggregate_reuse_lift_rate']}`, "
            f"reuse=`{row['total_reuse_lift_count']}`, no_lift=`{row['total_no_lift_count']}`, trend=`{row['round_trend']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 compounding-rate report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_compounding_rate()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_compounding_rate={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
