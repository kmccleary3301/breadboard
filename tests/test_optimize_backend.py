from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    CandidateBundle,
    CandidateChange,
    MaterializedCandidate,
    MutationBounds,
    ReflectionPolicyInput,
    ReflectiveParetoBackendRequest,
    ReflectiveParetoBackendResult,
    SingleLocusGreedyBackend,
    SupportEnvelope,
    WrongnessGuidedReflectionPolicy,
    build_codex_dossier_backend_example,
    build_codex_dossier_backend_example_payload,
    build_codex_dossier_evaluation_example,
    build_opencode_prompt_config_tool_guidance_package_example,
    build_support_execution_benchmark_example,
    build_support_execution_coding_overlay_composition_example,
    build_support_execution_tool_guidance_coding_overlay_package_example,
    run_single_locus_greedy_backend,
    validate_bounded_candidate,
)
from agentic_coder_prototype.optimize.backend import StagedOptimizer, StagedOptimizerRequest, run_staged_optimizer
from agentic_coder_prototype.optimize.examples import build_staged_backend_comparison_example


def test_backend_example_round_trip() -> None:
    payload = build_codex_dossier_backend_example_payload()
    request = ReflectiveParetoBackendRequest.from_dict(payload["request"])
    result = ReflectiveParetoBackendResult.from_dict(payload["result"])

    assert request.request_id == result.request_id
    assert result.reflection_decision.should_mutate is True
    assert len(result.proposals) == 2


def test_backend_example_produces_tradeoff_candidates() -> None:
    example = build_codex_dossier_backend_example()
    result = example["result"]

    assert isinstance(result, ReflectiveParetoBackendResult)
    assert result.reflection_decision.recommended_loci == ["tool.render.exec_command"]
    assert len(result.proposals) == 2
    assert len(result.portfolio.entries) == 2
    assert any(entry.score_kind == "predicted" for entry in result.portfolio.entries)
    assert sorted(len(proposal.candidate.applied_loci) for proposal in result.proposals) == [1, 2]


def test_single_locus_greedy_backend_limits_proposals() -> None:
    example = build_codex_dossier_backend_example()
    request = example["request"]

    result = run_single_locus_greedy_backend(request)

    assert isinstance(result, ReflectiveParetoBackendResult)
    assert result.backend_id == "single_locus_greedy.v1"
    assert len(result.proposals) == 1
    assert len(result.portfolio.entries) <= 2
    assert result.metadata["proposal_limit"] == 1


def test_single_locus_greedy_backend_class_uses_same_result_contract() -> None:
    example = build_codex_dossier_backend_example()
    backend = SingleLocusGreedyBackend()

    result = backend.run(example["request"])

    assert isinstance(result, ReflectiveParetoBackendResult)
    assert result.backend_id == "single_locus_greedy.v1"


def test_reflection_policy_uses_wrongness_not_only_outcome() -> None:
    example = build_codex_dossier_evaluation_example()
    target = example["target"]
    candidate = example["candidate"]
    dataset = example["dataset"]
    evaluation = example["evaluation"]
    materialized = build_codex_dossier_backend_example()["request"].baseline_materialized_candidate
    assert isinstance(candidate, CandidateBundle)

    policy = WrongnessGuidedReflectionPolicy()
    blocked = policy.reflect(
        ReflectionPolicyInput(
            target=target,
            baseline_candidate=candidate,
            baseline_materialized_candidate=materialized,
            dataset=dataset,
            evaluations=[evaluation.from_dict({**evaluation.to_dict(), "wrongness_reports": []})],
            mutation_bounds=MutationBounds(),
        )
    )
    allowed = policy.reflect(
        ReflectionPolicyInput(
            target=target,
            baseline_candidate=candidate,
            baseline_materialized_candidate=materialized,
            dataset=dataset,
            evaluations=[evaluation],
            mutation_bounds=MutationBounds(),
        )
    )

    assert blocked.should_mutate is False
    assert "wrongness evidence" in str(blocked.declined_reason)
    assert allowed.should_mutate is True
    assert allowed.recommended_loci == ["tool.render.exec_command"]


def test_validate_bounded_candidate_rejects_out_of_scope_loci() -> None:
    example = build_codex_dossier_evaluation_example()
    target = example["target"]
    candidate = CandidateBundle(
        candidate_id="cand.bad.scope",
        source_target_id=target.target_id,
        applied_loci=["missing.locus"],
        changes=[CandidateChange(locus_id="missing.locus", value={"text": "bad"})],
    )

    with pytest.raises(ValueError, match="unknown loci"):
        validate_bounded_candidate(target, candidate, MutationBounds())


def test_validate_bounded_candidate_rejects_broad_overlay() -> None:
    example = build_codex_dossier_evaluation_example()
    target = example["target"]
    candidate = CandidateBundle(
        candidate_id="cand.bad.blast_radius",
        source_target_id=target.target_id,
        applied_loci=["tool.render.exec_command", "prompt.section.optimization_guidance"],
        changes=[
            CandidateChange(locus_id="tool.render.exec_command", value={"description": "tight"}),
            CandidateChange(locus_id="prompt.section.optimization_guidance", value={"text": "tight"}),
        ],
    )

    with pytest.raises(ValueError, match="max_changed_loci"):
        validate_bounded_candidate(target, candidate, MutationBounds(max_changed_loci=1))


