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


DEFAULT_REGISTRY = ROOT / "artifacts" / "darwin" / "stage5" / "family_registry" / "family_registry_v0.json"
DEFAULT_RATE = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_rate" / "compounding_rate_v0.json"
DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer" / "bounded_transfer_outcomes_v0.json"
DEFAULT_COMPOSITION = ROOT / "artifacts" / "darwin" / "stage5" / "composition_canary" / "composition_canary_v0.json"
DEFAULT_REPLAY = ROOT / "artifacts" / "darwin" / "stage5" / "replay_posture" / "replay_posture_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "scaled_scorecard"
OUT_JSON = OUT_DIR / "scaled_scorecard_v0.json"
OUT_MD = OUT_DIR / "scaled_scorecard_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_scaled_scorecard(
    *,
    registry_path: Path = DEFAULT_REGISTRY,
    compounding_rate_path: Path = DEFAULT_RATE,
    transfer_path: Path = DEFAULT_TRANSFER,
    composition_path: Path = DEFAULT_COMPOSITION,
    replay_path: Path = DEFAULT_REPLAY,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    registry = _load_json(registry_path)
    compounding_rate = _load_json(compounding_rate_path)
    transfer = _load_json(transfer_path)
    composition = _load_json(composition_path)
    replay = _load_json(replay_path)

    lane_summary_map = {str(row.get("lane_id") or ""): dict(row) for row in list(compounding_rate.get("lane_summaries") or [])}
    replay_map = {str(row.get("subject_id") or ""): dict(row) for row in list(replay.get("rows") or []) if str(row.get("subject_type") or "") == "family"}
    retained_family_ids = {str(row.get("family_id") or "") for row in list(transfer.get("rows") or []) if str(row.get("transfer_status") or "") == "retained"}

    rows = []
    for row in list(registry.get("rows") or []):
        lane_summary = lane_summary_map.get(str(row.get("lane_id") or ""), {})
        rows.append(
            {
                "family_id": str(row.get("family_id") or ""),
                "lane_id": str(row.get("lane_id") or ""),
                "family_kind": str(row.get("family_kind") or ""),
                "stage5_family_state": str(row.get("stage5_family_state") or ""),
                "lane_weight": str(row.get("lane_weight") or "unset"),
                "aggregate_reuse_lift_rate": float(lane_summary.get("aggregate_reuse_lift_rate") or 0.0) if str(row.get("stage5_family_state") or "") != "held_back" else 0.0,
                "total_reuse_lift_count": int(lane_summary.get("total_reuse_lift_count") or 0) if str(row.get("stage5_family_state") or "") != "held_back" else 0,
                "total_no_lift_count": int(lane_summary.get("total_no_lift_count") or 0) if str(row.get("stage5_family_state") or "") != "held_back" else 0,
                "retained_transfer": str(row.get("family_id") or "") in retained_family_ids,
                "composition_result": str(composition.get("result") or ""),
                "replay_status": str(dict(replay_map.get(str(row.get("family_id") or "")) or {}).get("replay_status") or "unknown"),
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.scaled_scorecard.v0",
        "source_refs": {
            "family_registry_ref": path_ref(registry_path),
            "compounding_rate_ref": path_ref(compounding_rate_path),
            "bounded_transfer_ref": path_ref(transfer_path),
            "composition_canary_ref": path_ref(composition_path),
            "replay_posture_ref": path_ref(replay_path),
        },
        "row_count": len(rows),
        "rows": rows,
        "retained_transfer_count": sum(1 for row in rows if bool(row.get("retained_transfer"))),
        "composition_result": str(composition.get("result") or ""),
    }
    lines = ["# Stage-5 Scaled Scorecard", ""]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}` / `{row['family_kind']}`: state=`{row['stage5_family_state']}`, reuse_rate=`{row['aggregate_reuse_lift_rate']}`, "
            f"retained_transfer=`{row['retained_transfer']}`, replay=`{row['replay_status']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 scaled scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_scaled_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_scaled_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
