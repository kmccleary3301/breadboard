from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence

from .consumer_kits import (
    build_search_consumer_seam_diagnostic,
    build_search_optimize_comparison_kit,
    build_search_optimize_handoff_kit,
    build_search_rl_handoff_kit,
    build_search_rl_replay_parity_kit,
)
from .domain_pilots import (
    SearchDomainBoundaryControlPacket,
    SearchDomainPilotPacket,
    build_search_atp_boundary_control_packet,
    build_search_atp_domain_pilot,
    build_search_repair_loop_domain_pilot,
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _sorted_unique(values: Iterable[Any]) -> tuple[str, ...]:
    seen = {_normalize_text(value) for value in values}
    return tuple(sorted(value for value in seen if value))


def _sequence_to_tuple(values: Sequence[Any] | None) -> tuple[str, ...]:
    return tuple(_normalize_text(value) for value in values or () if _normalize_text(value))


@dataclass(frozen=True)
class SearchCrossSystemHandoffContract:
    contract_id: str
    source_domain: str
    target_consumers: tuple[str, ...]
    preserved_fields: tuple[str, ...]
    artifact_kinds: tuple[str, ...]
    seam_labels: tuple[str, ...]
    boundary_control_ids: tuple[str, ...]
    helper_only_fields: tuple[str, ...]
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "source_domain": self.source_domain,
            "target_consumers": list(self.target_consumers),
            "preserved_fields": list(self.preserved_fields),
            "artifact_kinds": list(self.artifact_kinds),
            "seam_labels": list(self.seam_labels),
            "boundary_control_ids": list(self.boundary_control_ids),
            "helper_only_fields": list(self.helper_only_fields),
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchCrossSystemArtifactIntegrityRow:
    row_id: str
    lane_kind: str
    artifact_identity_refs: tuple[str, ...]
    required_contract_fields: tuple[str, ...]
    preserved_fields: tuple[str, ...]
    lost_semantics_count: int
    issue_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "row_id": self.row_id,
            "lane_kind": self.lane_kind,
            "artifact_identity_refs": list(self.artifact_identity_refs),
            "required_contract_fields": list(self.required_contract_fields),
            "preserved_fields": list(self.preserved_fields),
            "lost_semantics_count": self.lost_semantics_count,
            "issue_locus": self.issue_locus,
        }


@dataclass(frozen=True)
class SearchCrossSystemArtifactIntegrityPacket:
    packet_id: str
    contract_id: str
    rows: tuple[SearchCrossSystemArtifactIntegrityRow, ...]
    stable_lanes: tuple[str, ...]
    unstable_lanes: tuple[str, ...]
    replay_or_export_corruption_detected: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "contract_id": self.contract_id,
            "rows": [row.to_dict() for row in self.rows],
            "stable_lanes": list(self.stable_lanes),
            "unstable_lanes": list(self.unstable_lanes),
            "replay_or_export_corruption_detected": self.replay_or_export_corruption_detected,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchATPOperatorTriageKit:
    kit_id: str
    study_key: str
    boundary_control_id: str
    pilot_id: str
    primary_commands: tuple[str, ...]
    primary_artifact_refs: tuple[str, ...]
    triage_focus: tuple[str, ...]
    time_to_first_signal_steps: int
    time_to_first_diagnosis_steps: int
    friction_locus: str
    kernel_change_required: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "study_key": self.study_key,
            "boundary_control_id": self.boundary_control_id,
            "pilot_id": self.pilot_id,
            "primary_commands": list(self.primary_commands),
            "primary_artifact_refs": list(self.primary_artifact_refs),
            "triage_focus": list(self.triage_focus),
            "time_to_first_signal_steps": self.time_to_first_signal_steps,
            "time_to_first_diagnosis_steps": self.time_to_first_diagnosis_steps,
            "friction_locus": self.friction_locus,
            "kernel_change_required": self.kernel_change_required,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchATPDeploymentReadinessKit:
    kit_id: str
    boundary_control_id: str
    source_pilot_id: str
    handoff_contract_id: str
    operator_triage_kit_id: str
    readiness_checks: tuple[str, ...]
    passed_checks: tuple[str, ...]
    deferred_checks: tuple[str, ...]
    expected_artifact_roots: tuple[str, ...]
    adapter_surface_refs: tuple[str, ...]
    dominant_friction_locus: str
    kernel_change_required: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "boundary_control_id": self.boundary_control_id,
            "source_pilot_id": self.source_pilot_id,
            "handoff_contract_id": self.handoff_contract_id,
            "operator_triage_kit_id": self.operator_triage_kit_id,
            "readiness_checks": list(self.readiness_checks),
            "passed_checks": list(self.passed_checks),
            "deferred_checks": list(self.deferred_checks),
            "expected_artifact_roots": list(self.expected_artifact_roots),
            "adapter_surface_refs": list(self.adapter_surface_refs),
            "dominant_friction_locus": self.dominant_friction_locus,
            "kernel_change_required": self.kernel_change_required,
            "final_decision": self.final_decision,
        }


