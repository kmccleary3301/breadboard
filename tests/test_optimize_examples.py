from __future__ import annotations

from agentic_coder_prototype.optimize import (
    BenchmarkRunManifest,
    CandidateBundle,
    CandidateComparisonResult,
    ObjectiveBreakdownResult,
    PromotionEvidenceSummary,
    TransferCohortManifest,
    MaterializedCandidate,
    OptimizationTarget,
    build_codex_dossier_example,
    build_codex_dossier_example_payload,
    build_next_frontier_optimize_cohort_packet,
    build_next_frontier_optimize_cohort_packet_payload,
    build_next_frontier_optimize_transfer_packet,
    build_next_frontier_optimize_transfer_packet_payload,
    build_next_frontier_optimize_live_experiment_cell,
    build_next_frontier_optimize_live_experiment_cell_payload,
    build_next_frontier_optimize_pressure_synthesis,
    build_next_frontier_optimize_pressure_synthesis_payload,
    build_next_frontier_optimize_second_cohort_packet,
    build_next_frontier_optimize_second_cohort_packet_payload,
    build_next_frontier_optimize_second_transfer_packet,
    build_next_frontier_optimize_second_transfer_packet_payload,
    build_next_frontier_optimize_second_live_experiment_cell,
    build_next_frontier_optimize_second_live_experiment_cell_payload,
    build_next_frontier_optimize_tranche_synthesis_v2,
    build_next_frontier_optimize_tranche_synthesis_v2_payload,
    build_next_frontier_dag_to_optimize_composition_packet,
    build_next_frontier_dag_to_optimize_composition_packet_payload,
    build_next_frontier_optimize_final_closeout_packet,
    build_next_frontier_optimize_final_closeout_packet_payload,
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


def test_next_frontier_optimize_cohort_packet_is_bounded_and_executable() -> None:
    example = build_next_frontier_optimize_cohort_packet()
    payload = build_next_frontier_optimize_cohort_packet_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert manifest.benchmark_kind == "dag_packet_comparison"
    assert comparison.outcome == "non_inferior"
    assert comparison.better_candidate_id == "packet.search.replication_v1.tot_game24"
    assert breakdown.aggregate_objectives["auditability"] >= 0.8
    assert payload["study_note"]["pain_classification"] == "reporting_gap_only"


def test_next_frontier_optimize_transfer_packet_tracks_transfer_supported_claims() -> None:
    example = build_next_frontier_optimize_transfer_packet()
    payload = build_next_frontier_optimize_transfer_packet_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    cohort = TransferCohortManifest.from_dict(payload["transfer_cohort"])
    summary = PromotionEvidenceSummary.from_dict(payload["promotion_summary"])

    assert manifest.benchmark_kind == "dag_packet_transfer_follow_on"
    assert cohort.cohort_kind == "bounded_packet_family_transfer"
    assert summary.claim_tier == "transfer_supported"
    assert summary.transfer_cohort_ids == [cohort.cohort_id]
    assert payload["study_note"]["pain_classification"] == "helper_level_only"


def test_next_frontier_optimize_live_cell_and_synthesis_close_first_study_loop() -> None:
    example = build_next_frontier_optimize_live_experiment_cell()
    payload = build_next_frontier_optimize_live_experiment_cell_payload()
    synthesis = build_next_frontier_optimize_pressure_synthesis()
    synthesis_payload = build_next_frontier_optimize_pressure_synthesis_payload()

    assert payload["live_cell"]["status"] == "complete"
    assert payload["live_cell"]["winner"] == "packet.search.replication_v1.codetree_patch"
    assert payload["live_cell"]["budget_envelope"]["max_llm_calls"] == 24
    assert synthesis["recommended_outcome"] == "keep_optimize_frozen"
    assert synthesis_payload["next_frontier_ready"] == "frontier_c_rl_adapter_use"


def test_next_frontier_optimize_second_cohort_packet_is_distinct_and_bounded() -> None:
    payload = build_next_frontier_optimize_second_cohort_packet_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    comparison = CandidateComparisonResult.from_dict(payload["comparison_result"])
    breakdown = ObjectiveBreakdownResult.from_dict(payload["objective_breakdown_result"])

    assert manifest.benchmark_kind == "dag_packet_downstream_consumer_readiness"
    assert comparison.better_candidate_id == "packet.search.replication_v1.codetree_patch"
    assert breakdown.aggregate_objectives["adapter_readiness"] >= 0.8
    assert payload["study_note"]["pain_classification"] == "evaluator_and_reporting_only"


def test_next_frontier_optimize_second_transfer_packet_uses_selection_prior() -> None:
    payload = build_next_frontier_optimize_second_transfer_packet_payload()
    manifest = BenchmarkRunManifest.from_dict(payload["manifest"])
    cohort = TransferCohortManifest.from_dict(payload["transfer_cohort"])
    summary = PromotionEvidenceSummary.from_dict(payload["promotion_summary"])

    assert manifest.benchmark_kind == "dag_packet_consumer_follow_on"
    assert cohort.cohort_kind == "bounded_packet_family_transfer"
    assert summary.claim_tier == "transfer_supported"
    assert payload["study_note"]["pain_classification"] == "helper_level_only"


def test_next_frontier_optimize_second_live_cell_and_v2_synthesis_close_loop() -> None:
    payload = build_next_frontier_optimize_second_live_experiment_cell_payload()
    synthesis_payload = build_next_frontier_optimize_tranche_synthesis_v2_payload()

    assert payload["live_cell"]["status"] == "complete"
    assert payload["live_cell"]["winner"] == "packet.search.replication_v1.codetree_patch"
    assert payload["live_cell"]["budget_envelope"]["max_llm_calls"] == 18
    assert synthesis_payload["recommended_outcome"] == "keep_optimize_frozen"
    assert synthesis_payload["next_frontier_ready"] == "frontier_d_cross_system_composition"


def test_next_frontier_dag_to_optimize_composition_packet_is_well_formed() -> None:
    packet = build_next_frontier_dag_to_optimize_composition_packet()
    payload = build_next_frontier_dag_to_optimize_composition_packet_payload()

    assert payload["source_packet_id"] == "packet.search.replication_v1.codetree_patch"
    assert payload["handoff_contract"]["claim_limit_honesty"] is True
    assert packet["composition_report"]["composed_cleanly"] is True
    assert packet["composition_report"]["repeated_shape_gap_detected"] is False


def test_next_frontier_optimize_final_closeout_packet_keeps_optimize_frozen() -> None:
    packet = build_next_frontier_optimize_final_closeout_packet()
    payload = build_next_frontier_optimize_final_closeout_packet_payload()

    assert payload["final_decision"] == "keep_optimize_frozen"
    assert payload["repeated_shape_gap_detected"] is False
    assert payload["reviewed_loops"] == 2
    assert "bounded cohort comparison" in packet["proven_capabilities"]
