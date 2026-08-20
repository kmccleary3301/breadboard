from __future__ import annotations

from dataclasses import dataclass
from typing import Dict



@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeMatrixPacket:
    packet_id: str
    baseline_convergence_matrix_id: str
    baseline_convergence_closeout_id: str
    baseline_stochasticity_closeout_id: str
    objective_regime_family: str
    objective_regime_labels: tuple[str, ...]
    inherited_source_packet_ids: tuple[str, ...]
    inherited_budget_cell_ids: tuple[str, ...]
    compared_consumers: tuple[str, ...]
    primary_objectives: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    false_positive_conditions: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'baseline_convergence_matrix_id': self.baseline_convergence_matrix_id,
            'baseline_convergence_closeout_id': self.baseline_convergence_closeout_id,
            'baseline_stochasticity_closeout_id': self.baseline_stochasticity_closeout_id,
            'objective_regime_family': self.objective_regime_family,
            'objective_regime_labels': list(self.objective_regime_labels),
            'inherited_source_packet_ids': list(self.inherited_source_packet_ids),
            'inherited_budget_cell_ids': list(self.inherited_budget_cell_ids),
            'compared_consumers': list(self.compared_consumers),
            'primary_objectives': list(self.primary_objectives),
            'matrix_checks': list(self.matrix_checks),
            'false_positive_conditions': list(self.false_positive_conditions),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    packet_id: str
    objective_regime_matrix_id: str
    objective_regime_family: str
    objective_regime_labels: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    divergence_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'objective_regime_matrix_id': self.objective_regime_matrix_id,
            'objective_regime_family': self.objective_regime_family,
            'objective_regime_labels': list(self.objective_regime_labels),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'divergence_rows': list(self.divergence_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeCloseoutPacket:
    packet_id: str
    objective_regime_matrix_id: str
    objective_regime_divergence_ledger_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'objective_regime_matrix_id': self.objective_regime_matrix_id,
            'objective_regime_divergence_ledger_id': self.objective_regime_divergence_ledger_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }



@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeForensicReadPacket:
    packet_id: str
    compared_regime_families: tuple[str, ...]
    allowed_moving_fields: tuple[str, ...]
    protected_fields: tuple[str, ...]
    forensic_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'compared_regime_families': list(self.compared_regime_families),
            'allowed_moving_fields': list(self.allowed_moving_fields),
            'protected_fields': list(self.protected_fields),
            'forensic_rows': list(self.forensic_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeProgramCloseoutPacket:
    packet_id: str
    completed_regime_families: tuple[str, ...]
    forensic_read_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'completed_regime_families': list(self.completed_regime_families),
            'forensic_read_id': self.forensic_read_id,
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineObjectiveRegimeNextLocusPacket:
    packet_id: str
    program_closeout_id: str
    recommended_loci: tuple[str, ...]
    disfavored_loci: tuple[str, ...]
    planner_trigger_state: str
    final_recommendation: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'program_closeout_id': self.program_closeout_id,
            'recommended_loci': list(self.recommended_loci),
            'disfavored_loci': list(self.disfavored_loci),
            'planner_trigger_state': self.planner_trigger_state,
            'final_recommendation': self.final_recommendation,
            'dominant_locus': self.dominant_locus,
        }

BASELINE_CONVERGENCE_MATRIX_ID = 'search.platform.phase8.offline_convergence_matrix.v1'
BASELINE_CONVERGENCE_CLOSEOUT_ID = 'search.platform.phase8.offline_convergence_closeout.v1'
BASELINE_STOCHASTICITY_CLOSEOUT_ID = 'search.platform.phase10.offline_stochasticity_scoring_jitter_closeout.v1'
INHERITED_SOURCE_PACKET_IDS = (
    'dag_v4_bavt.v1',
    'dag_v4_team_of_thoughts.v1',
    'dag_v4_dci.v1',
    'dag_v4_codetree_v2.v1',
    'dag_v4_got_v2.v1',
    'dag_v4_tot_v2.v1',
    'dag_v4_moa_v2.v1',
    'dag_v4_final_adjudication.v1',
)
INHERITED_BUDGET_CELL_IDS = (
    'search.offline_convergence.cell.audit_small.v1',
    'search.offline_convergence.cell.audit_medium.v1',
    'search.offline_convergence.cell.audit_compressed.v1',
)
COMPARED_CONSUMERS = ('optimize', 'rl')


