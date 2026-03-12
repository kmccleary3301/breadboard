from __future__ import annotations

from agentic_coder_prototype.optimize import (
    EvaluationRecord,
    MaterializedCandidate,
    ReplayConformanceGateInput,
    SupportEnvelope,
    SupportEnvelopeGateInput,
    build_codex_dossier_promotion_examples,
    evaluate_conformance_gate,
    evaluate_promotion_gates,
    evaluate_replay_gate,
    evaluate_support_envelope_gate,
)


def _clone_evaluation(evaluation: EvaluationRecord, **updates: object) -> EvaluationRecord:
    payload = evaluation.to_dict()
    new_evaluation_id = updates.get("evaluation_id")
    if new_evaluation_id and "normalized_diagnostics" not in updates:
        normalized = []
        for bundle in payload.get("normalized_diagnostics", []):
            copied_bundle = dict(bundle)
            copied_bundle["evaluation_id"] = new_evaluation_id
            normalized.append(copied_bundle)
        payload["normalized_diagnostics"] = normalized
    payload.update(updates)
    return EvaluationRecord.from_dict(payload)


def test_replay_gate_requires_replay_and_detects_missing_evidence() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    frontier = example["frontier_blocked"]
    result = evaluate_replay_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=frontier["materialized_candidate"],
            evaluation=frontier["evaluation"],
        )
    )

    assert result.status == "insufficient_evidence"
    assert result.gate_kind == "replay"
    assert "required" in result.reason


def test_replay_gate_pass_and_fail_are_explicit() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    promotable = example["promotable"]

    passing = evaluate_replay_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=promotable["materialized_candidate"],
            evaluation=promotable["evaluation"],
        )
    )
    failing_eval = _clone_evaluation(
        promotable["evaluation"],
        evaluation_id="eval.codex_dossier.replay_fail.001",
        gate_results={
            "replay_gate_green": False,
            "conformance_gate_green": True,
            "support_envelope_preserved": True,
        },
    )
    failing = evaluate_replay_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=promotable["materialized_candidate"],
            evaluation=failing_eval,
        )
    )

    assert passing.status == "pass"
    assert failing.status == "fail"


def test_conformance_gate_requires_schema_and_bundle() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    frontier = example["frontier_blocked"]

    result = evaluate_conformance_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=frontier["materialized_candidate"],
            evaluation=frontier["evaluation"],
        )
    )

    assert result.status == "insufficient_evidence"
    assert result.gate_kind == "conformance"


def test_conformance_gate_pass_and_fail_are_explicit() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    promotable = example["promotable"]

    passing = evaluate_conformance_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=promotable["materialized_candidate"],
            evaluation=promotable["evaluation"],
        )
    )
    failing_eval = _clone_evaluation(
        promotable["evaluation"],
        evaluation_id="eval.codex_dossier.conformance_fail.001",
        gate_results={
            "replay_gate_green": True,
            "conformance_gate_green": False,
            "support_envelope_preserved": True,
        },
    )
    failing = evaluate_conformance_gate(
        ReplayConformanceGateInput(
            target=target,
            materialized_candidate=promotable["materialized_candidate"],
            evaluation=failing_eval,
        )
    )

    assert passing.status == "pass"
    assert failing.status == "fail"


def test_support_envelope_gate_blocks_tool_surface_widening() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    support_fail = example["support_fail"]

    result = evaluate_support_envelope_gate(
        SupportEnvelopeGateInput(
            target=target,
            materialized_candidate=support_fail["materialized_candidate"],
            evaluation=support_fail["evaluation"],
        )
    )

    assert result.status == "fail"
    assert result.metadata["expansion"]["tools"] == ["mcp_manager"]
    assert result.evidence_refs


