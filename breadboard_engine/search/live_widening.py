from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict


WORKSPACE_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
PHASE4_ROOT = WORKSPACE_ROOT / "docs_tmp" / "platform" / "phase_4" / "live_artifacts"
PHASE4_PACK_ROOT = PHASE4_ROOT / "hilbert_packs_smoke"
PHASE4_BUNDLE_SUMMARY = PHASE4_ROOT / "bb_comparison_bundle_summary.json"
PHASE4_CANONICAL_ROOT = PHASE4_ROOT / "canonical_bounded_publish"

PACK_B_CORE_MANIFEST = PHASE4_PACK_ROOT / "pack_b_core_noimo_minif2f_v1" / "pack_metadata.json"
PACK_B_MEDIUM_MANIFEST = PHASE4_PACK_ROOT / "pack_b_medium_noimo530_minif2f_v1" / "pack_metadata.json"

PACK_B_CORE_INDEX = PHASE4_CANONICAL_ROOT / "core_canonical_baseline_index_v1.json"
PACK_B_CORE_REPORT = PHASE4_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_report.json"
PACK_B_CORE_VALIDATION = PHASE4_CANONICAL_ROOT / "pack_b_core_noimo_minif2f_v1_validation.json"

PACK_B_MEDIUM_INDEX = PHASE4_CANONICAL_ROOT / "medium_canonical_baseline_index_v1.json"
PACK_B_MEDIUM_REPORT = PHASE4_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_report.json"
PACK_B_MEDIUM_VALIDATION = PHASE4_CANONICAL_ROOT / "pack_b_medium_noimo530_minif2f_v1_validation.json"


@dataclass(frozen=True)
class SearchLiveWideningPackRow:
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
class SearchLiveWideningMatrixPacket:
    packet_id: str
    source_family_id: str
    pack_rows: tuple[SearchLiveWideningPackRow, ...]
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
class SearchLiveWideningConsumerConvergencePacket:
    packet_id: str
    widening_matrix_id: str
    optimize_execution_id: str
    rl_execution_id: str
    compared_pack_ids: tuple[str, ...]
    compared_budget_cell_ids: tuple[str, ...]
    convergence_rows: tuple[str, ...]
    remaining_blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "widening_matrix_id": self.widening_matrix_id,
            "optimize_execution_id": self.optimize_execution_id,
            "rl_execution_id": self.rl_execution_id,
            "compared_pack_ids": list(self.compared_pack_ids),
            "compared_budget_cell_ids": list(self.compared_budget_cell_ids),
            "convergence_rows": list(self.convergence_rows),
            "remaining_blocker_rows": list(self.remaining_blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveWideningCloseoutPacket:
    packet_id: str
    widening_matrix_id: str
    widening_convergence_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "widening_matrix_id": self.widening_matrix_id,
            "widening_convergence_id": self.widening_convergence_id,
            "ready_rows": list(self.ready_rows),
            "remaining_follow_on_rows": list(self.remaining_follow_on_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_widening_matrix_packet() -> SearchLiveWideningMatrixPacket:
    return SearchLiveWideningMatrixPacket(
        packet_id="search.platform.phase4.live_widening_matrix.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        pack_rows=(
            SearchLiveWideningPackRow(
                pack_id="pack_b_core_noimo_minif2f_v1",
                task_count=6,
                pack_manifest=str(PACK_B_CORE_MANIFEST),
                bundle_summary=str(PHASE4_BUNDLE_SUMMARY),
                canonical_index=str(PACK_B_CORE_INDEX),
                canonical_report=str(PACK_B_CORE_REPORT),
                canonical_validation=str(PACK_B_CORE_VALIDATION),
                dominant_locus="platform_local",
            ),
            SearchLiveWideningPackRow(
                pack_id="pack_b_medium_noimo530_minif2f_v1",
                task_count=5,
                pack_manifest=str(PACK_B_MEDIUM_MANIFEST),
                bundle_summary=str(PHASE4_BUNDLE_SUMMARY),
                canonical_index=str(PACK_B_MEDIUM_INDEX),
                canonical_report=str(PACK_B_MEDIUM_REPORT),
                canonical_validation=str(PACK_B_MEDIUM_VALIDATION),
                dominant_locus="platform_local",
            ),
        ),
        budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
        ),
        command_ids=(
            "build_hilbert_comparison_packs_v2_phase4_two_pack",
            "build_hilbert_bb_comparison_bundle_v1_phase4_two_pack_summary",
            "build_atp_hilbert_canonical_baselines_v1_phase4_core_bounded_publish",
            "build_atp_hilbert_canonical_baselines_v1_phase4_medium_bounded_publish",
        ),
        final_decision="execute_two_pack_two_budget_live_matrix",
        dominant_locus="harness_local",
    )


def build_search_live_widening_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_live_widening_matrix_packet().to_dict()


def build_search_live_widening_matrix_payload() -> Dict[str, object]:
    packet = build_search_live_widening_matrix_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_widening_matrix.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase4_live_widening",
    }


def build_search_live_widening_consumer_convergence_packet() -> SearchLiveWideningConsumerConvergencePacket:
    return SearchLiveWideningConsumerConvergencePacket(
        packet_id="search.platform.phase4.live_widening_convergence.v1",
        widening_matrix_id="search.platform.phase4.live_widening_matrix.v1",
        optimize_execution_id="search.platform.phase3.live_optimize_execution.v1",
        rl_execution_id="search.platform.phase3.live_rl_execution.v1",
        compared_pack_ids=(
            "pack_b_core_noimo_minif2f_v1",
            "pack_b_medium_noimo530_minif2f_v1",
        ),
        compared_budget_cell_ids=(
            "search.cross_execution.cell.audit_small.v1",
            "search.cross_execution.cell.audit_medium.v1",
        ),
        convergence_rows=(
            "two_pack_publication_green",
            "two_budget_anchor_declared",
            "optimize_lane_reusable",
            "rl_lane_reusable",
            "widened_consumer_convergence_green",
        ),
        remaining_blocker_rows=(),
        final_decision="close_two_pack_two_budget_live_convergence",
        dominant_locus="consumer_local",
    )


def build_search_live_widening_consumer_convergence_packet_wrapper() -> Dict[str, object]:
    return build_search_live_widening_consumer_convergence_packet().to_dict()


def build_search_live_widening_consumer_convergence_payload() -> Dict[str, object]:
    packet = build_search_live_widening_consumer_convergence_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_widening_convergence.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase4_live_widening",
    }


def build_search_live_widening_closeout_packet() -> SearchLiveWideningCloseoutPacket:
    return SearchLiveWideningCloseoutPacket(
        packet_id="search.platform.phase4.live_widening_closeout.v1",
        widening_matrix_id="search.platform.phase4.live_widening_matrix.v1",
        widening_convergence_id="search.platform.phase4.live_widening_convergence.v1",
        ready_rows=(
            "two_pack_matrix_green",
            "two_budget_matrix_green",
            "paired_consumer_reuse_green",
            "widened_live_convergence_green",
        ),
        remaining_follow_on_rows=(),
        final_decision="close_widened_live_matrix_and_keep_next_work_platform_local",
        dominant_locus="platform_local",
    )


def build_search_live_widening_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_live_widening_closeout_packet().to_dict()


def build_search_live_widening_closeout_payload() -> Dict[str, object]:
    packet = build_search_live_widening_closeout_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_widening_closeout.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase4_live_widening",
    }
