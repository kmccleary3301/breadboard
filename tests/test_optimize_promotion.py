from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from agentic_coder_prototype.optimize import (
    BenchmarkRunManifest,
    CandidateComparisonResult,
    EvaluationRecord,
    GateResult,
    ObjectiveBreakdownResult,
    PromotionDecision,
    PromotionEvidenceSummary,
    PromotionRecord,
    build_codex_opencode_replay_config_transfer_cohort_follow_on_example,
    build_codex_opencode_transfer_cohort_example,
    build_codex_dossier_promotion_examples,
    build_codex_dossier_promotion_examples_payload,
    build_coding_overlay_benchmark_example,
    build_opencode_prompt_config_tool_guidance_package_example,
    build_promotion_evidence_summary,
    build_support_execution_benchmark_example,
    build_support_execution_coding_overlay_composition_example,
    build_support_execution_tool_guidance_coding_overlay_package_example,
    build_tool_guidance_benchmark_example,
    create_promotion_record,
    evaluate_family_promotion_gate,
    promote_candidate,
)


def test_promotion_examples_round_trip() -> None:
    payload = build_codex_dossier_promotion_examples_payload()
    promotable_record = PromotionRecord.from_dict(payload["promotable"]["record"])
    promotable_decision = PromotionDecision.from_dict(payload["promotable"]["decision"])
    frontier_record = PromotionRecord.from_dict(payload["frontier_blocked"]["record"])
    support_fail_record = PromotionRecord.from_dict(payload["support_fail"]["record"])

    assert promotable_record.state == "promotable"
    assert promotable_decision.next_state == "promotable"
    assert frontier_record.state == "frontier"
    assert support_fail_record.state == "rejected"


def test_promotion_examples_cover_frontier_rejected_and_promotable() -> None:
    example = build_codex_dossier_promotion_examples()

    promotable = example["promotable"]
    frontier_blocked = example["frontier_blocked"]
    support_fail = example["support_fail"]

    assert promotable["record"].state == "promotable"
    assert frontier_blocked["record"].state == "frontier"
    assert "replay" in frontier_blocked["record"].blocked_by_gate_kinds
    assert "conformance" in frontier_blocked["record"].blocked_by_gate_kinds
    assert support_fail["record"].state == "rejected"
    assert "support_envelope" in support_fail["decision"].blocked_by_gate_kinds


def test_promotion_record_valid_transitions_preserve_lineage() -> None:
    record = create_promotion_record(
        record_id="promotion.test.001",
        target_id="target.codex_dossier.tool_render",
        candidate_id="cand.codex_dossier.001",
        created_at="2026-03-12T14:00:00.000Z",
    )
    evaluated = record.mark_evaluated(
        transitioned_at="2026-03-12T14:00:01.000Z",
        reason="evaluation attached",
        evidence=record.evidence,
    )
    frontier = evaluated.move_to_frontier(
        transitioned_at="2026-03-12T14:00:02.000Z",
        reason="retained on frontier",
    )
    gated = frontier.move_to_gated(
        transitioned_at="2026-03-12T14:00:03.000Z",
        reason="gates executed",
        gate_results=[
            GateResult(
                gate_id="gate.test.001",
                gate_kind="replay",
                status="pass",
                target_id=frontier.target_id,
                candidate_id=frontier.candidate_id,
                reason="ok",
            )
        ],
    )
    promotable = gated.move_to_promotable(
        transitioned_at="2026-03-12T14:00:04.000Z",
        reason="all gates passed",
        gate_results=gated.gate_results,
    )

    assert promotable.target_id == record.target_id
    assert promotable.candidate_id == record.candidate_id
    assert promotable.state_history == ["draft", "evaluated", "frontier", "gated", "promotable"]


