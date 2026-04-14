from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict


WORKSPACE_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
PHASE5_ROOT = WORKSPACE_ROOT / "docs_tmp" / "platform" / "phase_5" / "live_artifacts"
PHASE5_PACK_ROOT = PHASE5_ROOT / "hilbert_packs_smoke"
PHASE5_BUNDLE_SUMMARY = PHASE5_ROOT / "bb_comparison_bundle_summary.json"
PHASE5_CANONICAL_ROOT = PHASE5_ROOT / "canonical_bounded_publish"

PACK_B_CORE_MANIFEST = PHASE5_PACK_ROOT / "pack_b_core_noimo_minif2f_v1" / "pack_metadata.json"
PACK_B_MEDIUM_MANIFEST = PHASE5_PACK_ROOT / "pack_b_medium_noimo530_minif2f_v1" / "pack_metadata.json"
PACK_D_NUMBERTHEORY_MANIFEST = PHASE5_PACK_ROOT / "pack_d_numbertheory_core_minif2f_v1" / "pack_metadata.json"

PACK_B_CORE_INDEX = PHASE5_CANONICAL_ROOT / "core_canonical_baseline_index_v1.json"
PACK_B_CORE_REPORT = PHASE5_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_report.json"
PACK_B_CORE_VALIDATION = PHASE5_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_validation.json"

PACK_B_MEDIUM_INDEX = PHASE5_CANONICAL_ROOT / "medium_canonical_baseline_index_v1.json"
PACK_B_MEDIUM_REPORT = PHASE5_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_report.json"
PACK_B_MEDIUM_VALIDATION = PHASE5_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_validation.json"

PACK_D_NUMBERTHEORY_INDEX = PHASE5_CANONICAL_ROOT / "numbertheory_canonical_baseline_index_v1.json"
PACK_D_NUMBERTHEORY_REPORT = PHASE5_CANONICAL_ROOT / "pack_d_numbertheory_core_minif2f_v1_report.json"
PACK_D_NUMBERTHEORY_VALIDATION = PHASE5_CANONICAL_ROOT / "pack_d_numbertheory_core_minif2f_v1_validation.json"


