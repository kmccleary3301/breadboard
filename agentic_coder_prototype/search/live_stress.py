from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict


WORKSPACE_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
PHASE6_ROOT = WORKSPACE_ROOT / "docs_tmp" / "platform" / "phase_6" / "live_artifacts"
PHASE6_PACK_ROOT = PHASE6_ROOT / "hilbert_packs_smoke"
PHASE6_BUNDLE_SUMMARY = PHASE6_ROOT / "bb_comparison_bundle_summary.json"
PHASE6_CANONICAL_ROOT = PHASE6_ROOT / "canonical_bounded_publish"

PACK_B_CORE_MANIFEST = PHASE6_PACK_ROOT / "pack_b_core_noimo_minif2f_v1" / "pack_metadata.json"
PACK_B_MEDIUM_MANIFEST = PHASE6_PACK_ROOT / "pack_b_medium_noimo530_minif2f_v1" / "pack_metadata.json"
PACK_D_NUMBERTHEORY_MANIFEST = PHASE6_PACK_ROOT / "pack_d_numbertheory_core_minif2f_v1" / "pack_metadata.json"
PACK_E_ALGEBRA_MANIFEST = PHASE6_PACK_ROOT / "pack_e_algebra_core_minif2f_v1" / "pack_metadata.json"

PACK_B_CORE_INDEX = PHASE6_CANONICAL_ROOT / "core_canonical_baseline_index_v1.json"
PACK_B_CORE_REPORT = PHASE6_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_report.json"
PACK_B_CORE_VALIDATION = PHASE6_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_validation.json"

PACK_B_MEDIUM_INDEX = PHASE6_CANONICAL_ROOT / "medium_canonical_baseline_index_v1.json"
PACK_B_MEDIUM_REPORT = PHASE6_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_report.json"
PACK_B_MEDIUM_VALIDATION = PHASE6_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_validation.json"

PACK_D_NUMBERTHEORY_INDEX = PHASE6_CANONICAL_ROOT / "numbertheory_canonical_baseline_index_v1.json"
PACK_D_NUMBERTHEORY_REPORT = PHASE6_CANONICAL_ROOT / "pack_d_numbertheory_core_minif2f_v1_report.json"
PACK_D_NUMBERTHEORY_VALIDATION = PHASE6_CANONICAL_ROOT / "pack_d_numbertheory_core_minif2f_v1_validation.json"

PACK_E_ALGEBRA_INDEX = PHASE6_CANONICAL_ROOT / "algebra_canonical_baseline_index_v1.json"
PACK_E_ALGEBRA_REPORT = PHASE6_CANONICAL_ROOT / "pack_e_algebra_core_minif2f_v1_report.json"
PACK_E_ALGEBRA_VALIDATION = PHASE6_CANONICAL_ROOT / "pack_e_algebra_core_minif2f_v1_validation.json"


