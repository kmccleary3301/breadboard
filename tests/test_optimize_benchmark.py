from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    BackendComparisonResult,
    BenchmarkRunManifest,
    BenchmarkRunResult,
    CandidateComparisonResult,
    build_backend_comparison_example,
    build_backend_comparison_example_payload,
    build_paired_candidate_comparison,
    build_coding_overlay_benchmark_example,
    build_coding_overlay_benchmark_example_payload,
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
    build_staged_backend_comparison_example,
    build_staged_backend_comparison_example_payload,
)


def test_support_execution_benchmark_example_is_self_consistent() -> None:
    example = build_support_execution_benchmark_example()
    manifest = example["manifest"]
    comparison = example["comparison_result"]
    result = example["benchmark_result"]
    dataset = example["dataset"]
    child_candidate = example["child_candidate"]

    assert isinstance(manifest, BenchmarkRunManifest)
    assert isinstance(comparison, CandidateComparisonResult)
    assert isinstance(result, BenchmarkRunResult)

    assert manifest.target_id == dataset.samples[0].target_id
    assert manifest.dataset_id == dataset.dataset_id
    assert set(manifest.sample_ids()) == {sample.sample_id for sample in dataset.samples}
    assert manifest.hidden_hold_sample_ids() == ["sample.support_execution.hold.001"]
    assert comparison.manifest_id == manifest.manifest_id
    assert comparison.better_candidate_id == child_candidate.candidate_id
    assert set(comparison.held_out_sample_ids).issubset(set(manifest.hidden_hold_sample_ids()))
    assert result.comparison_results[0].comparison_id == comparison.comparison_id
    assert result.promotion_readiness_summary["requires_review"] is True


def test_support_execution_benchmark_payload_round_trips() -> None:
    payload = build_support_execution_benchmark_example_payload()

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    result = BenchmarkRunResult.from_dict(payload["benchmark_result"])

    assert manifest.manifest_id == "manifest.support_execution.v1"
    assert comparison.manifest_id == manifest.manifest_id
    assert result.manifest_id == manifest.manifest_id
    assert result.comparison_results[0].comparison_id == comparison.comparison_id


def test_paired_comparison_rejects_unknown_samples() -> None:
    manifest = build_support_execution_benchmark_example()["manifest"]

    with pytest.raises(ValueError, match="unknown sample ids"):
        build_paired_candidate_comparison(
            manifest,
            comparison_id="comparison.bad.unknown",
            parent_candidate_id="cand.parent",
            child_candidate_id="cand.child",
            outcome="win",
            compared_sample_ids=["sample.unknown"],
            held_out_sample_ids=[],
            trial_count=1,
            rationale="bad comparison",
            better_candidate_id="cand.child",
        )


def test_paired_comparison_requires_hidden_hold_membership() -> None:
    manifest = build_support_execution_benchmark_example()["manifest"]

    with pytest.raises(ValueError, match="hidden_hold"):
        build_paired_candidate_comparison(
            manifest,
            comparison_id="comparison.bad.hold",
            parent_candidate_id="cand.parent",
            child_candidate_id="cand.child",
            outcome="win",
            compared_sample_ids=["sample.support_execution.train.001"],
            held_out_sample_ids=["sample.support_execution.train.001"],
            trial_count=1,
            rationale="bad hold selection",
            better_candidate_id="cand.child",
        )


def test_tool_guidance_benchmark_example_round_trips() -> None:
    example = build_tool_guidance_benchmark_example()
    payload = build_tool_guidance_benchmark_example_payload()

    assert example["manifest"].benchmark_kind == "tool_guidance_pack"
    assert example["comparison_result"].outcome == "win"
    assert example["benchmark_result"].promotion_readiness_summary["eligible_for_promotion"] is True

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    result = BenchmarkRunResult.from_dict(payload["benchmark_result"])
    assert manifest.hidden_hold_sample_ids() == ["sample.tool_guidance.hold.001"]
    assert result.manifest_id == manifest.manifest_id


def test_coding_overlay_benchmark_example_round_trips() -> None:
    example = build_coding_overlay_benchmark_example()
    payload = build_coding_overlay_benchmark_example_payload()

    assert example["manifest"].benchmark_kind == "coding_overlay_pack"
    assert example["comparison_result"].held_out_sample_ids == ["sample.coding_overlay.hold.001"]
    assert example["benchmark_result"].promotion_readiness_summary["eligible_for_promotion"] is True

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    assert manifest.manifest_id == comparison.manifest_id


