from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage6_compounding_rate_v0 import build_stage6_compounding_rate
from scripts.build_darwin_stage6_economics_attribution_v0 import build_stage6_economics_attribution
from scripts.build_darwin_stage6_family_registry_v0 import build_stage6_family_registry
from scripts.build_darwin_stage6_replay_posture_v0 import build_stage6_replay_posture
from scripts.build_darwin_stage6_scorecard_v0 import build_stage6_scorecard
from scripts.build_darwin_stage6_transfer_compounding_linkage_v0 import build_stage6_transfer_compounding_linkage


def test_build_stage6_tranche3_artifacts(tmp_path: Path) -> None:
    compounding_path = tmp_path / "broader_compounding_v0.json"
    compounding_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "lane_id": "lane.scheduling",
                        "family_state": "retained_transfer_source",
                        "broader_compounding_status": "positive_broader_compounding",
                        "score_lift_vs_family_lockout": 0.2,
                        "score_lift_vs_single_lockout": 0.4,
                        "runtime_lift_vs_family_lockout_ms": 10,
                        "runtime_lift_vs_single_lockout_ms": 20,
                        "cost_lift_vs_family_lockout_usd": 0.0,
                        "cost_lift_vs_single_lockout_usd": 0.0,
                        "warm_start": {"execution_mode": "local_baseline", "provider_origin": "local"},
                        "family_lockout": {"execution_mode": "local_baseline"},
                        "single_family_lockout": {"execution_mode": "local_baseline"},
                    }
                ],
                "family_center_decision": "hold_single_retained_family_center",
            }
        ),
        encoding="utf-8",
    )
    transfer_path = tmp_path / "transfer_outcome_summary_v1.json"
    transfer_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "transfer_case_id": "transfer.systems.scheduling",
                        "source_lane_id": "lane.systems",
                        "target_lane_id": "lane.scheduling",
                        "family_id": "family.systems.policy",
                        "family_kind": "policy",
                        "activation_status": "active",
                        "lane_weight": "primary_proving_lane",
                        "transfer_status": "retained",
                        "transfer_reason": "target_lane_score_improved_materially",
                        "replay_status": "supported",
                    },
                    {
                        "transfer_case_id": "transfer.repo.systems",
                        "source_lane_id": "lane.repo_swe",
                        "target_lane_id": "lane.systems",
                        "family_id": "family.repo.topology",
                        "family_kind": "topology",
                        "activation_status": "challenge",
                        "lane_weight": "challenge_lane",
                        "transfer_status": "activation_probe",
                        "transfer_reason": "active_family_has_positive_stage6_compounding_signal",
                        "replay_status": "observed",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    rate = build_stage6_compounding_rate(source_path=compounding_path, out_dir=tmp_path / "rate")
    registry = build_stage6_family_registry(transfer_path=transfer_path, compounding_path=compounding_path, out_dir=tmp_path / "registry")
    economics = build_stage6_economics_attribution(source_path=compounding_path, out_dir=tmp_path / "econ")
    linkage = build_stage6_transfer_compounding_linkage(transfer_path=transfer_path, rate_path=tmp_path / "rate" / "compounding_rate_v0.json", out_dir=tmp_path / "linkage")
    replay = build_stage6_replay_posture(transfer_path=transfer_path, compounding_path=compounding_path, out_dir=tmp_path / "replay")
    scorecard = build_stage6_scorecard(
        registry_path=tmp_path / "registry" / "family_registry_v0.json",
        rate_path=tmp_path / "rate" / "compounding_rate_v0.json",
        economics_path=tmp_path / "econ" / "economics_attribution_v0.json",
        linkage_path=tmp_path / "linkage" / "transfer_compounding_linkage_v0.json",
        replay_path=tmp_path / "replay" / "replay_posture_v0.json",
        out_dir=tmp_path / "scorecard",
    )
    assert rate["row_count"] == 1
    assert registry["row_count"] == 3
    assert economics["row_count"] == 1
    assert linkage["row_count"] == 1
    assert replay["row_count"] >= 3
    payload = json.loads((tmp_path / "scorecard" / "scorecard_v0.json").read_text(encoding="utf-8"))
    assert scorecard["row_count"] == 3
    assert payload["rows"][0]["broader_compounding_confidence"] == "positive"
