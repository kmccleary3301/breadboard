from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .offline_convergence import (
    build_search_offline_convergence_closeout_packet,
    build_search_offline_convergence_comparison_probe_packet,
    build_search_offline_convergence_divergence_ledger_packet,
    build_search_offline_convergence_matrix_packet,
)


@dataclass(frozen=True)
class SearchOfflineStochasticityOrderingMatrixPacket:
    packet_id: str
    baseline_matrix_id: str
    baseline_divergence_ledger_id: str
    baseline_comparison_probe_id: str
    baseline_closeout_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    inherited_source_packet_ids: tuple[str, ...]
    inherited_budget_cell_ids: tuple[str, ...]
    compared_consumers: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'baseline_matrix_id': self.baseline_matrix_id,
            'baseline_divergence_ledger_id': self.baseline_divergence_ledger_id,
            'baseline_comparison_probe_id': self.baseline_comparison_probe_id,
            'baseline_closeout_id': self.baseline_closeout_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'inherited_source_packet_ids': list(self.inherited_source_packet_ids),
            'inherited_budget_cell_ids': list(self.inherited_budget_cell_ids),
            'compared_consumers': list(self.compared_consumers),
            'matrix_checks': list(self.matrix_checks),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityRepeatedRunLedgerPacket:
    packet_id: str
    ordering_matrix_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    repeated_run_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'ordering_matrix_id': self.ordering_matrix_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'repeated_run_rows': list(self.repeated_run_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityCloseoutPacket:
    packet_id: str
    ordering_matrix_id: str
    repeated_run_ledger_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'ordering_matrix_id': self.ordering_matrix_id,
            'repeated_run_ledger_id': self.repeated_run_ledger_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


def _ordering_labels() -> tuple[str, ...]:
    return (
        'ordering.source_rows.reverse.v1',
        'ordering.consumer_inputs.rotate_left.v1',
        'ordering.replay_export.reverse.v1',
    )


def build_search_offline_stochasticity_ordering_matrix_packet() -> SearchOfflineStochasticityOrderingMatrixPacket:
    baseline_matrix = build_search_offline_convergence_matrix_packet()
    baseline_divergence = build_search_offline_convergence_divergence_ledger_packet()
    baseline_comparison = build_search_offline_convergence_comparison_probe_packet()
    baseline_closeout = build_search_offline_convergence_closeout_packet()
    return SearchOfflineStochasticityOrderingMatrixPacket(
        packet_id='search.platform.phase10.offline_stochasticity_ordering_matrix.v1',
        baseline_matrix_id=baseline_matrix.packet_id,
        baseline_divergence_ledger_id=baseline_divergence.packet_id,
        baseline_comparison_probe_id=baseline_comparison.packet_id,
        baseline_closeout_id=baseline_closeout.packet_id,
        perturbation_family='ordering_perturbation',
        perturbation_labels=_ordering_labels(),
        inherited_source_packet_ids=tuple(row.source_packet_id for row in baseline_matrix.source_rows),
        inherited_budget_cell_ids=tuple(cell.cell_id for cell in baseline_matrix.budget_cells),
        compared_consumers=baseline_matrix.compared_consumers,
        matrix_checks=(
            'baseline_surface_frozen_before_perturbation',
            'ordering_labels_reproducible_and_explicit',
            'source_set_fixed_during_ordering_branch',
            'budget_cells_fixed_during_ordering_branch',
        ),
        final_decision='execute_offline_ordering_perturbation_before_any_tie_break_or_compaction_branch',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_ordering_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_ordering_matrix_packet().to_dict()


def build_search_offline_stochasticity_ordering_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_ordering_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_ordering_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_repeated_run_ledger_packet() -> SearchOfflineStochasticityRepeatedRunLedgerPacket:
    matrix = build_search_offline_stochasticity_ordering_matrix_packet()
    return SearchOfflineStochasticityRepeatedRunLedgerPacket(
        packet_id='search.platform.phase10.offline_stochasticity_repeated_run_ledger.v1',
        ordering_matrix_id=matrix.packet_id,
        perturbation_family=matrix.perturbation_family,
        perturbation_labels=matrix.perturbation_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        repeated_run_rows=(
            'ordering_source_row_reverse_did_not_force_optimize_rl_split',
            'ordering_consumer_input_rotation_did_not_force_optimize_rl_split',
            'ordering_replay_export_reverse_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_run_disagreement_detected_under_ordering_perturbation',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two ordering labels and more than one budget cell',
        final_decision='keep_ordering_perturbation_read_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_repeated_run_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_repeated_run_ledger_packet().to_dict()


def build_search_offline_stochasticity_repeated_run_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_repeated_run_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_repeated_run_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_closeout_packet() -> SearchOfflineStochasticityCloseoutPacket:
    matrix = build_search_offline_stochasticity_ordering_matrix_packet()
    ledger = build_search_offline_stochasticity_repeated_run_ledger_packet()
    return SearchOfflineStochasticityCloseoutPacket(
        packet_id='search.platform.phase10.offline_stochasticity_closeout.v1',
        ordering_matrix_id=matrix.packet_id,
        repeated_run_ledger_id=ledger.packet_id,
        ready_rows=(
            'scope_lock_green',
            'baseline_surface_freeze_green',
            'ordering_perturbation_family_green',
            'no_ordering_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'tie_break_perturbation_branch_deferred',
            'compaction_perturbation_branch_deferred',
        ),
        final_decision='close_phase0_and_phase1_ordering_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_stochasticity_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_closeout_packet().to_dict()


def build_search_offline_stochasticity_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


@dataclass(frozen=True)
class SearchOfflineStochasticityTieBreakMatrixPacket:
    packet_id: str
    baseline_ordering_matrix_id: str
    baseline_repeated_run_ledger_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    inherited_source_packet_ids: tuple[str, ...]
    inherited_budget_cell_ids: tuple[str, ...]
    compared_consumers: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'baseline_ordering_matrix_id': self.baseline_ordering_matrix_id,
            'baseline_repeated_run_ledger_id': self.baseline_repeated_run_ledger_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'inherited_source_packet_ids': list(self.inherited_source_packet_ids),
            'inherited_budget_cell_ids': list(self.inherited_budget_cell_ids),
            'compared_consumers': list(self.compared_consumers),
            'matrix_checks': list(self.matrix_checks),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityTieBreakLedgerPacket:
    packet_id: str
    tie_break_matrix_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    repeated_run_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'tie_break_matrix_id': self.tie_break_matrix_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'repeated_run_rows': list(self.repeated_run_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityTieBreakCloseoutPacket:
    packet_id: str
    tie_break_matrix_id: str
    tie_break_ledger_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'tie_break_matrix_id': self.tie_break_matrix_id,
            'tie_break_ledger_id': self.tie_break_ledger_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


def _tie_break_labels() -> tuple[str, ...]:
    return (
        'tie_break.optimize_rank_prefers_earliest_contract_stable.v1',
        'tie_break.rl_rank_prefers_export_density.v1',
        'tie_break.shared_rank_prefers_budget_conservative_path.v1',
    )


def build_search_offline_stochasticity_tie_break_matrix_packet() -> SearchOfflineStochasticityTieBreakMatrixPacket:
    ordering = build_search_offline_stochasticity_ordering_matrix_packet()
    ledger = build_search_offline_stochasticity_repeated_run_ledger_packet()
    return SearchOfflineStochasticityTieBreakMatrixPacket(
        packet_id='search.platform.phase10.offline_stochasticity_tie_break_matrix.v1',
        baseline_ordering_matrix_id=ordering.packet_id,
        baseline_repeated_run_ledger_id=ledger.packet_id,
        perturbation_family='tie_break_perturbation',
        perturbation_labels=_tie_break_labels(),
        inherited_source_packet_ids=ordering.inherited_source_packet_ids,
        inherited_budget_cell_ids=ordering.inherited_budget_cell_ids,
        compared_consumers=ordering.compared_consumers,
        matrix_checks=(
            'ordering_branch_frozen_before_tie_break_branch',
            'tie_break_labels_reproducible_and_explicit',
            'source_set_fixed_during_tie_break_branch',
            'budget_cells_fixed_during_tie_break_branch',
        ),
        final_decision='execute_offline_tie_break_perturbation_before_any_compaction_branch',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_tie_break_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_tie_break_matrix_packet().to_dict()


def build_search_offline_stochasticity_tie_break_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_tie_break_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_tie_break_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_tie_break_ledger_packet() -> SearchOfflineStochasticityTieBreakLedgerPacket:
    matrix = build_search_offline_stochasticity_tie_break_matrix_packet()
    return SearchOfflineStochasticityTieBreakLedgerPacket(
        packet_id='search.platform.phase10.offline_stochasticity_tie_break_ledger.v1',
        tie_break_matrix_id=matrix.packet_id,
        perturbation_family=matrix.perturbation_family,
        perturbation_labels=matrix.perturbation_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        repeated_run_rows=(
            'tie_break_optimize_rank_preference_did_not_force_optimize_rl_split',
            'tie_break_rl_export_density_preference_did_not_force_optimize_rl_split',
            'tie_break_budget_conservative_preference_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_run_disagreement_detected_under_tie_break_perturbation',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two tie-break labels and more than one budget cell',
        final_decision='keep_tie_break_perturbation_read_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_tie_break_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_tie_break_ledger_packet().to_dict()


def build_search_offline_stochasticity_tie_break_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_tie_break_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_tie_break_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_tie_break_closeout_packet() -> SearchOfflineStochasticityTieBreakCloseoutPacket:
    matrix = build_search_offline_stochasticity_tie_break_matrix_packet()
    ledger = build_search_offline_stochasticity_tie_break_ledger_packet()
    return SearchOfflineStochasticityTieBreakCloseoutPacket(
        packet_id='search.platform.phase10.offline_stochasticity_tie_break_closeout.v1',
        tie_break_matrix_id=matrix.packet_id,
        tie_break_ledger_id=ledger.packet_id,
        ready_rows=(
            'ordering_branch_frozen_green',
            'tie_break_perturbation_family_green',
            'no_tie_break_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'compaction_perturbation_branch_deferred',
        ),
        final_decision='close_phase2_tie_break_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_stochasticity_tie_break_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_tie_break_closeout_packet().to_dict()


def build_search_offline_stochasticity_tie_break_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_tie_break_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_tie_break_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


@dataclass(frozen=True)
class SearchOfflineStochasticityCompactionMatrixPacket:
    packet_id: str
    baseline_tie_break_matrix_id: str
    baseline_tie_break_ledger_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    inherited_source_packet_ids: tuple[str, ...]
    inherited_budget_cell_ids: tuple[str, ...]
    compared_consumers: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'baseline_tie_break_matrix_id': self.baseline_tie_break_matrix_id,
            'baseline_tie_break_ledger_id': self.baseline_tie_break_ledger_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'inherited_source_packet_ids': list(self.inherited_source_packet_ids),
            'inherited_budget_cell_ids': list(self.inherited_budget_cell_ids),
            'compared_consumers': list(self.compared_consumers),
            'matrix_checks': list(self.matrix_checks),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityCompactionLedgerPacket:
    packet_id: str
    compaction_matrix_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    repeated_run_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'compaction_matrix_id': self.compaction_matrix_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'repeated_run_rows': list(self.repeated_run_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityCompactionCloseoutPacket:
    packet_id: str
    compaction_matrix_id: str
    compaction_ledger_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'compaction_matrix_id': self.compaction_matrix_id,
            'compaction_ledger_id': self.compaction_ledger_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


def _compaction_labels() -> tuple[str, ...]:
    return (
        'compaction.variant.minimal_contract_preserve.v1',
        'compaction.variant.replay_first_preserve.v1',
        'compaction.variant.order_stable_reduce.v1',
    )


def build_search_offline_stochasticity_compaction_matrix_packet() -> SearchOfflineStochasticityCompactionMatrixPacket:
    tie_break = build_search_offline_stochasticity_tie_break_matrix_packet()
    ledger = build_search_offline_stochasticity_tie_break_ledger_packet()
    return SearchOfflineStochasticityCompactionMatrixPacket(
        packet_id='search.platform.phase10.offline_stochasticity_compaction_matrix.v1',
        baseline_tie_break_matrix_id=tie_break.packet_id,
        baseline_tie_break_ledger_id=ledger.packet_id,
        perturbation_family='compaction_perturbation',
        perturbation_labels=_compaction_labels(),
        inherited_source_packet_ids=tie_break.inherited_source_packet_ids,
        inherited_budget_cell_ids=tie_break.inherited_budget_cell_ids,
        compared_consumers=tie_break.compared_consumers,
        matrix_checks=(
            'tie_break_branch_frozen_before_compaction_branch',
            'compaction_labels_reproducible_and_explicit',
            'source_set_fixed_during_compaction_branch',
            'budget_cells_fixed_during_compaction_branch',
        ),
        final_decision='execute_offline_compaction_perturbation_as_last_bounded_phase10_branch',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_compaction_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_compaction_matrix_packet().to_dict()


def build_search_offline_stochasticity_compaction_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_compaction_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_compaction_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_compaction_ledger_packet() -> SearchOfflineStochasticityCompactionLedgerPacket:
    matrix = build_search_offline_stochasticity_compaction_matrix_packet()
    return SearchOfflineStochasticityCompactionLedgerPacket(
        packet_id='search.platform.phase10.offline_stochasticity_compaction_ledger.v1',
        compaction_matrix_id=matrix.packet_id,
        perturbation_family=matrix.perturbation_family,
        perturbation_labels=matrix.perturbation_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        repeated_run_rows=(
            'compaction_minimal_contract_variant_did_not_force_optimize_rl_split',
            'compaction_replay_first_variant_did_not_force_optimize_rl_split',
            'compaction_order_stable_reduce_variant_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_run_disagreement_detected_under_compaction_perturbation',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two compaction labels and more than one budget cell',
        final_decision='keep_compaction_perturbation_read_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_compaction_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_compaction_ledger_packet().to_dict()


def build_search_offline_stochasticity_compaction_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_compaction_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_compaction_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_compaction_closeout_packet() -> SearchOfflineStochasticityCompactionCloseoutPacket:
    matrix = build_search_offline_stochasticity_compaction_matrix_packet()
    ledger = build_search_offline_stochasticity_compaction_ledger_packet()
    return SearchOfflineStochasticityCompactionCloseoutPacket(
        packet_id='search.platform.phase10.offline_stochasticity_compaction_closeout.v1',
        compaction_matrix_id=matrix.packet_id,
        compaction_ledger_id=ledger.packet_id,
        ready_rows=(
            'ordering_branch_green',
            'tie_break_branch_green',
            'compaction_perturbation_family_green',
            'no_compaction_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'local_scoring_jitter_branch_deferred',
        ),
        final_decision='close_phase3_compaction_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_stochasticity_compaction_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_compaction_closeout_packet().to_dict()


def build_search_offline_stochasticity_compaction_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_compaction_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_compaction_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


@dataclass(frozen=True)
class SearchOfflineStochasticityScoringJitterMatrixPacket:
    packet_id: str
    baseline_compaction_matrix_id: str
    baseline_compaction_ledger_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    inherited_source_packet_ids: tuple[str, ...]
    inherited_budget_cell_ids: tuple[str, ...]
    compared_consumers: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'baseline_compaction_matrix_id': self.baseline_compaction_matrix_id,
            'baseline_compaction_ledger_id': self.baseline_compaction_ledger_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'inherited_source_packet_ids': list(self.inherited_source_packet_ids),
            'inherited_budget_cell_ids': list(self.inherited_budget_cell_ids),
            'compared_consumers': list(self.compared_consumers),
            'matrix_checks': list(self.matrix_checks),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityScoringJitterLedgerPacket:
    packet_id: str
    scoring_jitter_matrix_id: str
    perturbation_family: str
    perturbation_labels: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    repeated_run_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'scoring_jitter_matrix_id': self.scoring_jitter_matrix_id,
            'perturbation_family': self.perturbation_family,
            'perturbation_labels': list(self.perturbation_labels),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'repeated_run_rows': list(self.repeated_run_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineStochasticityScoringJitterCloseoutPacket:
    packet_id: str
    scoring_jitter_matrix_id: str
    scoring_jitter_ledger_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'scoring_jitter_matrix_id': self.scoring_jitter_matrix_id,
            'scoring_jitter_ledger_id': self.scoring_jitter_ledger_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


def _scoring_jitter_labels() -> tuple[str, ...]:
    return (
        'scoring_jitter.seed_003_contract_weight_plus.v1',
        'scoring_jitter.seed_017_export_density_minus.v1',
        'scoring_jitter.seed_101_budget_margin_mix.v1',
    )


def build_search_offline_stochasticity_scoring_jitter_matrix_packet() -> SearchOfflineStochasticityScoringJitterMatrixPacket:
    compaction = build_search_offline_stochasticity_compaction_matrix_packet()
    ledger = build_search_offline_stochasticity_compaction_ledger_packet()
    return SearchOfflineStochasticityScoringJitterMatrixPacket(
        packet_id='search.platform.phase10.offline_stochasticity_scoring_jitter_matrix.v1',
        baseline_compaction_matrix_id=compaction.packet_id,
        baseline_compaction_ledger_id=ledger.packet_id,
        perturbation_family='local_scoring_jitter_perturbation',
        perturbation_labels=_scoring_jitter_labels(),
        inherited_source_packet_ids=compaction.inherited_source_packet_ids,
        inherited_budget_cell_ids=compaction.inherited_budget_cell_ids,
        compared_consumers=compaction.compared_consumers,
        matrix_checks=(
            'compaction_branch_frozen_before_scoring_jitter_branch',
            'scoring_jitter_labels_reproducible_and_explicit',
            'source_set_fixed_during_scoring_jitter_branch',
            'budget_cells_fixed_during_scoring_jitter_branch',
            'jitter_local_to_comparison_surface_only',
        ),
        final_decision='execute_offline_scoring_jitter_perturbation_as_final_phase10_branch',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_scoring_jitter_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_scoring_jitter_matrix_packet().to_dict()


def build_search_offline_stochasticity_scoring_jitter_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_scoring_jitter_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_scoring_jitter_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_scoring_jitter_ledger_packet() -> SearchOfflineStochasticityScoringJitterLedgerPacket:
    matrix = build_search_offline_stochasticity_scoring_jitter_matrix_packet()
    return SearchOfflineStochasticityScoringJitterLedgerPacket(
        packet_id='search.platform.phase10.offline_stochasticity_scoring_jitter_ledger.v1',
        scoring_jitter_matrix_id=matrix.packet_id,
        perturbation_family=matrix.perturbation_family,
        perturbation_labels=matrix.perturbation_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        repeated_run_rows=(
            'scoring_jitter_contract_weight_plus_did_not_force_optimize_rl_split',
            'scoring_jitter_export_density_minus_did_not_force_optimize_rl_split',
            'scoring_jitter_budget_margin_mix_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_run_disagreement_detected_under_scoring_jitter_perturbation',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two deterministic jitter labels and more than one budget cell',
        final_decision='keep_scoring_jitter_perturbation_read_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_stochasticity_scoring_jitter_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_scoring_jitter_ledger_packet().to_dict()


def build_search_offline_stochasticity_scoring_jitter_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_scoring_jitter_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_scoring_jitter_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }


def build_search_offline_stochasticity_scoring_jitter_closeout_packet() -> SearchOfflineStochasticityScoringJitterCloseoutPacket:
    matrix = build_search_offline_stochasticity_scoring_jitter_matrix_packet()
    ledger = build_search_offline_stochasticity_scoring_jitter_ledger_packet()
    return SearchOfflineStochasticityScoringJitterCloseoutPacket(
        packet_id='search.platform.phase10.offline_stochasticity_scoring_jitter_closeout.v1',
        scoring_jitter_matrix_id=matrix.packet_id,
        scoring_jitter_ledger_id=ledger.packet_id,
        ready_rows=(
            'ordering_branch_green',
            'tie_break_branch_green',
            'compaction_branch_green_or_below_threshold',
            'scoring_jitter_perturbation_family_green',
            'no_scoring_jitter_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(),
        final_decision='close_phase4_scoring_jitter_branch_and_prepare_phase10_playbook_closeout_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_stochasticity_scoring_jitter_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_stochasticity_scoring_jitter_closeout_packet().to_dict()


def build_search_offline_stochasticity_scoring_jitter_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_stochasticity_scoring_jitter_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_stochasticity_scoring_jitter_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase10_offline_stochasticity',
    }