def test_promotion_record_rejects_illegal_transition() -> None:
    record = create_promotion_record(
        record_id="promotion.test.002",
        target_id="target.codex_dossier.tool_render",
        candidate_id="cand.codex_dossier.002",
        created_at="2026-03-12T14:10:00.000Z",
    )

    with pytest.raises(ValueError, match="illegal promotion transition"):
        record.promote(
            transitioned_at="2026-03-12T14:10:01.000Z",
            reason="cannot skip straight to promoted",
        )


def test_promotion_terminal_state_behavior_is_explicit() -> None:
    example = build_codex_dossier_promotion_examples()
    rejected = example["support_fail"]["record"]

    archived = rejected.archive(
        transitioned_at="2026-03-12T14:20:00.000Z",
        reason="preserve rejected candidate lineage without leaving it active",
    )

    assert archived.state == "archived"
    assert archived.state_history[-2:] == ["rejected", "archived"]


def _clone_manifest(manifest: BenchmarkRunManifest, **updates: object) -> BenchmarkRunManifest:
    payload = manifest.to_dict()
    payload.update(updates)
    return BenchmarkRunManifest.from_dict(payload)


def _clone_comparison(
    comparison: CandidateComparisonResult,
    **updates: object,
) -> CandidateComparisonResult:
    payload = comparison.to_dict()
    payload.update(updates)
    return CandidateComparisonResult.from_dict(payload)


def _clone_evaluation(evaluation: EvaluationRecord, **updates: object) -> EvaluationRecord:
    payload = evaluation.to_dict()
    payload.update(updates)
    if "evaluation_id" in updates and "normalized_diagnostics" not in updates:
        evaluation_id = str(updates["evaluation_id"])
        payload["normalized_diagnostics"] = [
            {**bundle, "evaluation_id": evaluation_id}
            for bundle in payload.get("normalized_diagnostics", [])
        ]
    return EvaluationRecord.from_dict(payload)


def _clone_objective_breakdown(result: ObjectiveBreakdownResult, **updates: object) -> ObjectiveBreakdownResult:
    payload = result.to_dict()
    payload.update(updates)
    return ObjectiveBreakdownResult.from_dict(payload)


def _support_execution_evaluation(example: Mapping[str, Any]) -> EvaluationRecord:
    promotable = build_codex_dossier_promotion_examples()["promotable"]
    return _clone_evaluation(
        promotable["evaluation"],
        evaluation_id=f"eval.{example['child_candidate'].candidate_id}",
        target_id=example["target"].target_id,
        candidate_id=example["child_candidate"].candidate_id,
        dataset_id=example["dataset"].dataset_id,
        dataset_version=example["dataset"].dataset_version,
        sample_id=example["dataset"].samples[1].sample_id,
        evaluation_input_compatibility={
            **example["child_materialized_candidate"].evaluation_input_compatibility,
            "conformance_bundle": "codex-e4",
        },
        support_envelope_snapshot=example["target"].support_envelope.to_dict(),
    )


def _composition_evaluation(example: Mapping[str, Any]) -> EvaluationRecord:
    promotable = build_codex_dossier_promotion_examples()["promotable"]
    return _clone_evaluation(
        promotable["evaluation"],
        evaluation_id=f"eval.{example['composed_candidate'].candidate_id}",
        target_id=example["target"].target_id,
        candidate_id=example["composed_candidate"].candidate_id,
        dataset_id=example["dataset"].dataset_id,
        dataset_version=example["dataset"].dataset_version,
        sample_id=example["dataset"].samples[1].sample_id,
        evaluation_input_compatibility={
            **example["composed_materialized_candidate"].evaluation_input_compatibility,
            "conformance_bundle": "codex-e4",
        },
        support_envelope_snapshot=example["target"].support_envelope.to_dict(),
    )


def _evidence_summary_from_record(record: PromotionRecord) -> PromotionEvidenceSummary:
    metadata: Mapping[str, Any] = record.evidence.metadata
    payload = metadata.get("evidence_summary")
    assert isinstance(payload, Mapping)
    return PromotionEvidenceSummary.from_dict(payload)


