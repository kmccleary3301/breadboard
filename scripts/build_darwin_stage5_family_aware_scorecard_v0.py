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


DEFAULT_COMPOUNDING_QUALITY = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_quality" / "compounding_quality_v0.json"
DEFAULT_REPO_SWE_CHALLENGE = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_challenge_refresh" / "compounding_pilot_v0.json"
DEFAULT_SYSTEMS_CONFIRMATION = ROOT / "artifacts" / "darwin" / "stage5" / "systems_confirmation" / "compounding_pilot_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "family_aware_scorecard"
OUT_JSON = OUT_DIR / "family_aware_scorecard_v0.json"
OUT_MD = OUT_DIR / "family_aware_scorecard_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _interpretation_note(*, lane_id: str, family_surface_status: str, reuse_lift_count: int, no_lift_count: int) -> str:
    if lane_id == "lane.systems" and reuse_lift_count >= no_lift_count:
        return "systems_primary_positive"
    if lane_id == "lane.repo_swe" and family_surface_status.startswith("settled_") and reuse_lift_count < no_lift_count:
        return "repo_swe_settled_but_weaker"
    if family_surface_status == "stale_or_incomplete":
        return "family_surface_not_clean"
    return "mixed_or_open"


def build_stage5_family_aware_scorecard(
    *,
    compounding_quality_path: Path = DEFAULT_COMPOUNDING_QUALITY,
    repo_swe_challenge_path: Path = DEFAULT_REPO_SWE_CHALLENGE,
    systems_confirmation_path: Path = DEFAULT_SYSTEMS_CONFIRMATION,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    quality_payload = _load_json(compounding_quality_path)
    repo_swe_challenge = _load_json(repo_swe_challenge_path) if repo_swe_challenge_path.exists() else {}
    systems_confirmation = _load_json(systems_confirmation_path) if systems_confirmation_path.exists() else {}

    confirmation_map = {
        "lane.repo_swe": repo_swe_challenge,
        "lane.systems": systems_confirmation,
    }
    rows = []
    for row in list(quality_payload.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        confirmation = confirmation_map.get(lane_id, {})
        family_surface_status = str(row.get("family_surface_status") or "unknown")
        rows.append(
            {
                "lane_id": lane_id,
                "lane_weight": str(row.get("lane_weight") or "unset"),
                "family_surface_status": family_surface_status,
                "claim_eligible_comparison_count": int(row.get("claim_eligible_comparison_count") or 0),
                "reuse_lift_count": int(row.get("reuse_lift_count") or 0),
                "flat_count": int(row.get("flat_count") or 0),
                "no_lift_count": int(row.get("no_lift_count") or 0),
                "live_run_status": str(confirmation.get("run_completion_status") or ("complete" if bool(row.get("live_review_round_complete")) else "partial_or_stale")),
                "challenge_or_confirmation_ref": path_ref(repo_swe_challenge_path if lane_id == "lane.repo_swe" else systems_confirmation_path) if confirmation else None,
                "interpretation_note": _interpretation_note(
                    lane_id=lane_id,
                    family_surface_status=family_surface_status,
                    reuse_lift_count=int(row.get("reuse_lift_count") or 0),
                    no_lift_count=int(row.get("no_lift_count") or 0),
                ),
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.family_aware_scorecard.v0",
        "source_refs": {
            "compounding_quality_ref": path_ref(compounding_quality_path),
            "repo_swe_challenge_ref": path_ref(repo_swe_challenge_path) if repo_swe_challenge_path.exists() else None,
            "systems_confirmation_ref": path_ref(systems_confirmation_path) if systems_confirmation_path.exists() else None,
        },
        "row_count": len(rows),
        "rows": rows,
    }

    lines = ["# Stage-5 Family-Aware Scorecard", ""]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}`: weight=`{row['lane_weight']}`, family_surface=`{row['family_surface_status']}`, "
            f"reuse_lift=`{row['reuse_lift_count']}`, flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`, "
            f"live_run=`{row['live_run_status']}`, note=`{row['interpretation_note']}`"
        )

    out_json = out_dir / OUT_JSON.name
    out_md = out_dir / OUT_MD.name
    write_json(out_json, payload)
    write_text(out_md, "\n".join(lines) + "\n")
    return {
        "out_json": str(out_json),
        "row_count": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 family-aware scorecard.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_family_aware_scorecard()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_family_aware_scorecard={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
