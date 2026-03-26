from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    ArtifactRef,
    CandidateBundle,
    CandidateChange,
    MaterializedCandidate,
    MutableLocus,
    OptimizationInvariant,
    OptimizationTarget,
    SupportEnvelope,
    materialize_candidate,
)


def _build_target() -> OptimizationTarget:
    return OptimizationTarget(
        target_id="target.codex_dossier",
        target_kind="agent_config_overlay",
        baseline_artifact_refs=[
            ArtifactRef(ref="agent_configs/codex_0-107-0_e4_3-6-2026.yaml", media_type="text/yaml")
        ],
        mutable_loci=[
            MutableLocus(
                locus_id="tool.render",
                locus_kind="tool_description",
                selector="tools.exec_command.description",
                mutation_kind="replace",
            ),
            MutableLocus(
                locus_id="prompt.section",
                locus_kind="prompt_section",
                selector="developer_prompt.optimization_guidance",
                mutation_kind="replace",
            ),
        ],
        support_envelope=SupportEnvelope(
            tools=["exec_command", "apply_patch"],
            execution_profiles=["codex-e4"],
            environments=["workspace-write"],
            providers=["openai"],
            models=["gpt-5.2"],
            assumptions={"requires_replay_gate": True},
        ),
        invariants=[
            OptimizationInvariant(
                invariant_id="keep-tool-surface-bounded",
                description="candidate must not widen the supported tool surface",
            )
        ],
        metadata={"owner": "optimization-v1"},
    )


def test_optimization_target_round_trip() -> None:
    target = _build_target()
    restored = OptimizationTarget.from_dict(target.to_dict())
    assert restored == target
    assert restored.locus_ids() == ["tool.render", "prompt.section"]


def test_candidate_bundle_validates_against_target() -> None:
    target = _build_target()
    candidate = CandidateBundle(
        candidate_id="cand.001",
        source_target_id=target.target_id,
        applied_loci=["tool.render", "prompt.section"],
        changes=[
            CandidateChange(locus_id="tool.render", value={"text": "Run shell commands clearly."}),
            CandidateChange(locus_id="prompt.section", value={"text": "Prefer bounded overlays."}),
        ],
        provenance={"backend": "reflective_pareto"},
    )

    candidate.validate_against_target(target)
    restored = CandidateBundle.from_dict(candidate.to_dict())
    assert restored == candidate


def test_candidate_bundle_rejects_unknown_locus() -> None:
    target = _build_target()
    candidate = CandidateBundle(
        candidate_id="cand.002",
        source_target_id=target.target_id,
        applied_loci=["missing.locus"],
        changes=[CandidateChange(locus_id="missing.locus", value={"text": "bad"})],
    )

    with pytest.raises(ValueError, match="unknown loci"):
        candidate.validate_against_target(target)


def test_materialize_candidate_uses_target_support_envelope_by_default() -> None:
    target = _build_target()
    candidate = CandidateBundle(
        candidate_id="cand.003",
        source_target_id=target.target_id,
        applied_loci=["tool.render"],
        changes=[CandidateChange(locus_id="tool.render", value={"text": "shorter tool wording"})],
    )

    materialized = materialize_candidate(
        target,
        candidate,
        effective_artifact={"config": {"tools": {"exec_command": {"description": "shorter tool wording"}}}},
        evaluation_input_compatibility={"replay": True, "schema": 2},
    )

    assert isinstance(materialized, MaterializedCandidate)
    assert materialized.support_envelope == target.support_envelope
    assert materialized.applied_loci == ["tool.render"]
    assert materialized.evaluation_input_compatibility["replay"] is True


def test_target_rejects_duplicate_locus_ids() -> None:
    with pytest.raises(ValueError, match="duplicate locus_id"):
        OptimizationTarget(
            target_id="dup.target",
            target_kind="agent_config_overlay",
            baseline_artifact_refs=[ArtifactRef(ref="agent_configs/example.yaml")],
            mutable_loci=[
                MutableLocus(locus_id="same", locus_kind="prompt_section", selector="a"),
                MutableLocus(locus_id="same", locus_kind="prompt_section", selector="b"),
            ],
        )