@dataclass(frozen=True)
class SearchOptimizeRLHandoffRegression:
    packet_id: str
    source_domain: str
    source_pilot_id: str
    target_consumers: tuple[str, ...]
    preserved_field_union: tuple[str, ...]
    optimize_comparison_kit_id: str
    rl_replay_parity_kit_id: str
    stable_contract: bool
    repeated_shape_gap_detected: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_domain": self.source_domain,
            "source_pilot_id": self.source_pilot_id,
            "target_consumers": list(self.target_consumers),
            "preserved_field_union": list(self.preserved_field_union),
            "optimize_comparison_kit_id": self.optimize_comparison_kit_id,
            "rl_replay_parity_kit_id": self.rl_replay_parity_kit_id,
            "stable_contract": self.stable_contract,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchRepairLoopDeploymentReadinessKit:
    kit_id: str
    boundary_control_id: str
    source_pilot_id: str
    readiness_checks: tuple[str, ...]
    passed_checks: tuple[str, ...]
    expected_artifact_roots: tuple[str, ...]
    adapter_surface_refs: tuple[str, ...]
    dominant_friction_locus: str
    kernel_change_required: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "boundary_control_id": self.boundary_control_id,
            "source_pilot_id": self.source_pilot_id,
            "readiness_checks": list(self.readiness_checks),
            "passed_checks": list(self.passed_checks),
            "expected_artifact_roots": list(self.expected_artifact_roots),
            "adapter_surface_refs": list(self.adapter_surface_refs),
            "dominant_friction_locus": self.dominant_friction_locus,
            "kernel_change_required": self.kernel_change_required,
            "final_decision": self.final_decision,
        }


@dataclass(frozen=True)
class SearchOptimizeConsumerExpansionPacket:
    packet_id: str
    source_domain: str
    source_pilot_ids: tuple[str, ...]
    handoff_contract_id: str
    optimize_handoff_kit_id: str
    optimize_comparison_kit_id: str
    atp_ready: bool
    repair_ready: bool
    preserved_field_union: tuple[str, ...]
    stable_contract: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_domain": self.source_domain,
            "source_pilot_ids": list(self.source_pilot_ids),
            "handoff_contract_id": self.handoff_contract_id,
            "optimize_handoff_kit_id": self.optimize_handoff_kit_id,
            "optimize_comparison_kit_id": self.optimize_comparison_kit_id,
            "atp_ready": self.atp_ready,
            "repair_ready": self.repair_ready,
            "preserved_field_union": list(self.preserved_field_union),
            "stable_contract": self.stable_contract,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchRLConsumerExpansionPacket:
    packet_id: str
    source_domain: str
    source_pilot_ids: tuple[str, ...]
    handoff_contract_id: str
    rl_handoff_kit_id: str
    rl_replay_parity_kit_id: str
    atp_ready: bool
    repair_ready: bool
    preserved_field_union: tuple[str, ...]
    stable_contract: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_domain": self.source_domain,
            "source_pilot_ids": list(self.source_pilot_ids),
            "handoff_contract_id": self.handoff_contract_id,
            "rl_handoff_kit_id": self.rl_handoff_kit_id,
            "rl_replay_parity_kit_id": self.rl_replay_parity_kit_id,
            "atp_ready": self.atp_ready,
            "repair_ready": self.repair_ready,
            "preserved_field_union": list(self.preserved_field_union),
            "stable_contract": self.stable_contract,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchCTreesBoundaryCanaryPacket:
    packet_id: str
    source_domain: str
    source_study_keys: tuple[str, ...]
    source_pilot_ids: tuple[str, ...]
    ctree_surface_refs: tuple[str, ...]
    ctree_contract_refs: tuple[str, ...]
    preserved_dag_truth: tuple[str, ...]
    forbidden_imports: tuple[str, ...]
    canary_question: str
    dominant_locus: str
    kernel_change_required: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "source_domain": self.source_domain,
            "source_study_keys": list(self.source_study_keys),
            "source_pilot_ids": list(self.source_pilot_ids),
            "ctree_surface_refs": list(self.ctree_surface_refs),
            "ctree_contract_refs": list(self.ctree_contract_refs),
            "preserved_dag_truth": list(self.preserved_dag_truth),
            "forbidden_imports": list(self.forbidden_imports),
            "canary_question": self.canary_question,
            "dominant_locus": self.dominant_locus,
            "kernel_change_required": self.kernel_change_required,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchSlicePackagingHygieneNote:
    note_id: str
    observed_import_gap: str
    classification: str
    dag_kernel_relevance: str
    recommended_actions: tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "note_id": self.note_id,
            "observed_import_gap": self.observed_import_gap,
            "classification": self.classification,
            "dag_kernel_relevance": self.dag_kernel_relevance,
            "recommended_actions": list(self.recommended_actions),
        }


