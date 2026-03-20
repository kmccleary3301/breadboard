from __future__ import annotations

import json
from pathlib import Path

from scripts import build_darwin_stage4_bounded_transfer_outcomes_v0 as transfer_mod
from scripts import build_darwin_stage4_failed_transfer_taxonomy_v0 as failed_mod
from scripts import build_darwin_stage4_family_candidates_v0 as candidates_mod
from scripts import build_darwin_stage4_family_memo_v0 as memo_mod
from scripts import build_darwin_stage4_family_promotion_report_v0 as promotion_mod
from scripts import build_darwin_stage4_family_registry_v0 as registry_mod
from scripts import build_darwin_stage4_family_scorecard_v0 as scorecard_mod
from scripts import build_darwin_stage4_family_verification_bundle_v0 as verify_mod
from scripts import build_darwin_stage4_second_family_decision_v0 as second_mod


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_stage4_family_program_round_trip(tmp_path, monkeypatch) -> None:
    deep = tmp_path / "deep"
    out = tmp_path / "family"

    _write(
        deep / "matched_budget_comparisons_v0.json",
        {
            "rows": [
                *[
                    {
                        "lane_id": "lane.repo_swe",
                        "operator_id": "mut.topology.single_to_pev_v1",
                        "comparison_valid": True,
                        "positive_power_signal": True,
                    }
                    for _ in range(4)
                ],
                *[
                    {
                        "lane_id": "lane.repo_swe",
                        "operator_id": "mut.tool_scope.add_git_diff_v1",
                        "comparison_valid": True,
                        "positive_power_signal": True,
                    }
                    for _ in range(2)
                ],
                *[
                    {
                        "lane_id": "lane.systems",
                        "operator_id": "mut.policy.shadow_memory_enable_v1",
                        "comparison_valid": True,
                        "positive_power_signal": True,
                    }
                    for _ in range(4)
                ],
                *[
                    {
                        "lane_id": "lane.systems",
                        "operator_id": "mut.topology.single_to_pev_v1",
                        "comparison_valid": True,
                        "positive_power_signal": True,
                    }
                    for _ in range(2)
                ],
            ]
        },
    )
    _write(
        deep / "family_prior_stability_v0.json",
        {
            "rows": [
                {"lane_id": "lane.repo_swe", "operator_id": "mut.topology.single_to_pev_v1", "latest_priority": 0.95, "priority_span": 0.05},
                {"lane_id": "lane.repo_swe", "operator_id": "mut.tool_scope.add_git_diff_v1", "latest_priority": 0.80, "priority_span": 0.10},
                {"lane_id": "lane.systems", "operator_id": "mut.policy.shadow_memory_enable_v1", "latest_priority": 0.70, "priority_span": 0.10},
                {"lane_id": "lane.systems", "operator_id": "mut.topology.single_to_pev_v1", "latest_priority": 0.90, "priority_span": 0.08},
            ]
        },
    )
    _write(
        deep / "replay_checks_v0.json",
        {
            "rows": [
                {"lane_id": "lane.repo_swe", "operator_id": "mut.topology.single_to_pev_v1", "replay_supported": True},
                {"lane_id": "lane.systems", "operator_id": "mut.policy.shadow_memory_enable_v1", "replay_supported": True},
            ]
        },
    )
    _write(
        deep / "campaign_runs_v0.json",
        {
            "runs": [
                {"lane_id": "lane.repo_swe", "operator_id": "mut.topology.single_to_pev_v1", "candidate_id": "cand.repo.top.1"},
                {"lane_id": "lane.repo_swe", "operator_id": "mut.tool_scope.add_git_diff_v1", "candidate_id": "cand.repo.tool.1"},
                {"lane_id": "lane.systems", "operator_id": "mut.policy.shadow_memory_enable_v1", "candidate_id": "cand.sys.pol.1"},
                {"lane_id": "lane.systems", "operator_id": "mut.topology.single_to_pev_v1", "candidate_id": "cand.sys.top.1"},
            ]
        },
    )

    monkeypatch.setattr(candidates_mod, "COMPARISONS", deep / "matched_budget_comparisons_v0.json")
    monkeypatch.setattr(candidates_mod, "PRIOR_STABILITY", deep / "family_prior_stability_v0.json")
    monkeypatch.setattr(candidates_mod, "REPLAY", deep / "replay_checks_v0.json")
    monkeypatch.setattr(candidates_mod, "RUNS", deep / "campaign_runs_v0.json")
    monkeypatch.setattr(candidates_mod, "OUT_JSON", out / "family_candidates_v0.json")
    monkeypatch.setattr(candidates_mod, "OUT_MD", out / "family_candidates_v0.md")

    monkeypatch.setattr(promotion_mod, "CANDIDATES", out / "family_candidates_v0.json")
    monkeypatch.setattr(promotion_mod, "OUT_JSON", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(promotion_mod, "OUT_MD", out / "family_promotion_report_v0.md")
    monkeypatch.setattr(promotion_mod, "LEDGER_JSON", out / "stage4_decision_ledger_v1.json")

    monkeypatch.setattr(second_mod, "PROMOTION", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(second_mod, "OUT_JSON", out / "second_family_decision_v0.json")
    monkeypatch.setattr(second_mod, "OUT_MD", out / "second_family_decision_v0.md")

    monkeypatch.setattr(registry_mod, "PROMOTION", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(registry_mod, "OUT_JSON", out / "family_registry_v0.json")
    monkeypatch.setattr(registry_mod, "OUT_MD", out / "family_registry_v0.md")

    monkeypatch.setattr(transfer_mod, "PROMOTION", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(transfer_mod, "REGISTRY", out / "family_registry_v0.json")
    monkeypatch.setattr(transfer_mod, "CANDIDATES", out / "family_candidates_v0.json")
    monkeypatch.setattr(transfer_mod, "REPLAY", deep / "replay_checks_v0.json")
    monkeypatch.setattr(transfer_mod, "OUT_JSON", out / "bounded_transfer_outcomes_v0.json")
    monkeypatch.setattr(transfer_mod, "OUT_MD", out / "bounded_transfer_outcomes_v0.md")
    monkeypatch.setattr(transfer_mod, "LEDGER_JSON", out / "stage4_decision_ledger_v1.json")

    monkeypatch.setattr(failed_mod, "TRANSFER", out / "bounded_transfer_outcomes_v0.json")
    monkeypatch.setattr(failed_mod, "OUT_JSON", out / "failed_transfer_taxonomy_v0.json")
    monkeypatch.setattr(failed_mod, "OUT_MD", out / "failed_transfer_taxonomy_v0.md")

    monkeypatch.setattr(scorecard_mod, "REGISTRY", out / "family_registry_v0.json")
    monkeypatch.setattr(scorecard_mod, "TRANSFER", out / "bounded_transfer_outcomes_v0.json")
    monkeypatch.setattr(scorecard_mod, "OUT_JSON", out / "family_scorecard_v0.json")
    monkeypatch.setattr(scorecard_mod, "OUT_MD", out / "family_scorecard_v0.md")

    monkeypatch.setattr(memo_mod, "PROMOTION", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(memo_mod, "REGISTRY", out / "family_registry_v0.json")
    monkeypatch.setattr(memo_mod, "TRANSFER", out / "bounded_transfer_outcomes_v0.json")
    monkeypatch.setattr(memo_mod, "FAILED", out / "failed_transfer_taxonomy_v0.json")
    monkeypatch.setattr(memo_mod, "SCORECARD", out / "family_scorecard_v0.json")
    monkeypatch.setattr(memo_mod, "SECOND", out / "second_family_decision_v0.json")
    monkeypatch.setattr(memo_mod, "OUT_JSON", out / "family_memo_v0.json")
    monkeypatch.setattr(memo_mod, "OUT_MD", out / "family_memo_v0.md")

    monkeypatch.setattr(verify_mod, "PROMOTION", out / "family_promotion_report_v0.json")
    monkeypatch.setattr(verify_mod, "REGISTRY", out / "family_registry_v0.json")
    monkeypatch.setattr(verify_mod, "TRANSFER", out / "bounded_transfer_outcomes_v0.json")
    monkeypatch.setattr(verify_mod, "FAILED", out / "failed_transfer_taxonomy_v0.json")
    monkeypatch.setattr(verify_mod, "SCORECARD", out / "family_scorecard_v0.json")
    monkeypatch.setattr(verify_mod, "MEMO", out / "family_memo_v0.json")
    monkeypatch.setattr(verify_mod, "SECOND", out / "second_family_decision_v0.json")
    monkeypatch.setattr(verify_mod, "LEDGER", out / "stage4_decision_ledger_v1.json")
    monkeypatch.setattr(verify_mod, "OUT_JSON", out / "family_verification_bundle_v0.json")

    candidates_summary = candidates_mod.build_stage4_family_candidates()
    promotion_summary = promotion_mod.build_stage4_family_promotion_report()
    second_summary = second_mod.build_stage4_second_family_decision()
    registry_summary = registry_mod.build_stage4_family_registry()
    transfer_summary = transfer_mod.build_stage4_bounded_transfer_outcomes()
    failed_summary = failed_mod.build_stage4_failed_transfer_taxonomy()
    scorecard_summary = scorecard_mod.build_stage4_family_scorecard()
    memo_summary = memo_mod.build_stage4_family_memo()
    verify_summary = verify_mod.build_stage4_family_verification_bundle()

    assert candidates_summary["row_count"] == 4
    assert promotion_summary["row_count"] == 4
    assert second_summary["decision"] == "two_promoted_families"
    assert registry_summary["row_count"] == 4
    assert transfer_summary["row_count"] == 3
    assert failed_summary["row_count"] == 2
    assert scorecard_summary["row_count"] == 4
    assert memo_summary["out_json"].endswith("family_memo_v0.json")
    assert verify_summary["promoted_family_count"] == 2