def test_build_promotion_evidence_summary_round_trips() -> None:
    example = build_support_execution_benchmark_example()
    summary = build_promotion_evidence_summary(
        summary_id="evidence_summary.support_execution.001",
        candidate_id=example["child_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
        review_required=True,
        metadata={"lane": "support_execution"},
    )

    round_tripped = PromotionEvidenceSummary.from_dict(summary.to_dict())

    assert round_tripped.summary_id == summary.summary_id
    assert round_tripped.manifest_ids == [example["manifest"].manifest_id]
    assert round_tripped.held_out_sample_ids == ["sample.support_execution.hold.001"]
    assert round_tripped.regression_sample_ids == ["sample.support_execution.regression.001"]
    assert round_tripped.compared_regression_sample_ids == ["sample.support_execution.regression.001"]
    assert round_tripped.outcome_counts == {"win": 1}
    assert round_tripped.evaluation_suite_ids == [example["evaluation_suite"].suite_id]
    assert round_tripped.objective_suite_ids == [example["objective_suite"].suite_id]
    assert round_tripped.target_family_ids == [example["target_family"].family_id]
    assert round_tripped.search_space_ids == [example["search_space"].search_space_id]
    assert round_tripped.objective_breakdown_result_ids == [example["objective_breakdown_result"].result_id]
    assert round_tripped.review_class == "support_honesty"
    assert round_tripped.objective_breakdown_status == "complete"
    assert round_tripped.review_required is True


def test_build_promotion_evidence_summary_captures_composed_family_metadata() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    summary = build_promotion_evidence_summary(
        summary_id="evidence_summary.support_execution_coding_overlay.001",
        candidate_id=example["composed_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
        review_required=True,
        metadata={"lane": "support_execution_coding_overlay_composed"},
    )

    assert summary.composition_ids == [example["family_composition"].composition_id]
    assert summary.member_family_ids == [
        "family.support_execution.v2",
        "family.coding_overlay.v2",
    ]
    assert summary.member_family_coverage["family.support_execution.v2"]["present"] is True
    assert summary.member_family_coverage["family.coding_overlay.v2"]["present"] is True
    assert set(summary.coupling_risk_summary["coupled_loci_groups"]) == {
        "replay_and_planning",
        "support_and_editing",
    }
    assert summary.transfer_slice_ids == [
        "model_tier.nano_first_openai",
        "package.codex_dossier.prompt_config",
    ]
    assert summary.review_class == "support_sensitive_coding_overlay"


def test_promote_candidate_includes_evidence_summary_for_benchmark_backed_lane() -> None:
    example = build_support_execution_benchmark_example()
    evaluation = _support_execution_evaluation(example)
    record, decision = promote_candidate(
        record_id="promotion.support_execution.001",
        target=example["target"],
        materialized_candidate=example["child_materialized_candidate"],
        evaluation=evaluation,
        created_at="2026-03-14T10:00:00.000Z",
        gated_at="2026-03-14T10:00:01.000Z",
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    summary = _evidence_summary_from_record(record)

    assert record.state == "frontier"
    assert decision.next_state == "frontier"
    assert summary.manifest_ids == [example["manifest"].manifest_id]
    assert summary.outcome_counts == {"win": 1}
    assert summary.review_required is True
    assert "family_promotion" in decision.blocked_by_gate_kinds


def test_promote_candidate_keeps_inconclusive_comparison_on_frontier() -> None:
    example = build_support_execution_benchmark_example()
    evaluation = _support_execution_evaluation(example)
    comparison = _clone_comparison(
        example["comparison_result"],
        comparison_id="comparison.support_execution.inconclusive",
        outcome="inconclusive",
        better_candidate_id=None,
    )

    record, decision = promote_candidate(
        record_id="promotion.support_execution.inconclusive",
        target=example["target"],
        materialized_candidate=example["child_materialized_candidate"],
        evaluation=evaluation,
        created_at="2026-03-14T10:05:00.000Z",
        gated_at="2026-03-14T10:05:01.000Z",
        benchmark_manifest=example["manifest"],
        comparison_results=[comparison],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    assert record.state == "frontier"
    assert decision.next_state == "frontier"
    assert "comparison" in decision.blocked_by_gate_kinds
    summary = _evidence_summary_from_record(record)
    assert summary.outcome_counts == {"inconclusive": 1}


def test_promote_candidate_rejects_regression_loss() -> None:
    example = build_support_execution_benchmark_example()
    evaluation = _support_execution_evaluation(example)
    comparison = _clone_comparison(
        example["comparison_result"],
        comparison_id="comparison.support_execution.loss",
        outcome="loss",
        better_candidate_id=example["parent_candidate"].candidate_id,
    )

    record, decision = promote_candidate(
        record_id="promotion.support_execution.loss",
        target=example["target"],
        materialized_candidate=example["child_materialized_candidate"],
        evaluation=evaluation,
        created_at="2026-03-14T10:10:00.000Z",
        gated_at="2026-03-14T10:10:01.000Z",
        benchmark_manifest=example["manifest"],
        comparison_results=[comparison],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    assert record.state == "rejected"
    assert decision.next_state == "rejected"
    assert decision.blocked_by_gate_kinds == ["comparison", "family_promotion"]


def test_evaluate_family_promotion_gate_requires_member_family_coverage_for_composition() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    broken_breakdown = _clone_objective_breakdown(
        example["objective_breakdown_result"],
        member_family_breakdowns={"family.support_execution.v2": {"support_honesty": 0.9875}},
    )

    gate = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["composed_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[broken_breakdown],
    )

    assert gate.status == "insufficient_evidence"
    assert "member-family coverage" in gate.reason
    assert gate.metadata["missing_member_family_ids"] == ["family.coding_overlay.v2"]


def test_promote_candidate_keeps_review_sensitive_composed_lane_on_frontier() -> None:
    example = build_support_execution_coding_overlay_composition_example()
    evaluation = _composition_evaluation(example)

    record, decision = promote_candidate(
        record_id="promotion.support_execution_coding_overlay.001",
        target=example["target"],
        materialized_candidate=example["composed_materialized_candidate"],
        evaluation=evaluation,
        created_at="2026-03-19T11:00:00.000Z",
        gated_at="2026-03-19T11:00:01.000Z",
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    summary = _evidence_summary_from_record(record)

    assert record.state == "frontier"
    assert decision.next_state == "frontier"
    assert "family_promotion" in decision.blocked_by_gate_kinds
    assert summary.composition_ids == [example["family_composition"].composition_id]
    assert summary.member_family_ids == list(example["family_composition"].member_family_ids)
    assert summary.transfer_slice_ids == [
        "model_tier.nano_first_openai",
        "package.codex_dossier.prompt_config",
    ]


def test_build_promotion_evidence_summary_captures_typed_transfer_slices_and_model_tier_audit() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()
    summary = build_promotion_evidence_summary(
        summary_id="evidence_summary.support_execution_tool_guidance_coding_overlay.v4",
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
        review_required=True,
        metadata={"lane": "support_execution_tool_guidance_coding_overlay_package"},
    )

    assert summary.transfer_slice_ids == [
        "environment.workspace_write_replay_safe",
        "model_tier.nano_first_openai",
        "package.codex_dossier.current",
    ]
    assert [item.slice_kind for item in summary.transfer_slices] == ["package", "model_tier", "environment"]
    assert summary.model_tier_audit["default_model"] == "gpt-5.4-nano"
    assert summary.model_tier_audit["triggered"] is False
    assert summary.transfer_slice_status["package.codex_dossier.current"]["status"] == "pass"
    assert summary.blocked_transfer_slice_ids == []
    assert summary.inconclusive_transfer_slice_ids == []
    assert summary.attribution_summary["required"] is True
    assert summary.attribution_summary["present"] is True


def test_build_promotion_evidence_summary_tracks_opencode_package_tier_audit() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    summary = build_promotion_evidence_summary(
        summary_id="evidence_summary.opencode_prompt_config_tool_guidance.001",
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
        review_required=True,
        metadata={"lane": "opencode_prompt_config_tool_guidance_package"},
    )

    assert summary.transfer_slice_ids == [
        "model_tier.nano_first_openai",
        "package.opencode_1_2_17.current",
        "provider_model.openai_gpt_5_4_pair",
        "tool_pack.opencode_native_responses",
    ]
    assert summary.model_tier_audit["default_model"] == "gpt-5.4-nano"
    assert summary.model_tier_audit["escalation_model"] == "gpt-5.4-mini"
    assert summary.model_tier_audit["triggered"] is True
    assert summary.model_tier_audit["trigger_reason"] == "ambiguous_hidden_hold_prompt_tool_pack_margin"
    assert summary.family_risk_summary["composed_family"] is True
    assert summary.transfer_slice_status["model_tier.nano_first_openai"]["status"] == "audited_pass"
    assert summary.attribution_summary["required"] is True
    assert summary.attribution_summary["present"] is True


def test_promote_candidate_requires_more_trials_for_stochastic_manifest() -> None:
    example = build_support_execution_benchmark_example()
    evaluation = _support_execution_evaluation(example)
    stochastic_manifest = _clone_manifest(
        example["manifest"],
        stochasticity_class="seeded_stochastic",
        rerun_policy={"max_trials": 3, "flake_on_nonrepeatable": True},
    )

    record, decision = promote_candidate(
        record_id="promotion.support_execution.stochastic",
        target=example["target"],
        materialized_candidate=example["child_materialized_candidate"],
        evaluation=evaluation,
        created_at="2026-03-14T10:15:00.000Z",
        gated_at="2026-03-14T10:15:01.000Z",
        benchmark_manifest=stochastic_manifest,
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    assert record.state == "frontier"
    assert decision.next_state == "frontier"
    summary = _evidence_summary_from_record(record)
    assert summary.stochasticity_class == "seeded_stochastic"
    assert summary.minimum_required_trials == 3
    assert summary.observed_trial_count == 1


def test_family_promotion_gate_passes_for_tool_guidance_family() -> None:
    example = build_tool_guidance_benchmark_example()

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["child_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    assert result.status == "pass"
    assert result.gate_kind == "family_promotion"


def test_family_promotion_gate_treats_non_inferior_as_promotable_for_non_review_heavy_family() -> None:
    example = build_coding_overlay_benchmark_example()
    comparison = _clone_comparison(
        example["comparison_result"],
        comparison_id="comparison.coding_overlay.non_inferior",
        outcome="non_inferior",
        better_candidate_id=None,
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["child_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[comparison],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[example["objective_breakdown_result"]],
    )

    assert result.status == "pass"


def test_family_promotion_gate_blocks_partial_objective_breakdown() -> None:
    example = build_tool_guidance_benchmark_example()
    blocked_breakdown = ObjectiveBreakdownResult.from_dict(
        {
            **example["objective_breakdown_result"].to_dict(),
            "blocked_components": {"guardrail_preservation": {"reason": "missing replay evidence"}},
        }
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["child_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        target_family=example["target_family"],
        search_space=example["search_space"],
        objective_breakdown_results=[blocked_breakdown],
    )

    assert result.status == "insufficient_evidence"
    assert "partially blocked" in result.reason


def test_family_promotion_gate_blocks_blocked_transfer_slice_on_package_lane() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()
    blocked_breakdown = _clone_objective_breakdown(
        example["objective_breakdown_result"],
        slice_status={
            **example["objective_breakdown_result"].slice_status,
            "package.codex_dossier.current": {"status": "blocked", "promotion_role": "required"},
        },
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[blocked_breakdown],
    )

    assert result.status == "insufficient_evidence"
    assert "transfer slices" in result.reason
    assert result.metadata["blocked_transfer_slice_ids"] == ["package.codex_dossier.current"]


def test_family_promotion_gate_blocks_inconclusive_transfer_slice_on_package_lane() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    inconclusive_breakdown = _clone_objective_breakdown(
        example["objective_breakdown_result"],
        slice_status={
            **example["objective_breakdown_result"].slice_status,
            "model_tier.nano_first_openai": {"status": "inconclusive", "promotion_role": "required"},
        },
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[inconclusive_breakdown],
    )

    assert result.status == "insufficient_evidence"
    assert "inconclusive" in result.reason
    assert result.metadata["inconclusive_transfer_slice_ids"] == ["model_tier.nano_first_openai"]


def test_family_promotion_gate_blocks_optimistic_scope_expansion() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    expanded_breakdown = _clone_objective_breakdown(
        example["objective_breakdown_result"],
        metadata={
            **example["objective_breakdown_result"].metadata,
            "applicability_scope_status": "expanded",
        },
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[expanded_breakdown],
    )

    assert result.status == "fail"
    assert "expanded beyond the declared package" in result.reason


def test_family_promotion_gate_requires_member_family_attribution_when_triggered() -> None:
    example = build_opencode_prompt_config_tool_guidance_package_example()
    unattributed_breakdown = _clone_objective_breakdown(
        example["objective_breakdown_result"],
        metadata={
            **example["objective_breakdown_result"].metadata,
            "member_family_attribution": {},
        },
    )

    result = evaluate_family_promotion_gate(
        target=example["target"],
        candidate_id=example["package_candidate"].candidate_id,
        benchmark_manifest=example["manifest"],
        comparison_results=[example["comparison_result"]],
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["family_composition"],
        search_space=example["search_space"],
        objective_breakdown_results=[unattributed_breakdown],
    )

    assert result.status == "insufficient_evidence"
    assert "member-family attribution" in result.reason
    assert result.metadata["attribution_required"] is True
    assert result.metadata["attribution_present"] is False


def test_transfer_cohort_claim_tier_and_status_are_explicit() -> None:
    example = build_codex_opencode_transfer_cohort_example()
    codex_summary = example["codex_cell"]["promotion_summary"]
    opencode_summary = example["opencode_cell"]["promotion_summary"]
    cohort_id = example["transfer_cohort"].cohort_id

    assert codex_summary.claim_tier == "transfer_supported"
    assert opencode_summary.claim_tier == "transfer_supported"
    assert codex_summary.transfer_cohort_ids == [cohort_id]
    assert opencode_summary.transfer_cohort_ids == [cohort_id]
    assert codex_summary.transfer_cohort_status[cohort_id]["status"] == "transfer_supported"
    assert opencode_summary.transfer_cohort_status[cohort_id]["status"] == "transfer_supported"


def test_transfer_cohort_follow_on_reviewability_and_audit_are_explicit() -> None:
    example = build_codex_opencode_replay_config_transfer_cohort_follow_on_example()
    codex_summary = example["codex_cell"]["promotion_summary"]
    opencode_summary = example["opencode_cell"]["promotion_summary"]
    cohort_id = example["transfer_cohort"].cohort_id

    assert codex_summary.review_required is True
    assert opencode_summary.review_required is True
    assert codex_summary.claim_tier == "transfer_supported"
    assert opencode_summary.transfer_cohort_status[cohort_id]["mini_audit_triggered"] is True
    assert opencode_summary.metadata["follow_on"] is True


def test_package_examples_remain_explicitly_package_local() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()

    assert example["promotion_summary"].claim_tier == "package_local"


def test_build_promotion_evidence_summary_rejects_transfer_claim_without_cohort() -> None:
    example = build_support_execution_tool_guidance_coding_overlay_package_example()

    with pytest.raises(ValueError, match="require explicit transfer cohorts"):
        build_promotion_evidence_summary(
            summary_id="summary.invalid.transfer_claim.001",
            candidate_id=example["package_candidate"].candidate_id,
            benchmark_manifest=example["manifest"],
            comparison_results=[example["comparison_result"]],
            evaluation_suite=example["evaluation_suite"],
            objective_suite=example["objective_suite"],
            family_composition=example["family_composition"],
            search_space=example["search_space"],
            objective_breakdown_results=[example["objective_breakdown_result"]],
            claim_tier="transfer_supported",
            review_required=True,
        )


def test_build_promotion_evidence_summary_requires_supported_status_for_cohort_supported_claim() -> None:
    example = build_codex_opencode_transfer_cohort_example()

    with pytest.raises(ValueError, match="cohort-supported status"):
        build_promotion_evidence_summary(
            summary_id="summary.invalid.cohort_supported.001",
            candidate_id=example["codex_cell"]["cohort_candidate"].candidate_id,
            benchmark_manifest=example["codex_cell"]["manifest"],
            comparison_results=example["codex_cell"]["benchmark_result"].comparison_results,
            evaluation_suite=example["evaluation_suite"],
            objective_suite=example["objective_suite"],
            family_composition=example["codex_cell"]["package_example"]["family_composition"],
            search_space=example["codex_cell"]["package_example"]["search_space"],
            transfer_cohorts=[example["transfer_cohort"]],
            objective_breakdown_results=[example["codex_cell"]["objective_breakdown_result"]],
            claim_tier="cohort_supported",
            transfer_cohort_status=example["codex_cell"]["promotion_summary"].transfer_cohort_status,
            review_required=True,
        )


def test_build_promotion_evidence_summary_accepts_explicit_cohort_supported_claim_with_review_and_attribution() -> None:
    example = build_codex_opencode_transfer_cohort_example()
    cohort_id = example["transfer_cohort"].cohort_id
    supported_status = {
        cohort_id: {
            **example["codex_cell"]["promotion_summary"].transfer_cohort_status[cohort_id],
            "status": "cohort_supported",
        }
    }

    summary = build_promotion_evidence_summary(
        summary_id="summary.valid.cohort_supported.001",
        candidate_id=example["codex_cell"]["cohort_candidate"].candidate_id,
        benchmark_manifest=example["codex_cell"]["manifest"],
        comparison_results=example["codex_cell"]["benchmark_result"].comparison_results,
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["codex_cell"]["package_example"]["family_composition"],
        search_space=example["codex_cell"]["package_example"]["search_space"],
        transfer_cohorts=[example["transfer_cohort"]],
        objective_breakdown_results=[example["codex_cell"]["objective_breakdown_result"]],
        claim_tier="cohort_supported",
        transfer_cohort_status=supported_status,
        review_required=True,
    )

    assert summary.claim_tier == "cohort_supported"
    assert summary.attribution_summary["required"] is True
    assert summary.family_risk_summary["claim_tier"] == "cohort_supported"


def test_family_promotion_gate_blocks_cohort_supported_claim_without_supported_evidence() -> None:
    example = build_codex_opencode_transfer_cohort_example()
    manifest = BenchmarkRunManifest.from_dict(
        {
            **example["codex_cell"]["manifest"].to_dict(),
            "promotion_relevance": {
                **dict(example["codex_cell"]["manifest"].promotion_relevance),
                "claim_tier": "cohort_supported",
                "transfer_cohort_status": example["codex_cell"]["promotion_summary"].transfer_cohort_status,
            },
        }
    )

    result = evaluate_family_promotion_gate(
        target=example["codex_cell"]["package_example"]["target"],
        candidate_id=example["codex_cell"]["cohort_candidate"].candidate_id,
        benchmark_manifest=manifest,
        comparison_results=example["codex_cell"]["benchmark_result"].comparison_results,
        evaluation_suite=example["evaluation_suite"],
        objective_suite=example["objective_suite"],
        family_composition=example["codex_cell"]["package_example"]["family_composition"],
        search_space=example["codex_cell"]["package_example"]["search_space"],
        objective_breakdown_results=[example["codex_cell"]["objective_breakdown_result"]],
    )

    assert result.status == "insufficient_evidence"
    assert "cohort-supported" in result.reason
    assert result.metadata["claim_tier"] == "cohort_supported"