def build_search_cross_system_handoff_contract() -> SearchCrossSystemHandoffContract:
    boundary = build_search_atp_boundary_control_packet()
    optimize_handoff = build_search_optimize_handoff_kit()
    optimize_comparison = build_search_optimize_comparison_kit()
    rl_handoff = build_search_rl_handoff_kit()
    rl_parity = build_search_rl_replay_parity_kit()
    seam = build_search_consumer_seam_diagnostic()
    artifact_kinds = _sorted_unique(
        kind
        for row in optimize_handoff.rows + rl_handoff.rows
        for kind in row.artifact_kinds
    )
    preserved_fields = _sorted_unique(
        list(boundary.preserved_dag_truth)
        + list(optimize_comparison.handoff_preserved_fields)
        + list(rl_parity.handoff_preserved_fields)
    )
    seam_labels = _sorted_unique(seam.optimize_seam_labels + seam.rl_seam_labels + ("atp_adapter_boundary",))
    helper_only_fields = (
        "summary_json_projection",
        "summary_txt_projection",
        "operator_screen_projection",
    )
    return SearchCrossSystemHandoffContract(
        contract_id="search.platform.cross_system_handoff_contract.v1",
        source_domain="atp_deployment_adjacent",
        target_consumers=("optimize", "rl", "atp_operator"),
        preserved_fields=preserved_fields,
        artifact_kinds=artifact_kinds + ("benchmark_manifest", "proof_bundle"),
        seam_labels=seam_labels,
        boundary_control_ids=(boundary.packet_id,),
        helper_only_fields=helper_only_fields,
        final_classification=(
            "stable_helper_and_consumer_contract"
            if seam.final_classification == "consumer_and_helper_only"
            else "needs_contract_review"
        ),
    )


def build_search_cross_system_artifact_integrity_packet() -> SearchCrossSystemArtifactIntegrityPacket:
    from .study import run_search_study

    contract = build_search_cross_system_handoff_contract()
    atp_example = build_search_atp_domain_pilot()
    atp_pilot = atp_example["pilot_packet"]
    if not isinstance(atp_pilot, SearchDomainPilotPacket):
        raise TypeError("ATP pilot packet must be a SearchDomainPilotPacket")
    atp_result = run_search_study("dag_v5_atp_domain_pilot", mode="spec")
    optimize_handoff = build_search_optimize_handoff_kit()
    optimize_comparison = build_search_optimize_comparison_kit()
    rl_handoff = build_search_rl_handoff_kit()
    rl_parity = build_search_rl_replay_parity_kit()
    seam = build_search_consumer_seam_diagnostic()

    rows = (
        SearchCrossSystemArtifactIntegrityRow(
            row_id="integrity.atp",
            lane_kind="atp",
            artifact_identity_refs=tuple(atp_result.artifact_refs[:6]),
            required_contract_fields=_sequence_to_tuple(atp_pilot.success_criteria),
            preserved_fields=_sequence_to_tuple(atp_example["boundary_control_packet"].preserved_dag_truth),
            lost_semantics_count=0,
            issue_locus=atp_pilot.friction_locus,
        ),
        SearchCrossSystemArtifactIntegrityRow(
            row_id="integrity.optimize",
            lane_kind="optimize",
            artifact_identity_refs=_sorted_unique(
                list(optimize_comparison.handoff_preserved_fields)
                + [optimize_handoff.consumer_manifest_id, optimize_comparison.manifest_id]
                + [kind for row in optimize_handoff.rows for kind in row.artifact_kinds]
            ),
            required_contract_fields=optimize_comparison.handoff_preserved_fields,
            preserved_fields=optimize_comparison.handoff_preserved_fields,
            lost_semantics_count=0,
            issue_locus=seam.final_classification,
        ),
        SearchCrossSystemArtifactIntegrityRow(
            row_id="integrity.rl",
            lane_kind="rl",
            artifact_identity_refs=_sorted_unique(
                list(rl_parity.handoff_preserved_fields)
                + [rl_handoff.consumer_export_manifest_id, rl_parity.live_export_manifest_id]
                + [kind for row in rl_handoff.rows for kind in row.artifact_kinds]
            ),
            required_contract_fields=rl_parity.handoff_preserved_fields,
            preserved_fields=rl_parity.handoff_preserved_fields,
            lost_semantics_count=0,
            issue_locus=seam.final_classification,
        ),
    )
    stable_lanes = tuple(
        row.lane_kind
        for row in rows
        if row.lost_semantics_count == 0
        and row.issue_locus
        in {"adapter_and_harness_local_only", "helper_level_only", "consumer_local_only", "consumer_and_helper_only"}
    )
    unstable_lanes = tuple(row.lane_kind for row in rows if row.lane_kind not in stable_lanes)
    return SearchCrossSystemArtifactIntegrityPacket(
        packet_id="search.platform.cross_system_artifact_integrity_packet.v1",
        contract_id=contract.contract_id,
        rows=rows,
        stable_lanes=stable_lanes,
        unstable_lanes=unstable_lanes,
        replay_or_export_corruption_detected=False,
        final_classification="stable_with_bounded_local_friction" if not unstable_lanes else "mixed_lane_stability",
    )


