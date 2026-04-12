from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from .atp_production import (
    build_search_atp_consumer_handoff_stabilization_packet,
    build_search_atp_stage_b_closeout_packet,
)
from .consumer_kits import (
    build_search_consumer_seam_diagnostic,
    build_search_optimize_comparison_kit,
    build_search_optimize_handoff_kit,
    build_search_rl_handoff_kit,
    build_search_rl_replay_parity_kit,
)
from .deployment_readiness import build_search_repair_loop_deployment_readiness_kit


def _sorted_unique(values: Iterable[Any]) -> tuple[str, ...]:
    seen = {str(value or "").strip() for value in values}
    return tuple(sorted(value for value in seen if value))


@dataclass(frozen=True)
class SearchStageCOptimizeConsumerizationPacket:
    packet_id: str
    stage_b_closeout_id: str
    optimize_handoff_kit_id: str
    optimize_comparison_kit_id: str
    source_family_refs: tuple[str, ...]
    accepted_artifact_kinds: tuple[str, ...]
    required_contract_fields: tuple[str, ...]
    source_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stage_b_closeout_id": self.stage_b_closeout_id,
            "optimize_handoff_kit_id": self.optimize_handoff_kit_id,
            "optimize_comparison_kit_id": self.optimize_comparison_kit_id,
            "source_family_refs": list(self.source_family_refs),
            "accepted_artifact_kinds": list(self.accepted_artifact_kinds),
            "required_contract_fields": list(self.required_contract_fields),
            "source_consumers": list(self.source_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchStageCRLConsumerizationPacket:
    packet_id: str
    stage_b_closeout_id: str
    rl_handoff_kit_id: str
    rl_replay_parity_kit_id: str
    source_family_refs: tuple[str, ...]
    accepted_artifact_kinds: tuple[str, ...]
    required_contract_fields: tuple[str, ...]
    source_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stage_b_closeout_id": self.stage_b_closeout_id,
            "rl_handoff_kit_id": self.rl_handoff_kit_id,
            "rl_replay_parity_kit_id": self.rl_replay_parity_kit_id,
            "source_family_refs": list(self.source_family_refs),
            "accepted_artifact_kinds": list(self.accepted_artifact_kinds),
            "required_contract_fields": list(self.required_contract_fields),
            "source_consumers": list(self.source_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchStageCRepairLoopConsumerLanePacket:
    packet_id: str
    stage_b_closeout_id: str
    repair_readiness_kit_id: str
    source_family_refs: tuple[str, ...]
    expected_artifact_roots: tuple[str, ...]
    bounded_lane_checks: tuple[str, ...]
    source_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "stage_b_closeout_id": self.stage_b_closeout_id,
            "repair_readiness_kit_id": self.repair_readiness_kit_id,
            "source_family_refs": list(self.source_family_refs),
            "expected_artifact_roots": list(self.expected_artifact_roots),
            "bounded_lane_checks": list(self.bounded_lane_checks),
            "source_consumers": list(self.source_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchStageCConsumerConvergencePacket:
    packet_id: str
    optimize_consumerization_id: str
    rl_consumerization_id: str
    seam_diagnostic_id: str
    shared_source_family_refs: tuple[str, ...]
    shared_source_consumers: tuple[str, ...]
    converged_contract_fields: tuple[str, ...]
    consumer_loci: tuple[str, ...]
    convergence_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "optimize_consumerization_id": self.optimize_consumerization_id,
            "rl_consumerization_id": self.rl_consumerization_id,
            "seam_diagnostic_id": self.seam_diagnostic_id,
            "shared_source_family_refs": list(self.shared_source_family_refs),
            "shared_source_consumers": list(self.shared_source_consumers),
            "converged_contract_fields": list(self.converged_contract_fields),
            "consumer_loci": list(self.consumer_loci),
            "convergence_checks": list(self.convergence_checks),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchStageCRepairLoopContainmentPacket:
    packet_id: str
    repair_loop_consumer_lane_id: str
    source_family_refs: tuple[str, ...]
    bounded_lane_checks: tuple[str, ...]
    excluded_semantic_imports: tuple[str, ...]
    containment_status: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "repair_loop_consumer_lane_id": self.repair_loop_consumer_lane_id,
            "source_family_refs": list(self.source_family_refs),
            "bounded_lane_checks": list(self.bounded_lane_checks),
            "excluded_semantic_imports": list(self.excluded_semantic_imports),
            "containment_status": self.containment_status,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchStageCCloseoutPacket:
    packet_id: str
    optimize_consumerization_id: str
    rl_consumerization_id: str
    repair_loop_consumer_lane_id: str
    consumer_convergence_id: str
    repair_loop_containment_id: str
    source_family_refs: tuple[str, ...]
    accepted_source_consumers: tuple[str, ...]
    exit_checks: tuple[str, ...]
    remaining_deferred_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "optimize_consumerization_id": self.optimize_consumerization_id,
            "rl_consumerization_id": self.rl_consumerization_id,
            "repair_loop_consumer_lane_id": self.repair_loop_consumer_lane_id,
            "consumer_convergence_id": self.consumer_convergence_id,
            "repair_loop_containment_id": self.repair_loop_containment_id,
            "source_family_refs": list(self.source_family_refs),
            "accepted_source_consumers": list(self.accepted_source_consumers),
            "exit_checks": list(self.exit_checks),
            "remaining_deferred_checks": list(self.remaining_deferred_checks),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_stage_c_optimize_consumerization_packet() -> SearchStageCOptimizeConsumerizationPacket:
    closeout = build_search_atp_stage_b_closeout_packet()
    stabilization = build_search_atp_consumer_handoff_stabilization_packet()
    handoff = build_search_optimize_handoff_kit()
    comparison = build_search_optimize_comparison_kit()
    artifact_kinds = _sorted_unique(
        kind for row in handoff.rows for kind in row.artifact_kinds
    )
    contract_fields = _sorted_unique(
        list(field for row in handoff.rows for field in row.handoff_contract)
        + list(stabilization.stabilized_handoff_checks)
    )
    return SearchStageCOptimizeConsumerizationPacket(
        packet_id="search.platform.stage_c.optimize_consumerization.v1",
        stage_b_closeout_id=closeout.packet_id,
        optimize_handoff_kit_id=handoff.kit_id,
        optimize_comparison_kit_id=comparison.kit_id,
        source_family_refs=closeout.source_family_refs,
        accepted_artifact_kinds=artifact_kinds,
        required_contract_fields=contract_fields,
        source_consumers=closeout.stage_c_ready_consumers,
        final_decision="continue_optimize_consumerization_over_published_atp_source_family",
        dominant_locus="consumer_local",
    )


def build_search_stage_c_rl_consumerization_packet() -> SearchStageCRLConsumerizationPacket:
    closeout = build_search_atp_stage_b_closeout_packet()
    stabilization = build_search_atp_consumer_handoff_stabilization_packet()
    handoff = build_search_rl_handoff_kit()
    parity = build_search_rl_replay_parity_kit()
    artifact_kinds = _sorted_unique(
        kind for row in handoff.rows for kind in row.artifact_kinds
    )
    contract_fields = _sorted_unique(
        list(field for row in handoff.rows for field in row.handoff_contract)
        + list(stabilization.stabilized_handoff_checks)
    )
    return SearchStageCRLConsumerizationPacket(
        packet_id="search.platform.stage_c.rl_consumerization.v1",
        stage_b_closeout_id=closeout.packet_id,
        rl_handoff_kit_id=handoff.kit_id,
        rl_replay_parity_kit_id=parity.kit_id,
        source_family_refs=closeout.source_family_refs,
        accepted_artifact_kinds=artifact_kinds,
        required_contract_fields=contract_fields,
        source_consumers=closeout.stage_c_ready_consumers,
        final_decision="continue_rl_consumerization_over_published_atp_source_family",
        dominant_locus="consumer_local",
    )


def build_search_stage_c_repair_loop_consumer_lane_packet() -> SearchStageCRepairLoopConsumerLanePacket:
    closeout = build_search_atp_stage_b_closeout_packet()
    readiness = build_search_repair_loop_deployment_readiness_kit()
    return SearchStageCRepairLoopConsumerLanePacket(
        packet_id="search.platform.stage_c.repair_loop_consumer_lane.v1",
        stage_b_closeout_id=closeout.packet_id,
        repair_readiness_kit_id=readiness.kit_id,
        source_family_refs=closeout.source_family_refs,
        expected_artifact_roots=readiness.expected_artifact_roots,
        bounded_lane_checks=(
            "published_atp_source_family_visible",
            "workspace_snapshot_boundary_explicit",
            "patch_bundle_lineage_visible",
            "repair_lane_stays_bounded",
        ),
        source_consumers=closeout.stage_c_ready_consumers,
        final_decision="continue_bounded_repair_loop_consumer_lane_over_published_atp_source_family",
        dominant_locus="adapter_local",
    )


def build_search_stage_c_consumer_convergence_packet() -> SearchStageCConsumerConvergencePacket:
    optimize = build_search_stage_c_optimize_consumerization_packet()
    rl = build_search_stage_c_rl_consumerization_packet()
    diagnostic = build_search_consumer_seam_diagnostic()
    converged_fields = _sorted_unique(
        list(optimize.required_contract_fields) + list(rl.required_contract_fields)
    )
    return SearchStageCConsumerConvergencePacket(
        packet_id="search.platform.stage_c.consumer_convergence.v1",
        optimize_consumerization_id=optimize.packet_id,
        rl_consumerization_id=rl.packet_id,
        seam_diagnostic_id=diagnostic.diagnostic_id,
        shared_source_family_refs=optimize.source_family_refs,
        shared_source_consumers=optimize.source_consumers,
        converged_contract_fields=converged_fields,
        consumer_loci=_sorted_unique((optimize.dominant_locus, rl.dominant_locus)),
        convergence_checks=(
            "shared_source_family_visible",
            "consumer_contract_union_explicit",
            "cross_consumer_seam_read_preserved",
            "no_kernel_or_semantic_import",
        ),
        final_decision="continue_cross_consumer_convergence_over_published_atp_source_family",
        dominant_locus="consumer_local",
    )


def build_search_stage_c_repair_loop_containment_packet() -> SearchStageCRepairLoopContainmentPacket:
    repair = build_search_stage_c_repair_loop_consumer_lane_packet()
    return SearchStageCRepairLoopContainmentPacket(
        packet_id="search.platform.stage_c.repair_loop_containment.v1",
        repair_loop_consumer_lane_id=repair.packet_id,
        source_family_refs=repair.source_family_refs,
        bounded_lane_checks=repair.bounded_lane_checks,
        excluded_semantic_imports=(
            "workspace_phase_machine_import",
            "receipt_rehydration_import",
            "dag_kernel_patch_semantics_import",
        ),
        containment_status="bounded_and_adapter_local_only",
        final_decision="keep_repair_loop_consumer_lane_bounded_and_out_of_platform_semantic_expansion",
        dominant_locus="adapter_local",
    )


def build_search_stage_c_closeout_packet() -> SearchStageCCloseoutPacket:
    optimize = build_search_stage_c_optimize_consumerization_packet()
    rl = build_search_stage_c_rl_consumerization_packet()
    repair = build_search_stage_c_repair_loop_consumer_lane_packet()
    convergence = build_search_stage_c_consumer_convergence_packet()
    containment = build_search_stage_c_repair_loop_containment_packet()
    return SearchStageCCloseoutPacket(
        packet_id="search.platform.stage_c.closeout.v1",
        optimize_consumerization_id=optimize.packet_id,
        rl_consumerization_id=rl.packet_id,
        repair_loop_consumer_lane_id=repair.packet_id,
        consumer_convergence_id=convergence.packet_id,
        repair_loop_containment_id=containment.packet_id,
        source_family_refs=optimize.source_family_refs,
        accepted_source_consumers=optimize.source_consumers,
        exit_checks=(
            "optimize_consumerization_stable",
            "rl_consumerization_stable",
            "repair_loop_lane_bounded",
            "cross_consumer_convergence_explicit",
            "repair_loop_containment_explicit",
        ),
        remaining_deferred_checks=(),
        final_decision="exit_stage_c_and_treat_published_atp_backed_consumers_as_stable_platform_surface",
        dominant_locus="consumer_local",
    )


def build_search_stage_c_optimize_consumerization_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_optimize_consumerization_packet()
    return {
        "stage_c_optimize_consumerization_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_stage_c_optimize_consumerization_payload() -> Dict[str, Any]:
    return {
        "stage_c_optimize_consumerization_packet": build_search_stage_c_optimize_consumerization_packet().to_dict(),
    }


def build_search_stage_c_rl_consumerization_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_rl_consumerization_packet()
    return {
        "stage_c_rl_consumerization_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_stage_c_rl_consumerization_payload() -> Dict[str, Any]:
    return {
        "stage_c_rl_consumerization_packet": build_search_stage_c_rl_consumerization_packet().to_dict(),
    }


def build_search_stage_c_repair_loop_consumer_lane_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_repair_loop_consumer_lane_packet()
    return {
        "stage_c_repair_loop_consumer_lane_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs + packet.expected_artifact_roots),
    }


def build_search_stage_c_repair_loop_consumer_lane_payload() -> Dict[str, Any]:
    return {
        "stage_c_repair_loop_consumer_lane_packet": build_search_stage_c_repair_loop_consumer_lane_packet().to_dict(),
    }


def build_search_stage_c_consumer_convergence_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_consumer_convergence_packet()
    return {
        "stage_c_consumer_convergence_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.shared_source_family_refs),
    }


def build_search_stage_c_consumer_convergence_payload() -> Dict[str, Any]:
    return {
        "stage_c_consumer_convergence_packet": build_search_stage_c_consumer_convergence_packet().to_dict(),
    }


def build_search_stage_c_repair_loop_containment_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_repair_loop_containment_packet()
    return {
        "stage_c_repair_loop_containment_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_stage_c_repair_loop_containment_payload() -> Dict[str, Any]:
    return {
        "stage_c_repair_loop_containment_packet": build_search_stage_c_repair_loop_containment_packet().to_dict(),
    }


def build_search_stage_c_closeout_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_stage_c_closeout_packet()
    return {
        "stage_c_closeout_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_stage_c_closeout_payload() -> Dict[str, Any]:
    return {
        "stage_c_closeout_packet": build_search_stage_c_closeout_packet().to_dict(),
    }
