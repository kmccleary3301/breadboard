from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage5_scaled_scorecard_v0 import build_stage5_scaled_scorecard


def test_build_stage5_scaled_scorecard_combines_registry_and_transfer(tmp_path: Path) -> None:
    registry_path = tmp_path / "family_registry_v0.json"
    rate_path = tmp_path / "compounding_rate_v0.json"
    transfer_path = tmp_path / "bounded_transfer_outcomes_v0.json"
    composition_path = tmp_path / "composition_canary_v0.json"
    replay_path = tmp_path / "replay_posture_v0.json"
    registry_path.write_text(json.dumps({"rows": [{"family_id": "family.systems.policy", "lane_id": "lane.systems", "family_kind": "policy", "stage5_family_state": "active_proving", "lane_weight": "primary_proving_lane"}]}), encoding="utf-8")
    rate_path.write_text(json.dumps({"lane_summaries": [{"lane_id": "lane.systems", "aggregate_reuse_lift_rate": 0.3, "total_reuse_lift_count": 7, "total_no_lift_count": 5}]}), encoding="utf-8")
    transfer_path.write_text(json.dumps({"rows": [{"family_id": "family.systems.policy", "transfer_status": "retained"}]}), encoding="utf-8")
    composition_path.write_text(json.dumps({"result": "composition_not_authorized"}), encoding="utf-8")
    replay_path.write_text(json.dumps({"rows": [{"subject_type": "family", "subject_id": "family.systems.policy", "replay_status": "replay_observed_but_weak"}]}), encoding="utf-8")
    summary = build_stage5_scaled_scorecard(
        registry_path=registry_path,
        compounding_rate_path=rate_path,
        transfer_path=transfer_path,
        composition_path=composition_path,
        replay_path=replay_path,
        out_dir=tmp_path / "out",
    )
    payload = json.loads(Path(summary["out_json"]).read_text(encoding="utf-8"))
    assert payload["retained_transfer_count"] == 1
    assert payload["rows"][0]["replay_status"] == "replay_observed_but_weak"
