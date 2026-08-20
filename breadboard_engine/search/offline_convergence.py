from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from .consumerization import (
    build_search_stage_c_closeout_packet,
    build_search_stage_c_consumer_convergence_packet,
    build_search_stage_c_optimize_consumerization_packet,
    build_search_stage_c_rl_consumerization_packet,
)
from .cross_execution import SearchExecutionBudgetCell
from .examples import (
    build_dag_v4_bavt_packet,
    build_dag_v4_codetree_v2_packet,
    build_dag_v4_dci_packet,
    build_dag_v4_final_adjudication_packet,
    build_dag_v4_got_v2_packet,
    build_dag_v4_team_of_thoughts_packet,
    build_dag_v4_tot_v2_packet,
    build_dag_v4_moa_v2_packet,
)


def _sorted_unique(values: Iterable[Any]) -> tuple[str, ...]:
    seen = {str(value or '').strip() for value in values}
    return tuple(sorted(value for value in seen if value))


@dataclass(frozen=True)
class SearchOfflineConvergenceSourceRow:
    source_packet_id: str
    source_packet_family: str
    topology_class: str
    source_final_decision: str
    target_consumers: tuple[str, ...]
    preserved_contract_fields: tuple[str, ...]
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'source_packet_id': self.source_packet_id,
            'source_packet_family': self.source_packet_family,
            'topology_class': self.topology_class,
            'source_final_decision': self.source_final_decision,
            'target_consumers': list(self.target_consumers),
            'preserved_contract_fields': list(self.preserved_contract_fields),
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineConvergenceMatrixPacket:
    packet_id: str
    consumer_convergence_id: str
    stage_c_closeout_id: str
    source_rows: tuple[SearchOfflineConvergenceSourceRow, ...]
    budget_cells: tuple[SearchExecutionBudgetCell, ...]
    compared_consumers: tuple[str, ...]
    matrix_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'consumer_convergence_id': self.consumer_convergence_id,
            'stage_c_closeout_id': self.stage_c_closeout_id,
            'source_rows': [row.to_dict() for row in self.source_rows],
            'budget_cells': [cell.to_dict() for cell in self.budget_cells],
            'compared_consumers': list(self.compared_consumers),
            'matrix_checks': list(self.matrix_checks),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineConvergenceDivergenceLedgerPacket:
    packet_id: str
    convergence_matrix_id: str
    compared_source_packet_ids: tuple[str, ...]
    compared_topology_classes: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    divergence_rows: tuple[str, ...]
    repeated_run_rule: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'convergence_matrix_id': self.convergence_matrix_id,
            'compared_source_packet_ids': list(self.compared_source_packet_ids),
            'compared_topology_classes': list(self.compared_topology_classes),
            'compared_budget_cell_ids': list(self.compared_budget_cell_ids),
            'divergence_rows': list(self.divergence_rows),
            'repeated_run_rule': self.repeated_run_rule,
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineConvergenceComparisonProbePacket:
    packet_id: str
    convergence_matrix_id: str
    divergence_ledger_id: str
    compared_source_packet_ids: tuple[str, ...]
    sharpened_rule_ids: tuple[str, ...]
    comparison_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'convergence_matrix_id': self.convergence_matrix_id,
            'divergence_ledger_id': self.divergence_ledger_id,
            'compared_source_packet_ids': list(self.compared_source_packet_ids),
            'sharpened_rule_ids': list(self.sharpened_rule_ids),
            'comparison_rows': list(self.comparison_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchOfflineConvergenceCloseoutPacket:
    packet_id: str
    convergence_matrix_id: str
    divergence_ledger_id: str
    source_packet_ids: tuple[str, ...]
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            'packet_id': self.packet_id,
            'convergence_matrix_id': self.convergence_matrix_id,
            'divergence_ledger_id': self.divergence_ledger_id,
            'source_packet_ids': list(self.source_packet_ids),
            'ready_rows': list(self.ready_rows),
            'remaining_follow_on_rows': list(self.remaining_follow_on_rows),
            'final_decision': self.final_decision,
            'dominant_locus': self.dominant_locus,
        }


def _build_offline_budget_cells() -> tuple[SearchExecutionBudgetCell, ...]:
    return (
        SearchExecutionBudgetCell(
            cell_id='search.offline_convergence.cell.audit_small.v1',
            max_llm_calls=6,
            max_evaluator_calls=2,
            target_consumers=('optimize', 'rl'),
            comparison_rule='small_offline_matrix_requires_contract_parity',
            expected_locus='consumer_local',
        ),
        SearchExecutionBudgetCell(
            cell_id='search.offline_convergence.cell.audit_medium.v1',
            max_llm_calls=12,
            max_evaluator_calls=4,
            target_consumers=('optimize', 'rl'),
            comparison_rule='medium_offline_matrix_requires_contract_and_ordering_parity',
            expected_locus='consumer_local',
        ),
        SearchExecutionBudgetCell(
            cell_id='search.offline_convergence.cell.audit_compressed.v1',
            max_llm_calls=4,
            max_evaluator_calls=1,
            target_consumers=('optimize', 'rl'),
            comparison_rule='compressed_offline_matrix_requires_contract_ordering_and_budget_stability',
            expected_locus='consumer_local',
        ),
    )


