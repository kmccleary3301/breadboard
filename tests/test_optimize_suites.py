from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    EvaluationSuiteManifest,
    FamilyCompositionManifest,
    ObjectiveBreakdownResult,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
    TransferCohortManifest,
    build_codex_opencode_replay_config_transfer_cohort_follow_on_example,
    build_codex_opencode_replay_config_transfer_cohort_follow_on_example_payload,
    build_coding_overlay_benchmark_example,
    build_coding_overlay_benchmark_example_payload,
    build_codex_opencode_transfer_cohort_example,
    build_codex_opencode_transfer_cohort_example_payload,
    build_opencode_prompt_config_tool_guidance_package_example,
    build_opencode_prompt_config_tool_guidance_package_example_payload,
    build_opencode_prompt_config_tool_guidance_verifier_follow_on_example,
    build_opencode_prompt_config_tool_guidance_verifier_follow_on_example_payload,
    build_support_execution_benchmark_example,
    build_support_execution_benchmark_example_payload,
    build_support_execution_coding_overlay_composition_example,
    build_support_execution_coding_overlay_composition_example_payload,
    build_support_execution_tool_guidance_coding_overlay_package_example,
    build_support_execution_tool_guidance_coding_overlay_package_example_payload,
    build_support_execution_coding_overlay_verifier_follow_on_example,
    build_support_execution_coding_overlay_verifier_follow_on_example_payload,
    build_tool_guidance_coding_overlay_composition_example,
    build_tool_guidance_coding_overlay_composition_example_payload,
    build_tool_guidance_benchmark_example,
    build_tool_guidance_benchmark_example_payload,
)
from agentic_coder_prototype.optimize.examples import (
    build_coding_overlay_verifier_experiment_example,
    build_coding_overlay_verifier_experiment_example_payload,
)
from agentic_coder_prototype.optimize.suites import TransferSliceManifest, VerifierAugmentedExperimentResult


