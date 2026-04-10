from __future__ import annotations

import json
from pathlib import Path


def test_run_stage6_broader_transfer_matrix_emits_retained_and_invalid_rows(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage6_broader_transfer_matrix_v0 as module

    tranche1_dir = tmp_path / "tranche1"
    tranche1_dir.mkdir(parents=True, exist_ok=True)
    (tranche1_dir / "family_activation_v1.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "family_id": "family.systems.policy",
                        "lane_id": "lane.systems",
                        "family_kind": "policy",
                        "activation_enabled": True,
                        "activation_status": "active",
                        "lane_weight": "primary_proving_lane",
                        "transfer_targets": ["lane.scheduling"],
                        "replay_status": "supported",
                    },
                    {
                        "family_id": "family.repo.topology",
                        "lane_id": "lane.repo_swe",
                        "family_kind": "topology",
                        "activation_enabled": True,
                        "activation_status": "challenge",
                        "lane_weight": "challenge_lane",
                        "transfer_targets": ["lane.systems"],
                        "replay_status": "observed",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tranche1_dir / "compounding_cases_v2.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "compounding_case_id": "case.systems.r1",
                        "lane_id": "lane.systems",
                        "family_id": "family.systems.policy",
                        "family_kind": "policy",
                        "comparison_envelope": {"comparison_envelope_digest": "a" * 64},
                        "conclusion": "flat",
                    },
                    {
                        "compounding_case_id": "case.repo.r1",
                        "lane_id": "lane.repo_swe",
                        "family_id": "family.repo.topology",
                        "family_kind": "topology",
                        "comparison_envelope": {"comparison_envelope_digest": "b" * 64},
                        "conclusion": "reuse_lift",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tranche1_dir / "provider_segmentation_v1.json").write_text(
        json.dumps({"provider_segmentation_status": "claim_rows_segmented", "claim_rows_have_canonical_provider_segmentation": True}),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "_campaign_lookup", lambda: {"lane.scheduling": {"lane_id": "lane.scheduling"}})
    monkeypatch.setattr(
        module,
        "_run_scheduling_transfer_path",
        lambda **_: {
            "source_lane_id": "lane.systems",
            "target_lane_id": "lane.scheduling",
            "family_id": "family.systems.policy",
            "family_kind": "policy",
            "comparison_valid": True,
            "invalid_reason": None,
            "target_baseline_primary_score": 0.6,
            "target_transferred_primary_score": 1.0,
            "target_score_lift": 0.4,
            "target_execution_status": "complete",
            "target_candidate_ref": "cand",
            "target_evaluation_ref": "eval",
            "replay_status": "supported",
            "transfer_policy_mode": "systems_primary_hybrid_scheduling_transfer_v1",
            "transfer_policy_rationale": "bounded systems transfer",
        },
    )

    out_dir = tmp_path / "tranche2"
    summary = module.run_stage6_broader_transfer_matrix(tranche1_dir=tranche1_dir, out_dir=out_dir)
    payload = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert payload["retained_count"] == 1
    transfer_cases = json.loads((out_dir / "transfer_cases_v2.json").read_text(encoding="utf-8"))
    statuses = {row["transfer_status"] for row in transfer_cases["rows"]}
    assert "retained" in statuses
    assert "invalid" in statuses
