from __future__ import annotations

from agentic_coder_prototype.optimize import (
    CandidateBundle,
    MaterializedCandidate,
    OptimizationTarget,
    build_codex_dossier_example,
    build_codex_dossier_example_payload,
)


def test_codex_dossier_example_is_self_consistent() -> None:
    example = build_codex_dossier_example()
    target = example["target"]
    candidate = example["candidate"]
    materialized = example["materialized"]

    assert isinstance(target, OptimizationTarget)
    assert isinstance(candidate, CandidateBundle)
    assert isinstance(materialized, MaterializedCandidate)
    candidate.validate_against_target(target)
    assert materialized.support_envelope == target.support_envelope
    assert materialized.applied_loci == candidate.applied_loci


def test_codex_dossier_example_payload_round_trips() -> None:
    payload = build_codex_dossier_example_payload()

    target = OptimizationTarget.from_dict(payload["target"])
    candidate = CandidateBundle.from_dict(payload["candidate"])
    materialized = MaterializedCandidate.from_dict(payload["materialized"])

    candidate.validate_against_target(target)
    assert materialized.source_target_id == target.target_id
    assert materialized.applied_loci == candidate.applied_loci
