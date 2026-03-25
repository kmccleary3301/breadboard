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


DEFAULT_POLICY_STABILITY = ROOT / "artifacts" / "darwin" / "stage5" / "policy_stability" / "policy_stability_v0.json"
DEFAULT_CROSS_LANE_REVIEW = ROOT / "artifacts" / "darwin" / "stage5" / "cross_lane_review" / "cross_lane_review_v0.json"
DEFAULT_SYSTEMS_WEIGHTED = ROOT / "artifacts" / "darwin" / "stage5" / "systems_weighted" / "systems_weighted_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "systems_weighted_live_review"
OUT_JSON = OUT_DIR / "systems_weighted_live_review_v0.json"
OUT_MD = OUT_DIR / "systems_weighted_live_review_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_systems_weighted_live_review(
    *,
    policy_stability_path: Path = DEFAULT_POLICY_STABILITY,
    cross_lane_review_path: Path = DEFAULT_CROSS_LANE_REVIEW,
    systems_weighted_path: Path = DEFAULT_SYSTEMS_WEIGHTED,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    policy_payload = _load_json(policy_stability_path)
    cross_lane_payload = _load_json(cross_lane_review_path)
    systems_weighted_payload = _load_json(systems_weighted_path)

    weighted_rows = {str(row.get("lane_id") or ""): dict(row) for row in list(systems_weighted_payload.get("rows") or [])}
    weighted_bundle_complete = bool(systems_weighted_payload.get("bundle_complete"))
    weighted_completed_row_count = int(systems_weighted_payload.get("completed_row_count") or 0)
    weighted_row_count = int(systems_weighted_payload.get("row_count") or 0)
    lane_rows = []
    for row in list(policy_payload.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        cross_lane_row = next((candidate for candidate in list(cross_lane_payload.get("rows") or []) if str(candidate.get("lane_id") or "") == lane_id), {})
        weighted_row = weighted_rows.get(lane_id, {})
        lane_rows.append(
            {
                "lane_id": lane_id,
                "lane_weight": str(cross_lane_row.get("lane_weight") or str(weighted_row.get("lane_weight") or "unset")),
                "stability_class": str(row.get("stability_class") or ""),
                "claim_eligible_comparison_count": int(row.get("claim_eligible_comparison_count") or 0),
                "comparison_valid_count": int(row.get("comparison_valid_count") or 0),
                "reuse_lift_count": int(row.get("reuse_lift_count") or 0),
                "flat_count": int(row.get("flat_count") or 0),
                "no_lift_count": int(row.get("no_lift_count") or 0),
                "reuse_lift_rate": float(row.get("reuse_lift_rate") or 0.0),
                "round_complete": bool(weighted_row.get("round_complete")),
                "live_claim_surface_status": str(weighted_row.get("live_claim_surface_status") or "unknown"),
            }
        )

    lane_map = {row["lane_id"]: row for row in lane_rows}
    systems_row = lane_map.get("lane.systems", {})
    repo_row = lane_map.get("lane.repo_swe", {})
    systems_primary_supported = (
        str(systems_row.get("lane_weight") or "") == "primary_proving_lane"
        and int(systems_row.get("claim_eligible_comparison_count") or 0) > 0
        and int(systems_row.get("reuse_lift_count") or 0) >= int(systems_row.get("no_lift_count") or 0)
    )
    repo_challenge_supported = (
        str(repo_row.get("lane_weight") or "") == "challenge_lane"
        and int(repo_row.get("claim_eligible_comparison_count") or 0) > 0
    )

    payload = {
        "schema": "breadboard.darwin.stage5.systems_weighted_live_review.v0",
        "source_refs": {
            "policy_stability_ref": path_ref(policy_stability_path),
            "cross_lane_review_ref": path_ref(cross_lane_review_path),
            "systems_weighted_ref": path_ref(systems_weighted_path),
        },
        "systems_weighted_bundle_complete": weighted_bundle_complete,
        "systems_weighted_completed_row_count": weighted_completed_row_count,
        "systems_weighted_row_count": weighted_row_count,
        "systems_weighted_live_run_status": "complete" if weighted_bundle_complete and weighted_completed_row_count == weighted_row_count else "partial_or_stale",
        "current_primary_lane_id": str(cross_lane_payload.get("current_primary_lane_id") or ""),
        "row_count": len(lane_rows),
        "rows": lane_rows,
        "systems_primary_supported": systems_primary_supported,
        "repo_challenge_supported": repo_challenge_supported,
        "next_step": "repo_swe_family_ab_repair_or_clean_live_rerun",
        "next_step_reason": "systems_primary_is_supported_by_live_evidence_but_repo_swe_family_surface_is_still stale".replace(" ", "_"),
    }

    lines = [
        "# Stage-5 Systems-Weighted Live Review",
        "",
        f"- policy stability: `{path_ref(policy_stability_path)}`",
        f"- cross-lane review: `{path_ref(cross_lane_review_path)}`",
        f"- systems-weighted bundle: `{path_ref(systems_weighted_path)}`",
        f"- systems-weighted live run status: `{payload['systems_weighted_live_run_status']}`",
        f"- systems primary supported: `{systems_primary_supported}`",
        f"- repo challenge supported: `{repo_challenge_supported}`",
    ]
    for row in lane_rows:
        lines.append(
            f"- `{row['lane_id']}`: weight=`{row['lane_weight']}`, claim_eligible=`{row['claim_eligible_comparison_count']}`, "
            f"reuse_lift=`{row['reuse_lift_count']}`, flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`"
        )

    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {
        "out_json": str(out_dir / OUT_JSON.name),
        "systems_primary_supported": systems_primary_supported,
        "repo_challenge_supported": repo_challenge_supported,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 systems-weighted live review bundle.")
    parser.add_argument("--policy-stability", default=str(DEFAULT_POLICY_STABILITY))
    parser.add_argument("--cross-lane-review", default=str(DEFAULT_CROSS_LANE_REVIEW))
    parser.add_argument("--systems-weighted", default=str(DEFAULT_SYSTEMS_WEIGHTED))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_systems_weighted_live_review(
        policy_stability_path=Path(args.policy_stability),
        cross_lane_review_path=Path(args.cross_lane_review),
        systems_weighted_path=Path(args.systems_weighted),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_systems_weighted_live_review={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