def _contract_first_labels() -> tuple[str, ...]:
    return (
        'contract_first.ordering_fidelity_priority.v1',
        'contract_first.contract_stability_over_export_density.v1',
        'contract_first.conservative_closeout_preserve.v1',
    )


def build_search_offline_objective_regime_contract_first_matrix_packet() -> SearchOfflineObjectiveRegimeMatrixPacket:
    return SearchOfflineObjectiveRegimeMatrixPacket(
        packet_id='search.platform.phase11.offline_objective_regime_contract_first_matrix.v1',
        baseline_convergence_matrix_id=BASELINE_CONVERGENCE_MATRIX_ID,
        baseline_convergence_closeout_id=BASELINE_CONVERGENCE_CLOSEOUT_ID,
        baseline_stochasticity_closeout_id=BASELINE_STOCHASTICITY_CLOSEOUT_ID,
        objective_regime_family='contract_first',
        objective_regime_labels=_contract_first_labels(),
        inherited_source_packet_ids=INHERITED_SOURCE_PACKET_IDS,
        inherited_budget_cell_ids=INHERITED_BUDGET_CELL_IDS,
        compared_consumers=COMPARED_CONSUMERS,
        primary_objectives=(
            'contract_stability_first',
            'ordering_fidelity_second',
            'closeout_decision_stability_third',
            'export_density_only_after_contract_preservation',
        ),
        matrix_checks=(
            'offline_objective_regime_scope_lock_green',
            'phase9_baseline_surface_frozen_before_regime_change',
            'phase10_stochasticity_closeout_frozen_before_regime_change',
            'source_set_fixed_during_contract_first_branch',
            'budget_cells_fixed_during_contract_first_branch',
            'no_live_or_provider_dependency_imported',
        ),
        false_positive_conditions=(
            'mere_export_density_drop_without_consumer_split',
            'summary_relabel_without_closeout_difference',
            'ordering_only_shift_without_objective_regime_effect',
        ),
        final_decision='execute_contract_first_regime_before_budget_conservative_or_export_density_first',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_contract_first_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_contract_first_matrix_packet().to_dict()


def build_search_offline_objective_regime_contract_first_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_contract_first_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_contract_first_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_contract_first_divergence_ledger_packet() -> SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    matrix = build_search_offline_objective_regime_contract_first_matrix_packet()
    return SearchOfflineObjectiveRegimeDivergenceLedgerPacket(
        packet_id='search.platform.phase11.offline_objective_regime_contract_first_divergence_ledger.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_family=matrix.objective_regime_family,
        objective_regime_labels=matrix.objective_regime_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        divergence_rows=(
            'contract_stability_priority_did_not_force_optimize_rl_split',
            'ordering_fidelity_priority_did_not_force_optimize_rl_split',
            'conservative_closeout_priority_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_disagreement_detected_under_contract_first_regime',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two contract-first regime labels and more than one budget cell',
        final_decision='keep_contract_first_regime_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_contract_first_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_contract_first_divergence_ledger_packet().to_dict()


def build_search_offline_objective_regime_contract_first_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_contract_first_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_contract_first_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_contract_first_closeout_packet() -> SearchOfflineObjectiveRegimeCloseoutPacket:
    matrix = build_search_offline_objective_regime_contract_first_matrix_packet()
    ledger = build_search_offline_objective_regime_contract_first_divergence_ledger_packet()
    return SearchOfflineObjectiveRegimeCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_contract_first_closeout.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_divergence_ledger_id=ledger.packet_id,
        ready_rows=(
            'scope_lock_green',
            'baseline_surface_freeze_green',
            'contract_first_regime_family_green',
            'no_contract_first_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'budget_conservative_regime_branch_deferred',
            'export_density_first_regime_branch_deferred',
        ),
        final_decision='close_phase0_and_phase1_contract_first_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_contract_first_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_contract_first_closeout_packet().to_dict()


def build_search_offline_objective_regime_contract_first_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_contract_first_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_contract_first_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }



def _budget_conservative_labels() -> tuple[str, ...]:
    return (
        'budget_conservative.llm_call_ceiling_priority.v1',
        'budget_conservative.evaluator_call_sparing.v1',
        'budget_conservative.closeout_margin_preserve.v1',
    )


def build_search_offline_objective_regime_budget_conservative_matrix_packet() -> SearchOfflineObjectiveRegimeMatrixPacket:
    return SearchOfflineObjectiveRegimeMatrixPacket(
        packet_id='search.platform.phase11.offline_objective_regime_budget_conservative_matrix.v1',
        baseline_convergence_matrix_id=BASELINE_CONVERGENCE_MATRIX_ID,
        baseline_convergence_closeout_id=BASELINE_CONVERGENCE_CLOSEOUT_ID,
        baseline_stochasticity_closeout_id=BASELINE_STOCHASTICITY_CLOSEOUT_ID,
        objective_regime_family='budget_conservative',
        objective_regime_labels=_budget_conservative_labels(),
        inherited_source_packet_ids=INHERITED_SOURCE_PACKET_IDS,
        inherited_budget_cell_ids=INHERITED_BUDGET_CELL_IDS,
        compared_consumers=COMPARED_CONSUMERS,
        primary_objectives=(
            'budget_margin_preservation_first',
            'evaluator_sparing_second',
            'contract_stability_third',
            'export_density_only_after_budget_and_contract_preservation',
        ),
        matrix_checks=(
            'contract_first_branch_frozen_before_budget_conservative_branch',
            'phase9_baseline_surface_still_frozen',
            'phase10_stochasticity_closeout_still_frozen',
            'source_set_fixed_during_budget_conservative_branch',
            'budget_cells_fixed_during_budget_conservative_branch',
            'no_live_or_provider_dependency_imported',
        ),
        false_positive_conditions=(
            'mere_budget_cell_match_without_consumer_split',
            'reduced_export_density_without_closeout_difference',
            'unchanged_contract_stability_under_budget_shift',
        ),
        final_decision='execute_budget_conservative_regime_before_export_density_first_or_replay_parity_first',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_budget_conservative_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_budget_conservative_matrix_packet().to_dict()


def build_search_offline_objective_regime_budget_conservative_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_budget_conservative_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_budget_conservative_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_budget_conservative_divergence_ledger_packet() -> SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    matrix = build_search_offline_objective_regime_budget_conservative_matrix_packet()
    return SearchOfflineObjectiveRegimeDivergenceLedgerPacket(
        packet_id='search.platform.phase11.offline_objective_regime_budget_conservative_divergence_ledger.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_family=matrix.objective_regime_family,
        objective_regime_labels=matrix.objective_regime_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        divergence_rows=(
            'budget_margin_priority_did_not_force_optimize_rl_split',
            'evaluator_sparing_priority_did_not_force_optimize_rl_split',
            'closeout_margin_priority_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_disagreement_detected_under_budget_conservative_regime',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two budget-conservative regime labels and more than one budget cell',
        final_decision='keep_budget_conservative_regime_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_budget_conservative_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_budget_conservative_divergence_ledger_packet().to_dict()


def build_search_offline_objective_regime_budget_conservative_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_budget_conservative_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_budget_conservative_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_budget_conservative_closeout_packet() -> SearchOfflineObjectiveRegimeCloseoutPacket:
    matrix = build_search_offline_objective_regime_budget_conservative_matrix_packet()
    ledger = build_search_offline_objective_regime_budget_conservative_divergence_ledger_packet()
    return SearchOfflineObjectiveRegimeCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_budget_conservative_closeout.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_divergence_ledger_id=ledger.packet_id,
        ready_rows=(
            'contract_first_branch_green',
            'budget_conservative_regime_family_green',
            'baseline_surface_freeze_green',
            'no_budget_conservative_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'export_density_first_regime_branch_deferred',
            'replay_parity_first_regime_branch_deferred',
        ),
        final_decision='close_phase2_budget_conservative_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_budget_conservative_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_budget_conservative_closeout_packet().to_dict()


def build_search_offline_objective_regime_budget_conservative_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_budget_conservative_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_budget_conservative_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }



def _export_density_first_labels() -> tuple[str, ...]:
    return (
        'export_density_first.replay_bundle_richness_priority.v1',
        'export_density_first.handoff_compaction_tolerant_density.v1',
        'export_density_first.export_surface_maximize_before_budget_margin.v1',
    )


def build_search_offline_objective_regime_export_density_first_matrix_packet() -> SearchOfflineObjectiveRegimeMatrixPacket:
    return SearchOfflineObjectiveRegimeMatrixPacket(
        packet_id='search.platform.phase11.offline_objective_regime_export_density_first_matrix.v1',
        baseline_convergence_matrix_id=BASELINE_CONVERGENCE_MATRIX_ID,
        baseline_convergence_closeout_id=BASELINE_CONVERGENCE_CLOSEOUT_ID,
        baseline_stochasticity_closeout_id=BASELINE_STOCHASTICITY_CLOSEOUT_ID,
        objective_regime_family='export_density_first',
        objective_regime_labels=_export_density_first_labels(),
        inherited_source_packet_ids=INHERITED_SOURCE_PACKET_IDS,
        inherited_budget_cell_ids=INHERITED_BUDGET_CELL_IDS,
        compared_consumers=COMPARED_CONSUMERS,
        primary_objectives=(
            'export_density_first',
            'replay_bundle_richness_second',
            'contract_stability_third',
            'budget_margin_only_after_export_and_contract_preservation',
        ),
        matrix_checks=(
            'budget_conservative_branch_frozen_before_export_density_first_branch',
            'phase9_baseline_surface_still_frozen',
            'phase10_stochasticity_closeout_still_frozen',
            'source_set_fixed_during_export_density_first_branch',
            'budget_cells_fixed_during_export_density_first_branch',
            'no_live_or_provider_dependency_imported',
        ),
        false_positive_conditions=(
            'mere_export_field_count_change_without_consumer_split',
            'denser_handoff_without_closeout_difference',
            'contract_stability_unchanged_under_export_density_shift',
        ),
        final_decision='execute_export_density_first_regime_before_replay_parity_first_or_closeout_decision_first',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_export_density_first_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_export_density_first_matrix_packet().to_dict()


def build_search_offline_objective_regime_export_density_first_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_export_density_first_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_export_density_first_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_export_density_first_divergence_ledger_packet() -> SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    matrix = build_search_offline_objective_regime_export_density_first_matrix_packet()
    return SearchOfflineObjectiveRegimeDivergenceLedgerPacket(
        packet_id='search.platform.phase11.offline_objective_regime_export_density_first_divergence_ledger.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_family=matrix.objective_regime_family,
        objective_regime_labels=matrix.objective_regime_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        divergence_rows=(
            'export_density_priority_did_not_force_optimize_rl_split',
            'replay_bundle_richness_priority_did_not_force_optimize_rl_split',
            'handoff_density_priority_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_disagreement_detected_under_export_density_first_regime',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two export-density-first regime labels and more than one budget cell',
        final_decision='keep_export_density_first_regime_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_export_density_first_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_export_density_first_divergence_ledger_packet().to_dict()


def build_search_offline_objective_regime_export_density_first_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_export_density_first_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_export_density_first_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_export_density_first_closeout_packet() -> SearchOfflineObjectiveRegimeCloseoutPacket:
    matrix = build_search_offline_objective_regime_export_density_first_matrix_packet()
    ledger = build_search_offline_objective_regime_export_density_first_divergence_ledger_packet()
    return SearchOfflineObjectiveRegimeCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_export_density_first_closeout.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_divergence_ledger_id=ledger.packet_id,
        ready_rows=(
            'contract_first_branch_green',
            'budget_conservative_branch_green',
            'export_density_first_regime_family_green',
            'no_export_density_first_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'replay_parity_first_regime_branch_deferred',
            'closeout_decision_first_regime_branch_deferred',
        ),
        final_decision='close_phase3_export_density_first_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_export_density_first_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_export_density_first_closeout_packet().to_dict()


def build_search_offline_objective_regime_export_density_first_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_export_density_first_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_export_density_first_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def _replay_parity_first_labels() -> tuple[str, ...]:
    return (
        'replay_parity_first.export_replay_symmetry_priority.v1',
        'replay_parity_first.artifact_preservation_before_density.v1',
        'replay_parity_first.closeout_symmetry_preserve.v1',
    )


def build_search_offline_objective_regime_replay_parity_first_matrix_packet() -> SearchOfflineObjectiveRegimeMatrixPacket:
    return SearchOfflineObjectiveRegimeMatrixPacket(
        packet_id='search.platform.phase11.offline_objective_regime_replay_parity_first_matrix.v1',
        baseline_convergence_matrix_id=BASELINE_CONVERGENCE_MATRIX_ID,
        baseline_convergence_closeout_id=BASELINE_CONVERGENCE_CLOSEOUT_ID,
        baseline_stochasticity_closeout_id=BASELINE_STOCHASTICITY_CLOSEOUT_ID,
        objective_regime_family='replay_parity_first',
        objective_regime_labels=_replay_parity_first_labels(),
        inherited_source_packet_ids=INHERITED_SOURCE_PACKET_IDS,
        inherited_budget_cell_ids=INHERITED_BUDGET_CELL_IDS,
        compared_consumers=COMPARED_CONSUMERS,
        primary_objectives=(
            'replay_export_parity_first',
            'artifact_preservation_second',
            'contract_stability_third',
            'density_gain_only_after_replay_symmetry_preservation',
        ),
        matrix_checks=(
            'export_density_first_branch_frozen_before_replay_parity_first_branch',
            'phase9_baseline_surface_still_frozen',
            'phase10_stochasticity_closeout_still_frozen',
            'source_set_fixed_during_replay_parity_first_branch',
            'budget_cells_fixed_during_replay_parity_first_branch',
            'no_live_or_provider_dependency_imported',
        ),
        false_positive_conditions=(
            'mere_replay_field_reordering_without_consumer_split',
            'parity_annotation_change_without_closeout_difference',
            'artifact_preservation_change_without_repeated_disagreement',
        ),
        final_decision='execute_replay_parity_first_regime_before_any_closeout_decision_first_branch',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_replay_parity_first_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_replay_parity_first_matrix_packet().to_dict()


def build_search_offline_objective_regime_replay_parity_first_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_replay_parity_first_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_replay_parity_first_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_replay_parity_first_divergence_ledger_packet() -> SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    matrix = build_search_offline_objective_regime_replay_parity_first_matrix_packet()
    return SearchOfflineObjectiveRegimeDivergenceLedgerPacket(
        packet_id='search.platform.phase11.offline_objective_regime_replay_parity_first_divergence_ledger.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_family=matrix.objective_regime_family,
        objective_regime_labels=matrix.objective_regime_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        divergence_rows=(
            'replay_export_parity_priority_did_not_force_optimize_rl_split',
            'artifact_preservation_priority_did_not_force_optimize_rl_split',
            'closeout_symmetry_priority_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_disagreement_detected_under_replay_parity_first_regime',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two replay-parity-first regime labels and more than one budget cell',
        final_decision='keep_replay_parity_first_regime_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_replay_parity_first_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_replay_parity_first_divergence_ledger_packet().to_dict()


def build_search_offline_objective_regime_replay_parity_first_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_replay_parity_first_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_replay_parity_first_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_replay_parity_first_closeout_packet() -> SearchOfflineObjectiveRegimeCloseoutPacket:
    matrix = build_search_offline_objective_regime_replay_parity_first_matrix_packet()
    ledger = build_search_offline_objective_regime_replay_parity_first_divergence_ledger_packet()
    return SearchOfflineObjectiveRegimeCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_replay_parity_first_closeout.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_divergence_ledger_id=ledger.packet_id,
        ready_rows=(
            'contract_first_branch_green',
            'budget_conservative_branch_green',
            'export_density_first_branch_green',
            'replay_parity_first_regime_family_green',
            'no_replay_parity_first_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(
            'closeout_decision_first_regime_branch_deferred',
        ),
        final_decision='close_phase4_replay_parity_first_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_replay_parity_first_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_replay_parity_first_closeout_packet().to_dict()


def build_search_offline_objective_regime_replay_parity_first_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_replay_parity_first_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_replay_parity_first_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }



def _closeout_decision_first_labels() -> tuple[str, ...]:
    return (
        'closeout_decision_first.final_selection_stability_priority.v1',
        'closeout_decision_first.intermediate_richness_penalty.v1',
        'closeout_decision_first.conservative_termination_symmetry.v1',
    )


def build_search_offline_objective_regime_closeout_decision_first_matrix_packet() -> SearchOfflineObjectiveRegimeMatrixPacket:
    return SearchOfflineObjectiveRegimeMatrixPacket(
        packet_id='search.platform.phase11.offline_objective_regime_closeout_decision_first_matrix.v1',
        baseline_convergence_matrix_id=BASELINE_CONVERGENCE_MATRIX_ID,
        baseline_convergence_closeout_id=BASELINE_CONVERGENCE_CLOSEOUT_ID,
        baseline_stochasticity_closeout_id=BASELINE_STOCHASTICITY_CLOSEOUT_ID,
        objective_regime_family='closeout_decision_first',
        objective_regime_labels=_closeout_decision_first_labels(),
        inherited_source_packet_ids=INHERITED_SOURCE_PACKET_IDS,
        inherited_budget_cell_ids=INHERITED_BUDGET_CELL_IDS,
        compared_consumers=COMPARED_CONSUMERS,
        primary_objectives=(
            'closeout_decision_stability_first',
            'termination_symmetry_second',
            'contract_stability_third',
            'intermediate_richness_only_after_closeout_consistency',
        ),
        matrix_checks=(
            'replay_parity_first_branch_frozen_before_closeout_decision_first_branch',
            'phase9_baseline_surface_still_frozen',
            'phase10_stochasticity_closeout_still_frozen',
            'source_set_fixed_during_closeout_decision_first_branch',
            'budget_cells_fixed_during_closeout_decision_first_branch',
            'no_live_or_provider_dependency_imported',
        ),
        false_positive_conditions=(
            'mere_closeout_summary_relabel_without_consumer_split',
            'intermediate_richness_drop_without_final_decision_difference',
            'termination_annotation_change_without_repeated_disagreement',
        ),
        final_decision='execute_closeout_decision_first_as_final_phase11_regime_family',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_closeout_decision_first_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_closeout_decision_first_matrix_packet().to_dict()


def build_search_offline_objective_regime_closeout_decision_first_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_closeout_decision_first_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_closeout_decision_first_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_packet() -> SearchOfflineObjectiveRegimeDivergenceLedgerPacket:
    matrix = build_search_offline_objective_regime_closeout_decision_first_matrix_packet()
    return SearchOfflineObjectiveRegimeDivergenceLedgerPacket(
        packet_id='search.platform.phase11.offline_objective_regime_closeout_decision_first_divergence_ledger.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_family=matrix.objective_regime_family,
        objective_regime_labels=matrix.objective_regime_labels,
        compared_budget_cell_ids=matrix.inherited_budget_cell_ids,
        divergence_rows=(
            'final_selection_stability_priority_did_not_force_optimize_rl_split',
            'intermediate_richness_penalty_did_not_force_optimize_rl_split',
            'termination_symmetry_priority_did_not_force_optimize_rl_split',
            'no_threshold_satisfying_repeated_disagreement_detected_under_closeout_decision_first_regime',
        ),
        repeated_run_rule='only escalate if the same disagreement survives at least two closeout-decision-first regime labels and more than one budget cell',
        final_decision='keep_closeout_decision_first_regime_below_planner_threshold',
        dominant_locus='consumer_local',
    )


def build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_packet().to_dict()


def build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_closeout_decision_first_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_closeout_decision_first_closeout_packet() -> SearchOfflineObjectiveRegimeCloseoutPacket:
    matrix = build_search_offline_objective_regime_closeout_decision_first_matrix_packet()
    ledger = build_search_offline_objective_regime_closeout_decision_first_divergence_ledger_packet()
    return SearchOfflineObjectiveRegimeCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_closeout_decision_first_closeout.v1',
        objective_regime_matrix_id=matrix.packet_id,
        objective_regime_divergence_ledger_id=ledger.packet_id,
        ready_rows=(
            'contract_first_branch_green',
            'budget_conservative_branch_green',
            'export_density_first_branch_green',
            'replay_parity_first_branch_green',
            'closeout_decision_first_regime_family_green',
            'no_closeout_decision_first_branch_planner_trigger',
        ),
        remaining_follow_on_rows=(),
        final_decision='close_phase5_closeout_decision_first_branch_without_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_closeout_decision_first_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_closeout_decision_first_closeout_packet().to_dict()


def build_search_offline_objective_regime_closeout_decision_first_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_closeout_decision_first_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_closeout_decision_first_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_forensic_read_packet() -> SearchOfflineObjectiveRegimeForensicReadPacket:
    return SearchOfflineObjectiveRegimeForensicReadPacket(
        packet_id='search.platform.phase11.offline_objective_regime_forensic_read.v1',
        compared_regime_families=(
            'contract_first',
            'budget_conservative',
            'export_density_first',
            'replay_parity_first',
            'closeout_decision_first',
        ),
        allowed_moving_fields=(
            'objective_regime_family',
            'objective_regime_labels',
            'primary_objectives',
            'false_positive_conditions',
            'divergence_rows',
            'ready_rows',
            'remaining_follow_on_rows',
        ),
        protected_fields=(
            'baseline_convergence_matrix_id',
            'baseline_convergence_closeout_id',
            'baseline_stochasticity_closeout_id',
            'inherited_source_packet_ids',
            'inherited_budget_cell_ids',
            'compared_consumers',
        ),
        forensic_rows=(
            'baseline_ids_stable_across_all_regime_families',
            'source_packet_ids_stable_across_all_regime_families',
            'budget_cell_ids_stable_across_all_regime_families',
            'consumer_pair_stable_across_all_regime_families',
            'regime_metadata_changes_do_not_by_themselves_count_as_divergence',
            'no_packet_format_drift_detected_across_phase11_regime_families',
        ),
        final_decision='treat_phase11_regime_movement_as_forensically_separated_from_packet_drift',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_forensic_read_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_forensic_read_packet().to_dict()


def build_search_offline_objective_regime_forensic_read_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_forensic_read_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_forensic_read.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_program_closeout_packet() -> SearchOfflineObjectiveRegimeProgramCloseoutPacket:
    forensic = build_search_offline_objective_regime_forensic_read_packet()
    return SearchOfflineObjectiveRegimeProgramCloseoutPacket(
        packet_id='search.platform.phase11.offline_objective_regime_program_closeout.v1',
        completed_regime_families=(
            'contract_first',
            'budget_conservative',
            'export_density_first',
            'replay_parity_first',
            'closeout_decision_first',
        ),
        forensic_read_id=forensic.packet_id,
        ready_rows=(
            'all_phase11_regime_families_green',
            'forensic_read_green',
            'no_threshold_satisfying_repeated_optimize_rl_split',
            'no_phase11_planner_trigger',
        ),
        remaining_follow_on_rows=(),
        final_decision='close_phase11_without_planner_round_and_without_dag_architecture_change',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_program_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_program_closeout_packet().to_dict()


def build_search_offline_objective_regime_program_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_program_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_program_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }


def build_search_offline_objective_regime_next_locus_packet() -> SearchOfflineObjectiveRegimeNextLocusPacket:
    closeout = build_search_offline_objective_regime_program_closeout_packet()
    return SearchOfflineObjectiveRegimeNextLocusPacket(
        packet_id='search.platform.phase11.offline_objective_regime_next_locus.v1',
        program_closeout_id=closeout.packet_id,
        recommended_loci=(
            'consumer_local',
            'platform_local',
        ),
        disfavored_loci=(
            'dag_local',
            'planner_local',
            'live_provider_local',
        ),
        planner_trigger_state='not_triggered',
        final_recommendation='stop_phase11_here_or_open_only_a_bounded_consumer_or_platform_repair_tranche',
        dominant_locus='platform_local',
    )


def build_search_offline_objective_regime_next_locus_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_objective_regime_next_locus_packet().to_dict()


def build_search_offline_objective_regime_next_locus_payload() -> Dict[str, object]:
    packet = build_search_offline_objective_regime_next_locus_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_objective_regime_next_locus.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase11_offline_objective_regime',
    }
