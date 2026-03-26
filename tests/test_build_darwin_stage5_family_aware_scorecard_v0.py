from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_family_aware_scorecard_v0 import build_stage5_family_aware_scorecard


def test_build_stage5_family_aware_scorecard_emits_lane_rows(tmp_path: Path) -> None:
    quality_path = tmp_path / "compounding_quality_v0.json"
    repo_path = tmp_path / "repo_swe_challenge.json"
    systems_path = tmp_path / "systems_confirmation.json"
    quality_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "lane_id": "lane.systems",
                        "lane_weight": "primary_proving_lane",
                        "family_surface_status": "not_applicable",
                        "claim_eligible_comparison_count": 24,
                        "reuse_lift_count": 7,
                        "flat_count": 0,
                        "no_lift_count": 5,
                        "live_review_round_complete": True,
                    },
                    {
                        "lane_id": "lane.repo_swe",
                        "lane_weight": "challenge_lane",
                        "family_surface_status": "settled_topology",
                        "claim_eligible_comparison_count": 24,
                        "reuse_lift_count": 4,
                        "flat_count": 2,
                        "no_lift_count": 6,
                        "live_review_round_complete": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    repo_path.write_text(json.dumps({"run_completion_status": "complete"}), encoding="utf-8")
    systems_path.write_text(json.dumps({"run_completion_status": "complete"}), encoding="utf-8")

    summary = build_stage5_family_aware_scorecard(
        compounding_quality_path=quality_path,
        repo_swe_challenge_path=repo_path,
        systems_confirmation_path=systems_path,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    row_map = {row["lane_id"]: row for row in payload["rows"]}
    assert row_map["lane.systems"]["interpretation_note"] == "systems_primary_positive"
    assert row_map["lane.repo_swe"]["interpretation_note"] == "repo_swe_settled_but_weaker"
    assert row_map["lane.repo_swe"]["live_run_status"] == "complete"