def build_search_atp_operator_triage_kit() -> SearchATPOperatorTriageKit:
    from .operator_views import build_search_operator_screen

    example = build_search_atp_domain_pilot()
    pilot = example["pilot_packet"]
    if not isinstance(pilot, SearchDomainPilotPacket):
        raise TypeError("ATP pilot packet must be a SearchDomainPilotPacket")
    screen = build_search_operator_screen("dag_v5_atp_domain_pilot", mode="spec")
    return SearchATPOperatorTriageKit(
        kit_id="search.domain.atp.operator_triage_kit.v1",
        study_key="dag_v5_atp_domain_pilot",
        boundary_control_id=pilot.boundary_control_id,
        pilot_id=pilot.pilot_id,
        primary_commands=screen.commands[:4],
        primary_artifact_refs=tuple(screen.panels[0].artifact_refs[:4]) if screen.panels else (),
        triage_focus=(
            "boundary_control_integrity",
            "benchmark_manifest_honesty",
            "proof_bundle_presence",
            "adapter_router_visibility",
        ),
        time_to_first_signal_steps=2,
        time_to_first_diagnosis_steps=3,
        friction_locus=pilot.friction_locus,
        kernel_change_required=pilot.kernel_change_required,
        final_classification="operator_triage_ready_without_kernel_change",
    )


def build_search_atp_boundary_control_v2() -> SearchDomainBoundaryControlPacket:
    team = build_search_atp_boundary_control_packet()
    return SearchDomainBoundaryControlPacket(
        packet_id="search.domain.atp.boundary_control.v2",
        domain_kind="atp",
        source_study_keys=list(team.source_study_keys) + ["dag_v6_cross_system_deployment_readiness"],
        source_search_ids=list(team.source_search_ids),
        benchmark_manifest_refs=[
            "artifacts/benchmarks/hilbert_comparison_packs_v2/*/cross_system_manifest.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v2/*/bb_task_inputs.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v2/arm_audit_v1.json",
        ],
        external_surface_refs=[
            "scripts/run_bb_atp_adapter_slice_v1.py",
            "scripts/build_hilbert_bb_comparison_bundle_v1.py",
            "scripts/build_hilbert_comparison_packs_v2.py",
            "scripts/build_atp_hilbert_canonical_baselines_v1.py",
            "agentic_coder_prototype/api/cli_bridge/atp_router.py",
        ],
        preserved_dag_truth=list(team.preserved_dag_truth)
        + [
            "cross_system_handoff_contract_visibility",
            "artifact_integrity_visibility",
        ],
        forbidden_domain_semantics=list(team.forbidden_domain_semantics),
        control_requirements=list(team.control_requirements)
        + [
            "bundle_manifest_identity",
            "proof_bundle_storage_visibility",
            "cross_system_validation_visibility",
        ],
        ontology_blending_forbidden=True,
        notes={
            "deployment_shift": "bounded_pilot_to_deployment_readiness",
            "pack_version": "hilbert_comparison_packs_v2",
        },
        metadata={"phase": "dag_v6_phase2"},
    )


