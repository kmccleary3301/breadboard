from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_cross_lane_review_v0 import build_stage5_cross_lane_review


def test_build_stage5_cross_lane_review_promotes_systems_and_marks_repo_open(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_stability_v0.json"
    family_ab_path = tmp_path / "repo_swe_family_ab_v0.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema": "breadboard.darwin.stage5.policy_stability.v0",
                "rows": [
                    {
                        "lane_id": "lane.repo_swe",
                        "stability_class": "mixed_negative",
                        "policy_review_conclusion": "continue",
                        "reuse_lift_count": 4,
                        "flat_count": 2,
                        "no_lift_count": 6,
                        "reuse_lift_rate": 0.1667,
                    },
                    {
                        "lane_id": "lane.systems",
                        "stability_class": "mixed_positive",
                        "policy_review_conclusion": "continue",
                        "reuse_lift_count": 7,
                        "flat_count": 0,
                        "no_lift_count": 5,
                        "reuse_lift_rate": 0.2917,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    family_ab_path.write_text(
        json.dumps(
            {
                "schema": "breadboard.darwin.stage5.repo_swe_family_ab.v0",
                "family_totals": {
                    "topology": {
                        "reuse_lift_count": 3,
                        "flat_count": 5,
                        "no_lift_count": 4,
                    },
                    "tool_scope": {
                        "reuse_lift_count": 3,
                        "flat_count": 2,
                        "no_lift_count": 7,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    summary = build_stage5_cross_lane_review(
        policy_stability_path=policy_path,
        repo_swe_family_ab_path=family_ab_path,
        out_dir=tmp_path / "out",
    )

    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["schema"] == "breadboard.darwin.stage5.cross_lane_review.v0"
    assert payload["current_primary_lane_id"] == "lane.systems"
    assert payload["repo_swe_family_selection"]["family_selection_status"] == "open"
    lane_map = {row["lane_id"]: row for row in payload["rows"]}
    assert lane_map["lane.systems"]["lane_weight"] == "primary_proving_lane"
    assert lane_map["lane.repo_swe"]["lane_weight"] == "challenge_lane"
