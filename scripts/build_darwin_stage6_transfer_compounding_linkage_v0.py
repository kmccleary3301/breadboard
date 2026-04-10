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

DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage6" / "tranche2" / "broader_transfer_matrix" / "transfer_outcome_summary_v1.json"
DEFAULT_RATE = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "compounding_rate" / "compounding_rate_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "transfer_compounding_linkage"
OUT_JSON = OUT_DIR / "transfer_compounding_linkage_v0.json"
OUT_MD = OUT_DIR / "transfer_compounding_linkage_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_transfer_compounding_linkage(
    *,
    transfer_path: Path = DEFAULT_TRANSFER,
    rate_path: Path = DEFAULT_RATE,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    transfer = _load_json(transfer_path)
    rate = _load_json(rate_path)
    scheduling_summary = next((row for row in list(rate.get("lane_summaries") or []) if str(row.get("lane_id") or "") == "lane.scheduling"), {})
    rows = []
    for row in list(transfer.get("rows") or []):
        if str(row.get("target_lane_id") or "") != "lane.scheduling":
            continue
        rows.append(
            {
                "source_lane_id": str(row.get("source_lane_id") or ""),
                "target_lane_id": str(row.get("target_lane_id") or ""),
                "family_id": str(row.get("family_id") or ""),
                "transfer_status": str(row.get("transfer_status") or ""),
                "source_activation_status": str(row.get("activation_status") or ""),
                "target_broader_compounding_confidence": str(scheduling_summary.get("confidence_class") or ""),
                "target_positive_rate": float(scheduling_summary.get("positive_rate") or 0.0),
                "linkage_result": (
                    "retained_transfer_compounds"
                    if str(row.get("transfer_status") or "") == "retained" and float(scheduling_summary.get("positive_rate") or 0.0) > 0.0
                    else "non_retained_or_non_compounding"
                ),
            }
        )
    out = {
        "schema": "breadboard.darwin.stage6.transfer_compounding_linkage.v0",
        "source_refs": {
            "transfer_summary_ref": path_ref(transfer_path),
            "compounding_rate_ref": path_ref(rate_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Transfer-to-Compounding Linkage", ""]
    for row in rows:
        lines.append(f"- `{row['source_lane_id']} -> {row['target_lane_id']}`: `{row['linkage_result']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 transfer-to-compounding linkage surface.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_transfer_compounding_linkage()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_transfer_compounding_linkage={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