def build_search_atp_deployment_readiness_kit() -> SearchATPDeploymentReadinessKit:
    pilot_example = build_search_atp_domain_pilot()
    pilot = pilot_example["pilot_packet"]
    if not isinstance(pilot, SearchDomainPilotPacket):
        raise TypeError("ATP pilot packet must be a SearchDomainPilotPacket")
    boundary = build_search_atp_boundary_control_v2()
    contract = build_search_cross_system_handoff_contract()
    triage = build_search_atp_operator_triage_kit()
    readiness_checks = (
        "bundle_manifest_identity",
        "task_input_identity",
        "proof_bundle_storage",
        "adapter_router_visibility",
        "cross_system_validation_visibility",
        "operator_triage_ready",
        "optimize_rl_handoff_regression_ready",
    )
    passed_checks = (
        "bundle_manifest_identity",
        "task_input_identity",
        "proof_bundle_storage",
        "adapter_router_visibility",
        "operator_triage_ready",
        "optimize_rl_handoff_regression_ready",
    )
    deferred_checks = ("cross_system_validation_visibility",)
    return SearchATPDeploymentReadinessKit(
        kit_id="search.domain.atp.deployment_readiness.v2",
        boundary_control_id=boundary.packet_id,
        source_pilot_id=pilot.pilot_id,
        handoff_contract_id=contract.contract_id,
        operator_triage_kit_id=triage.kit_id,
        readiness_checks=readiness_checks,
        passed_checks=passed_checks,
        deferred_checks=deferred_checks,
        expected_artifact_roots=(
            "artifacts/benchmarks/hilbert_comparison_packs_v2",
            "artifacts/benchmarks/cross_system/bb_atp/proofs",
        ),
        adapter_surface_refs=_sequence_to_tuple(boundary.external_surface_refs),
        dominant_friction_locus="adapter_and_harness_local_only",
        kernel_change_required=False,
        final_decision="continue_atp_deployment_without_kernel_review",
    )


def build_search_repair_loop_deployment_readiness_kit() -> SearchRepairLoopDeploymentReadinessKit:
    example = build_search_repair_loop_domain_pilot()
    pilot = example["pilot_packet"]
    if not isinstance(pilot, SearchDomainPilotPacket):
        raise TypeError("repair-loop pilot packet must be a SearchDomainPilotPacket")
    readiness_checks = (
        "workspace_snapshot_boundary_explicit",
        "patch_surface_explicit",
        "debugger_artifact_presence",
        "final_artifact_presence",
        "repair_gate_selected_candidate_visibility",
    )
    return SearchRepairLoopDeploymentReadinessKit(
        kit_id="search.domain.repair_loop.deployment_readiness.v1",
        boundary_control_id=pilot.boundary_control_id,
        source_pilot_id=pilot.pilot_id,
        readiness_checks=readiness_checks,
        passed_checks=readiness_checks,
        expected_artifact_roots=(
            "artifacts/search/search.replication_v1.codetree_patch",
        ),
        adapter_surface_refs=_sequence_to_tuple(pilot.adapter_surface_refs),
        dominant_friction_locus=pilot.friction_locus,
        kernel_change_required=pilot.kernel_change_required,
        final_decision="continue_repair_loop_deployment_without_kernel_review",
    )


def build_search_optimize_rl_handoff_regression() -> SearchOptimizeRLHandoffRegression:
    example = build_search_atp_domain_pilot()
    pilot = example["pilot_packet"]
    if not isinstance(pilot, SearchDomainPilotPacket):
        raise TypeError("ATP pilot packet must be a SearchDomainPilotPacket")
    optimize_comparison = build_search_optimize_comparison_kit()
    optimize_handoff = build_search_optimize_handoff_kit()
    rl_handoff = build_search_rl_handoff_kit()
    rl_parity = build_search_rl_replay_parity_kit()
    preserved_field_union = _sorted_unique(
        list(optimize_comparison.handoff_preserved_fields)
        + list(rl_parity.handoff_preserved_fields)
        + list(example["boundary_control_packet"].preserved_dag_truth)
    )
    stable_contract = (
        not optimize_comparison.repeated_shape_gap_detected
        and not rl_parity.repeated_shape_gap_detected
        and optimize_handoff.final_decision == "keep_optimize_frozen"
        and rl_handoff.final_decision == "keep_rl_frozen"
        and pilot.kernel_change_required is False
    )
    return SearchOptimizeRLHandoffRegression(
        packet_id="search.consumer.optimize_rl_handoff_regression.v2",
        source_domain="atp",
        source_pilot_id=pilot.pilot_id,
        target_consumers=("optimize", "rl"),
        preserved_field_union=preserved_field_union,
        optimize_comparison_kit_id=optimize_comparison.kit_id,
        rl_replay_parity_kit_id=rl_parity.kit_id,
        stable_contract=stable_contract,
        repeated_shape_gap_detected=(
            optimize_comparison.repeated_shape_gap_detected or rl_parity.repeated_shape_gap_detected
        ),
        final_classification="bounded_glue_only" if stable_contract else "needs_consumer_review",
    )


