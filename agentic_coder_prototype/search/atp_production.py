from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .deployment_readiness import (
    build_search_atp_boundary_control_v2,
    build_search_atp_deployment_readiness_kit,
    build_search_atp_operator_triage_kit,
    build_search_cross_system_handoff_contract,
    build_search_optimize_rl_handoff_regression,
)


@dataclass(frozen=True)
class SearchATPBundlePublicationPacket:
    packet_id: str
    boundary_control_id: str
    source_pilot_id: str
    bundle_manifest_refs: tuple[str, ...]
    baseline_refs: tuple[str, ...]
    task_input_refs: tuple[str, ...]
    proof_bundle_refs: tuple[str, ...]
    publishable: bool
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "boundary_control_id": self.boundary_control_id,
            "source_pilot_id": self.source_pilot_id,
            "bundle_manifest_refs": list(self.bundle_manifest_refs),
            "baseline_refs": list(self.baseline_refs),
            "task_input_refs": list(self.task_input_refs),
            "proof_bundle_refs": list(self.proof_bundle_refs),
            "publishable": self.publishable,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchATPProductionLanePacket:
    packet_id: str
    readiness_kit_id: str
    bundle_publication_packet_id: str
    handoff_contract_id: str
    operator_triage_kit_id: str
    handoff_regression_id: str
    resolved_checks: tuple[str, ...]
    remaining_deferred_checks: tuple[str, ...]
    published_artifact_roots: tuple[str, ...]
    stable_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "readiness_kit_id": self.readiness_kit_id,
            "bundle_publication_packet_id": self.bundle_publication_packet_id,
            "handoff_contract_id": self.handoff_contract_id,
            "operator_triage_kit_id": self.operator_triage_kit_id,
            "handoff_regression_id": self.handoff_regression_id,
            "resolved_checks": list(self.resolved_checks),
            "remaining_deferred_checks": list(self.remaining_deferred_checks),
            "published_artifact_roots": list(self.published_artifact_roots),
            "stable_consumers": list(self.stable_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchATPOperatorProofTriagePacket:
    packet_id: str
    production_lane_id: str
    triage_kit_id: str
    proof_bundle_refs: tuple[str, ...]
    triage_focus: tuple[str, ...]
    operator_commands: tuple[str, ...]
    handoff_ready_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "production_lane_id": self.production_lane_id,
            "triage_kit_id": self.triage_kit_id,
            "proof_bundle_refs": list(self.proof_bundle_refs),
            "triage_focus": list(self.triage_focus),
            "operator_commands": list(self.operator_commands),
            "handoff_ready_consumers": list(self.handoff_ready_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchATPConsumerHandoffStabilizationPacket:
    packet_id: str
    production_lane_id: str
    handoff_contract_id: str
    handoff_regression_id: str
    preserved_field_union: tuple[str, ...]
    stable_consumers: tuple[str, ...]
    stabilized_handoff_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "production_lane_id": self.production_lane_id,
            "handoff_contract_id": self.handoff_contract_id,
            "handoff_regression_id": self.handoff_regression_id,
            "preserved_field_union": list(self.preserved_field_union),
            "stable_consumers": list(self.stable_consumers),
            "stabilized_handoff_checks": list(self.stabilized_handoff_checks),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchATPStageBCloseoutPacket:
    packet_id: str
    bundle_publication_packet_id: str
    production_lane_id: str
    operator_proof_triage_id: str
    consumer_handoff_stabilization_id: str
    source_family_refs: tuple[str, ...]
    stage_c_ready_consumers: tuple[str, ...]
    exit_checks: tuple[str, ...]
    remaining_deferred_checks: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "bundle_publication_packet_id": self.bundle_publication_packet_id,
            "production_lane_id": self.production_lane_id,
            "operator_proof_triage_id": self.operator_proof_triage_id,
            "consumer_handoff_stabilization_id": self.consumer_handoff_stabilization_id,
            "source_family_refs": list(self.source_family_refs),
            "stage_c_ready_consumers": list(self.stage_c_ready_consumers),
            "exit_checks": list(self.exit_checks),
            "remaining_deferred_checks": list(self.remaining_deferred_checks),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_atp_bundle_publication_packet() -> SearchATPBundlePublicationPacket:
    boundary = build_search_atp_boundary_control_v2()
    readiness = build_search_atp_deployment_readiness_kit()
    return SearchATPBundlePublicationPacket(
        packet_id="search.domain.atp.bundle_publication.v1",
        boundary_control_id=boundary.packet_id,
        source_pilot_id=readiness.source_pilot_id,
        bundle_manifest_refs=(
            "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json",
        ),
        baseline_refs=(
            "artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v2/arm_audit_v1.json",
        ),
        task_input_refs=(
            "artifacts/benchmarks/hilbert_comparison_packs_v2/*/bb_task_inputs.json",
        ),
        proof_bundle_refs=(
            "artifacts/benchmarks/cross_system/bb_atp/proofs",
        ),
        publishable=True,
        final_decision="publish_atp_bundles_and_baselines_as_stage_b_source_family",
        dominant_locus="adapter_local",
    )


def build_search_atp_production_lane_packet() -> SearchATPProductionLanePacket:
    readiness = build_search_atp_deployment_readiness_kit()
    publication = build_search_atp_bundle_publication_packet()
    contract = build_search_cross_system_handoff_contract()
    triage = build_search_atp_operator_triage_kit()
    regression = build_search_optimize_rl_handoff_regression()

    resolved_checks = tuple(check for check in readiness.passed_checks) + ("cross_system_validation_visibility",)

    return SearchATPProductionLanePacket(
        packet_id="search.domain.atp.production_lane.v1",
        readiness_kit_id=readiness.kit_id,
        bundle_publication_packet_id=publication.packet_id,
        handoff_contract_id=contract.contract_id,
        operator_triage_kit_id=triage.kit_id,
        handoff_regression_id=regression.packet_id,
        resolved_checks=resolved_checks,
        remaining_deferred_checks=(),
        published_artifact_roots=readiness.expected_artifact_roots,
        stable_consumers=("atp_operator", "optimize", "rl"),
        final_decision="continue_atp_production_lane_and_treat_atp_as_stage_b_source_family",
        dominant_locus="adapter_local",
    )


def build_search_atp_operator_proof_triage_packet() -> SearchATPOperatorProofTriagePacket:
    production_lane = build_search_atp_production_lane_packet()
    triage = build_search_atp_operator_triage_kit()
    publication = build_search_atp_bundle_publication_packet()

    return SearchATPOperatorProofTriagePacket(
        packet_id="search.domain.atp.operator_proof_triage.v1",
        production_lane_id=production_lane.packet_id,
        triage_kit_id=triage.kit_id,
        proof_bundle_refs=publication.proof_bundle_refs,
        triage_focus=triage.triage_focus + ("cross_system_handoff_visibility",),
        operator_commands=triage.primary_commands,
        handoff_ready_consumers=production_lane.stable_consumers,
        final_decision="use_operator_triage_on_real_atp_proof_bundles_before_consumer_expansion",
        dominant_locus="operator_local",
    )


def build_search_atp_consumer_handoff_stabilization_packet() -> SearchATPConsumerHandoffStabilizationPacket:
    production_lane = build_search_atp_production_lane_packet()
    contract = build_search_cross_system_handoff_contract()
    regression = build_search_optimize_rl_handoff_regression()

    return SearchATPConsumerHandoffStabilizationPacket(
        packet_id="search.domain.atp.consumer_handoff_stabilization.v1",
        production_lane_id=production_lane.packet_id,
        handoff_contract_id=contract.contract_id,
        handoff_regression_id=regression.packet_id,
        preserved_field_union=regression.preserved_field_union,
        stable_consumers=production_lane.stable_consumers,
        stabilized_handoff_checks=(
            "assessment_lineage_visibility",
            "evaluation_pack_identity",
            "comparison_packet_handoff",
            "trajectory_projection_handoff",
        ),
        final_decision="treat_atp_as_stable_upstream_source_for_stage_c_consumers",
        dominant_locus="consumer_local",
    )


def build_search_atp_stage_b_closeout_packet() -> SearchATPStageBCloseoutPacket:
    publication = build_search_atp_bundle_publication_packet()
    production_lane = build_search_atp_production_lane_packet()
    triage = build_search_atp_operator_proof_triage_packet()
    stabilization = build_search_atp_consumer_handoff_stabilization_packet()

    return SearchATPStageBCloseoutPacket(
        packet_id="search.domain.atp.stage_b_closeout.v1",
        bundle_publication_packet_id=publication.packet_id,
        production_lane_id=production_lane.packet_id,
        operator_proof_triage_id=triage.packet_id,
        consumer_handoff_stabilization_id=stabilization.packet_id,
        source_family_refs=publication.bundle_manifest_refs + publication.proof_bundle_refs,
        stage_c_ready_consumers=stabilization.stable_consumers,
        exit_checks=(
            "bundle_publication_stable",
            "production_lane_stable",
            "operator_proof_triage_ready",
            "consumer_handoff_stabilized",
        ),
        remaining_deferred_checks=(),
        final_decision="exit_stage_b_and_use_atp_as_stage_c_upstream_source_family",
        dominant_locus="adapter_local",
    )


def build_search_atp_bundle_publication_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_atp_bundle_publication_packet()
    return {
        "atp_bundle_publication_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.bundle_manifest_refs + packet.baseline_refs + packet.task_input_refs + packet.proof_bundle_refs),
    }


def build_search_atp_bundle_publication_payload() -> Dict[str, Any]:
    return {
        "atp_bundle_publication_packet": build_search_atp_bundle_publication_packet().to_dict(),
    }


def build_search_atp_production_lane_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_atp_production_lane_packet()
    publication = build_search_atp_bundle_publication_packet()
    return {
        "atp_production_lane_packet": packet,
        "atp_bundle_publication_packet": publication,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(publication.bundle_manifest_refs + publication.baseline_refs + publication.proof_bundle_refs),
    }


def build_search_atp_production_lane_payload() -> Dict[str, Any]:
    return {
        "atp_production_lane_packet": build_search_atp_production_lane_packet().to_dict(),
        "atp_bundle_publication_packet": build_search_atp_bundle_publication_packet().to_dict(),
    }


def build_search_atp_operator_proof_triage_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_atp_operator_proof_triage_packet()
    return {
        "atp_operator_proof_triage_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.proof_bundle_refs),
    }


def build_search_atp_operator_proof_triage_payload() -> Dict[str, Any]:
    return {
        "atp_operator_proof_triage_packet": build_search_atp_operator_proof_triage_packet().to_dict(),
    }


def build_search_atp_consumer_handoff_stabilization_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_atp_consumer_handoff_stabilization_packet()
    return {
        "atp_consumer_handoff_stabilization_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": (
            "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json",
            "artifacts/benchmarks/cross_system/bb_atp/proofs",
        ),
    }


def build_search_atp_consumer_handoff_stabilization_payload() -> Dict[str, Any]:
    return {
        "atp_consumer_handoff_stabilization_packet": build_search_atp_consumer_handoff_stabilization_packet().to_dict(),
    }


def build_search_atp_stage_b_closeout_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_atp_stage_b_closeout_packet()
    return {
        "atp_stage_b_closeout_packet": packet,
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": list(packet.source_family_refs),
    }


def build_search_atp_stage_b_closeout_payload() -> Dict[str, Any]:
    return {
        "atp_stage_b_closeout_packet": build_search_atp_stage_b_closeout_packet().to_dict(),
    }
