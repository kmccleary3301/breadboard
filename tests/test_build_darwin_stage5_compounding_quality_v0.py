from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_compounding_quality_v0 import build_stage5_compounding_quality


def test_build_stage5_compounding_quality_marks_repo_swe_family_contamination(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_stability_v0.json"
    cross_lane_path = tmp_path / "cross_lane_review_v0.json"
    systems_live_path = tmp_path / "systems_weighted_live_review_v0.json"
    repo_swe_family_path = tmp_path / "repo_swe_family_ab_v0.json"

    policy_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "lane_id": "lane.systems",
                        "stability_class": "mixed_positive",
                        "policy_review_conclusion": "continue",
                        "claim_eligible_comparison_count": 24,
                        "comparison_valid_count": 24,
                        "reuse_lift_count": 7,
                        "flat_count": 0,
                        "no_lift_count": 5,
                        "reuse_lift_rate": 0.2917,
                    },
                    {
                        "lane_id": "lane.repo_swe",
                        "stability_class": "mixed_negative",
                        "policy_review_conclusion": "tighten",
                        "claim_eligible_comparison_count": 24,
                        "comparison_valid_count": 24,
                        "reuse_lift_count": 4,
                        "flat_count": 2,
                        "no_lift_count": 6,
                        "reuse_lift_rate": 0.1667,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cross_lane_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"lane_id": "lane.systems", "lane_weight": "primary_proving_lane"},
                    {"lane_id": "lane.repo_swe", "lane_weight": "challenge_lane"},
                ]
            }
        ),
        encoding="utf-8",
    )
    systems_live_path.write_text(
        json.dumps(
            {
                "systems_weighted_live_run_status": "partial_or_stale",
                "rows": [
                    {"lane_id": "lane.systems", "round_complete": True, "live_claim_surface_status": "claim_eligible_live"},
                    {"lane_id": "lane.repo_swe", "round_complete": False, "live_claim_surface_status": "unknown"},
                ],
            }
        ),
        encoding="utf-8",
    )
    repo_swe_family_path.write_text(
        json.dumps(
            {
                "completion_status": "stale_or_incomplete",
                "family_selection_status": "stale_or_incomplete",
                "valid_round_row_count": 3,
                "stale_round_row_count": 1,
            }
        ),
        encoding="utf-8",
    )

    summary = build_stage5_compounding_quality(
        policy_stability_path=policy_path,
        cross_lane_review_path=cross_lane_path,
        systems_weighted_live_review_path=systems_live_path,
        repo_swe_family_ab_path=repo_swe_family_path,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    row_map = {row["lane_id"]: row for row in payload["rows"]}
    assert payload["systems_weighted_live_run_status"] == "partial_or_stale"
    assert row_map["lane.systems"]["lane_weight"] == "primary_proving_lane"
    assert row_map["lane.repo_swe"]["stale_family_surface_contamination"] is True
    assert row_map["lane.repo_swe"]["family_surface_status"] == "stale_or_incomplete"