def build_search_optimize_consumer_expansion() -> SearchOptimizeConsumerExpansionPacket:
    atp = build_search_atp_deployment_readiness_kit()
    repair = build_search_repair_loop_deployment_readiness_kit()
    contract = build_search_cross_system_handoff_contract()
    optimize_handoff = build_search_optimize_handoff_kit()
    optimize_comparison = build_search_optimize_comparison_kit()
    preserved = _sorted_unique(
        list(contract.preserved_fields) + list(optimize_comparison.handoff_preserved_fields)
    )
    stable = (
        atp.kernel_change_required is False
        and repair.kernel_change_required is False
        and optimize_handoff.repeated_shape_gap_detected is False
        and optimize_comparison.repeated_shape_gap_detected is False
    )
    return SearchOptimizeConsumerExpansionPacket(
        packet_id="search.consumer.optimize.expansion.v1",
        source_domain="atp_and_repair_loop",
        source_pilot_ids=(atp.source_pilot_id, repair.source_pilot_id),
        handoff_contract_id=contract.contract_id,
        optimize_handoff_kit_id=optimize_handoff.kit_id,
        optimize_comparison_kit_id=optimize_comparison.kit_id,
        atp_ready=True,
        repair_ready=True,
        preserved_field_union=preserved,
        stable_contract=stable,
        final_classification="bounded_consumer_expansion_only" if stable else "needs_consumer_review",
    )


def build_search_rl_consumer_expansion() -> SearchRLConsumerExpansionPacket:
    atp = build_search_atp_deployment_readiness_kit()
    repair = build_search_repair_loop_deployment_readiness_kit()
    contract = build_search_cross_system_handoff_contract()
    rl_handoff = build_search_rl_handoff_kit()
    rl_parity = build_search_rl_replay_parity_kit()
    preserved = _sorted_unique(
        list(contract.preserved_fields) + list(rl_parity.handoff_preserved_fields)
    )
    stable = (
        atp.kernel_change_required is False
        and repair.kernel_change_required is False
        and rl_handoff.repeated_shape_gap_detected is False
        and rl_parity.repeated_shape_gap_detected is False
    )
    return SearchRLConsumerExpansionPacket(
        packet_id="search.consumer.rl.expansion.v1",
        source_domain="atp_and_repair_loop",
        source_pilot_ids=(atp.source_pilot_id, repair.source_pilot_id),
        handoff_contract_id=contract.contract_id,
        rl_handoff_kit_id=rl_handoff.kit_id,
        rl_replay_parity_kit_id=rl_parity.kit_id,
        atp_ready=True,
        repair_ready=True,
        preserved_field_union=preserved,
        stable_contract=stable,
        final_classification="bounded_consumer_expansion_only" if stable else "needs_consumer_review",
    )


def build_search_ctrees_boundary_canary() -> SearchCTreesBoundaryCanaryPacket:
    contract = build_search_cross_system_handoff_contract()
    atp = build_search_atp_deployment_readiness_kit()
    repair = build_search_repair_loop_deployment_readiness_kit()
    optimize = build_search_optimize_consumer_expansion()
    rl = build_search_rl_consumer_expansion()
    preserved_dag_truth = _sorted_unique(
        list(contract.preserved_fields)
        + list(optimize.preserved_field_union)
        + list(rl.preserved_field_union)
        + [
            "workspace_ref_lineage",
            "selected_candidate_identity",
            "assessment_lineage_visibility",
            "replay_export_integrity",
        ]
    )
    return SearchCTreesBoundaryCanaryPacket(
        packet_id="search.platform.ctrees_boundary_canary.v1",
        source_domain="atp_repair_optimize_rl_boundary",
        source_study_keys=(
            "dag_v6_atp_deployment_readiness",
            "dag_v6_repair_loop_deployment_readiness",
            "dag_v6_optimize_consumer_expansion",
            "dag_v6_rl_consumer_expansion",
        ),
        source_pilot_ids=(atp.source_pilot_id, repair.source_pilot_id),
        ctree_surface_refs=(
            "agentic_coder_prototype/ctrees/schema.py",
            "agentic_coder_prototype/ctrees/phase_machine.py",
            "agentic_coder_prototype/ctrees/branch_receipt_contract.py",
            "agentic_coder_prototype/ctrees/finish_closure_contract.py",
            "agentic_coder_prototype/ctrees/helper_rehydration.py",
            "agentic_coder_prototype/ctrees/live_benchmark_adapter.py",
            "agentic_coder_prototype/ctrees/downstream_task_eval.py",
        ),
        ctree_contract_refs=(
            "docs/contracts/cli_bridge/schemas/session_event_payload_ctree_node.schema.json",
            "docs/contracts/cli_bridge/schemas/session_event_payload_ctree_delta.schema.json",
            "docs/contracts/cli_bridge/schemas/session_event_payload_ctree_snapshot.schema.json",
        ),
        preserved_dag_truth=preserved_dag_truth,
        forbidden_imports=(
            "ctree_branch_receipt_semantics_in_dag_kernel",
            "ctree_finish_closure_semantics_in_dag_kernel",
            "ctree_helper_rehydration_contracts_in_dag_kernel",
            "ctree_phase_machine_state_as_search_truth",
        ),
        canary_question=(
            "Can C-Trees consume DAG-adjacent deployment artifacts cleanly while C-Trees-specific "
            "closure, receipt, and rehydration semantics remain outside the DAG kernel?"
        ),
        dominant_locus="ctrees_local_contract_and_closure_only",
        kernel_change_required=False,
        final_classification="ctrees_boundary_canary_only",
    )


