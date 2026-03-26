from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_bounded_transfer_outcomes_v0 import build_stage5_bounded_transfer_outcomes


def test_build_stage5_bounded_transfer_outcomes_emits_retained_transfer(tmp_path: Path) -> None:
    registry_path = tmp_path / "family_registry_v0.json"
    quality_path = tmp_path / "compounding_quality_v0.json"
    stage4_transfer_path = tmp_path / "stage4_transfer.json"
    registry_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"family_id": "family.repo.topology", "lane_id": "lane.repo_swe", "family_kind": "topology", "stage5_family_state": "challenge_only", "transfer_eligibility": {"allowed_target_lanes": ["lane.systems"]}},
                    {"family_id": "family.systems.policy", "lane_id": "lane.systems", "family_kind": "policy", "stage5_family_state": "active_proving", "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]}},
                    {"family_id": "family.repo.tool", "lane_id": "lane.repo_swe", "family_kind": "tool_scope", "stage5_family_state": "held_back", "transfer_eligibility": {"allowed_target_lanes": []}},
                ]
            }
        ),
        encoding="utf-8",
    )
    quality_path.write_text(json.dumps({"rows": [{"lane_id": "lane.systems", "reuse_lift_count": 7, "no_lift_count": 5, "reuse_lift_rate": 0.29}]}), encoding="utf-8")
    stage4_transfer_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"transfer_id": "transfer.stage4.repo_swe.topology_to_systems.v0", "replay_status": "source_replay_supported"},
                    {"transfer_id": "transfer.stage4.systems.policy_to_scheduling.v0", "replay_status": "source_replay_missing"},
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = build_stage5_bounded_transfer_outcomes(
        stage5_registry_path=registry_path,
        compounding_quality_path=quality_path,
        stage4_transfer_path=stage4_transfer_path,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["retained_transfer_count"] == 1
    assert any(row["transfer_status"] == "retained" for row in payload["rows"])
