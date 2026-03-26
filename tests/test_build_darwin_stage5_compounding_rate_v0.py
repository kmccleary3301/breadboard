from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_compounding_rate_v0 import build_stage5_compounding_rate


def test_build_stage5_compounding_rate_emits_lane_summaries(tmp_path: Path) -> None:
    systems_weighted_path = tmp_path / "systems_weighted_compounding_v0.json"
    scaled_path = tmp_path / "scaled_compounding_v0.json"
    systems_weighted_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"round_index": 1, "lane_id": "lane.systems", "lane_weight": "primary_proving_lane", "round_complete": True, "claim_eligible_comparison_count": 8, "reuse_lift_count": 3, "flat_count": 1, "no_lift_count": 1},
                    {"round_index": 2, "lane_id": "lane.systems", "lane_weight": "primary_proving_lane", "round_complete": True, "claim_eligible_comparison_count": 8, "reuse_lift_count": 4, "flat_count": 0, "no_lift_count": 1},
                    {"round_index": 1, "lane_id": "lane.repo_swe", "lane_weight": "challenge_lane", "round_complete": True, "claim_eligible_comparison_count": 8, "reuse_lift_count": 1, "flat_count": 2, "no_lift_count": 3},
                ]
            }
        ),
        encoding="utf-8",
    )
    scaled_path.write_text(json.dumps({"systems_weighted_ref": str(systems_weighted_path)}), encoding="utf-8")
    summary = build_stage5_compounding_rate(scaled_path=scaled_path, out_dir=tmp_path / "out")
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    lane_map = {row["lane_id"]: row for row in payload["lane_summaries"]}
    assert lane_map["lane.systems"]["aggregate_reuse_lift_rate"] > lane_map["lane.repo_swe"]["aggregate_reuse_lift_rate"]
    assert lane_map["lane.systems"]["round_trend"] == "improving"