def test_validate_bounded_candidate_rejects_support_drift() -> None:
    example = build_codex_dossier_backend_example()
    request = example["request"]
    proposal = example["result"].proposals[0]
    broadened = MaterializedCandidate(
        candidate_id=proposal.candidate.candidate_id,
        source_target_id=request.target.target_id,
        applied_loci=proposal.candidate.applied_loci,
        effective_artifact={"overlay_by_locus": {}},
        effective_tool_surface=request.baseline_materialized_candidate.effective_tool_surface,
        support_envelope=SupportEnvelope.from_dict(
            {
                **request.target.support_envelope.to_dict(),
                "tools": request.target.support_envelope.tools + ["new_tool"],
            }
        ),
        evaluation_input_compatibility=request.baseline_materialized_candidate.evaluation_input_compatibility,
    )

    with pytest.raises(ValueError, match="support envelope"):
        validate_bounded_candidate(
            request.target,
            proposal.candidate,
            request.mutation_bounds,
            materialized=broadened,
        )


def _build_staged_request() -> StagedOptimizerRequest:
    example = build_support_execution_benchmark_example()
    staged_example = build_staged_backend_comparison_example()
    backend_request = staged_example["family_requests"][0]
    first_family = staged_example["family_examples"][0]
    assert first_family["target_family"].family_id == example["target_family"].family_id
    return StagedOptimizerRequest(
        request_id="staged_request.backend_test.001",
        backend_request=backend_request,
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
    )


def test_staged_optimizer_builds_deterministic_stage_plan() -> None:
    request = _build_staged_request()
    backend = StagedOptimizer()

    left = backend.build_stage_plan(request)
    right = backend.build_stage_plan(request)

    assert [item.to_dict() for item in left] == [item.to_dict() for item in right]
    assert left[0].allowed_loci == ["policy.support_claim_limited_actions"]
    assert "hidden_hold" not in left[0].allowed_split_visibilities


def test_staged_optimizer_request_rejects_search_space_mismatch() -> None:
    example = build_support_execution_benchmark_example()
    staged_example = build_staged_backend_comparison_example()
    backend_request = staged_example["family_requests"][0]
    bad_search_space = example["search_space"].from_dict(
        {
            **example["search_space"].to_dict(),
            "family_id": "family.bad",
        }
    )

    with pytest.raises(ValueError, match="search space family_id"):
        StagedOptimizerRequest(
            request_id="staged_request.bad.001",
            backend_request=backend_request,
            evaluation_suite=example["evaluation_suite"],
            objective_suite=example["objective_suite"],
            target_family=example["target_family"],
            search_space=bad_search_space,
        )


def test_staged_backend_comparison_example_is_fixed_methodology() -> None:
    example = build_staged_backend_comparison_example()
    comparison = example["backend_comparison"]

    assert comparison.winner_backend_id == "staged_optimizer.v1"
    assert len(example["reflective_results"]) == 3
    assert len(example["staged_results"]) == 3
    assert set(comparison.manifest_ids) == {
        "manifest.support_execution.v1",
        "manifest.tool_guidance.v1_5",
        "manifest.coding_overlay.v1_5",
    }
    assert comparison.reproducibility_notes["fixed_methodology"] is True
    assert comparison.metadata["family_ids"] == [
        "family.support_execution.v2",
        "family.tool_guidance.v2",
        "family.coding_overlay.v2",
    ]


def test_staged_optimizer_records_private_search_policy_trace_for_composed_lane() -> None:
    example = build_support_execution_coding_overlay_composition_example()

    result = run_staged_optimizer(example["staged_request"])

    trace = result.metadata["search_policy_trace"]

    assert result.metadata["final_selected_candidate_id"] == trace[-1]["selected_candidate_id"]
    assert len(trace) == 2
    assert trace[0]["policy_kind"] == "bounded_composed_scalarization_v1"
    assert trace[0]["model_tier"] == "gpt-5.4-nano"
    assert "hidden_hold_deferred" in trace[0]["blocked_components"]
    assert "review_required" in trace[1]["blocked_components"]


def test_staged_optimizer_private_model_escalation_is_auditable() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    request = StagedOptimizerRequest.from_dict(
        {
            **example["staged_request"].to_dict(),
            "metadata": {"search_policy_signal": "ambiguous_hidden_hold"},
        }
    )

    result = run_staged_optimizer(request)
    trace = result.metadata["search_policy_trace"]

    assert trace[-1]["escalation_considered"] is True
    assert trace[-1]["escalation_triggered"] is True
    assert trace[-1]["escalation_reason"] == "ambiguous_hidden_hold"
    assert trace[-1]["model_tier"] == "gpt-5.4-mini"


def test_staged_optimizer_records_private_search_policy_trace_for_v4_codex_package_lane() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()

    result = run_staged_optimizer(example["staged_request"])
    trace = result.metadata["search_policy_trace"]

    assert trace
    assert trace[-1]["metadata"]["transfer_slice_status"]["package.codex_dossier.current"]["status"] == "pass"
    assert "model_tier_audit_active" not in trace[-1]["metadata"]["slice_penalties"]
    assert trace[-1]["metadata"]["unfair_mixed_tier_backend_comparison_forbidden"] is False


def test_staged_optimizer_records_private_search_policy_trace_for_v4_opencode_package_lane() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()

    result = run_staged_optimizer(example["staged_request"])
    trace = result.metadata["search_policy_trace"]

    assert trace
    assert trace[-1]["escalation_triggered"] is True
    assert trace[-1]["escalation_reason"] == "ambiguous_hidden_hold"
    assert trace[-1]["metadata"]["transfer_slice_status"]["model_tier.nano_first_openai"]["status"] == "audited_pass"
    assert "model_tier_audit_active" in trace[-1]["metadata"]["slice_penalties"]
    assert "hidden_hold_deferred" in trace[-1]["blocked_components"]