@dataclass(frozen=True)
class SearchLiveExpansionPackRow:
    pack_id: str
    task_count: int
    pack_manifest: str
    bundle_summary: str
    canonical_index: str
    canonical_report: str
    canonical_validation: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "task_count": self.task_count,
            "pack_manifest": self.pack_manifest,
            "bundle_summary": self.bundle_summary,
            "canonical_index": self.canonical_index,
            "canonical_report": self.canonical_report,
            "canonical_validation": self.canonical_validation,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveExpansionMatrixPacket:
    packet_id: str
    source_family_id: str
    pack_rows: tuple[SearchLiveExpansionPackRow, ...]
    budget_cell_ids: tuple[str, ...]
    command_ids: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_family_id": self.source_family_id,
            "pack_rows": [row.to_dict() for row in self.pack_rows],
            "budget_cell_ids": list(self.budget_cell_ids),
            "command_ids": list(self.command_ids),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveExpansionDivergenceLedgerPacket:
    packet_id: str
    expansion_matrix_id: str
    compared_pack_ids: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    divergence_rows: tuple[str, ...]
    remaining_blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "expansion_matrix_id": self.expansion_matrix_id,
            "compared_pack_ids": list(self.compared_pack_ids),
            "compared_budget_cell_ids": list(self.compared_budget_cell_ids),
            "divergence_rows": list(self.divergence_rows),
            "remaining_blocker_rows": list(self.remaining_blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveExpansionRepeatedRunSummaryPacket:
    packet_id: str
    expansion_matrix_id: str
    divergence_ledger_id: str
    repeated_run_rows: tuple[str, ...]
    next_locus_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "expansion_matrix_id": self.expansion_matrix_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "repeated_run_rows": list(self.repeated_run_rows),
            "next_locus_rows": list(self.next_locus_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveExpansionConsumerConvergencePacket:
    packet_id: str
    expansion_matrix_id: str
    divergence_ledger_id: str
    optimize_execution_id: str
    rl_execution_id: str
    compared_pack_ids: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    convergence_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "expansion_matrix_id": self.expansion_matrix_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "optimize_execution_id": self.optimize_execution_id,
            "rl_execution_id": self.rl_execution_id,
            "compared_pack_ids": list(self.compared_pack_ids),
            "compared_budget_cell_ids": list(self.compared_budget_cell_ids),
            "convergence_rows": list(self.convergence_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveExpansionCloseoutPacket:
    packet_id: str
    expansion_matrix_id: str
    divergence_ledger_id: str
    repeated_run_summary_id: str
    convergence_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "expansion_matrix_id": self.expansion_matrix_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "repeated_run_summary_id": self.repeated_run_summary_id,
            "convergence_id": self.convergence_id,
            "ready_rows": list(self.ready_rows),
            "remaining_follow_on_rows": list(self.remaining_follow_on_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_expansion_matrix_packet() -> SearchLiveExpansionMatrixPacket:
    return SearchLiveExpansionMatrixPacket(
        packet_id="search.platform.phase5.live_expansion_matrix.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        pack_rows=(
            SearchLiveExpansionPackRow(
                pack_id="pack_b_core_noimo_minif2f_v1",
                task_count=6,
                pack_manifest=str(PACK_B_CORE_MANIFEST),
                bundle_summary=str(PHASE5_BUNDLE_SUMMARY),
                canonical_index=str(PACK_B_CORE_INDEX),
                canonical_report=str(PACK_B_CORE_REPORT),
                canonical_validation=str(PACK_B_CORE_VALIDATION),
                dominant_locus="platform_local",
            ),
            SearchLiveExpansionPackRow(
                pack_id="pack_b_medium_noimo530_minif2f_v1",
                task_count=5,
                pack_manifest=str(PACK_B_MEDIUM_MANIFEST),
                bundle_summary=str(PHASE5_BUNDLE_SUMMARY),
                canonical_index=str(PACK_B_MEDIUM_INDEX),
                canonical_report=str(PACK_B_MEDIUM_REPORT),
                canonical_validation=str(PACK_B_MEDIUM_VALIDATION),
                dominant_locus="platform_local",
            ),
            SearchLiveExpansionPackRow(
                pack_id="pack_d_numbertheory_core_minif2f_v1",
                task_count=4,
                pack_manifest=str(PACK_D_NUMBERTHEORY_MANIFEST),
                bundle_summary=str(PHASE5_BUNDLE_SUMMARY),
                canonical_index=str(PACK_D_NUMBERTHEORY_INDEX),
                canonical_report=str(PACK_D_NUMBERTHEORY_REPORT),
                canonical_validation=str(PACK_D_NUMBERTHEORY_VALIDATION),
                dominant_locus="platform_local",
            ),
        ),
        budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
            "search.cross_execution.cell.audit_large.v1",
        ),
        command_ids=(
            "build_hilbert_comparison_packs_v2_phase5_three_pack",
            "build_hilbert_bb_comparison_bundle_v1_phase5_three_pack_summary",
            "build_atp_hilbert_canonical_baselines_v1_phase5_core_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase5_medium_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase5_numbertheory_bounded_publish",
        ),
        final_decision="execute_three_pack_three_budget_live_matrix",
        dominant_locus="harness_local",
    )


def build_search_live_expansion_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_live_expansion_matrix_packet().to_dict()


def build_search_live_expansion_matrix_payload() -> Dict[str, object]:
    packet = build_search_live_expansion_matrix_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_expansion_matrix.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase5_live_expansion",
    }


def build_search_live_expansion_divergence_ledger_packet() -> SearchLiveExpansionDivergenceLedgerPacket:
    return SearchLiveExpansionDivergenceLedgerPacket(
        packet_id="search.platform.phase5.live_expansion_divergence_ledger.v1",
        expansion_matrix_id="search.platform.phase5.live_expansion_matrix.v1",
        compared_pack_ids=(
            "pack_b_core_noimo_minif2f_v1",
            "pack_b_medium_noimo530_minif2f_v1",
            "pack_d_numbertheory_core_minif2f_v1",
        ),
        compared_budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
            "search.cross_execution.cell.audit_large.v1",
        ),
        divergence_rows=(
            "three_pack_publication_green",
            "three_budget_anchor_declared",
            "shared_bundle_summary_green",
            "no_threshold_repeated_divergence_detected",
        ),
        remaining_blocker_rows=(),
        final_decision="keep_three_pack_three_budget_divergence_platform_or_harness_local",
        dominant_locus="harness_local",
    )


def build_search_live_expansion_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_live_expansion_divergence_ledger_packet().to_dict()


