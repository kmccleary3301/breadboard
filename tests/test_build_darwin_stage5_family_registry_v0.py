from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_family_registry_v0 import build_stage5_family_registry


def test_build_stage5_family_registry_marks_active_and_held_back(tmp_path: Path) -> None:
    tracking_path = tmp_path / "family_reuse_tracking_v0.json"
    decision_path = tmp_path / "third_family_decision_v0.json"
    tracking_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"family_id": "family.systems.policy", "lane_id": "lane.systems", "family_kind": "policy", "family_key": "policy.shadow", "stage4_lifecycle_status": "promoted", "stage5_family_state": "active_proving", "lane_weight": "primary_proving_lane", "activation_readiness": "current_center", "transfer_eligibility": {"allowed_target_lanes": ["lane.scheduling"]}, "replay_status": "missing", "evidence_refs": []},
                    {"family_id": "family.repo.tool", "lane_id": "lane.repo_swe", "family_kind": "tool_scope", "family_key": "tool", "stage4_lifecycle_status": "withheld", "stage5_family_state": "held_back", "lane_weight": "challenge_lane", "activation_readiness": "needs_fresh_evidence", "transfer_eligibility": {"allowed_target_lanes": []}, "replay_status": "missing", "evidence_refs": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    decision_path.write_text(json.dumps({"decision": "hold_two_family_center"}), encoding="utf-8")
    summary = build_stage5_family_registry(reuse_tracking_path=tracking_path, third_family_decision_path=decision_path, out_dir=tmp_path / "out")
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    row_map = {row["family_id"]: row for row in payload["rows"]}
    assert row_map["family.systems.policy"]["transfer_candidate"] is True
    assert row_map["family.repo.tool"]["composition_eligibility"]["allowed"] is False
