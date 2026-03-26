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


DEFAULT_STAGE4_REGISTRY = ROOT / "artifacts" / "darwin" / "stage4" / "family_program" / "family_registry_v0.json"
DEFAULT_RATE = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_rate" / "compounding_rate_v0.json"
DEFAULT_REPO_SWE_FAMILY_AB = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab" / "repo_swe_family_ab_v0.json"
DEFAULT_CROSS_LANE = ROOT / "artifacts" / "darwin" / "stage5" / "cross_lane_review" / "cross_lane_review_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "family_reuse_tracking"
OUT_JSON = OUT_DIR / "family_reuse_tracking_v0.json"
OUT_MD = OUT_DIR / "family_reuse_tracking_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_family_reuse_tracking(
    *,
    stage4_registry_path: Path = DEFAULT_STAGE4_REGISTRY,
    compounding_rate_path: Path = DEFAULT_RATE,
    repo_swe_family_ab_path: Path = DEFAULT_REPO_SWE_FAMILY_AB,
    cross_lane_review_path: Path = DEFAULT_CROSS_LANE,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    stage4_registry = _load_json(stage4_registry_path)
    compounding_rate = _load_json(compounding_rate_path)
    repo_swe_family_ab = _load_json(repo_swe_family_ab_path)
    cross_lane_review = _load_json(cross_lane_review_path)
    lane_summary_map = {str(row.get("lane_id") or ""): dict(row) for row in list(compounding_rate.get("lane_summaries") or [])}
    repo_preferred_family = str(repo_swe_family_ab.get("preferred_family_kind") or "")
    lane_weight_map = {str(row.get("lane_id") or ""): str(row.get("lane_weight") or "unset") for row in list(cross_lane_review.get("rows") or [])}

    rows = []
    for row in list(stage4_registry.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        family_kind = str(row.get("family_kind") or "")
        lane_summary = lane_summary_map.get(lane_id, {})
        if lane_id == "lane.systems" and family_kind == "policy":
            stage5_state = "active_proving"
        elif lane_id == "lane.repo_swe" and family_kind == repo_preferred_family:
            stage5_state = "challenge_only"
        else:
            stage5_state = "held_back"
        rows.append(
            {
                "family_id": str(row.get("family_id") or ""),
                "lane_id": lane_id,
                "family_kind": family_kind,
                "family_key": str(row.get("family_key") or ""),
                "stage4_lifecycle_status": str(row.get("lifecycle_status") or ""),
                "stage5_family_state": stage5_state,
                "lane_weight": lane_weight_map.get(lane_id, "unset"),
                "round_count": int(lane_summary.get("round_count") or 0) if stage5_state != "held_back" else 0,
                "reuse_lift_count": int(lane_summary.get("total_reuse_lift_count") or 0) if stage5_state != "held_back" else 0,
                "flat_count": int(lane_summary.get("total_flat_count") or 0) if stage5_state != "held_back" else 0,
                "no_lift_count": int(lane_summary.get("total_no_lift_count") or 0) if stage5_state != "held_back" else 0,
                "reuse_lift_rate": float(lane_summary.get("aggregate_reuse_lift_rate") or 0.0) if stage5_state != "held_back" else 0.0,
                "activation_readiness": (
                    "current_center" if stage5_state in {"active_proving", "challenge_only"} else "needs_fresh_evidence"
                ),
                "replay_status": str(row.get("replay_status") or ""),
                "transfer_eligibility": dict(row.get("transfer_eligibility") or {}),
                "evidence_refs": list(row.get("evidence_refs") or []),
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.family_reuse_tracking.v0",
        "source_refs": {
            "stage4_registry_ref": path_ref(stage4_registry_path),
            "compounding_rate_ref": path_ref(compounding_rate_path),
            "repo_swe_family_ab_ref": path_ref(repo_swe_family_ab_path),
            "cross_lane_review_ref": path_ref(cross_lane_review_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    lines = ["# Stage-5 Family Reuse Tracking", ""]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}` / `{row['family_kind']}`: state=`{row['stage5_family_state']}`, reuse=`{row['reuse_lift_count']}`, "
            f"flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`, readiness=`{row['activation_readiness']}`"
        )
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 family reuse tracking surface.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_family_reuse_tracking()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_family_reuse_tracking={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
