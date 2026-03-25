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
DEFAULT_SYSTEMS_WEIGHTED_LIVE_REVIEW = ROOT / "artifacts" / "darwin" / "stage5" / "systems_weighted_live_review" / "systems_weighted_live_review_v0.json"
DEFAULT_REPO_SWE_FAMILY_AB = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab" / "repo_swe_family_ab_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_quality"
OUT_JSON = OUT_DIR / "compounding_quality_v0.json"
OUT_MD = OUT_DIR / "compounding_quality_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_compounding_quality(
    *,
    policy_stability_path: Path = DEFAULT_POLICY_STABILITY,
    cross_lane_review_path: Path = DEFAULT_CROSS_LANE_REVIEW,
    systems_weighted_live_review_path: Path = DEFAULT_SYSTEMS_WEIGHTED_LIVE_REVIEW,
    repo_swe_family_ab_path: Path = DEFAULT_REPO_SWE_FAMILY_AB,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    policy_payload = _load_json(policy_stability_path)
    cross_lane_payload = _load_json(cross_lane_review_path)
    systems_live_payload = _load_json(systems_weighted_live_review_path)
    repo_swe_family_payload = _load_json(repo_swe_family_ab_path)

    cross_lane_rows = {
        str(row.get("lane_id") or ""): dict(row)
        for row in list(cross_lane_payload.get("rows") or [])
    }
    systems_live_rows = {
        str(row.get("lane_id") or ""): dict(row)
        for row in list(systems_live_payload.get("rows") or [])
    }
    repo_swe_family_status = {
        "completion_status": str(repo_swe_family_payload.get("completion_status") or "unknown"),
        "family_selection_status": str(repo_swe_family_payload.get("family_selection_status") or "unknown"),
        "preferred_family_kind": repo_swe_family_payload.get("preferred_family_kind"),
        "valid_round_row_count": int(repo_swe_family_payload.get("valid_round_row_count") or 0),
        "stale_round_row_count": int(repo_swe_family_payload.get("stale_round_row_count") or 0),
    }

    rows = []
    for row in list(policy_payload.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        cross_lane_row = cross_lane_rows.get(lane_id, {})
        systems_live_row = systems_live_rows.get(lane_id, {})
        rows.append(
            {
                "lane_id": lane_id,
                "lane_weight": str(cross_lane_row.get("lane_weight") or "unset"),
                "stability_class": str(row.get("stability_class") or ""),
                "policy_review_conclusion": str(row.get("policy_review_conclusion") or ""),
                "claim_eligible_comparison_count": int(row.get("claim_eligible_comparison_count") or 0),
                "comparison_valid_count": int(row.get("comparison_valid_count") or 0),
                "reuse_lift_count": int(row.get("reuse_lift_count") or 0),
                "flat_count": int(row.get("flat_count") or 0),
                "no_lift_count": int(row.get("no_lift_count") or 0),
                "reuse_lift_rate": float(row.get("reuse_lift_rate") or 0.0),
                "live_review_round_complete": bool(systems_live_row.get("round_complete")),
                "live_claim_surface_status": str(systems_live_row.get("live_claim_surface_status") or "unknown"),
                "stale_family_surface_contamination": lane_id == "lane.repo_swe" and repo_swe_family_status["completion_status"] != "complete",
                "family_surface_status": repo_swe_family_status["family_selection_status"] if lane_id == "lane.repo_swe" else "not_applicable",
            }
        )

    payload = {
        "schema": "breadboard.darwin.stage5.compounding_quality.v0",
        "source_refs": {
            "policy_stability_ref": path_ref(policy_stability_path),
            "cross_lane_review_ref": path_ref(cross_lane_review_path),
            "systems_weighted_live_review_ref": path_ref(systems_weighted_live_review_path),
            "repo_swe_family_ab_ref": path_ref(repo_swe_family_ab_path),
        },
        "systems_weighted_live_run_status": str(systems_live_payload.get("systems_weighted_live_run_status") or "unknown"),
        "repo_swe_family_surface": repo_swe_family_status,
        "row_count": len(rows),
        "rows": rows,
    }

    lines = [
        "# Stage-5 Compounding Quality",
        "",
        f"- systems-weighted live run status: `{payload['systems_weighted_live_run_status']}`",
        f"- repo_swe family surface: `{repo_swe_family_status['family_selection_status']}`",
        f"- repo_swe family completion: `{repo_swe_family_status['completion_status']}`",
    ]
    for row in rows:
        lines.append(
            f"- `{row['lane_id']}`: weight=`{row['lane_weight']}`, stability=`{row['stability_class']}`, "
            f"reuse_lift=`{row['reuse_lift_count']}`, flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`, "
            f"family_surface=`{row['family_surface_status']}`"
        )

    out_json = out_dir / OUT_JSON.name
    out_md = out_dir / OUT_MD.name
    write_json(out_json, payload)
    write_text(out_md, "\n".join(lines) + "\n")
    return {
        "out_json": str(out_json),
        "row_count": len(rows),
        "systems_weighted_live_run_status": payload["systems_weighted_live_run_status"],
        "repo_swe_family_selection_status": repo_swe_family_status["family_selection_status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 compounding-quality summary.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_compounding_quality()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_compounding_quality={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