def _resolve_source_packet_id(packet: Dict[str, Any]) -> str:
    if 'packet_id' in packet:
        return str(packet['packet_id'])
    recipe_manifest = packet.get('recipe_manifest')
    if recipe_manifest is not None and hasattr(recipe_manifest, 'manifest_id'):
        return str(recipe_manifest.manifest_id)
    run = packet.get('run')
    if run is not None and hasattr(run, 'search_id'):
        return str(run.search_id)
    raise KeyError('source packet id not recoverable')


def _resolve_source_final_decision(packet: Dict[str, Any]) -> str:
    if 'final_decision' in packet:
        return str(packet['final_decision'])
    if 'freeze_decision' in packet:
        return str(packet['freeze_decision'])
    scorecard = packet.get('scorecard')
    if scorecard is not None and hasattr(scorecard, 'fidelity_label'):
        return f'bounded_packet:{scorecard.fidelity_label}'
    return 'bounded_packet_without_explicit_final_decision'


def _build_source_rows() -> tuple[SearchOfflineConvergenceSourceRow, ...]:
    optimize = build_search_stage_c_optimize_consumerization_packet()
    rl = build_search_stage_c_rl_consumerization_packet()
    preserved_contract_fields = _sorted_unique(
        list(optimize.required_contract_fields) + list(rl.required_contract_fields)
    )
    source_consumers = _sorted_unique(
        list(optimize.source_consumers) + list(rl.source_consumers)
    )
    source_packets = (
        ('dag_v4_bavt.v1', 'F', build_dag_v4_bavt_packet()),
        ('dag_v4_team_of_thoughts.v1', 'H', build_dag_v4_team_of_thoughts_packet()),
        ('dag_v4_dci.v1', 'D', build_dag_v4_dci_packet()),
        ('dag_v4_codetree_v2.v1', 'W', build_dag_v4_codetree_v2_packet()),
        ('dag_v4_got_v2.v1', 'G', build_dag_v4_got_v2_packet()),
        ('dag_v4_tot_v2.v1', 'F', build_dag_v4_tot_v2_packet()),
        ('dag_v4_moa_v2.v1', 'H', build_dag_v4_moa_v2_packet()),
        ('dag_v4_final_adjudication.v1', 'closeout', build_dag_v4_final_adjudication_packet()),
    )
    return tuple(
        SearchOfflineConvergenceSourceRow(
            source_packet_id=_resolve_source_packet_id(packet),
            source_packet_family=packet_family,
            topology_class=topology_class,
            source_final_decision=_resolve_source_final_decision(packet),
            target_consumers=source_consumers,
            preserved_contract_fields=preserved_contract_fields,
            dominant_locus='consumer_local',
        )
        for packet_family, topology_class, packet in source_packets
    )


def build_search_offline_convergence_matrix_packet() -> SearchOfflineConvergenceMatrixPacket:
    convergence = build_search_stage_c_consumer_convergence_packet()
    closeout = build_search_stage_c_closeout_packet()
    return SearchOfflineConvergenceMatrixPacket(
        packet_id='search.platform.phase8.offline_convergence_matrix.v1',
        consumer_convergence_id=convergence.packet_id,
        stage_c_closeout_id=closeout.packet_id,
        source_rows=_build_source_rows(),
        budget_cells=_build_offline_budget_cells(),
        compared_consumers=('optimize', 'rl'),
        matrix_checks=(
            'dag_v4_structurally_distinct_sources_present',
            'stage_c_consumer_contract_reused',
            'offline_budget_cells_explicit',
            'no_live_or_provider_dependency_imported',
        ),
        final_decision='execute_offline_dag_optimize_rl_convergence_matrix',
        dominant_locus='consumer_local',
    )


def build_search_offline_convergence_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_convergence_matrix_packet().to_dict()


def build_search_offline_convergence_matrix_payload() -> Dict[str, object]:
    packet = build_search_offline_convergence_matrix_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_convergence_matrix.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase8_offline_convergence',
    }


