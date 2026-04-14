from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict


WORKSPACE_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
LIVE_ARTIFACT_ROOT = WORKSPACE_ROOT / "docs_tmp" / "platform" / "phase_3" / "live_artifacts"
HILBERT_PACK_ROOT = LIVE_ARTIFACT_ROOT / "hilbert_packs_smoke"
HILBERT_PACK_MANIFEST = (
    HILBERT_PACK_ROOT / "pack_b_core_noimo_minif2f_v1" / "pack_metadata.json"
)
CANONICAL_BASELINE_PREFLIGHT = LIVE_ARTIFACT_ROOT / "canonical_baseline_preflight_v1.json"


@dataclass(frozen=True)
class SearchLiveCommandSpec:
    command_id: str
    argv: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    purpose: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "command_id": self.command_id,
            "argv": list(self.argv),
            "expected_outputs": list(self.expected_outputs),
            "purpose": self.purpose,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveCommandObservation:
    command_id: str
    status: str
    exit_code: int
    observed_locus: str
    outcome_class: str
    detail: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "command_id": self.command_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "observed_locus": self.observed_locus,
            "outcome_class": self.outcome_class,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SearchLiveHarnessCommandMatrixPacket:
    packet_id: str
    source_family_id: str
    stage_b_closeout_id: str
    stage_c_closeout_id: str
    command_specs: tuple[SearchLiveCommandSpec, ...]
    artifact_root: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_family_id": self.source_family_id,
            "stage_b_closeout_id": self.stage_b_closeout_id,
            "stage_c_closeout_id": self.stage_c_closeout_id,
            "command_specs": [spec.to_dict() for spec in self.command_specs],
            "artifact_root": self.artifact_root,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveHarnessSmokePacket:

    packet_id: str
    command_matrix_id: str
    observations: tuple[SearchLiveCommandObservation, ...]
    positive_path_ids: tuple[str, ...]
    failure_path_ids: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    next_action: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "command_matrix_id": self.command_matrix_id,
            "observations": [obs.to_dict() for obs in self.observations],
            "positive_path_ids": list(self.positive_path_ids),
            "failure_path_ids": list(self.failure_path_ids),
            "artifact_refs": list(self.artifact_refs),
            "next_action": self.next_action,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_harness_command_matrix_packet() -> SearchLiveHarnessCommandMatrixPacket:
    return SearchLiveHarnessCommandMatrixPacket(
        packet_id="search.platform.phase3.live_harness_command_matrix.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        stage_b_closeout_id="search.domain.atp.stage_b_closeout.v1",
        stage_c_closeout_id="search.platform.stage_c.closeout.v1",
        command_specs=(
            SearchLiveCommandSpec(
                command_id="build_hilbert_comparison_packs_v2_smoke",
                argv=(
                    "python",
                    "scripts/build_hilbert_comparison_packs_v2.py",
                    "--out-root",
                    str(HILBERT_PACK_ROOT),
                    "--pack",
                    "pack_b_core_noimo_minif2f_v1",
                ),
                expected_outputs=(
                    str(HILBERT_PACK_ROOT / "summary.json"),
                    str(HILBERT_PACK_MANIFEST),
                ),
                purpose="Generate one real ATP-backed comparison-pack slice with bounded output volume.",
                dominant_locus="harness_local",
            ),
            SearchLiveCommandSpec(
                command_id="build_hilbert_bb_comparison_bundle_v1_probe",
                argv=(
                    "python",
                    "scripts/build_hilbert_bb_comparison_bundle_v1.py",
                    "--pack-manifest",
                    str(HILBERT_PACK_MANIFEST),
                ),
                expected_outputs=(
                    str(LIVE_ARTIFACT_ROOT / "build_hilbert_bb_comparison_bundle_v1.stdout.txt"),
                    str(LIVE_ARTIFACT_ROOT / "build_hilbert_bb_comparison_bundle_v1.stderr.txt"),
                ),
                purpose="Probe the live bundle boundary and verify v1 consumers can read a published prebuilt v2 pack.",
                dominant_locus="platform_local",
            ),
            SearchLiveCommandSpec(
                command_id="build_atp_hilbert_canonical_baselines_v1_probe",
                argv=(
                    "python",
                    "scripts/build_atp_hilbert_canonical_baselines_v1.py",
                    "--preflight-only",
                    "--preflight-out",
                    str(CANONICAL_BASELINE_PREFLIGHT),
                ),
                expected_outputs=(
                    str(CANONICAL_BASELINE_PREFLIGHT),
                ),
                purpose="Probe live baseline publication preflight and classify missing artifact roots without hard failure.",
                dominant_locus="platform_local",
            ),
            SearchLiveCommandSpec(
                command_id="run_bb_atp_adapter_slice_v1_help_probe",
                argv=("python", "scripts/run_bb_atp_adapter_slice_v1.py", "--help"),
                expected_outputs=(str(LIVE_ARTIFACT_ROOT / "run_bb_atp_adapter_slice_v1.help.txt"),),
                purpose="Confirm the real ATP runner contract is invocable before full engine-backed execution.",
                dominant_locus="adapter_local",
            ),
        ),
        artifact_root=str(LIVE_ARTIFACT_ROOT),
        final_decision="execute_harness_live_smoke_now",
        dominant_locus="harness_local",
    )