def test_support_execution_v2_suite_family_artifacts_round_trip() -> None:
    example = build_support_execution_benchmark_example()
    payload = build_support_execution_benchmark_example_payload()

    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])
    target_family = TargetFamilyManifest.from_dict(payload["target_family"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert example["manifest"].metadata["evaluation_suite_id"] == evaluation_suite.suite_id
    assert objective_suite.evaluation_suite_id == evaluation_suite.suite_id
    assert target_family.evaluation_suite_id == evaluation_suite.suite_id
    assert target_family.objective_suite_id == objective_suite.suite_id
    assert search_space.family_id == target_family.family_id
    assert objective_breakdown.objective_suite_id == objective_suite.suite_id
    assert objective_breakdown.candidate_id == example["child_candidate"].candidate_id
    assert objective_breakdown.aggregate_objectives["eligible_for_promotion"] is False


def test_support_execution_v2_binds_objective_breakdown_into_promotion_surface() -> None:
    example = build_support_execution_benchmark_example()
    result = example["benchmark_result"]
    objective_breakdown = example["objective_breakdown_result"]

    assert result.promotion_readiness_summary["objective_breakdown_result_id"] == objective_breakdown.result_id
    assert result.promotion_readiness_summary["objective_suite_id"] == example["objective_suite"].suite_id
    assert result.promotion_readiness_summary["target_family_id"] == example["target_family"].family_id


def test_tool_guidance_v2_suite_family_artifacts_round_trip() -> None:
    example = build_tool_guidance_benchmark_example()
    payload = build_tool_guidance_benchmark_example_payload()

    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])
    target_family = TargetFamilyManifest.from_dict(payload["target_family"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert example["manifest"].metadata["evaluation_suite_id"] == evaluation_suite.suite_id
    assert objective_suite.evaluation_suite_id == evaluation_suite.suite_id
    assert target_family.objective_suite_id == objective_suite.suite_id
    assert search_space.family_id == target_family.family_id
    assert objective_breakdown.aggregate_objectives["eligible_for_promotion"] is True
    assert target_family.runtime_context_assumptions["tool_pack_profile"] == "codex-dossier-default"


def test_coding_overlay_v2_suite_family_artifacts_round_trip() -> None:
    example = build_coding_overlay_benchmark_example()
    payload = build_coding_overlay_benchmark_example_payload()

    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])
    target_family = TargetFamilyManifest.from_dict(payload["target_family"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert example["manifest"].metadata["evaluation_suite_id"] == evaluation_suite.suite_id
    assert objective_suite.evaluation_suite_id == evaluation_suite.suite_id
    assert target_family.objective_suite_id == objective_suite.suite_id
    assert search_space.family_id == target_family.family_id
    assert objective_breakdown.aggregate_objectives["eligible_for_promotion"] is True
    assert target_family.runtime_context_assumptions["requires_apply_patch_tool"] is True


def test_objective_suite_requires_known_frontier_dimensions() -> None:
    with pytest.raises(ValueError, match="unknown objective channels"):
        ObjectiveSuiteManifest(
            suite_id="objsuite.bad",
            evaluation_suite_id="evalsuite.bad",
            objective_channels={"support_honesty": {"direction": "maximize"}},
            frontier_dimensions=["unknown_dimension"],
        )


def test_search_space_rejects_unknown_locus_constraints() -> None:
    with pytest.raises(ValueError, match="unknown loci"):
        SearchSpaceManifest(
            search_space_id="searchspace.bad",
            family_id="family.bad",
            allowed_loci=["locus.allowed"],
            mutation_kinds_by_locus={"locus.allowed": ["replace"]},
            value_domains_by_locus={"locus.allowed": {"kind": "enum"}},
            semantic_constraints={"locus.unknown": {"must_preserve_honesty": True}},
        )


def test_family_composition_manifest_requires_unique_members() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        FamilyCompositionManifest(
            composition_id="composition.bad",
            member_family_ids=["family.one", "family.one"],
            composition_kind="joint",
            shared_target_scope="shared_scope",
            evaluation_suite_id="evalsuite.bad",
            objective_suite_id="objsuite.bad",
            search_space_id="searchspace.bad",
            review_class="bounded",
            promotion_class="bounded_change",
        )


def test_search_space_requires_exactly_one_scope_binding() -> None:
    with pytest.raises(ValueError, match="exactly one of family_id or composition_id"):
        SearchSpaceManifest(
            search_space_id="searchspace.bad.scope",
            family_id="family.bad",
            composition_id="composition.bad",
            allowed_loci=["locus.allowed"],
            mutation_kinds_by_locus={"locus.allowed": ["replace"]},
            value_domains_by_locus={"locus.allowed": {"kind": "enum"}},
        )


def test_search_space_rejects_unknown_coupled_loci() -> None:
    with pytest.raises(ValueError, match="coupled_loci_groups references unknown loci"):
        SearchSpaceManifest(
            search_space_id="searchspace.bad.groups",
            family_id="family.bad",
            allowed_loci=["locus.allowed"],
            mutation_kinds_by_locus={"locus.allowed": ["replace"]},
            value_domains_by_locus={"locus.allowed": {"kind": "enum"}},
            coupled_loci_groups={"bad_group": ["locus.unknown"]},
        )


def _payload_contains_forbidden_key(value: object, forbidden_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in forbidden_keys:
                return True
            if _payload_contains_forbidden_key(nested, forbidden_keys):
                return True
    if isinstance(value, list):
        return any(_payload_contains_forbidden_key(item, forbidden_keys) for item in value)
    return False


def test_coding_overlay_verifier_experiment_round_trip() -> None:
    example = build_coding_overlay_verifier_experiment_example()
    payload = build_coding_overlay_verifier_experiment_example_payload()

    verifier_experiment = VerifierAugmentedExperimentResult.from_dict(payload["verifier_experiment"])
    family_example = example["family_example"]

    assert verifier_experiment.experiment_kind == "verifier_augmented_refinement"
    assert verifier_experiment.evaluation_suite_id == family_example["evaluation_suite"].suite_id
    assert verifier_experiment.objective_suite_id == family_example["objective_suite"].suite_id
    assert verifier_experiment.target_family_id == family_example["target_family"].family_id
    assert verifier_experiment.search_space_id == family_example["search_space"].search_space_id
    assert verifier_experiment.baseline_candidate_id == family_example["child_candidate"].candidate_id
    assert verifier_experiment.refined_candidate_id == example["refined_candidate"].candidate_id
    assert "bounded_edit_scope_verifier.v1" in verifier_experiment.verifier_stack


def test_coding_overlay_verifier_experiment_stays_family_bound_without_darwin_ontology() -> None:
    payload = build_coding_overlay_verifier_experiment_example_payload()
    verifier_payload = payload["verifier_experiment"]

    assert verifier_payload["metadata"]["non_kernel"] is True
    assert verifier_payload["metadata"]["darwin_boundary"] == "not_reopened"
    assert _payload_contains_forbidden_key(
        payload,
        {"campaign_id", "archive_id", "island_id", "genealogy_id"},
    ) is False


def test_tool_guidance_coding_overlay_composition_round_trip() -> None:
    example = build_tool_guidance_coding_overlay_composition_example()
    payload = build_tool_guidance_coding_overlay_composition_example_payload()

    composition = FamilyCompositionManifest.from_dict(payload["family_composition"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert composition.composition_id == "composition.tool_guidance_coding_overlay.v3"
    assert composition.member_family_ids == [
        "family.tool_guidance.v2",
        "family.coding_overlay.v2",
    ]
    assert search_space.composition_id == composition.composition_id
    assert search_space.stage_partitions["seed_guidance_pair"] == [
        "tool.render.exec_command",
        "prompt.section.optimization_guidance",
    ]
    assert objective_breakdown.member_family_breakdowns["family.tool_guidance.v2"]["tool_clarity"] == 0.8525
    assert example["staged_result"].metadata["composition_id"] == composition.composition_id
    assert example["benchmark_result"].metadata["model_policy"] == "nano_only"


def test_composition_payload_stays_outside_darwin_and_public_reward_ontology() -> None:
    payload = build_tool_guidance_coding_overlay_composition_example_payload()

    assert payload["evaluation_suite"]["metadata"]["model_policy"] == "nano_only"
    assert _payload_contains_forbidden_key(
        payload,
        {"campaign_id", "archive_id", "island_id", "genealogy_id", "reward_suite_id"},
    ) is False


def test_support_execution_coding_overlay_composition_round_trip() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    payload = build_support_execution_coding_overlay_composition_example_payload()

    composition = FamilyCompositionManifest.from_dict(payload["family_composition"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert composition.composition_id == "composition.support_execution_coding_overlay.v3"
    assert composition.member_family_ids == [
        "family.support_execution.v2",
        "family.coding_overlay.v2",
    ]
    assert search_space.composition_id == composition.composition_id
    assert search_space.stage_partitions["support_config_seed"] == [
        "policy.support_claim_limited_actions",
        "policy.execution_profile.selection",
    ]
    assert objective_breakdown.member_family_breakdowns["family.support_execution.v2"]["support_honesty"] == 0.9875
    assert example["staged_result"].metadata["composition_id"] == composition.composition_id
    assert example["benchmark_result"].metadata["model_policy"] == "nano_first"


def test_support_execution_coding_overlay_composition_records_model_tier_policy() -> None:
    payload = build_support_execution_coding_overlay_composition_example_payload()

    assert payload["evaluation_suite"]["rerun_policy"]["default_model"] == "gpt-5.4-nano"
    assert payload["evaluation_suite"]["rerun_policy"]["escalation_model"] == "gpt-5.4-mini"
    assert payload["evaluation_suite"]["metadata"]["mini_escalation_policy"] == "auditable_justified_only"
    assert payload["benchmark_result"]["variance_summary"]["mini_escalation_triggered"] is False


def test_support_execution_coding_overlay_verifier_follow_on_round_trip() -> None:
    example = build_support_execution_coding_overlay_verifier_follow_on_example()
    payload = build_support_execution_coding_overlay_verifier_follow_on_example_payload()

    verifier_experiment = VerifierAugmentedExperimentResult.from_dict(payload["verifier_experiment"])
    composition = FamilyCompositionManifest.from_dict(payload["composition_example"]["family_composition"])

    assert verifier_experiment.experiment_kind == "verifier_augmented_composed_refinement"
    assert verifier_experiment.evaluation_suite_id == payload["composition_example"]["evaluation_suite"]["suite_id"]
    assert verifier_experiment.objective_suite_id == payload["composition_example"]["objective_suite"]["suite_id"]
    assert verifier_experiment.search_space_id == payload["composition_example"]["search_space"]["search_space_id"]
    assert verifier_experiment.baseline_candidate_id == payload["composition_example"]["composed_candidate"]["candidate_id"]
    assert verifier_experiment.refined_candidate_id == example["refined_candidate"].candidate_id
    assert verifier_experiment.metadata["composition_id"] == composition.composition_id
    assert verifier_experiment.metadata["specialization_scope"] == "coding_overlay_member_inside_composed_lane"


def test_composed_verifier_follow_on_stays_narrow_and_outside_darwin_ontology() -> None:
    payload = build_support_execution_coding_overlay_verifier_follow_on_example_payload()
    verifier_payload = payload["verifier_experiment"]

    assert verifier_payload["metadata"]["non_kernel"] is True
    assert verifier_payload["metadata"]["darwin_boundary"] == "not_reopened"
    assert verifier_payload["metadata"]["model_policy"] == "nano_first"
    assert payload["composition_example"]["evaluation_suite"]["rerun_policy"]["default_model"] == "gpt-5.4-nano"
    assert payload["composition_example"]["evaluation_suite"]["rerun_policy"]["escalation_model"] == "gpt-5.4-mini"
    assert _payload_contains_forbidden_key(
        payload,
        {"campaign_id", "archive_id", "island_id", "genealogy_id", "reward_suite_id"},
    ) is False


def test_transfer_slice_manifest_round_trip() -> None:
    slice_manifest = TransferSliceManifest(
        slice_id="package.codex_dossier.current",
        slice_kind="package",
        selector={"artifact_ref": "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"},
        promotion_role="required",
        visibility="hidden_hold",
        metadata={"phase": "v4"},
    )

    round_tripped = TransferSliceManifest.from_dict(slice_manifest.to_dict())

    assert round_tripped.slice_id == slice_manifest.slice_id
    assert round_tripped.slice_kind == "package"
    assert round_tripped.visibility == "hidden_hold"


def test_transfer_cohort_manifest_round_trip() -> None:
    cohort = TransferCohortManifest(
        cohort_id="cohort.test.transfer.v5",
        cohort_kind="bounded_two_package_transfer",
        member_slice_ids=[
            "package.codex_dossier.current",
            "package.opencode_1_2_17.current",
            "model_tier.nano_first_openai",
        ],
        claim_scope={"package_ids": ["codex_dossier.current", "opencode_1_2_17.current"]},
        coverage_policy={"requires_hidden_hold_per_package": True},
        metadata={"phase": "v5"},
    )

    round_tripped = TransferCohortManifest.from_dict(cohort.to_dict())

    assert round_tripped.cohort_id == cohort.cohort_id
    assert round_tripped.cohort_kind == "bounded_two_package_transfer"
    assert round_tripped.member_slice_ids == cohort.member_slice_ids


def test_support_execution_tool_guidance_coding_overlay_package_round_trip() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()
    payload = build_support_execution_tool_guidance_coding_overlay_package_example_payload()

    composition = FamilyCompositionManifest.from_dict(payload["family_composition"])
    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])
    transfer_slices = [TransferSliceManifest.from_dict(item) for item in payload["transfer_slices"]]

    assert composition.composition_id == "composition.support_execution_tool_guidance_coding_overlay.v4"
    assert len(composition.member_family_ids) == 3
    assert evaluation_suite.signal_channels["semantic_judge"]["source_kind"] == "model_judge"
    assert "package_coherence" in objective_suite.channel_dependencies
    assert search_space.composition_id == composition.composition_id
    assert objective_breakdown.signal_status["review_gate"]["status"] == "required"
    assert objective_breakdown.slice_status["package.codex_dossier.current"]["status"] == "pass"
    assert [item.slice_kind for item in transfer_slices] == ["package", "model_tier", "environment"]
    assert example["promotion_summary"].transfer_slice_ids == sorted(item.slice_id for item in transfer_slices)
    assert example["promotion_summary"].attribution_summary["present"] is True


def test_opencode_prompt_config_tool_guidance_package_round_trip() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    payload = build_opencode_prompt_config_tool_guidance_package_example_payload()

    composition = FamilyCompositionManifest.from_dict(payload["family_composition"])
    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])
    search_space = SearchSpaceManifest.from_dict(payload["search_space"])
    objective_breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])
    transfer_slices = [TransferSliceManifest.from_dict(item) for item in payload["transfer_slices"]]

    assert composition.composition_id == "composition.opencode_prompt_config_tool_guidance.v4"
    assert len(composition.member_family_ids) == 3
    assert evaluation_suite.rerun_policy["default_model"] == "gpt-5.4-nano"
    assert evaluation_suite.rerun_policy["escalation_model"] == "gpt-5.4-mini"
    assert evaluation_suite.signal_channels["model_tier_audit"]["source_kind"] == "model_policy_audit"
    assert "package_transfer" in objective_suite.channel_dependencies
    assert search_space.composition_id == composition.composition_id
    assert search_space.stage_partitions["prompt_seed"] == [
        "prompt.pack.base.system",
        "prompt.pack.base.builder",
    ]
    assert objective_breakdown.slice_status["package.opencode_1_2_17.current"]["status"] == "pass"
    assert objective_breakdown.signal_status["model_tier_audit"]["status"] == "audited_pass"
    assert [item.slice_kind for item in transfer_slices] == [
        "package",
        "model_tier",
        "tool_pack",
        "provider_model",
    ]
    assert example["promotion_summary"].model_tier_audit["triggered"] is True
    assert example["promotion_summary"].attribution_summary["present"] is True
    assert payload["staged_result"]["metadata"]["search_policy_trace"][-1]["model_tier"] == "gpt-5.4-mini"


def test_opencode_package_verifier_follow_on_round_trip() -> None:
    example = build_opencode_prompt_config_tool_guidance_verifier_follow_on_example()
    payload = build_opencode_prompt_config_tool_guidance_verifier_follow_on_example_payload()

    verifier_experiment = VerifierAugmentedExperimentResult.from_dict(payload["verifier_experiment"])
    composition = FamilyCompositionManifest.from_dict(payload["package_example"]["family_composition"])
    transfer_slices = [TransferSliceManifest.from_dict(item) for item in payload["package_example"]["transfer_slices"]]

    assert verifier_experiment.experiment_kind == "verifier_augmented_package_refinement"
    assert verifier_experiment.evaluation_suite_id == payload["package_example"]["evaluation_suite"]["suite_id"]
    assert verifier_experiment.objective_suite_id == payload["package_example"]["objective_suite"]["suite_id"]
    assert verifier_experiment.search_space_id == payload["package_example"]["search_space"]["search_space_id"]
    assert verifier_experiment.baseline_candidate_id == payload["package_example"]["package_candidate"]["candidate_id"]
    assert verifier_experiment.refined_candidate_id == example["refined_candidate"].candidate_id
    assert verifier_experiment.metadata["composition_id"] == composition.composition_id
    assert verifier_experiment.metadata["specialization_scope"] == "prompt_pack_and_bounded_config_members_inside_package_lane"
    assert verifier_experiment.metadata["transfer_slice_ids"] == [item.slice_id for item in transfer_slices]


def test_opencode_package_verifier_follow_on_stays_narrow_and_outside_darwin_ontology() -> None:
    payload = build_opencode_prompt_config_tool_guidance_verifier_follow_on_example_payload()
    verifier_payload = payload["verifier_experiment"]

    assert verifier_payload["metadata"]["non_kernel"] is True
    assert verifier_payload["metadata"]["darwin_boundary"] == "not_reopened"
    assert verifier_payload["metadata"]["model_policy"] == "nano_first"
    assert payload["package_example"]["evaluation_suite"]["rerun_policy"]["default_model"] == "gpt-5.4-nano"
    assert payload["package_example"]["evaluation_suite"]["rerun_policy"]["escalation_model"] == "gpt-5.4-mini"
    assert _payload_contains_forbidden_key(
        payload,
        {"campaign_id", "archive_id", "island_id", "genealogy_id", "reward_suite_id"},
    ) is False


def test_codex_opencode_transfer_cohort_round_trip() -> None:
    example = build_codex_opencode_transfer_cohort_example()
    payload = build_codex_opencode_transfer_cohort_example_payload()

    cohort = TransferCohortManifest.from_dict(payload["transfer_cohort"])
    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])

    assert cohort.cohort_id == "cohort.codex_dossier_current.opencode_1_2_17.v5"
    assert cohort.member_slice_ids == [
        "package.codex_dossier.current",
        "package.opencode_1_2_17.current",
        "model_tier.nano_first_openai",
    ]
    assert evaluation_suite.metadata["evaluation_truth"] == "primary"
    assert objective_suite.promotion_annotations["requires_transfer_cohort_support"] is True
    assert payload["codex_cell"]["promotion_summary"]["claim_tier"] == "transfer_supported"
    assert payload["opencode_cell"]["promotion_summary"]["claim_tier"] == "transfer_supported"
    assert payload["cohort_rollup"]["status"] == "supported"
    assert example["codex_cell"]["benchmark_result"].transfer_cohort_status[cohort.cohort_id]["model_policy"] == "nano_only"