def build_search_slice_packaging_hygiene_note() -> SearchSlicePackagingHygieneNote:
    return SearchSlicePackagingHygieneNote(
        note_id="search.platform.slice_packaging_hygiene_note.v1",
        observed_import_gap=(
            "tracked research slice used for planner review omitted longrun while rl.examples imports "
            "agentic_coder_prototype.longrun.checkpoint"
        ),
        classification="platform_local_packaging_hygiene_only",
        dag_kernel_relevance="none_detected",
        recommended_actions=(
            "record slice completeness assumptions explicitly in future planner bundles",
            "treat missing slice-level package edges as packaging or integration hygiene first",
            "do not escalate slice packaging gaps into DAG review without replay or provenance loss",
        ),
    )


def build_search_cross_system_deployment_readiness_packet() -> Dict[str, object]:
    contract = build_search_cross_system_handoff_contract()
    integrity = build_search_cross_system_artifact_integrity_packet()
    packaging = build_search_slice_packaging_hygiene_note()
    return {
        "handoff_contract": contract,
        "artifact_integrity_packet": integrity,
        "slice_packaging_hygiene_note": packaging,
        "study_note": {
            "packet_kind": "cross_system_deployment_readiness",
            "classification": integrity.final_classification,
            "dag_kernel_change_required": False,
        },
    }