def build_search_live_harness_command_matrix_packet_wrapper() -> Dict[str, object]:
    return build_search_live_harness_command_matrix_packet().to_dict()


def build_search_live_harness_command_matrix_payload() -> Dict[str, object]:
    packet = build_search_live_harness_command_matrix_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_harness_command_matrix.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }


def build_search_live_harness_smoke_packet() -> SearchLiveHarnessSmokePacket:
    return SearchLiveHarnessSmokePacket(
        packet_id="search.platform.phase3.live_harness_smoke.v1",
        command_matrix_id="search.platform.phase3.live_harness_command_matrix.v1",
        observations=(
            SearchLiveCommandObservation(
                command_id="build_hilbert_comparison_packs_v2_smoke",
                status="ok",
                exit_code=0,
                observed_locus="harness_local",
                outcome_class="positive_path",
                detail="Generated one bounded comparison-pack slice and a summary.json successfully.",
            ),
            SearchLiveCommandObservation(
                command_id="build_hilbert_bb_comparison_bundle_v1_probe",
                status="ok",
                exit_code=0,
                observed_locus="platform_local",
                outcome_class="compatibility_path",
                detail="The bundle builder accepted a published prebuilt v2 pack manifest and returned a compatibility bundle payload.",
            ),
            SearchLiveCommandObservation(
                command_id="build_atp_hilbert_canonical_baselines_v1_probe",
                status="ok",
                exit_code=0,
                observed_locus="platform_local",
                outcome_class="preflight_classification",
                detail="Canonical baseline publication preflight completed cleanly and classified missing benchmark artifacts without a hard failure.",
            ),
            SearchLiveCommandObservation(
                command_id="run_bb_atp_adapter_slice_v1_help_probe",
                status="ok",
                exit_code=0,
                observed_locus="adapter_local",
                outcome_class="positive_path",
                detail="The ATP runner help contract rendered cleanly, so the command surface is invocable.",
            ),
        ),
        positive_path_ids=(
            "build_hilbert_comparison_packs_v2_smoke",
            "build_hilbert_bb_comparison_bundle_v1_probe",
            "build_atp_hilbert_canonical_baselines_v1_probe",
            "run_bb_atp_adapter_slice_v1_help_probe",
        ),
        failure_path_ids=(),
        artifact_refs=(
            str(HILBERT_PACK_ROOT / "summary.json"),
            str(LIVE_ARTIFACT_ROOT / "build_hilbert_bb_comparison_bundle_v1.stdout.txt"),
            str(CANONICAL_BASELINE_PREFLIGHT),
            str(LIVE_ARTIFACT_ROOT / "run_bb_atp_adapter_slice_v1.help.txt"),
        ),
        next_action="promote the repaired platform-local live boundary into optimize-live and rl-live widening with the current ATP-backed source family",
        final_decision="proceed_to_optimize_live_and_rl_live_widening",
        dominant_locus="harness_local",
    )


@dataclass(frozen=True)
class SearchLiveOptimizeExecutionPacket:
    packet_id: str
    smoke_packet_id: str
    source_family_id: str
    source_artifact_refs: tuple[str, ...]
    optimize_comparison_id: str
    optimize_live_cell_id: str
    budget_cell_id: str
    readiness_rows: tuple[str, ...]
    blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "smoke_packet_id": self.smoke_packet_id,
            "source_family_id": self.source_family_id,
            "source_artifact_refs": list(self.source_artifact_refs),
            "optimize_comparison_id": self.optimize_comparison_id,
            "optimize_live_cell_id": self.optimize_live_cell_id,
            "budget_cell_id": self.budget_cell_id,
            "readiness_rows": list(self.readiness_rows),
            "blocker_rows": list(self.blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_harness_smoke_packet_wrapper() -> Dict[str, object]:
    return build_search_live_harness_smoke_packet().to_dict()


def build_search_live_harness_smoke_payload() -> Dict[str, object]:
    packet = build_search_live_harness_smoke_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_harness_smoke.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }


def build_search_live_optimize_execution_packet() -> SearchLiveOptimizeExecutionPacket:
    return SearchLiveOptimizeExecutionPacket(
        packet_id="search.platform.phase3.live_optimize_execution.v1",
        smoke_packet_id="search.platform.phase3.live_harness_smoke.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        source_artifact_refs=(
            str(HILBERT_PACK_ROOT / "summary.json"),
            str(HILBERT_PACK_MANIFEST),
            str(Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/artifacts/benchmarks/hilbert_comparison_packs_v1/bb_comparison_bundle_summary.json")),
            str(CANONICAL_BASELINE_PREFLIGHT),
        ),
        optimize_comparison_id="comparison.next_frontier.cohort.dag_packet_compare.v1",
        optimize_live_cell_id="optimize.next_frontier.live_cell.dag_packets.v1",
        budget_cell_id="search.cross_execution.cell.audit_small.v1",
        readiness_rows=(
            "pack_summary_available",
            "pack_manifest_available",
            "bundle_summary_available",
            "canonical_baseline_preflight_available",
            "live_harness_smoke_green",
        ),
        blocker_rows=(
            "canonical_baseline_publish_not_yet_green",
        ),
        final_decision="execute_bounded_optimize_live_on_repaired_atp_artifacts",
        dominant_locus="consumer_local",
    )


def build_search_live_optimize_execution_packet_wrapper() -> Dict[str, object]:
    return build_search_live_optimize_execution_packet().to_dict()


def build_search_live_optimize_execution_payload() -> Dict[str, object]:
    packet = build_search_live_optimize_execution_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_optimize_execution.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }


@dataclass(frozen=True)
class SearchLiveRLExecutionPacket:
    packet_id: str
    smoke_packet_id: str
    source_family_id: str
    source_artifact_refs: tuple[str, ...]
    rl_trainer_export_manifest_id: str
    rl_parity_live_manifest_id: str
    rl_parity_replay_manifest_id: str
    budget_cell_id: str
    readiness_rows: tuple[str, ...]
    blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "smoke_packet_id": self.smoke_packet_id,
            "source_family_id": self.source_family_id,
            "source_artifact_refs": list(self.source_artifact_refs),
            "rl_trainer_export_manifest_id": self.rl_trainer_export_manifest_id,
            "rl_parity_live_manifest_id": self.rl_parity_live_manifest_id,
            "rl_parity_replay_manifest_id": self.rl_parity_replay_manifest_id,
            "budget_cell_id": self.budget_cell_id,
            "readiness_rows": list(self.readiness_rows),
            "blocker_rows": list(self.blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_rl_execution_packet() -> SearchLiveRLExecutionPacket:
    return SearchLiveRLExecutionPacket(
        packet_id="search.platform.phase3.live_rl_execution.v1",
        smoke_packet_id="search.platform.phase3.live_harness_smoke.v1",
        source_family_id="search.domain.atp.stage_b_closeout.v1",
        source_artifact_refs=(
            str(HILBERT_PACK_ROOT / "summary.json"),
            str(HILBERT_PACK_MANIFEST),
            str(Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/artifacts/benchmarks/hilbert_comparison_packs_v1/bb_comparison_bundle_summary.json")),
            str(CANONICAL_BASELINE_PREFLIGHT),
        ),
        rl_trainer_export_manifest_id="bb.rl.next_frontier.export_manifest.trainer.v1",
        rl_parity_live_manifest_id="bb.rl.next_frontier.export_manifest.parity.live.v1",
        rl_parity_replay_manifest_id="bb.rl.next_frontier.export_manifest.parity.replay.v1",
        budget_cell_id="search.cross_execution.cell.audit_small.v1",
        readiness_rows=(
            "pack_summary_available",
            "pack_manifest_available",
            "bundle_summary_available",
            "canonical_baseline_preflight_available",
            "live_harness_smoke_green",
            "rl_manifest_family_known",
        ),
        blocker_rows=(
            "canonical_baseline_publish_not_yet_green",
        ),
        final_decision="execute_bounded_rl_live_on_repaired_atp_artifacts",
        dominant_locus="consumer_local",
    )


def build_search_live_rl_execution_packet_wrapper() -> Dict[str, object]:
    return build_search_live_rl_execution_packet().to_dict()


def build_search_live_rl_execution_payload() -> Dict[str, object]:
    packet = build_search_live_rl_execution_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_rl_execution.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }


@dataclass(frozen=True)
class SearchLiveConsumerConvergencePacket:
    packet_id: str
    optimize_execution_id: str
    rl_execution_id: str
    shared_source_family_id: str
    shared_budget_cell_id: str
    shared_artifact_refs: tuple[str, ...]
    convergence_rows: tuple[str, ...]
    remaining_blocker_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "optimize_execution_id": self.optimize_execution_id,
            "rl_execution_id": self.rl_execution_id,
            "shared_source_family_id": self.shared_source_family_id,
            "shared_budget_cell_id": self.shared_budget_cell_id,
            "shared_artifact_refs": list(self.shared_artifact_refs),
            "convergence_rows": list(self.convergence_rows),
            "remaining_blocker_rows": list(self.remaining_blocker_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchLiveCloseoutPacket:
    packet_id: str
    harness_smoke_id: str
    optimize_execution_id: str
    rl_execution_id: str
    consumer_convergence_id: str
    ready_rows: tuple[str, ...]
    remaining_follow_on_rows: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "harness_smoke_id": self.harness_smoke_id,
            "optimize_execution_id": self.optimize_execution_id,
            "rl_execution_id": self.rl_execution_id,
            "consumer_convergence_id": self.consumer_convergence_id,
            "ready_rows": list(self.ready_rows),
            "remaining_follow_on_rows": list(self.remaining_follow_on_rows),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_live_consumer_convergence_packet() -> SearchLiveConsumerConvergencePacket:
    return SearchLiveConsumerConvergencePacket(
        packet_id="search.platform.phase3.live_consumer_convergence.v1",
        optimize_execution_id="search.platform.phase3.live_optimize_execution.v1",
        rl_execution_id="search.platform.phase3.live_rl_execution.v1",
        shared_source_family_id="search.domain.atp.stage_b_closeout.v1",
        shared_budget_cell_id="search.cross_execution.cell.audit_small.v1",
        shared_artifact_refs=(
            str(HILBERT_PACK_ROOT / "summary.json"),
            str(HILBERT_PACK_MANIFEST),
            str(Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/artifacts/benchmarks/hilbert_comparison_packs_v1/bb_comparison_bundle_summary.json")),
            str(CANONICAL_BASELINE_PREFLIGHT),
        ),
        convergence_rows=(
            "shared_atp_source_family",
            "shared_budget_anchor",
            "shared_bundle_summary",
            "shared_preflight_baseline_classification",
            "paired_consumer_readiness_green",
        ),
        remaining_blocker_rows=(
            "canonical_baseline_publish_not_yet_green",
        ),
        final_decision="close_live_consumer_pair_and_keep_remaining_publication_gap_platform_local",
        dominant_locus="consumer_local",
    )


def build_search_live_consumer_convergence_packet_wrapper() -> Dict[str, object]:
    return build_search_live_consumer_convergence_packet().to_dict()


def build_search_live_consumer_convergence_payload() -> Dict[str, object]:
    packet = build_search_live_consumer_convergence_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_consumer_convergence.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }


def build_search_live_closeout_packet() -> SearchLiveCloseoutPacket:
    return SearchLiveCloseoutPacket(
        packet_id="search.platform.phase3.live_closeout.v1",
        harness_smoke_id="search.platform.phase3.live_harness_smoke.v1",
        optimize_execution_id="search.platform.phase3.live_optimize_execution.v1",
        rl_execution_id="search.platform.phase3.live_rl_execution.v1",
        consumer_convergence_id="search.platform.phase3.live_consumer_convergence.v1",
        ready_rows=(
            "harness_smoke_green",
            "platform_repairs_validated",
            "optimize_live_green",
            "rl_live_green",
            "paired_consumer_convergence_green",
        ),
        remaining_follow_on_rows=(
            "canonical_baseline_publish_not_yet_green",
        ),
        final_decision="close_live_execution_tranche_and_keep_next_work_platform_local",
        dominant_locus="platform_local",
    )


def build_search_live_closeout_packet_wrapper() -> Dict[str, object]:
    return build_search_live_closeout_packet().to_dict()


def build_search_live_closeout_payload() -> Dict[str, object]:
    packet = build_search_live_closeout_packet()
    return {
        "packet": packet.to_dict(),
        "packet_family": "search_live_closeout.v1",
        "packet_id": packet.packet_id,
        "phase": "platform_phase3_live",
    }
