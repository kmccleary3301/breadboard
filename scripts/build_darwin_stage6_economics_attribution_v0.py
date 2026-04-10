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
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "economics_attribution"
OUT_JSON = OUT_DIR / "economics_attribution_v0.json"
OUT_MD = OUT_DIR / "economics_attribution_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_economics_attribution(*, source_path: Path = DEFAULT_SOURCE, out_dir: Path = OUT_DIR) -> dict[str, object]:
    payload = _load_json(source_path)
    rows = []
    for row in list(payload.get("rows") or []):
        rows.append(
            {
                "lane_id": str(row.get("lane_id") or ""),
                "source_lane_id": str(row.get("source_lane_id") or ""),
                "provider_segmentation_status": str(row.get("provider_segmentation_status") or ""),
                "warm_start_execution_mode": str(dict(row.get("warm_start") or {}).get("execution_mode") or ""),
                "warm_start_provider_origin": str(dict(row.get("warm_start") or {}).get("provider_origin") or ""),
                "family_lockout_execution_mode": str(dict(row.get("family_lockout") or {}).get("execution_mode") or ""),
                "single_family_lockout_execution_mode": str(dict(row.get("single_family_lockout") or {}).get("execution_mode") or ""),
                "runtime_lift_vs_family_lockout_ms": int(row.get("runtime_lift_vs_family_lockout_ms") or 0),
                "runtime_lift_vs_single_lockout_ms": int(row.get("runtime_lift_vs_single_lockout_ms") or 0),
                "cost_lift_vs_family_lockout_usd": float(row.get("cost_lift_vs_family_lockout_usd") or 0.0),
                "cost_lift_vs_single_lockout_usd": float(row.get("cost_lift_vs_single_lockout_usd") or 0.0),
                "score_lift_vs_family_lockout": float(row.get("score_lift_vs_family_lockout") or 0.0),
                "score_lift_vs_single_lockout": float(row.get("score_lift_vs_single_lockout") or 0.0),
            }
        )
    out = {
        "schema": "breadboard.darwin.stage6.economics_attribution.v0",
        "source_refs": {"broader_compounding_ref": path_ref(source_path)},
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Economics Attribution", ""]
    for row in rows:
        lines.append(f"- `{row['lane_id']}`: score_vs_lockout=`{row['score_lift_vs_family_lockout']}`, runtime_vs_lockout_ms=`{row['runtime_lift_vs_family_lockout_ms']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 economics attribution surface.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_economics_attribution()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_economics_attribution={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