@dataclass(frozen=True)
class SearchLiveStressPackRow:
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
class SearchLiveStressMatrixPacket:
    packet_id: str
    source_family_id: str
    pack_rows: tuple[SearchLiveStressPackRow, ...]
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
class SearchLiveStressDivergenceLedgerPacket:
    packet_id: str
    stress_matrix_id: str
    compared_pack_ids: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    divergence_rows: tuple[str, ...]
    remaining_blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stress_matrix_id": self.stress_matrix_id,
            "compared_pack_ids": list(self.compared_pack_ids),
            "compared_budget_cell_ids": list(self.compared_budget_cell_ids),
            "divergence_rows": list(self.divergence_rows),
            "remaining_blocker_rows": list(self.remaining_blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveStressRepeatedRunSummaryPacket:
    packet_id: str
    stress_matrix_id: str
    divergence_ledger_id: str
    repeated_run_rows: tuple[str, ...]
    next_locus_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stress_matrix_id": self.stress_matrix_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "repeated_run_rows": list(self.repeated_run_rows),
            "next_locus_rows": list(self.next_locus_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveStressConsumerConvergencePacket:
    packet_id: str
    stress_matrix_id: str
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
            "stress_matrix_id": self.stress_matrix_id,
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
class SearchLiveStressCloseoutPacket:
    packet_id: str
    stress_matrix_id: str
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
            "stress_matrix_id": self.stress_matrix_id,
            "divergence_ledger_id": self.divergence_ledger_id,
            "repeated_run_summary_id": self.repeated_run_summary_id,
            "convergence_id": self.convergence_id,
            "ready_rows": list(self.ready_rows),
            "remaining_follow_on_rows": list(self.remaining_follow_on_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_stress_matrix_packet() -> SearchLiveStressMatrixPacket:
    return SearchLiveStressMatrixPacket(
        packet_id="search.platform.phase6.live_stress_matrix.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        pack_rows=(
            SearchLiveStressPackRow("pack_b_core_noimo_minif2f_v1", 6, str(PACK_B_CORE_MANIFEST), str(PHASE6_BUNDLE_SUMMARY), str(PACK_B_CORE_INDEX), str(PACK_B_CORE_REPORT), str(PACK_B_CORE_VALIDATION), "platform_local"),
            SearchLiveStressPackRow("pack_b_medium_noimo530_minif2f_v1", 5, str(PACK_B_MEDIUM_MANIFEST), str(PHASE6_BUNDLE_SUMMARY), str(PACK_B_MEDIUM_INDEX), str(PACK_B_MEDIUM_REPORT), str(PACK_B_MEDIUM_VALIDATION), "platform_local"),
            SearchLiveStressPackRow("pack_d_numbertheory_core_minif2f_v1", 4, str(PACK_D_NUMBERTHEORY_MANIFEST), str(PHASE6_BUNDLE_SUMMARY), str(PACK_D_NUMBERTHEORY_INDEX), str(PACK_D_NUMBERTHEORY_REPORT), str(PACK_D_NUMBERTHEORY_VALIDATION), "platform_local"),
            SearchLiveStressPackRow("pack_e_algebra_core_minif2f_v1", 5, str(PACK_E_ALGEBRA_MANIFEST), str(PHASE6_BUNDLE_SUMMARY), str(PACK_E_ALGEBRA_INDEX), str(PACK_E_ALGEBRA_REPORT), str(PACK_E_ALGEBRA_VALIDATION), "platform_local"),
        ),
        budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
            "search.cross_execution.cell.audit_large.v1",
            "search.cross_execution.cell.audit_xlarge.v1",
        ),
        command_ids=(
            "build_hilbert_comparison_packs_v2_phase6_four_pack",
            "build_hilbert_bb_comparison_bundle_v1_phase6_four_pack_summary",
            "build_atp_hilbert_canonical_baselines_v1_phase6_core_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase6_medium_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase6_numbertheory_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase6_algebra_bounded_publish",
        ),
        final_decision="execute_four_pack_four_budget_live_stress_matrix",
        dominant_locus="harness_local",
    )


def build_search_live_stress_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_live_stress_matrix_packet().to_dict()


def build_search_live_stress_matrix_payload() -> Dict[str, object]:
    packet = build_search_live_stress_matrix_packet()
    return {"packet": packet.to_dict(), "packet_family": "search_live_stress_matrix.v1", "packet_id": packet.packet_id, "phase": "platform_phase6_live_stress"}


def build_search_live_stress_divergence_ledger_packet() -> SearchLiveStressDivergenceLedgerPacket:
    return SearchLiveStressDivergenceLedgerPacket(
        packet_id="search.platform.phase6.live_stress_divergence_ledger.v1",
        stress_matrix_id="search.platform.phase6.live_stress_matrix.v1",
        compared_pack_ids=("pack_b_core_noimo_minif2f_v1", "pack_b_medium_noimo530_minif2f_v1", "pack_d_numbertheory_core_minif2f_v1", "pack_e_algebra_core_minif2f_v1"),
        compared_budget_cell_ids=("search.cross_execution.cell.audit_small.v1", "search.cross_execution.cell.audit_medium.v1", "search.cross_execution.cell.audit_large.v1", "search.cross_execution.cell.audit_xlarge.v1"),
        divergence_rows=(
            "four_pack_publication_green",
            "four_budget_anchor_declared",
            "cross_domain_algebra_row_green",
            "no_threshold_repeated_divergence_detected",
        ),
        remaining_blocker_rows=(),
        final_decision="keep_four_pack_four_budget_divergence_platform_or_harness_local",
        dominant_locus="harness_local",
    )


def build_search_live_stress_divergence_ledger_packet_wrapper() -> Dict[str, object]:
    return build_search_live_stress_divergence_ledger_packet().to_dict()


def build_search_live_stress_divergence_ledger_payload() -> Dict[str, object]:
    packet = build_search_live_stress_divergence_ledger_packet()
    return {"packet": packet.to_dict(), "packet_family": "search_live_stress_divergence_ledger.v1", "packet_id": packet.packet_id, "phase": "platform_phase6_live_stress"}


def build_search_live_stress_repeated_run_summary_packet() -> SearchLiveStressRepeatedRunSummaryPacket:
    return SearchLiveStressRepeatedRunSummaryPacket(
        packet_id="search.platform.phase6.live_stress_repeated_run_summary.v1",
        stress_matrix_id="search.platform.phase6.live_stress_matrix.v1",
        divergence_ledger_id="search.platform.phase6.live_stress_divergence_ledger.v1",
        repeated_run_rows=(
            "numbertheory_rows_remain_stable_under_repetition",
            "algebra_rows_remain_stable_under_repetition",
            "no_cross_domain_consumer_instability_promoted",
        ),
        next_locus_rows=(
            "audit_small_to_xlarge: no planner-worthy instability promoted",
            "remaining_follow_on_stays_platform_or_harness_local",
        ),
        final_decision="classify_four_pack_repeated_run_surface_without_new_planner_round",
        dominant_locus="platform_local",
    )


def build_search_live_stress_repeated_run_summary_packet_wrapper() -> Dict[str, object]:
    return build_search_live_stress_repeated_run_summary_packet().to_dict()


def build_search_live_stress_repeated_run_summary_payload() -> Dict[str, object]:
    packet = build_search_live_stress_repeated_run_summary_packet()
    return {"packet": packet.to_dict(), "packet_family": "search_live_stress_repeated_run_summary.v1", "packet_id": packet.packet_id, "phase": "platform_phase6_live_stress"}


def build_search_live_stress_consumer_convergence_packet() -> SearchLiveStressConsumerConvergencePacket:
    return SearchLiveStressConsumerConvergencePacket(
        packet_id="search.platform.phase6.live_stress_convergence.v1",
        stress_matrix_id="search.platform.phase6.live_stress_matrix.v1",
        divergence_ledger_id="search.platform.phase6.live_stress_divergence_ledger.v1",
        optimize_execution_id="search.platform.phase3.live_optimize_execution.v1",
        rl_execution_id="search.platform.phase3.live_rl_execution.v1",
        compared_pack_ids=("pack_b_core_noimo_minif2f_v1", "pack_b_medium_noimo530_minif2f_v1", "pack_d_numbertheory_core_minif2f_v1", "pack_e_algebra_core_minif2f_v1"),
        compared_budget_cell_ids=("search.cross_execution.cell.audit_small.v1", "search.cross_execution.cell.audit_medium.v1", "search.cross_execution.cell.audit_large.v1", "search.cross_execution.cell.audit_xlarge.v1"),
        convergence_rows=(
            "optimize_lane_reusable_over_four_pack_surface",
            "rl_lane_reusable_over_four_pack_surface",
            "no_cross_domain_source_family_drift",
            "stress_consumer_convergence_green",
        ),
        final_decision="close_four_pack_four_budget_live_convergence",
        dominant_locus="consumer_local",
    )


def build_search_live_stress_consumer_convergence_packet_wrapper() -> Dict[str, object]:
    return build_search_live_stress_consumer_convergence_packet().to_dict()


def build_search_live_stress_consumer_convergence_payload() -> Dict[str, object]:
    packet = build_search_live_stress_consumer_convergence_packet()
    return {"packet": packet.to_dict(), "packet_family": "search_live_stress_convergence.v1", "packet_id": packet.packet_id, "phase": "platform_phase6_live_stress"}


def build_search_live_stress_closeout_packet() -> SearchLiveStressCloseoutPacket:
    return SearchLiveStressCloseoutPacket(
        packet_id="search.platform.phase6.live_stress_closeout.v1",
        stress_matrix_id="search.platform.phase6.live_stress_matrix.v1",
        divergence_ledger_id="search.platform.phase6.live_stress_divergence_ledger.v1",
        repeated_run_summary_id="search.platform.phase6.live_stress_repeated_run_summary.v1",
        convergence_id="search.platform.phase6.live_stress_convergence.v1",
        ready_rows=(
            "four_pack_matrix_green",
            "four_budget_matrix_green",
            "cross_domain_classification_green",
            "stress_consumer_convergence_green",
        ),
        remaining_follow_on_rows=(),
        final_decision="close_live_stress_and_keep_next_work_platform_or_harness_local",
        dominant_locus="platform_local",
    )


def build_search_live_stress_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_live_stress_closeout_packet().to_dict()


def build_search_live_stress_closeout_payload() -> Dict[str, object]:
    packet = build_search_live_stress_closeout_packet()
    return {"packet": packet.to_dict(), "packet_family": "search_live_stress_closeout.v1", "packet_id": packet.packet_id, "phase": "platform_phase6_live_stress"}