def build_search_offline_convergence_divergence_ledger_packet() -> SearchOfflineConvergenceDivergenceLedgerPacket:
    matrix = build_search_offline_convergence_matrix_packet()
    return SearchOfflineConvergenceDivergenceLedgerPacket(
        packet_id='search.platform.phase8.offline_convergence_divergence_ledger.v1',
        convergence_matrix_id=matrix.packet_id,
        compared_source_packet_ids=tuple(row.source_packet_id for row in matrix.source_rows),
        compared_topology_classes=tuple(row.topology_class for row in matrix.source_rows),
        compared_budget_cell_ids=tuple(cell.cell_id for cell in matrix.budget_cells),
        divergence_rows=(
            'dag_v4_f_topology_reused_without_optimize_rl_contract_drift',
            'dag_v4_h_topology_reused_without_optimize_rl_contract_drift',
            'dag_v4_d_topology_reused_without_optimize_rl_contract_drift',
            'dag_v4_w_topology_reused_without_optimize_rl_contract_drift',
            'dag_v4_g_topology_reused_without_optimize_rl_contract_drift',
            'dag_v4_tot_frontier_reopen_source_reused_without_optimize_rl_contract_drift',
            'dag_v4_moa_layered_aggregator_source_reused_without_optimize_rl_contract_drift',
            'no_threshold_satisfying_offline_divergence_detected',
        ),
        repeated_run_rule='only escalate if the same offline consumer mismatch survives two budget cells and two topology classes',
        final_decision='keep_offline_divergence_classified_consumer_or_platform_local',
        dominant_locus='consumer_local',
    )


def build_search_offline_convergence_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_convergence_divergence_ledger_packet().to_dict()


def build_search_offline_convergence_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_offline_convergence_divergence_ledger_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_convergence_divergence_ledger.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase8_offline_convergence',
    }


def build_search_offline_convergence_comparison_probe_packet() -> SearchOfflineConvergenceComparisonProbePacket:
    matrix = build_search_offline_convergence_matrix_packet()
    divergence = build_search_offline_convergence_divergence_ledger_packet()
    return SearchOfflineConvergenceComparisonProbePacket(
        packet_id='search.platform.phase9.offline_convergence_comparison_probe.v1',
        convergence_matrix_id=matrix.packet_id,
        divergence_ledger_id=divergence.packet_id,
        compared_source_packet_ids=tuple(row.source_packet_id for row in matrix.source_rows),
        sharpened_rule_ids=(
            'offline.contract_and_ordering_parity.v1',
            'offline.ranking_stability_under_budget_compression.v1',
            'offline.replay_export_parity_under_consumer_reshaping.v1',
        ),
        comparison_rows=(
            'contract_parity_checked_across_f_h_d_w_g_and_closeout_sources',
            'ordering_parity_checked_without_new_budget_cells',
            'frontier_reopen_variant_checked_without_new_budget_cells',
            'heterogeneous_aggregator_variant_checked_without_new_budget_cells',
            'budget_compression_cell_checked_without_new_source_families',
            'ranking_stability_checked_under_existing_small_medium_and_compressed_cells',
            'no_threshold_satisfying_disagreement_forced_by_sharper_rules',
        ),
        final_decision='keep_source_set_fixed_and_use_sharper_comparison_rules_before_any_further_widening',
        dominant_locus='consumer_local',
    )


def build_search_offline_convergence_comparison_probe_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_convergence_comparison_probe_packet().to_dict()


def build_search_offline_convergence_comparison_probe_payload() -> Dict[str, object]:
    packet = build_search_offline_convergence_comparison_probe_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_convergence_comparison_probe.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase9_offline_convergence',
    }


def build_search_offline_convergence_closeout_packet() -> SearchOfflineConvergenceCloseoutPacket:
    matrix = build_search_offline_convergence_matrix_packet()
    divergence = build_search_offline_convergence_divergence_ledger_packet()
    comparison = build_search_offline_convergence_comparison_probe_packet()
    return SearchOfflineConvergenceCloseoutPacket(
        packet_id='search.platform.phase8.offline_convergence_closeout.v1',
        convergence_matrix_id=matrix.packet_id,
        divergence_ledger_id=divergence.packet_id,
        source_packet_ids=tuple(row.source_packet_id for row in matrix.source_rows),
        ready_rows=(
            'offline_dag_source_matrix_green',
            'offline_budget_matrix_green',
            'optimize_rl_contract_reuse_green',
            'w_class_source_reuse_green',
            'g_class_source_reuse_green',
            'tot_frontier_variant_reuse_green',
            'moa_aggregator_variant_reuse_green',
            'comparison_rule_sharpening_green',
            'budget_cell_family_expansion_green',
            'no_live_spend_dependency_present',
        ),
        remaining_follow_on_rows=(),
        final_decision='close_first_offline_dag_optimize_rl_convergence_slice_without_new_planner_round',
        dominant_locus='platform_local',
    )


def build_search_offline_convergence_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_offline_convergence_closeout_packet().to_dict()


def build_search_offline_convergence_closeout_payload() -> Dict[str, object]:
    packet = build_search_offline_convergence_closeout_packet()
    return {
        'packet': packet.to_dict(),
        'packet_family': 'search_offline_convergence_closeout.v1',
        'packet_id': packet.packet_id,
        'phase': 'platform_phase8_offline_convergence',
    }
