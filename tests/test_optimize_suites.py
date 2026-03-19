from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    EvaluationSuiteManifest,
    ObjectiveBreakdownResult,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
    build_coding_overlay_benchmark_example,
    build_coding_overlay_benchmark_example_payload,
    build_support_execution_benchmark_example,
    build_support_execution_benchmark_example_payload,
    build_tool_guidance_benchmark_example,
    build_tool_guidance_benchmark_example_payload,
)


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
