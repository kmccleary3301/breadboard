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

DEFAULT_SOURCE = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "broader_compounding" / "broader_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "compounding_rate"
OUT_JSON = OUT_DIR / "compounding_rate_v0.json"
OUT_MD = OUT_DIR / "compounding_rate_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_compounding_rate(*, source_path: Path = DEFAULT_SOURCE, out_dir: Path = OUT_DIR) -> dict[str, object]:
    payload = _load_json(source_path)
    rows = list(payload.get("rows") or [])
    lane_summaries = []
    for lane_id in sorted({str(row.get("lane_id") or "") for row in rows}):
        lane_rows = [row for row in rows if str(row.get("lane_id") or "") == lane_id]
        positive = sum(1 for row in lane_rows if str(row.get("broader_compounding_status") or "") == "positive_broader_compounding")
        flat = sum(1 for row in lane_rows if str(row.get("broader_compounding_status") or "") == "flat_broader_compounding")
        negative = sum(1 for row in lane_rows if str(row.get("broader_compounding_status") or "") == "negative_broader_compounding")
        inconclusive = sum(1 for row in lane_rows if str(row.get("broader_compounding_status") or "") == "inconclusive_broader_compounding")
        valid = len(lane_rows)
        lane_summaries.append(
            {
                "lane_id": lane_id,
                "lane_weight": str(lane_rows[0].get("family_state") or ""),
                "round_count": len(lane_rows),
                "valid_comparison_count": valid,
                "positive_count": positive,
                "flat_count": flat,
                "negative_count": negative,
                "inconclusive_count": inconclusive,
                "positive_rate": round(positive / valid, 4) if valid else 0.0,
                "confidence_class": "positive" if positive else "mixed_or_flat" if flat else "negative_or_inconclusive",
                "trend": "stable_positive" if positive == valid else "mixed",
            }
        )
    out = {
        "schema": "breadboard.darwin.stage6.compounding_rate.v0",
        "source_refs": {"broader_compounding_ref": path_ref(source_path)},
        "row_count": len(rows),
        "rows": rows,
        "lane_summary_count": len(lane_summaries),
        "lane_summaries": lane_summaries,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Compounding Rate", ""]
    for row in lane_summaries:
        lines.append(f"- `{row['lane_id']}`: positive=`{row['positive_count']}`, flat=`{row['flat_count']}`, rate=`{row['positive_rate']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 compounding-rate report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_compounding_rate()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_compounding_rate={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