def test_support_envelope_gate_blocks_environment_widening() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    promotable = example["promotable"]
    widened_candidate = MaterializedCandidate(
        candidate_id=promotable["materialized_candidate"].candidate_id,
        source_target_id=target.target_id,
        applied_loci=promotable["materialized_candidate"].applied_loci,
        effective_artifact=promotable["materialized_candidate"].effective_artifact,
        effective_tool_surface=promotable["materialized_candidate"].effective_tool_surface,
        support_envelope=SupportEnvelope(
            tools=target.support_envelope.tools,
            execution_profiles=target.support_envelope.execution_profiles,
            environments=target.support_envelope.environments + ["internet-enabled"],
            providers=target.support_envelope.providers,
            models=target.support_envelope.models,
            assumptions=dict(target.support_envelope.assumptions),
        ),
        evaluation_input_compatibility=promotable["materialized_candidate"].evaluation_input_compatibility,
    )

    result = evaluate_support_envelope_gate(
        SupportEnvelopeGateInput(
            target=target,
            materialized_candidate=widened_candidate,
            evaluation=promotable["evaluation"],
        )
    )

    assert result.status == "fail"
    assert result.metadata["expansion"]["environments"] == ["internet-enabled"]


def test_support_envelope_gate_blocks_provider_and_model_widening() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    promotable = example["promotable"]
    widened_candidate = MaterializedCandidate(
        candidate_id=promotable["materialized_candidate"].candidate_id,
        source_target_id=target.target_id,
        applied_loci=promotable["materialized_candidate"].applied_loci,
        effective_artifact=promotable["materialized_candidate"].effective_artifact,
        effective_tool_surface=promotable["materialized_candidate"].effective_tool_surface,
        support_envelope=SupportEnvelope(
            tools=target.support_envelope.tools,
            execution_profiles=target.support_envelope.execution_profiles,
            environments=target.support_envelope.environments,
            providers=target.support_envelope.providers + ["anthropic"],
            models=target.support_envelope.models + ["claude-sonnet"],
            assumptions=dict(target.support_envelope.assumptions),
        ),
        evaluation_input_compatibility=promotable["materialized_candidate"].evaluation_input_compatibility,
    )

    result = evaluate_support_envelope_gate(
        SupportEnvelopeGateInput(
            target=target,
            materialized_candidate=widened_candidate,
            evaluation=promotable["evaluation"],
        )
    )

    assert result.status == "fail"
    assert result.metadata["expansion"]["providers"] == ["anthropic"]
    assert result.metadata["expansion"]["models"] == ["claude-sonnet"]


def test_support_envelope_gate_passes_on_exact_preservation_and_narrowing() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    promotable = example["promotable"]
    exact = evaluate_support_envelope_gate(
        SupportEnvelopeGateInput(
            target=target,
            materialized_candidate=promotable["materialized_candidate"],
            evaluation=promotable["evaluation"],
        )
    )
    narrowed_candidate = MaterializedCandidate(
        candidate_id=promotable["materialized_candidate"].candidate_id,
        source_target_id=target.target_id,
        applied_loci=promotable["materialized_candidate"].applied_loci,
        effective_artifact=promotable["materialized_candidate"].effective_artifact,
        effective_tool_surface=promotable["materialized_candidate"].effective_tool_surface,
        support_envelope=SupportEnvelope(
            tools=["exec_command", "apply_patch"],
            execution_profiles=target.support_envelope.execution_profiles,
            environments=target.support_envelope.environments,
            providers=target.support_envelope.providers,
            models=target.support_envelope.models,
            assumptions=dict(target.support_envelope.assumptions),
        ),
        evaluation_input_compatibility=promotable["materialized_candidate"].evaluation_input_compatibility,
    )
    narrowing = evaluate_support_envelope_gate(
        SupportEnvelopeGateInput(
            target=target,
            materialized_candidate=narrowed_candidate,
            evaluation=promotable["evaluation"],
        )
    )

    assert exact.status == "pass"
    assert narrowing.status == "pass"


def test_combined_promotion_gates_keep_candidate_out_of_promotable_when_unresolved() -> None:
    example = build_codex_dossier_promotion_examples()
    target = example["backend_request"].target
    frontier = example["frontier_blocked"]

    results = evaluate_promotion_gates(
        target=target,
        materialized_candidate=frontier["materialized_candidate"],
        evaluation=frontier["evaluation"],
        prior_state=frontier["record"],
    )

    statuses = {result.gate_kind: result.status for result in results}
    assert statuses["replay"] == "insufficient_evidence"
    assert statuses["conformance"] == "insufficient_evidence"
    assert frontier["record"].state != "promotable"
