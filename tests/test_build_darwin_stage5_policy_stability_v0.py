from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_policy_stability_v0 import build_stage5_policy_stability


def test_build_stage5_policy_stability_classifies_lanes(tmp_path: Path) -> None:
    bundle_path = tmp_path / "multilane_compounding_v0.json"
    lane_repo_summary = tmp_path / "lane_repo_swe_summary.json"
    lane_systems_summary = tmp_path / "lane_systems_summary.json"
    lane_repo_summary.write_text(
        json.dumps(
            {
                "claim_eligible_comparison_count": 8,
                "comparison_valid_count": 8,
                "reuse_lift_count": 0,
                "no_lift_count": 4,
                "policy_ref": "artifacts/darwin/stage5/tranche1/lane_repo_swe/search_policy_v2.json",
                "summary_path": str(lane_repo_summary),
            }
        ),
        encoding="utf-8",
    )
    lane_systems_summary.write_text(
        json.dumps(
            {
                "claim_eligible_comparison_count": 8,
                "comparison_valid_count": 8,
                "reuse_lift_count": 3,
                "no_lift_count": 1,
                "policy_ref": "artifacts/darwin/stage5/tranche1/lane_systems/search_policy_v2.json",
                "summary_path": str(lane_systems_summary),
            }
        ),
        encoding="utf-8",
    )
    bundle_path.write_text(
        json.dumps(
            {
                "schema": "breadboard.darwin.stage5.multilane_compounding.v0",
                "round_count": 2,
                "lane_count": 2,
                "lane_order": ["lane.repo_swe", "lane.systems"],
                "row_count": 4,
                "rows": [
                    {
                        "lane_id": "lane.repo_swe",
                        "round_index": 1,
                        "summary_ref": str(lane_repo_summary),
                    },
                    {
                        "lane_id": "lane.repo_swe",
                        "round_index": 2,
                        "summary_ref": str(lane_repo_summary),
                    },
                    {
                        "lane_id": "lane.systems",
                        "round_index": 1,
                        "summary_ref": str(lane_systems_summary),
                    },
                    {
                        "lane_id": "lane.systems",
                        "round_index": 2,
                        "summary_ref": str(lane_systems_summary),
                    },
                ],
                "lane_totals": {},
            }
        ),
        encoding="utf-8",
    )

    summary = build_stage5_policy_stability(bundle_path=bundle_path, out_dir=tmp_path / "out")
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["schema"] == "breadboard.darwin.stage5.policy_stability.v0"
    lane_map = {row["lane_id"]: row for row in payload["rows"]}
    assert lane_map["lane.repo_swe"]["stability_class"] == "stable_negative"
    assert lane_map["lane.systems"]["stability_class"] == "stable_positive"
    assert lane_map["lane.systems"]["policy_review_conclusion"] == "continue"