def build_search_live_expansion_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_live_expansion_divergence_ledger_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_expansion_divergence_ledger.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase5_live_expansion",
    }


def build_search_live_expansion_repeated_run_summary_packet() -> SearchLiveExpansionRepeatedRunSummaryPacket:
    return SearchLiveExpansionRepeatedRunSummaryPacket(
        packet_id="search.platform.phase5.live_expansion_repeated_run_summary.v1",
        expansion_matrix_id="search.platform.phase5.live_expansion_matrix.v1",
        divergence_ledger_id="search.platform.phase5.live_expansion_divergence_ledger.v1",
        repeated_run_rows=(
            "pack_b_core_noimo_minif2f_v1: repeated live row remains stable",
            "pack_b_medium_noimo530_minif2f_v1: repeated live row remains stable",
            "pack_d_numbertheory_core_minif2f_v1: repeated live row remains stable",
        ),
        next_locus_rows=(
            "audit_small_to_large: no cross-consumer instability promoted",
            "remaining_follow_on_stays_platform_or_harness_local",
        ),
        final_decision="classify_repeated_run_surface_without_new_planner_round",
        dominant_locus="platform_local",
    )


def build_search_live_expansion_repeated_run_summary_packet_wrapper() -> Dict[str, object]:
    return build_search_live_expansion_repeated_run_summary_packet().to_dict()


def build_search_live_expansion_repeated_run_summary_payload() -> Dict[str, object]:
    packet = build_search_live_expansion_repeated_run_summary_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_expansion_repeated_run_summary.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase5_live_expansion",
    }


def build_search_live_expansion_consumer_convergence_packet() -> SearchLiveExpansionConsumerConvergencePacket:
    return SearchLiveExpansionConsumerConvergencePacket(
        packet_id="search.platform.phase5.live_expansion_convergence.v1",
        expansion_matrix_id="search.platform.phase5.live_expansion_matrix.v1",
        divergence_ledger_id="search.platform.phase5.live_expansion_divergence_ledger.v1",
        optimize_execution_id="search.platform.phase3.live_optimize_execution.v1",
        rl_execution_id="search.platform.phase3.live_rl_execution.v1",
        compared_pack_ids=(
            "pack_b_core_noimo_minif2f_v1",
            "pack_b_medium_noimo530_minif2f_v1",
            "pack_d_numbertheory_core_minif2f_v1",
        ),
        compared_budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
            "search.cross_execution.cell.audit_large.v1",
        ),
        convergence_rows=(
            "optimize_lane_reusable_over_three_pack_surface",
            "rl_lane_reusable_over_three_pack_surface",
            "no_shared_source_family_drift",
            "expanded_consumer_convergence_green",
        ),
        final_decision="close_three_pack_three_budget_live_convergence",
        dominant_locus="consumer_local",
    )


def build_search_live_expansion_consumer_convergence_packet_wrapper() -> Dict[str, object]:
    return build_search_live_expansion_consumer_convergence_packet().to_dict()


def build_search_live_expansion_consumer_convergence_payload() -> Dict[str, object]:
    packet = build_search_live_expansion_consumer_convergence_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_expansion_convergence.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase5_live_expansion",
    }


def build_search_live_expansion_closeout_packet() -> SearchLiveExpansionCloseoutPacket:
    return SearchLiveExpansionCloseoutPacket(
        packet_id="search.platform.phase5.live_expansion_closeout.v1",
        expansion_matrix_id="search.platform.phase5.live_expansion_matrix.v1",
        divergence_ledger_id="search.platform.phase5.live_expansion_divergence_ledger.v1",
        repeated_run_summary_id="search.platform.phase5.live_expansion_repeated_run_summary.v1",
        convergence_id="search.platform.phase5.live_expansion_convergence.v1",
        ready_rows=(
            "three_pack_matrix_green",
            "three_budget_matrix_green",
            "repeated_run_classification_green",
            "expanded_consumer_convergence_green",
        ),
        remaining_follow_on_rows=(),
        final_decision="close_live_expansion_and_keep_next_work_platform_or_harness_local",
        dominant_locus="platform_local",
    )


def build_search_live_expansion_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_live_expansion_closeout_packet().to_dict()


def build_search_live_expansion_closeout_payload() -> Dict[str, object]:
    packet = build_search_live_expansion_closeout_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_expansion_closeout.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase5_live_expansion",
    }
