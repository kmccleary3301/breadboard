from __future__ import annotations

from agentic_coder_prototype.ctrees.holdout_generalization_pack import (
    build_phase10_full_holdout_scenarios,
    run_phase10_full_holdout_pack,
    run_phase10_pilot_holdout_pack,
)


def test_ctree_phase10_holdout_pack_runs_all_pilot_families() -> None:
    payload = run_phase10_pilot_holdout_pack()

    assert payload["schema_version"] == "phase10_holdout_generalization_pack_pilot_v1"
    assert payload["prompt_centric_baseline_status"] == "unresolved_not_run"
    assert payload["summary"]["scenario_count"] == 4
    assert payload["summary"]["family_count"] == 4
    assert "frozen_core" in payload["summary"]["system_ids"]
    assert "deterministic_reranker" in payload["summary"]["system_ids"]

    scenario_ids = {item["scenario_id"] for item in payload["scenarios"]}
    assert scenario_ids == {
        "pilot_resume_distractor_v1",
        "pilot_dependency_noise_v1",
        "pilot_semantic_pivot_v1",
        "pilot_subtree_salience_v1",
    }


def test_ctree_phase10_holdout_pack_stripped_support_has_zero_recall() -> None:
    payload = run_phase10_pilot_holdout_pack()

    for scenario in payload["scenarios"]:
        stripped = next(item for item in scenario["systems"] if item["system_id"] == "stripped_support")
        assert stripped["metrics"]["support_recall"] == 0.0
        assert stripped["counts"]["promoted_support_count"] == 0
        assert stripped["metrics"]["support_precision"] == 0.0


def test_ctree_phase10_full_holdout_pack_builds_16_base_scenarios_with_perturbations() -> None:
    scenarios = build_phase10_full_holdout_scenarios()

    assert len(scenarios) == 48
    assert len({item["base_scenario_id"] for item in scenarios}) == 16
    assert len([item for item in scenarios if item["perturbation"] == "base"]) == 16
    assert len([item for item in scenarios if item["perturbation"] == "order"]) == 16
    assert len([item for item in scenarios if item["perturbation"] == "tight_budget"]) == 16


def test_ctree_phase10_full_holdout_pack_emits_full_summary() -> None:
    payload = run_phase10_full_holdout_pack()

    assert payload["schema_version"] == "phase10_holdout_generalization_pack_v1"
    assert payload["prompt_centric_baseline_status"] == "resolved_external_bridge_checkpoint"
    assert payload["summary"]["scenario_count"] == 48
    assert payload["summary"]["base_scenario_count"] == 16
    assert payload["summary"]["family_count"] == 4
    assert payload["summary"]["order_perturbation_count"] == 16
    assert payload["summary"]["tight_budget_perturbation_count"] == 16
    assert "resume_distractors" in payload["summary"]["family_summaries"]
    assert "dependency_noise" in payload["summary"]["family_summaries"]
    assert "semantic_pivot" in payload["summary"]["family_summaries"]
    assert "subtree_salience" in payload["summary"]["family_summaries"]