def test_backend_comparison_example_uses_fixed_manifests() -> None:
    example = build_backend_comparison_example()
    comparison = example["backend_comparison"]

    assert isinstance(comparison, BackendComparisonResult)
    assert comparison.winner_backend_id == "reflective_pareto_backend_v1"
    assert len(comparison.manifest_ids) == 3
    assert len(example["reflective_benchmark_runs"]) == 3
    assert len(example["greedy_benchmark_runs"]) == 3
    assert {run.manifest_id for run in example["reflective_benchmark_runs"]} == set(comparison.manifest_ids)
    assert {run.manifest_id for run in example["greedy_benchmark_runs"]} == set(comparison.manifest_ids)
    assert example["greedy_backend_result"].backend_id == "single_locus_greedy.v1"


def test_backend_comparison_payload_round_trips() -> None:
    payload = build_backend_comparison_example_payload()

    comparison = BackendComparisonResult.from_dict(payload["backend_comparison"])
    reflective_runs = [BenchmarkRunResult.from_dict(item) for item in payload["reflective_benchmark_runs"]]
    greedy_runs = [BenchmarkRunResult.from_dict(item) for item in payload["greedy_benchmark_runs"]]

    assert comparison.winner_backend_id == "reflective_pareto_backend_v1"
    assert comparison.backend_run_ids["reflective_pareto_backend_v1"] == [run.run_id for run in reflective_runs]
    assert comparison.backend_run_ids["single_locus_greedy.v1"] == [run.run_id for run in greedy_runs]
    assert all(run.metadata["backend_family"] for run in reflective_runs + greedy_runs)


def test_staged_backend_comparison_payload_round_trips() -> None:
    payload = build_staged_backend_comparison_example_payload()

    comparison = BackendComparisonResult.from_dict(payload["backend_comparison"])

    assert comparison.winner_backend_id == "staged_optimizer.v1"
    assert comparison.reproducibility_notes["fixed_methodology"] is True
    assert len(payload["reflective_results"]) == 3
    assert len(payload["staged_results"]) == 3
    assert len(payload["family_requests"]) == 3


def test_tool_guidance_coding_overlay_composition_payload_round_trips() -> None:
    example = build_tool_guidance_coding_overlay_composition_example()
    payload = build_tool_guidance_coding_overlay_composition_example_payload()

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    result = BenchmarkRunResult.from_dict(payload["benchmark_result"])

    assert example["family_composition"].composition_id == "composition.tool_guidance_coding_overlay.v3"
    assert manifest.hidden_hold_sample_ids() == ["sample.tool_guidance_coding_overlay.hold.001"]
    assert comparison.manifest_id == manifest.manifest_id
    assert comparison.metadata["model_policy"] == "nano_only"
    assert result.metadata["composition_id"] == example["family_composition"].composition_id


def test_support_execution_coding_overlay_composition_payload_round_trips() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    payload = build_support_execution_coding_overlay_composition_example_payload()

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    result = BenchmarkRunResult.from_dict(payload["benchmark_result"])

    assert example["family_composition"].composition_id == "composition.support_execution_coding_overlay.v3"
    assert manifest.hidden_hold_sample_ids() == ["sample.support_execution_coding_overlay.hold.001"]
    assert comparison.manifest_id == manifest.manifest_id
    assert comparison.metadata["model_policy"] == "nano_first"
    assert result.metadata["composition_id"] == example["family_composition"].composition_id
    assert result.variance_summary["default_model"] == "gpt-5.4-nano"
    assert result.variance_summary["mini_escalation_triggered"] is False


def test_support_execution_coding_overlay_verifier_follow_on_payload_round_trips() -> None:
    example = build_support_execution_coding_overlay_verifier_follow_on_example()
    payload = build_support_execution_coding_overlay_verifier_follow_on_example_payload()

    manifest = BenchmarkRunManifest.from_dict(payload["composition_example"]["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])

    assert comparison.manifest_id == manifest.manifest_id
    assert comparison.parent_candidate_id == payload["composition_example"]["composed_candidate"]["candidate_id"]
    assert comparison.better_candidate_id == example["refined_candidate"].candidate_id
    assert comparison.metadata["experiment_kind"] == "verifier_augmented_composed_refinement"


def test_support_execution_tool_guidance_coding_overlay_package_payload_round_trips() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()
    payload = build_support_execution_tool_guidance_coding_overlay_package_example_payload()

    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    result = BenchmarkRunResult.from_dict(payload["benchmark_result"])

    assert example["family_composition"].composition_id == "composition.support_execution_tool_guidance_coding_overlay.v4"
    assert len(manifest.transfer_slices) == 3
    assert manifest.hidden_hold_sample_ids() == ["sample.support_execution_tool_guidance_coding_overlay.hold.001"]
    assert comparison.manifest_id == manifest.manifest_id
    assert comparison.metadata["baseline_kind"] == "v3_composed_baseline"
    assert result.promotion_readiness_summary["transfer_slice_ids"] == [
        "environment.workspace_write_replay_safe",
        "model_tier.nano_first_openai",
        "package.codex_dossier.current",
    ]