def test_transfer_cohort_payload_stays_narrow_and_outside_darwin_reward_ontology() -> None:
    payload = build_codex_opencode_transfer_cohort_example_payload()

    assert payload["evaluation_suite"]["metadata"]["reward_like_ranking"] == "private_only"
    assert _payload_contains_forbidden_key(
        payload,
        {
            "campaign_id",
            "archive_id",
            "island_id",
            "genealogy_id",
            "reward_suite_id",
            "search_policy_manifest_id",
        },
    ) is False


def test_transfer_cohort_follow_on_round_trip() -> None:
    example = build_codex_opencode_replay_config_transfer_cohort_follow_on_example()
    payload = build_codex_opencode_replay_config_transfer_cohort_follow_on_example_payload()

    cohort = TransferCohortManifest.from_dict(payload["transfer_cohort"])
    evaluation_suite = EvaluationSuiteManifest.from_dict(payload["evaluation_suite"])
    objective_suite = ObjectiveSuiteManifest.from_dict(payload["objective_suite"])

    assert cohort.cohort_id == "cohort.codex_dossier_current.opencode_1_2_17.replay_config.v5"
    assert cohort.metadata["follow_on"] is True
    assert evaluation_suite.rerun_policy["escalation_policy"] == "audited_semantic_tie_break_only"
    assert objective_suite.metadata["reward_like_ranking"] == "private_only"
    assert payload["cohort_rollup"]["status"] == "supported"
    assert payload["cohort_rollup"]["mini_audit_triggered"] is True
    assert example["opencode_cell"]["benchmark_result"].transfer_cohort_status[cohort.cohort_id]["model_policy"] == "nano_first"


def test_transfer_cohort_follow_on_adds_distinct_methodology_value() -> None:
    payload = build_codex_opencode_replay_config_transfer_cohort_follow_on_example_payload()

    assert payload["transfer_cohort"]["claim_scope"]["shared_family_emphasis"] == [
        "replay_safe_prompt_config_coherence",
        "package_scope_integrity",
    ]
    assert payload["evaluation_suite"]["signal_channels"]["model_tier_audit"]["source_kind"] == "model_policy_audit"
    assert payload["opencode_cell"]["promotion_summary"]["transfer_cohort_status"][
        payload["transfer_cohort"]["cohort_id"]
    ]["mini_audit_triggered"] is True
