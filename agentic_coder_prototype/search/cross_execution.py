from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .atp_production import build_search_atp_stage_b_closeout_packet
from .consumerization import (
    build_search_stage_c_closeout_packet,
    build_search_stage_c_consumer_convergence_packet,
    build_search_stage_c_optimize_consumerization_packet,
    build_search_stage_c_rl_consumerization_packet,
)


@dataclass(frozen=True)
class SearchExecutionBudgetCell:
    cell_id: str
    max_llm_calls: int
    max_evaluator_calls: int
    target_consumers: tuple[str, ...]
    comparison_rule: str
    expected_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "max_llm_calls": self.max_llm_calls,
            "max_evaluator_calls": self.max_evaluator_calls,
            "target_consumers": list(self.target_consumers),
            "comparison_rule": self.comparison_rule,
            "expected_locus": self.expected_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionCellResult:
    cell_id: str
    optimize_artifact_id: str
    decision_signal: str
    regression_status: str
    preserved_contract_fields: tuple[str, ...]
    expected_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "optimize_artifact_id": self.optimize_artifact_id,
            "decision_signal": self.decision_signal,
            "regression_status": self.regression_status,
            "preserved_contract_fields": list(self.preserved_contract_fields),
            "expected_locus": self.expected_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionMatrixPacket:
    packet_id: str
    stage_b_closeout_id: str
    stage_c_closeout_id: str
    optimize_comparison_id: str
    optimize_live_cell_id: str
    rl_trainer_export_manifest_id: str
    rl_parity_live_manifest_id: str
    rl_parity_replay_manifest_id: str
    source_family_refs: tuple[str, ...]
    budget_cells: tuple[SearchExecutionBudgetCell, ...]
    compared_consumers: tuple[str, ...]
    harness_focus: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stage_b_closeout_id": self.stage_b_closeout_id,
            "stage_c_closeout_id": self.stage_c_closeout_id,
            "optimize_comparison_id": self.optimize_comparison_id,
            "optimize_live_cell_id": self.optimize_live_cell_id,
            "rl_trainer_export_manifest_id": self.rl_trainer_export_manifest_id,
            "rl_parity_live_manifest_id": self.rl_parity_live_manifest_id,
            "rl_parity_replay_manifest_id": self.rl_parity_replay_manifest_id,
            "source_family_refs": list(self.source_family_refs),
            "budget_cells": [cell.to_dict() for cell in self.budget_cells],
            "compared_consumers": list(self.compared_consumers),
            "harness_focus": list(self.harness_focus),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionHarnessComparisonPacket:
    packet_id: str
    execution_matrix_id: str
    consumer_convergence_id: str
    compared_budget_cells: tuple[str, ...]
    optimize_artifact_ids: tuple[str, ...]
    rl_artifact_ids: tuple[str, ...]
    regression_checks: tuple[str, ...]
    divergence_summary: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "execution_matrix_id": self.execution_matrix_id,
            "consumer_convergence_id": self.consumer_convergence_id,
            "compared_budget_cells": list(self.compared_budget_cells),
            "optimize_artifact_ids": list(self.optimize_artifact_ids),
            "rl_artifact_ids": list(self.rl_artifact_ids),
            "regression_checks": list(self.regression_checks),
            "divergence_summary": list(self.divergence_summary),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionOptimizeExecutionPacket:
    packet_id: str
    execution_matrix_id: str
    stage_c_optimize_consumerization_id: str
    harness_comparison_id: str
    source_family_refs: tuple[str, ...]
    executed_cell_results: tuple[SearchCrossExecutionCellResult, ...]
    shared_budget_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "execution_matrix_id": self.execution_matrix_id,
            "stage_c_optimize_consumerization_id": self.stage_c_optimize_consumerization_id,
            "harness_comparison_id": self.harness_comparison_id,
            "source_family_refs": list(self.source_family_refs),
            "executed_cell_results": [result.to_dict() for result in self.executed_cell_results],
            "shared_budget_rule": self.shared_budget_rule,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionOptimizeRegressionLedgerPacket:
    packet_id: str
    optimize_execution_id: str
    compared_cell_ids: tuple[str, ...]
    usefulness_rows: tuple[str, ...]
    regression_guards: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "optimize_execution_id": self.optimize_execution_id,
            "compared_cell_ids": list(self.compared_cell_ids),
            "usefulness_rows": list(self.usefulness_rows),
            "regression_guards": list(self.regression_guards),
            "repeated_run_rule": self.repeated_run_rule,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionRLExecutionPacket:
    packet_id: str
    execution_matrix_id: str
    stage_c_rl_consumerization_id: str
    harness_comparison_id: str
    source_family_refs: tuple[str, ...]
    executed_cell_results: tuple[SearchCrossExecutionCellResult, ...]
    shared_budget_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "execution_matrix_id": self.execution_matrix_id,
            "stage_c_rl_consumerization_id": self.stage_c_rl_consumerization_id,
            "harness_comparison_id": self.harness_comparison_id,
            "source_family_refs": list(self.source_family_refs),
            "executed_cell_results": [result.to_dict() for result in self.executed_cell_results],
            "shared_budget_rule": self.shared_budget_rule,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionRLRegressionLedgerPacket:
    packet_id: str
    rl_execution_id: str
    compared_cell_ids: tuple[str, ...]
    coherence_rows: tuple[str, ...]
    regression_guards: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "rl_execution_id": self.rl_execution_id,
            "compared_cell_ids": list(self.compared_cell_ids),
            "coherence_rows": list(self.coherence_rows),
            "regression_guards": list(self.regression_guards),
            "repeated_run_rule": self.repeated_run_rule,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionDivergenceLedgerPacket:
    packet_id: str
    harness_comparison_id: str
    optimize_regression_ledger_id: str
    rl_regression_ledger_id: str
    compared_cell_ids: tuple[str, ...]
    divergence_rows: tuple[str, ...]
    classification_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "harness_comparison_id": self.harness_comparison_id,
            "optimize_regression_ledger_id": self.optimize_regression_ledger_id,
            "rl_regression_ledger_id": self.rl_regression_ledger_id,
            "compared_cell_ids": list(self.compared_cell_ids),
            "divergence_rows": list(self.divergence_rows),
            "classification_rule": self.classification_rule,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionRepeatedRunSummaryPacket:
    packet_id: str
    divergence_ledger_id: str
    optimize_execution_id: str
    rl_execution_id: str
    compared_cell_ids: tuple[str, ...]
    repeated_run_rows: tuple[str, ...]
    next_locus_classification: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "optimize_execution_id": self.optimize_execution_id,
            "rl_execution_id": self.rl_execution_id,
            "compared_cell_ids": list(self.compared_cell_ids),
            "repeated_run_rows": list(self.repeated_run_rows),
            "next_locus_classification": self.next_locus_classification,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionCloseoutPacket:
    packet_id: str
    divergence_ledger_id: str
    repeated_run_summary_id: str
    source_family_refs: tuple[str, ...]
    compared_cell_ids: tuple[str, ...]
    closeout_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "repeated_run_summary_id": self.repeated_run_summary_id,
            "source_family_refs": list(self.source_family_refs),
            "compared_cell_ids": list(self.compared_cell_ids),
            "closeout_checks": list(self.closeout_checks),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchCrossExecutionNextLocusPacket:
    packet_id: str
    closeout_id: str
    next_locus_classification: str
    recommended_next_move: str
    deferred_moves: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "closeout_id": self.closeout_id,
            "next_locus_classification": self.next_locus_classification,
            "recommended_next_move": self.recommended_next_move,
            "deferred_moves": list(self.deferred_moves),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_cross_execution_matrix_packet() -> SearchCrossExecutionMatrixPacket:
    from agentic_coder_prototype.optimize import (
        build_next_frontier_optimize_cohort_packet,
        build_next_frontier_optimize_live_experiment_cell,
    )
    from agentic_coder_prototype.rl import (
        build_next_frontier_rl_replay_live_parity_packet,
        build_next_frontier_rl_trainer_facing_export_packet,
    )

    stage_b = build_search_atp_stage_b_closeout_packet()
    stage_c = build_search_stage_c_closeout_packet()
    optimize_cohort = build_next_frontier_optimize_cohort_packet()
    optimize_live = build_next_frontier_optimize_live_experiment_cell()
    rl_trainer = build_next_frontier_rl_trainer_facing_export_packet()
    rl_parity = build_next_frontier_rl_replay_live_parity_packet()

    budget_cells = (
        SearchExecutionBudgetCell(
            cell_id="search.cross_execution.cell.audit_small.v1",
            max_llm_calls=12,
            max_evaluator_calls=3,
            target_consumers=("optimize", "rl"),
            comparison_rule="same_source_family_and_budget_cell",
            expected_locus="harness_local",
        ),
        SearchExecutionBudgetCell(
            cell_id="search.cross_execution.cell.audit_medium.v1",
            max_llm_calls=24,
            max_evaluator_calls=6,
            target_consumers=("optimize", "rl", "repair_loop"),
            comparison_rule="same_source_family_with_consumer_expansion",
            expected_locus="consumer_local",
        ),
    )
    return SearchCrossExecutionMatrixPacket(
        packet_id="search.platform.phase2.execution_matrix.v1",
        stage_b_closeout_id=stage_b.packet_id,
        stage_c_closeout_id=stage_c.packet_id,
        optimize_comparison_id=optimize_cohort["comparison_result"].comparison_id,
        optimize_live_cell_id=optimize_live["live_cell"]["cell_id"],
        rl_trainer_export_manifest_id=rl_trainer["export_manifest"].export_manifest_id,
        rl_parity_live_manifest_id=rl_parity["live_export_manifest"].export_manifest_id,
        rl_parity_replay_manifest_id=rl_parity["replay_export_manifest"].export_manifest_id,
        source_family_refs=stage_c.source_family_refs,
        budget_cells=budget_cells,
        compared_consumers=("optimize", "rl", "harness"),
        harness_focus=(
            "budget_cell_stability",
            "objective_vs_export_alignment",
            "replay_export_integrity_visibility",
            "divergence_locus_classification",
        ),
        final_decision="execute_matched_optimize_rl_cells_over_published_atp_source_family",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_harness_comparison_packet() -> SearchCrossExecutionHarnessComparisonPacket:
    matrix = build_search_cross_execution_matrix_packet()
    convergence = build_search_stage_c_consumer_convergence_packet()
    return SearchCrossExecutionHarnessComparisonPacket(
        packet_id="search.platform.phase2.harness_comparison.v1",
        execution_matrix_id=matrix.packet_id,
        consumer_convergence_id=convergence.packet_id,
        compared_budget_cells=tuple(cell.cell_id for cell in matrix.budget_cells),
        optimize_artifact_ids=(
            matrix.optimize_comparison_id,
            matrix.optimize_live_cell_id,
        ),
        rl_artifact_ids=(
            matrix.rl_trainer_export_manifest_id,
            matrix.rl_parity_live_manifest_id,
            matrix.rl_parity_replay_manifest_id,
        ),
        regression_checks=(
            "source_family_identity_preserved",
            "matched_budget_cells_explicit",
            "optimize_and_rl_artifacts_visible_together",
            "cross_consumer_convergence_linked",
        ),
        divergence_summary=(
            "optimize_decision_utility_vs_rl_export_fidelity_compared",
            "differences_remain_classification_local_until_repeated",
        ),
        final_decision="use_harness_owned_comparison_before_expanding_execution_matrix",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_optimize_execution_packet() -> SearchCrossExecutionOptimizeExecutionPacket:
    matrix = build_search_cross_execution_matrix_packet()
    harness = build_search_cross_execution_harness_comparison_packet()
    optimize_consumerization = build_search_stage_c_optimize_consumerization_packet()

    cell_results = (
        SearchCrossExecutionCellResult(
            cell_id=matrix.budget_cells[0].cell_id,
            optimize_artifact_id=matrix.optimize_comparison_id,
            decision_signal="matched_budget_decision_surface_visible",
            regression_status="bounded_cell_stable",
            preserved_contract_fields=(
                "source_family_refs",
                "budget_cell_identity",
                "comparison_rule",
            ),
            expected_locus="harness_local",
        ),
        SearchCrossExecutionCellResult(
            cell_id=matrix.budget_cells[1].cell_id,
            optimize_artifact_id=matrix.optimize_live_cell_id,
            decision_signal="live_cell_utility_read_stays_explicit",
            regression_status="consumer_lane_stable",
            preserved_contract_fields=(
                "source_family_refs",
                "budget_cell_identity",
                "consumer_expansion_visibility",
            ),
            expected_locus="consumer_local",
        ),
    )
    return SearchCrossExecutionOptimizeExecutionPacket(
        packet_id="search.platform.phase2.optimize_execution.v1",
        execution_matrix_id=matrix.packet_id,
        stage_c_optimize_consumerization_id=optimize_consumerization.packet_id,
        harness_comparison_id=harness.packet_id,
        source_family_refs=matrix.source_family_refs,
        executed_cell_results=cell_results,
        shared_budget_rule="reuse_matrix_cells_before_any_budget_growth",
        final_decision="repeat_optimize_cells_before_expanding_rl_budget_surface",
        dominant_locus="consumer_local",
    )


def build_search_cross_execution_optimize_regression_ledger_packet() -> SearchCrossExecutionOptimizeRegressionLedgerPacket:
    optimize_execution = build_search_cross_execution_optimize_execution_packet()
    return SearchCrossExecutionOptimizeRegressionLedgerPacket(
        packet_id="search.platform.phase2.optimize_regression_ledger.v1",
        optimize_execution_id=optimize_execution.packet_id,
        compared_cell_ids=tuple(result.cell_id for result in optimize_execution.executed_cell_results),
        usefulness_rows=(
            "audit_small: comparison_result_stays_budget_matched_and_actionable",
            "audit_medium: live_cell_signal_stays_visible_without_hiding_contract_fields",
        ),
        regression_guards=(
            "no_source_family_drift",
            "no_budget_cell_renaming",
            "no_harness_visibility_loss",
            "no_unclassified_decision_regression",
        ),
        repeated_run_rule="require_same_cell_regression_twice_before_reclassification",
        final_decision="treat_optimize_regression_as_harness_managed_until_cross_consumer_repeat",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_rl_execution_packet() -> SearchCrossExecutionRLExecutionPacket:
    matrix = build_search_cross_execution_matrix_packet()
    harness = build_search_cross_execution_harness_comparison_packet()
    rl_consumerization = build_search_stage_c_rl_consumerization_packet()

    cell_results = (
        SearchCrossExecutionCellResult(
            cell_id=matrix.budget_cells[0].cell_id,
            optimize_artifact_id=matrix.rl_trainer_export_manifest_id,
            decision_signal="trainer_export_surface_stays_visible",
            regression_status="bounded_export_cell_stable",
            preserved_contract_fields=(
                "source_family_refs",
                "budget_cell_identity",
                "export_manifest_id",
            ),
            expected_locus="adapter_local",
        ),
        SearchCrossExecutionCellResult(
            cell_id=matrix.budget_cells[1].cell_id,
            optimize_artifact_id=matrix.rl_parity_live_manifest_id,
            decision_signal="parity_manifests_remain_jointly_legible",
            regression_status="parity_lane_stable",
            preserved_contract_fields=(
                "source_family_refs",
                "budget_cell_identity",
                "parity_manifest_visibility",
            ),
            expected_locus="consumer_local",
        ),
    )
    return SearchCrossExecutionRLExecutionPacket(
        packet_id="search.platform.phase3.rl_execution.v1",
        execution_matrix_id=matrix.packet_id,
        stage_c_rl_consumerization_id=rl_consumerization.packet_id,
        harness_comparison_id=harness.packet_id,
        source_family_refs=matrix.source_family_refs,
        executed_cell_results=cell_results,
        shared_budget_rule="reuse_matrix_cells_before_any_rl_budget_growth",
        final_decision="repeat_rl_cells_before_expanding_harness_divergence_matrix",
        dominant_locus="adapter_local",
    )


def build_search_cross_execution_rl_regression_ledger_packet() -> SearchCrossExecutionRLRegressionLedgerPacket:
    rl_execution = build_search_cross_execution_rl_execution_packet()
    return SearchCrossExecutionRLRegressionLedgerPacket(
        packet_id="search.platform.phase3.rl_regression_ledger.v1",
        rl_execution_id=rl_execution.packet_id,
        compared_cell_ids=tuple(result.cell_id for result in rl_execution.executed_cell_results),
        coherence_rows=(
            "audit_small: trainer_export_manifest_stays_budget_matched_and visible",
            "audit_medium: parity_manifests_stay explicit without losing source identity",
        ),
        regression_guards=(
            "no_source_family_drift",
            "no_budget_cell_renaming",
            "no_export_manifest_identity_loss",
            "no_unclassified_parity_regression",
        ),
        repeated_run_rule="require_same_cell_rl_regression_twice_before_reclassification",
        final_decision="treat_rl_regression_as_harness_managed_until_cross_consumer_repeat",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_divergence_ledger_packet() -> SearchCrossExecutionDivergenceLedgerPacket:
    harness = build_search_cross_execution_harness_comparison_packet()
    optimize_ledger = build_search_cross_execution_optimize_regression_ledger_packet()
    rl_ledger = build_search_cross_execution_rl_regression_ledger_packet()
    return SearchCrossExecutionDivergenceLedgerPacket(
        packet_id="search.platform.phase4.divergence_ledger.v1",
        harness_comparison_id=harness.packet_id,
        optimize_regression_ledger_id=optimize_ledger.packet_id,
        rl_regression_ledger_id=rl_ledger.packet_id,
        compared_cell_ids=optimize_ledger.compared_cell_ids,
        divergence_rows=(
            "audit_small: optimize utility and rl export remain jointly visible under the same harness cell",
            "audit_medium: optimize live-cell and rl parity remain attributable without source-family drift",
        ),
        classification_rule="require repeated same-cell cross-consumer divergence before locus escalation",
        final_decision="keep divergence ledger harness_owned_until repeated cross_consumer evidence appears",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_repeated_run_summary_packet() -> SearchCrossExecutionRepeatedRunSummaryPacket:
    optimize_execution = build_search_cross_execution_optimize_execution_packet()
    rl_execution = build_search_cross_execution_rl_execution_packet()
    divergence_ledger = build_search_cross_execution_divergence_ledger_packet()
    return SearchCrossExecutionRepeatedRunSummaryPacket(
        packet_id="search.platform.phase4.repeated_run_summary.v1",
        divergence_ledger_id=divergence_ledger.packet_id,
        optimize_execution_id=optimize_execution.packet_id,
        rl_execution_id=rl_execution.packet_id,
        compared_cell_ids=divergence_ledger.compared_cell_ids,
        repeated_run_rows=(
            "audit_small: no threshold-satisfying repeated divergence yet",
            "audit_medium: repeated-run evidence still classified as harness_local_or_consumer_local_only",
        ),
        next_locus_classification="harness_local_pending_more_repetition",
        final_decision="continue_execution_tranche_without_new_architecture_or_planner_round",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_closeout_packet() -> SearchCrossExecutionCloseoutPacket:
    matrix = build_search_cross_execution_matrix_packet()
    divergence_ledger = build_search_cross_execution_divergence_ledger_packet()
    repeated_summary = build_search_cross_execution_repeated_run_summary_packet()
    return SearchCrossExecutionCloseoutPacket(
        packet_id="search.platform.phase5.closeout.v1",
        divergence_ledger_id=divergence_ledger.packet_id,
        repeated_run_summary_id=repeated_summary.packet_id,
        source_family_refs=matrix.source_family_refs,
        compared_cell_ids=divergence_ledger.compared_cell_ids,
        closeout_checks=(
            "same_source_family_preserved",
            "same_budget_cells_preserved",
            "optimize_and_rl_mirrors_executed",
            "divergence_and_repetition_reads_explicit",
        ),
        final_decision="close_cross_system_execution_tranche_and_keep_next_work_platform_local",
        dominant_locus="harness_local",
    )


def build_search_cross_execution_next_locus_packet() -> SearchCrossExecutionNextLocusPacket:
    closeout = build_search_cross_execution_closeout_packet()
    return SearchCrossExecutionNextLocusPacket(
        packet_id="search.platform.phase5.next_locus.v1",
        closeout_id=closeout.packet_id,
        next_locus_classification="platform_or_harness_local_before_any_new_planner_round",
        recommended_next_move="run_repeated_execution_on_same_cells_before_expanding_source_family",
        deferred_moves=(
            "no_new_dag_program",
            "no_new_architecture_review",
            "no_new_planner_round_until_repeated_divergence_exists",
        ),
        final_decision="keep_follow_on_work_execution_local_and_non_architectural",
        dominant_locus="platform_local",
    )


def build_search_cross_execution_matrix_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_matrix_packet()
    return {
        "cross_execution_matrix_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_cross_execution_matrix_payload() -> Dict[str, Any]:
    return {
        "cross_execution_matrix_packet": build_search_cross_execution_matrix_packet().to_dict(),
    }


def build_search_cross_execution_harness_comparison_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_harness_comparison_packet()
    matrix = build_search_cross_execution_matrix_packet()
    return {
        "cross_execution_harness_comparison_packet": packet,
        "cross_execution_matrix_packet": matrix,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(matrix.source_family_refs),
    }


def build_search_cross_execution_harness_comparison_payload() -> Dict[str, Any]:
    return {
        "cross_execution_harness_comparison_packet": build_search_cross_execution_harness_comparison_packet().to_dict(),
        "cross_execution_matrix_packet": build_search_cross_execution_matrix_packet().to_dict(),
    }


def build_search_cross_execution_optimize_execution_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_optimize_execution_packet()
    matrix = build_search_cross_execution_matrix_packet()
    return {
        "cross_execution_optimize_execution_packet": packet,
        "cross_execution_matrix_packet": matrix,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_cross_execution_optimize_execution_payload() -> Dict[str, Any]:
    return {
        "cross_execution_optimize_execution_packet": build_search_cross_execution_optimize_execution_packet().to_dict(),
        "cross_execution_matrix_packet": build_search_cross_execution_matrix_packet().to_dict(),
    }


def build_search_cross_execution_optimize_regression_ledger_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_optimize_regression_ledger_packet()
    optimize_execution = build_search_cross_execution_optimize_execution_packet()
    return {
        "cross_execution_optimize_regression_ledger_packet": packet,
        "cross_execution_optimize_execution_packet": optimize_execution,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(optimize_execution.source_family_refs),
    }


def build_search_cross_execution_optimize_regression_ledger_payload() -> Dict[str, Any]:
    return {
        "cross_execution_optimize_regression_ledger_packet": build_search_cross_execution_optimize_regression_ledger_packet().to_dict(),
        "cross_execution_optimize_execution_packet": build_search_cross_execution_optimize_execution_packet().to_dict(),
    }


def build_search_cross_execution_rl_execution_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_rl_execution_packet()
    matrix = build_search_cross_execution_matrix_packet()
    return {
        "cross_execution_rl_execution_packet": packet,
        "cross_execution_matrix_packet": matrix,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_cross_execution_rl_execution_payload() -> Dict[str, Any]:
    return {
        "cross_execution_rl_execution_packet": build_search_cross_execution_rl_execution_packet().to_dict(),
        "cross_execution_matrix_packet": build_search_cross_execution_matrix_packet().to_dict(),
    }


def build_search_cross_execution_rl_regression_ledger_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_rl_regression_ledger_packet()
    rl_execution = build_search_cross_execution_rl_execution_packet()
    return {
        "cross_execution_rl_regression_ledger_packet": packet,
        "cross_execution_rl_execution_packet": rl_execution,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(rl_execution.source_family_refs),
    }


def build_search_cross_execution_rl_regression_ledger_payload() -> Dict[str, Any]:
    return {
        "cross_execution_rl_regression_ledger_packet": build_search_cross_execution_rl_regression_ledger_packet().to_dict(),
        "cross_execution_rl_execution_packet": build_search_cross_execution_rl_execution_packet().to_dict(),
    }


def build_search_cross_execution_divergence_ledger_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_divergence_ledger_packet()
    return {
        "cross_execution_divergence_ledger_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(build_search_cross_execution_matrix_packet().source_family_refs),
    }


def build_search_cross_execution_divergence_ledger_payload() -> Dict[str, Any]:
    return {
        "cross_execution_divergence_ledger_packet": build_search_cross_execution_divergence_ledger_packet().to_dict(),
    }


def build_search_cross_execution_repeated_run_summary_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_repeated_run_summary_packet()
    return {
        "cross_execution_repeated_run_summary_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(build_search_cross_execution_matrix_packet().source_family_refs),
    }


def build_search_cross_execution_repeated_run_summary_payload() -> Dict[str, Any]:
    return {
        "cross_execution_repeated_run_summary_packet": build_search_cross_execution_repeated_run_summary_packet().to_dict(),
    }


def build_search_cross_execution_closeout_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_closeout_packet()
    return {
        "cross_execution_closeout_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_cross_execution_closeout_payload() -> Dict[str, Any]:
    return {
        "cross_execution_closeout_packet": build_search_cross_execution_closeout_packet().to_dict(),
    }


def build_search_cross_execution_next_locus_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_cross_execution_next_locus_packet()
    return {
        "cross_execution_next_locus_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(build_search_cross_execution_matrix_packet().source_family_refs),
    }


def build_search_cross_execution_next_locus_payload() -> Dict[str, Any]:
    return {
        "cross_execution_next_locus_packet": build_search_cross_execution_next_locus_packet().to_dict(),
    }
