from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_systems_weighted_live_review_v0 import build_stage5_systems_weighted_live_review


def test_build_stage5_systems_weighted_live_review_marks_systems_primary(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_stability_v0.json"
    cross_lane_path = tmp_path / "cross_lane_review_v0.json"
    weighted_path = tmp_path / "systems_weighted_compounding_v0.json"
    policy_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "lane_id": "lane.systems",
                        "stability_class": "mixed_positive",
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
                "current_primary_lane_id": "lane.systems",
                "repo_swe_family_selection": {"family_selection_status": "settled_topology"},
                "rows": [
                    {"lane_id": "lane.systems", "lane_weight": "primary_proving_lane"},
                    {"lane_id": "lane.repo_swe", "lane_weight": "challenge_lane"},
                ],
            }
        ),
        encoding="utf-8",
    )
    weighted_path.write_text(
        json.dumps(
            {
                "bundle_complete": True,
                "completed_row_count": 2,
                "row_count": 2,
                "rows": [
                    {"lane_id": "lane.systems", "lane_weight": "primary_proving_lane", "round_complete": True, "live_claim_surface_status": "claim_eligible_live"},
                    {"lane_id": "lane.repo_swe", "lane_weight": "challenge_lane", "round_complete": True, "live_claim_surface_status": "claim_eligible_live"},
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = build_stage5_systems_weighted_live_review(
        policy_stability_path=policy_path,
        cross_lane_review_path=cross_lane_path,
        systems_weighted_path=weighted_path,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["schema"] == "breadboard.darwin.stage5.systems_weighted_live_review.v0"
    assert payload["systems_weighted_live_run_status"] == "complete"
    assert payload["systems_primary_supported"] is True
    assert payload["repo_challenge_supported"] is True
    assert payload["next_step"] == "family_aware_proving_review_and_gate"