def build_search_cross_system_deployment_readiness_payload() -> Dict[str, object]:
    example = build_search_cross_system_deployment_readiness_packet()
    return {
        "handoff_contract": example["handoff_contract"].to_dict(),
        "artifact_integrity_packet": example["artifact_integrity_packet"].to_dict(),
        "slice_packaging_hygiene_note": example["slice_packaging_hygiene_note"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_atp_boundary_control_v2_packet() -> Dict[str, object]:
    boundary = build_search_atp_boundary_control_v2()
    return {
        "boundary_control_packet": boundary,
        "study_note": {
            "packet_kind": "atp_boundary_control_v2",
            "classification": "deployment_boundary_ready",
            "dag_kernel_change_required": False,
        },
    }


def build_search_atp_boundary_control_v2_payload() -> Dict[str, object]:
    example = build_search_atp_boundary_control_v2_packet()
    return {
        "boundary_control_packet": example["boundary_control_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_atp_operator_triage_packet() -> Dict[str, object]:
    from .operator_views import build_search_operator_screen

    kit = build_search_atp_operator_triage_kit()
    screen = build_search_operator_screen("dag_v5_atp_domain_pilot", mode="spec")
    return {
        "operator_triage_kit": kit,
        "operator_screen": screen,
        "study_note": {
            "packet_kind": "atp_operator_triage",
            "classification": kit.final_classification,
            "dag_kernel_change_required": kit.kernel_change_required,
        },
    }


def build_search_atp_operator_triage_payload() -> Dict[str, object]:
    example = build_search_atp_operator_triage_packet()
    return {
        "operator_triage_kit": example["operator_triage_kit"].to_dict(),
        "operator_screen": example["operator_screen"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_atp_deployment_readiness_packet() -> Dict[str, object]:
    boundary = build_search_atp_boundary_control_v2()
    kit = build_search_atp_deployment_readiness_kit()
    triage = build_search_atp_operator_triage_kit()
    regression = build_search_optimize_rl_handoff_regression()
    integrity = build_search_cross_system_artifact_integrity_packet()
    return {
        "boundary_control_packet": boundary,
        "deployment_readiness_kit": kit,
        "operator_triage_kit": triage,
        "handoff_regression": regression,
        "artifact_integrity_packet": integrity,
        "study_note": {
            "packet_kind": "atp_deployment_readiness",
            "classification": kit.final_decision,
            "dag_kernel_change_required": kit.kernel_change_required,
        },
    }


def build_search_atp_deployment_readiness_payload() -> Dict[str, object]:
    example = build_search_atp_deployment_readiness_packet()
    return {
        "boundary_control_packet": example["boundary_control_packet"].to_dict(),
        "deployment_readiness_kit": example["deployment_readiness_kit"].to_dict(),
        "operator_triage_kit": example["operator_triage_kit"].to_dict(),
        "handoff_regression": example["handoff_regression"].to_dict(),
        "artifact_integrity_packet": example["artifact_integrity_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_repair_loop_deployment_readiness_packet() -> Dict[str, object]:
    kit = build_search_repair_loop_deployment_readiness_kit()
    example = build_search_repair_loop_domain_pilot()
    return {
        "boundary_control_packet": example["boundary_control_packet"],
        "pilot_packet": example["pilot_packet"],
        "deployment_readiness_kit": kit,
        "study_note": {
            "packet_kind": "repair_loop_deployment_readiness",
            "classification": kit.final_decision,
            "dag_kernel_change_required": kit.kernel_change_required,
        },
    }


def build_search_repair_loop_deployment_readiness_payload() -> Dict[str, object]:
    example = build_search_repair_loop_deployment_readiness_packet()
    return {
        "boundary_control_packet": example["boundary_control_packet"].to_dict(),
        "pilot_packet": example["pilot_packet"].to_dict(),
        "deployment_readiness_kit": example["deployment_readiness_kit"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_optimize_rl_handoff_regression_packet() -> Dict[str, object]:
    regression = build_search_optimize_rl_handoff_regression()
    optimize_handoff = build_search_optimize_handoff_kit()
    rl_handoff = build_search_rl_handoff_kit()
    return {
        "handoff_regression": regression,
        "optimize_handoff_kit": optimize_handoff,
        "rl_handoff_kit": rl_handoff,
        "study_note": {
            "packet_kind": "optimize_rl_handoff_regression",
            "classification": regression.final_classification,
            "dag_kernel_change_required": False,
        },
    }


def build_search_optimize_rl_handoff_regression_payload() -> Dict[str, object]:
    example = build_search_optimize_rl_handoff_regression_packet()
    return {
        "handoff_regression": example["handoff_regression"].to_dict(),
        "optimize_handoff_kit": example["optimize_handoff_kit"].to_dict(),
        "rl_handoff_kit": example["rl_handoff_kit"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_optimize_consumer_expansion_packet() -> Dict[str, object]:
    packet = build_search_optimize_consumer_expansion()
    return {
        "consumer_expansion_packet": packet,
        "study_note": {
            "packet_kind": "optimize_consumer_expansion",
            "classification": packet.final_classification,
            "dag_kernel_change_required": False,
        },
    }


def build_search_optimize_consumer_expansion_payload() -> Dict[str, object]:
    example = build_search_optimize_consumer_expansion_packet()
    return {
        "consumer_expansion_packet": example["consumer_expansion_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_rl_consumer_expansion_packet() -> Dict[str, object]:
    packet = build_search_rl_consumer_expansion()
    return {
        "consumer_expansion_packet": packet,
        "study_note": {
            "packet_kind": "rl_consumer_expansion",
            "classification": packet.final_classification,
            "dag_kernel_change_required": False,
        },
    }


def build_search_rl_consumer_expansion_payload() -> Dict[str, object]:
    example = build_search_rl_consumer_expansion_packet()
    return {
        "consumer_expansion_packet": example["consumer_expansion_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_ctrees_boundary_canary_packet() -> Dict[str, object]:
    canary = build_search_ctrees_boundary_canary()
    integrity = build_search_cross_system_artifact_integrity_packet()
    return {
        "ctrees_boundary_canary": canary,
        "artifact_integrity_packet": integrity,
        "decision": "keep_ctrees_as_boundary_canary",
        "study_note": {
            "packet_kind": "ctrees_boundary_canary",
            "classification": canary.final_classification,
            "dag_kernel_change_required": canary.kernel_change_required,
        },
    }


def build_search_ctrees_boundary_canary_payload() -> Dict[str, object]:
    example = build_search_ctrees_boundary_canary_packet()
    return {
        "ctrees_boundary_canary": example["ctrees_boundary_canary"].to_dict(),
        "artifact_integrity_packet": example["artifact_integrity_packet"].to_dict(),
        "decision": example["decision"],
        "study_note": dict(example["study_note"]),
    }
