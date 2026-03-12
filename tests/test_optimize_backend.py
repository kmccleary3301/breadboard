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
    SupportEnvelope,
    WrongnessGuidedReflectionPolicy,
    build_codex_dossier_backend_example,
    build_codex_dossier_backend_example_payload,
    build_codex_dossier_evaluation_example,
    validate_bounded_candidate,
)


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
