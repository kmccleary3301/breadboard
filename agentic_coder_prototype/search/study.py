from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

from .deployment_readiness import (
    build_search_atp_boundary_control_v2_packet,
    build_search_atp_boundary_control_v2_payload,
    build_search_atp_deployment_readiness_packet,
    build_search_atp_deployment_readiness_payload,
    build_search_atp_operator_triage_packet,
    build_search_atp_operator_triage_payload,
    build_search_ctrees_boundary_canary_packet,
    build_search_ctrees_boundary_canary_payload,
    build_search_cross_system_deployment_readiness_packet,
    build_search_cross_system_deployment_readiness_payload,
    build_search_optimize_consumer_expansion_packet,
    build_search_optimize_consumer_expansion_payload,
    build_search_optimize_rl_handoff_regression_packet,
    build_search_optimize_rl_handoff_regression_payload,
    build_search_repair_loop_deployment_readiness_packet,
    build_search_repair_loop_deployment_readiness_payload,
    build_search_rl_consumer_expansion_packet,
    build_search_rl_consumer_expansion_payload,
)
from .atp_production import (
    build_search_atp_bundle_publication_packet_wrapper,
    build_search_atp_bundle_publication_payload,
    build_search_atp_consumer_handoff_stabilization_packet_wrapper,
    build_search_atp_consumer_handoff_stabilization_payload,
    build_search_atp_operator_proof_triage_packet_wrapper,
    build_search_atp_operator_proof_triage_payload,
    build_search_atp_production_lane_packet_wrapper,
    build_search_atp_production_lane_payload,
    build_search_atp_stage_b_closeout_packet_wrapper,
    build_search_atp_stage_b_closeout_payload,
)
from .consumerization import (
    build_search_stage_c_closeout_packet_wrapper,
    build_search_stage_c_closeout_payload,
    build_search_stage_c_consumer_convergence_packet_wrapper,
    build_search_stage_c_consumer_convergence_payload,
    build_search_stage_c_optimize_consumerization_packet_wrapper,
    build_search_stage_c_optimize_consumerization_payload,
    build_search_stage_c_repair_loop_consumer_lane_packet_wrapper,
    build_search_stage_c_repair_loop_consumer_lane_payload,
    build_search_stage_c_repair_loop_containment_packet_wrapper,
    build_search_stage_c_repair_loop_containment_payload,
    build_search_stage_c_rl_consumerization_packet_wrapper,
    build_search_stage_c_rl_consumerization_payload,
)
from .cross_execution import (
    build_search_cross_execution_harness_comparison_packet_wrapper,
    build_search_cross_execution_harness_comparison_payload,
    build_search_cross_execution_matrix_packet_wrapper,
    build_search_cross_execution_matrix_payload,
    build_search_cross_execution_optimize_execution_packet_wrapper,
    build_search_cross_execution_optimize_execution_payload,
    build_search_cross_execution_optimize_regression_ledger_packet_wrapper,
    build_search_cross_execution_optimize_regression_ledger_payload,
    build_search_cross_execution_rl_execution_packet_wrapper,
    build_search_cross_execution_rl_execution_payload,
    build_search_cross_execution_rl_regression_ledger_packet_wrapper,
    build_search_cross_execution_rl_regression_ledger_payload,
    build_search_cross_execution_divergence_ledger_packet_wrapper,
    build_search_cross_execution_divergence_ledger_payload,
    build_search_cross_execution_repeated_run_summary_packet_wrapper,
    build_search_cross_execution_repeated_run_summary_payload,
    build_search_cross_execution_closeout_packet_wrapper,
    build_search_cross_execution_closeout_payload,
    build_search_cross_execution_next_locus_packet_wrapper,
    build_search_cross_execution_next_locus_payload,
)
from .live_execution import (
    build_search_live_harness_command_matrix_packet_wrapper,
    build_search_live_harness_command_matrix_payload,
    build_search_live_harness_smoke_packet_wrapper,
    build_search_live_harness_smoke_payload,
    build_search_live_optimize_execution_packet_wrapper,
    build_search_live_optimize_execution_payload,
    build_search_live_rl_execution_packet_wrapper,
    build_search_live_rl_execution_payload,
    build_search_live_consumer_convergence_packet_wrapper,
    build_search_live_consumer_convergence_payload,
    build_search_live_closeout_packet_wrapper,
    build_search_live_closeout_payload,
)
from .live_widening import (
    build_search_live_widening_matrix_packet_wrapper,
    build_search_live_widening_matrix_payload,
    build_search_live_widening_consumer_convergence_packet_wrapper,
    build_search_live_widening_consumer_convergence_payload,
    build_search_live_widening_closeout_packet_wrapper,
    build_search_live_widening_closeout_payload,
)
from .live_expansion import (
    build_search_live_expansion_matrix_packet_wrapper,
    build_search_live_expansion_matrix_payload,
    build_search_live_expansion_divergence_ledger_packet_wrapper,
    build_search_live_expansion_divergence_ledger_payload,
    build_search_live_expansion_repeated_run_summary_packet_wrapper,
    build_search_live_expansion_repeated_run_summary_payload,
    build_search_live_expansion_consumer_convergence_packet_wrapper,
    build_search_live_expansion_consumer_convergence_payload,
    build_search_live_expansion_closeout_packet_wrapper,
    build_search_live_expansion_closeout_payload,
)
from .live_stress import (
    build_search_live_stress_matrix_packet_wrapper,
    build_search_live_stress_matrix_payload,
    build_search_live_stress_divergence_ledger_packet_wrapper,
    build_search_live_stress_divergence_ledger_payload,
    build_search_live_stress_repeated_run_summary_packet_wrapper,
    build_search_live_stress_repeated_run_summary_payload,
    build_search_live_stress_consumer_convergence_packet_wrapper,
    build_search_live_stress_consumer_convergence_payload,
    build_search_live_stress_closeout_packet_wrapper,
    build_search_live_stress_closeout_payload,
)
from .offline_convergence import (
    build_search_offline_convergence_matrix_packet_wrapper,
    build_search_offline_convergence_matrix_payload,
    build_search_offline_convergence_divergence_ledger_packet_wrapper,
    build_search_offline_convergence_divergence_ledger_payload,
    build_search_offline_convergence_closeout_packet_wrapper,
    build_search_offline_convergence_closeout_payload,
)
from .domain_pilots import (
    build_search_atp_domain_pilot,
    build_search_atp_domain_pilot_payload,
    build_search_repair_loop_domain_pilot,
    build_search_repair_loop_domain_pilot_payload,
)
from .examples import (
    build_dag_replication_v1_got_sorting_packet,
    build_dag_replication_v1_got_sorting_packet_payload,
    build_dag_replication_v1_tot_game24_packet,
    build_dag_replication_v1_tot_game24_packet_payload,
    build_dag_v3_rsa_replication_packet,
    build_dag_v3_rsa_replication_packet_payload,
    build_dag_v4_bavt_packet,
    build_dag_v4_bavt_packet_payload,
    build_dag_v4_dci_packet,
    build_dag_v4_dci_packet_payload,
    build_dag_v4_final_adjudication_packet,
    build_dag_v4_final_adjudication_packet_payload,
    build_dag_v4_team_of_thoughts_packet,
    build_dag_v4_team_of_thoughts_packet_payload,
)
from .inspection import (
    build_search_assessment_chain_view,
    build_search_lineage_view,
    build_search_replay_export_summary,
)
from .platform_publication import (
    build_search_platform_command_bundle_packet,
    build_search_platform_command_bundle_payload,
    build_search_platform_contract_publication_packet,
    build_search_platform_contract_publication_payload,
    build_search_platform_fixture_publication_packet_payload,
    build_search_platform_fixture_publication_packet_wrapper,
    build_search_platform_regression_harness_packet,
    build_search_platform_regression_harness_payload,
    build_search_platform_regression_entrypoint_packet,
    build_search_platform_regression_entrypoint_payload,
    build_search_platform_validator_packet_payload,
    build_search_platform_validator_packet_wrapper,
)
from .research_controls import (
    build_search_general_agent_control_packet,
    build_search_general_agent_control_packet_payload,
    build_search_tool_planning_tree_control_packet,
    build_search_tool_planning_tree_control_packet_payload,
)


PacketBuilder = Callable[[], Dict[str, Any]]


@dataclass(frozen=True)
class SearchStudyRegistryEntry:
    study_key: str
    title: str
    packet_family: str
    phase: str
    tags: tuple[str, ...]
    packet_builder: PacketBuilder
    payload_builder: PacketBuilder

    def to_dict(self) -> Dict[str, object]:
        return {
            "study_key": self.study_key,
            "title": self.title,
            "packet_family": self.packet_family,
            "phase": self.phase,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class SearchStudySummary:
    study_key: str
    title: str
    packet_family: str
    mode: str
    phase: str
    tags: tuple[str, ...]
    packet_keys: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    top_level_metrics: Dict[str, object]
    selected_candidate_id: str | None

    def to_dict(self) -> Dict[str, object]:
        return {
            "study_key": self.study_key,
            "title": self.title,
            "packet_family": self.packet_family,
            "mode": self.mode,
            "phase": self.phase,
            "tags": list(self.tags),
            "packet_keys": list(self.packet_keys),
            "artifact_refs": list(self.artifact_refs),
            "top_level_metrics": dict(self.top_level_metrics),
            "selected_candidate_id": self.selected_candidate_id,
        }

    def to_text(self) -> str:
        lines = [
            f"study_key: {self.study_key}",
            f"title: {self.title}",
            f"packet_family: {self.packet_family}",
            f"mode: {self.mode}",
            f"phase: {self.phase}",
            f"tags: {', '.join(self.tags) if self.tags else 'none'}",
            f"packet_keys: {', '.join(self.packet_keys[:8])}",
            f"artifact_refs: {len(self.artifact_refs)}",
        ]
        if self.selected_candidate_id:
            lines.append(f"selected_candidate_id: {self.selected_candidate_id}")
        if self.top_level_metrics:
            metric_bits = [f"{key}={value}" for key, value in self.top_level_metrics.items()]
            lines.append(f"top_level_metrics: {', '.join(metric_bits)}")
        return "\n".join(lines)


@dataclass(frozen=True)
class SearchStudyRunResult:
    registry_entry: SearchStudyRegistryEntry
    mode: str
    packet: Dict[str, Any]
    payload: Dict[str, Any] | None
    summary: SearchStudySummary

    @property
    def summary_json(self) -> Dict[str, object]:
        return self.summary.to_dict()

    @property
    def summary_txt(self) -> str:
        return self.summary.to_text()

    @property
    def artifact_refs(self) -> tuple[str, ...]:
        return self.summary.artifact_refs

    def inspect(self) -> Dict[str, object]:
        inspection: Dict[str, object] = {
            "registry_entry": self.registry_entry.to_dict(),
            "mode": self.mode,
            "summary_json": self.summary_json,
            "summary_txt": self.summary_txt,
            "packet_keys": sorted(self.packet.keys()),
            "payload_available": self.payload is not None,
        }
        run = self.packet.get("run")
        if run is not None:
            inspection["lineage_view"] = build_search_lineage_view(run).to_dict()
        assessment_lineage_packet = self.packet.get("assessment_lineage_packet")
        if isinstance(assessment_lineage_packet, Mapping) or hasattr(assessment_lineage_packet, "to_dict"):
            inspection["assessment_chain_view"] = build_search_assessment_chain_view(
                assessment_lineage_packet
            ).to_dict()
        replay_export_integrity_packet = self.packet.get("replay_export_integrity_packet")
        if run is not None:
            inspection["replay_export_summary"] = build_search_replay_export_summary(
                run,
                integrity_packet=replay_export_integrity_packet if replay_export_integrity_packet is not None else None,
            ).to_dict()
        return inspection

    def open_artifact(self, artifact_ref: str | None = None) -> str:
        if artifact_ref is None:
            if not self.artifact_refs:
                raise KeyError(f"{self.registry_entry.study_key} has no artifact refs")
            return self.artifact_refs[0]
        if artifact_ref not in self.artifact_refs:
            raise KeyError(f"{artifact_ref} is not present in {self.registry_entry.study_key}")
        return artifact_ref


class SearchStudyRegistry:
    def __init__(self, entries: Sequence[SearchStudyRegistryEntry] | None = None) -> None:
        self._entries: Dict[str, SearchStudyRegistryEntry] = {}
        for entry in entries or ():
            self.register(entry)

    def register(self, entry: SearchStudyRegistryEntry) -> None:
        self._entries[entry.study_key] = entry

    def get(self, study_key: str) -> SearchStudyRegistryEntry:
        return self._entries[study_key]

    def list_entries(self) -> List[SearchStudyRegistryEntry]:
        return [self._entries[key] for key in sorted(self._entries)]

    def run(self, study_key: str, *, mode: str = "spec") -> SearchStudyRunResult:
        if mode not in {"spec", "debug"}:
            raise ValueError(f"unsupported mode: {mode}")
        entry = self.get(study_key)
        packet = entry.packet_builder()
        payload = entry.payload_builder() if mode == "debug" else None
        summary = _build_study_summary(entry, packet=packet, mode=mode)
        return SearchStudyRunResult(
            registry_entry=entry,
            mode=mode,
            packet=packet,
            payload=payload,
            summary=summary,
        )


def _collect_artifact_refs(value: Any) -> List[str]:
    refs: List[str] = []
    seen: set[str] = set()

    def _visit(node: Any) -> None:
        if hasattr(node, "to_dict") and callable(node.to_dict):
            _visit(node.to_dict())
            return
        if isinstance(node, str):
            if (
                "artifacts/" in node
                or node.endswith(".json")
                or node.endswith(".md")
                or node.endswith(".yaml")
            ) and node not in seen:
                seen.add(node)
                refs.append(node)
            return
        if isinstance(node, Mapping):
            for child in node.values():
                _visit(child)
            return
        if isinstance(node, (list, tuple, set)):
            for child in node:
                _visit(child)

    _visit(value)
    return refs


def _extract_top_level_metrics(packet: Mapping[str, Any]) -> Dict[str, object]:
    metrics: Dict[str, object] = {}
    candidate_set = packet.get("candidate_set")
    if isinstance(candidate_set, Sequence) and not isinstance(candidate_set, (str, bytes)):
        metrics["candidate_set_size"] = len(candidate_set)
    topology_audit = packet.get("topology_audit")
    if hasattr(topology_audit, "to_dict") and callable(topology_audit.to_dict):
        topology_audit = topology_audit.to_dict()
    if isinstance(topology_audit, Mapping):
        topology_class = topology_audit.get("topology_class")
        if topology_class is not None:
            metrics["topology_class"] = topology_class
    frontier_policy_audit = packet.get("frontier_policy_audit")
    if hasattr(frontier_policy_audit, "to_dict") and callable(frontier_policy_audit.to_dict):
        frontier_policy_audit = frontier_policy_audit.to_dict()
    if isinstance(frontier_policy_audit, Mapping):
        topology_class = frontier_policy_audit.get("topology_class")
        if topology_class is not None:
            metrics["topology_class"] = topology_class
    repeated_shape = packet.get("repeated_shape_entry")
    if hasattr(repeated_shape, "to_dict") and callable(repeated_shape.to_dict):
        repeated_shape = repeated_shape.to_dict()
    if isinstance(repeated_shape, Mapping):
        gap_label = repeated_shape.get("gap_label")
        if gap_label is not None:
            metrics["gap_label"] = gap_label
    decision = packet.get("decision")
    if decision is not None:
        metrics["decision"] = decision
    freeze_decision = packet.get("freeze_decision")
    if freeze_decision is not None:
        metrics["decision"] = freeze_decision
    final_decision = packet.get("final_decision")
    if final_decision is not None:
        metrics["decision"] = final_decision
    final_classification = packet.get("final_classification")
    if final_classification is not None:
        metrics["final_classification"] = final_classification
    dominant_locus = packet.get("dominant_locus")
    if dominant_locus is not None:
        metrics["dominant_locus"] = dominant_locus
    dominant_friction_locus = packet.get("dominant_friction_locus")
    if dominant_friction_locus is not None:
        metrics["dominant_locus"] = dominant_friction_locus
    stable_contract = packet.get("stable_contract")
    if stable_contract is not None:
        metrics["stable_contract"] = stable_contract
    return metrics


def _extract_selected_candidate_id(packet: Mapping[str, Any]) -> str | None:
    selected = packet.get("selected_candidate_id")
    if isinstance(selected, str):
        return selected
    run = packet.get("run")
    if isinstance(run, Mapping):
        run_selected = run.get("selected_candidate_id")
        if isinstance(run_selected, str):
            return run_selected
    return None


def _build_study_summary(
    entry: SearchStudyRegistryEntry,
    *,
    packet: Dict[str, Any],
    mode: str,
) -> SearchStudySummary:
    packet_keys = tuple(sorted(packet.keys()))
    artifact_refs = tuple(_collect_artifact_refs(packet))
    return SearchStudySummary(
        study_key=entry.study_key,
        title=entry.title,
        packet_family=entry.packet_family,
        mode=mode,
        phase=entry.phase,
        tags=entry.tags,
        packet_keys=packet_keys,
        artifact_refs=artifact_refs,
        top_level_metrics=_extract_top_level_metrics(packet),
        selected_candidate_id=_extract_selected_candidate_id(packet),
    )


def compare_search_study_runs(
    left: SearchStudyRunResult,
    right: SearchStudyRunResult,
) -> Dict[str, object]:
    shared_artifacts = sorted(set(left.artifact_refs) & set(right.artifact_refs))
    return {
        "left_study_key": left.registry_entry.study_key,
        "right_study_key": right.registry_entry.study_key,
        "left_packet_family": left.registry_entry.packet_family,
        "right_packet_family": right.registry_entry.packet_family,
        "left_mode": left.mode,
        "right_mode": right.mode,
        "shared_artifact_refs": shared_artifacts,
        "left_only_packet_keys": sorted(set(left.packet.keys()) - set(right.packet.keys())),
        "right_only_packet_keys": sorted(set(right.packet.keys()) - set(left.packet.keys())),
        "left_top_level_metrics": dict(left.summary.top_level_metrics),
        "right_top_level_metrics": dict(right.summary.top_level_metrics),
    }


def build_default_search_study_registry() -> SearchStudyRegistry:
    return SearchStudyRegistry(
        entries=[
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_matrix",
                title="Cross-system execution matrix",
                packet_family="search_cross_execution_matrix.v1",
                phase="platform_phase2",
                tags=("platform", "execution", "atp", "optimize", "rl", "harness"),
                packet_builder=build_search_cross_execution_matrix_packet_wrapper,
                payload_builder=build_search_cross_execution_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_harness_comparison",
                title="Cross-system harness comparison",
                packet_family="search_cross_execution_harness_comparison.v1",
                phase="platform_phase2",
                tags=("platform", "execution", "harness", "comparison", "optimize", "rl"),
                packet_builder=build_search_cross_execution_harness_comparison_packet_wrapper,
                payload_builder=build_search_cross_execution_harness_comparison_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_optimize_execution",
                title="Cross-system optimize execution",
                packet_family="search_cross_execution_optimize_execution.v1",
                phase="platform_phase2",
                tags=("platform", "execution", "optimize", "budget", "atp"),
                packet_builder=build_search_cross_execution_optimize_execution_packet_wrapper,
                payload_builder=build_search_cross_execution_optimize_execution_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_optimize_regression_ledger",
                title="Cross-system optimize regression ledger",
                packet_family="search_cross_execution_optimize_regression_ledger.v1",
                phase="platform_phase2",
                tags=("platform", "execution", "optimize", "regression", "harness"),
                packet_builder=build_search_cross_execution_optimize_regression_ledger_packet_wrapper,
                payload_builder=build_search_cross_execution_optimize_regression_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_rl_execution",
                title="Cross-system RL execution",
                packet_family="search_cross_execution_rl_execution.v1",
                phase="platform_phase3",
                tags=("platform", "execution", "rl", "budget", "atp"),
                packet_builder=build_search_cross_execution_rl_execution_packet_wrapper,
                payload_builder=build_search_cross_execution_rl_execution_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_rl_regression_ledger",
                title="Cross-system RL regression ledger",
                packet_family="search_cross_execution_rl_regression_ledger.v1",
                phase="platform_phase3",
                tags=("platform", "execution", "rl", "regression", "harness"),
                packet_builder=build_search_cross_execution_rl_regression_ledger_packet_wrapper,
                payload_builder=build_search_cross_execution_rl_regression_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_divergence_ledger",
                title="Cross-system divergence ledger",
                packet_family="search_cross_execution_divergence_ledger.v1",
                phase="platform_phase4",
                tags=("platform", "execution", "harness", "divergence"),
                packet_builder=build_search_cross_execution_divergence_ledger_packet_wrapper,
                payload_builder=build_search_cross_execution_divergence_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_repeated_run_summary",
                title="Cross-system repeated-run summary",
                packet_family="search_cross_execution_repeated_run_summary.v1",
                phase="platform_phase4",
                tags=("platform", "execution", "harness", "classification"),
                packet_builder=build_search_cross_execution_repeated_run_summary_packet_wrapper,
                payload_builder=build_search_cross_execution_repeated_run_summary_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_closeout",
                title="Cross-system execution closeout",
                packet_family="search_cross_execution_closeout.v1",
                phase="platform_phase5",
                tags=("platform", "execution", "closeout"),
                packet_builder=build_search_cross_execution_closeout_packet_wrapper,
                payload_builder=build_search_cross_execution_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_cross_execution_next_locus",
                title="Cross-system next-locus classification",
                packet_family="search_cross_execution_next_locus.v1",
                phase="platform_phase5",
                tags=("platform", "execution", "governance", "classification"),
                packet_builder=build_search_cross_execution_next_locus_packet_wrapper,
                payload_builder=build_search_cross_execution_next_locus_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_harness_command_matrix",
                title="Live harness command matrix",
                packet_family="search_live_harness_command_matrix.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "harness", "atp"),
                packet_builder=build_search_live_harness_command_matrix_packet_wrapper,
                payload_builder=build_search_live_harness_command_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_harness_smoke",
                title="Live harness smoke bundle",
                packet_family="search_live_harness_smoke.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "harness", "classification"),
                packet_builder=build_search_live_harness_smoke_packet_wrapper,
                payload_builder=build_search_live_harness_smoke_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_optimize_execution",
                title="Live optimize execution readiness",
                packet_family="search_live_optimize_execution.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "optimize", "atp"),
                packet_builder=build_search_live_optimize_execution_packet_wrapper,
                payload_builder=build_search_live_optimize_execution_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_rl_execution",
                title="Live RL execution readiness",
                packet_family="search_live_rl_execution.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "rl", "atp"),
                packet_builder=build_search_live_rl_execution_packet_wrapper,
                payload_builder=build_search_live_rl_execution_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_consumer_convergence",
                title="Live consumer convergence",
                packet_family="search_live_consumer_convergence.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "convergence", "atp"),
                packet_builder=build_search_live_consumer_convergence_packet_wrapper,
                payload_builder=build_search_live_consumer_convergence_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_closeout",
                title="Live execution closeout",
                packet_family="search_live_closeout.v1",
                phase="platform_phase3_live",
                tags=("platform", "live", "closeout"),
                packet_builder=build_search_live_closeout_packet_wrapper,
                payload_builder=build_search_live_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_widening_matrix",
                title="Live widening matrix",
                packet_family="search_live_widening_matrix.v1",
                phase="platform_phase4_live_widening",
                tags=("platform", "live", "widening", "matrix"),
                packet_builder=build_search_live_widening_matrix_packet_wrapper,
                payload_builder=build_search_live_widening_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_widening_convergence",
                title="Live widening convergence",
                packet_family="search_live_widening_convergence.v1",
                phase="platform_phase4_live_widening",
                tags=("platform", "live", "widening", "convergence"),
                packet_builder=build_search_live_widening_consumer_convergence_packet_wrapper,
                payload_builder=build_search_live_widening_consumer_convergence_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_widening_closeout",
                title="Live widening closeout",
                packet_family="search_live_widening_closeout.v1",
                phase="platform_phase4_live_widening",
                tags=("platform", "live", "widening", "closeout"),
                packet_builder=build_search_live_widening_closeout_packet_wrapper,
                payload_builder=build_search_live_widening_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_expansion_matrix",
                title="Live expansion matrix",
                packet_family="search_live_expansion_matrix.v1",
                phase="platform_phase5_live_expansion",
                tags=("platform", "live", "expansion", "matrix"),
                packet_builder=build_search_live_expansion_matrix_packet_wrapper,
                payload_builder=build_search_live_expansion_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_expansion_divergence_ledger",
                title="Live expansion divergence ledger",
                packet_family="search_live_expansion_divergence_ledger.v1",
                phase="platform_phase5_live_expansion",
                tags=("platform", "live", "expansion", "divergence"),
                packet_builder=build_search_live_expansion_divergence_ledger_packet_wrapper,
                payload_builder=build_search_live_expansion_divergence_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_expansion_repeated_run_summary",
                title="Live expansion repeated-run summary",
                packet_family="search_live_expansion_repeated_run_summary.v1",
                phase="platform_phase5_live_expansion",
                tags=("platform", "live", "expansion", "classification"),
                packet_builder=build_search_live_expansion_repeated_run_summary_packet_wrapper,
                payload_builder=build_search_live_expansion_repeated_run_summary_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_expansion_convergence",
                title="Live expansion convergence",
                packet_family="search_live_expansion_convergence.v1",
                phase="platform_phase5_live_expansion",
                tags=("platform", "live", "expansion", "convergence"),
                packet_builder=build_search_live_expansion_consumer_convergence_packet_wrapper,
                payload_builder=build_search_live_expansion_consumer_convergence_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_expansion_closeout",
                title="Live expansion closeout",
                packet_family="search_live_expansion_closeout.v1",
                phase="platform_phase5_live_expansion",
                tags=("platform", "live", "expansion", "closeout"),
                packet_builder=build_search_live_expansion_closeout_packet_wrapper,
                payload_builder=build_search_live_expansion_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_stress_matrix",
                title="Live stress matrix",
                packet_family="search_live_stress_matrix.v1",
                phase="platform_phase6_live_stress",
                tags=("platform", "live", "stress", "matrix"),
                packet_builder=build_search_live_stress_matrix_packet_wrapper,
                payload_builder=build_search_live_stress_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_stress_divergence_ledger",
                title="Live stress divergence ledger",
                packet_family="search_live_stress_divergence_ledger.v1",
                phase="platform_phase6_live_stress",
                tags=("platform", "live", "stress", "divergence"),
                packet_builder=build_search_live_stress_divergence_ledger_packet_wrapper,
                payload_builder=build_search_live_stress_divergence_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_stress_repeated_run_summary",
                title="Live stress repeated-run summary",
                packet_family="search_live_stress_repeated_run_summary.v1",
                phase="platform_phase6_live_stress",
                tags=("platform", "live", "stress", "classification"),
                packet_builder=build_search_live_stress_repeated_run_summary_packet_wrapper,
                payload_builder=build_search_live_stress_repeated_run_summary_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_stress_convergence",
                title="Live stress convergence",
                packet_family="search_live_stress_convergence.v1",
                phase="platform_phase6_live_stress",
                tags=("platform", "live", "stress", "convergence"),
                packet_builder=build_search_live_stress_consumer_convergence_packet_wrapper,
                payload_builder=build_search_live_stress_consumer_convergence_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_live_stress_closeout",
                title="Live stress closeout",
                packet_family="search_live_stress_closeout.v1",
                phase="platform_phase6_live_stress",
                tags=("platform", "live", "stress", "closeout"),
                packet_builder=build_search_live_stress_closeout_packet_wrapper,
                payload_builder=build_search_live_stress_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_offline_convergence_matrix",
                title="Offline DAG optimize RL convergence matrix",
                packet_family="search_offline_convergence_matrix.v1",
                phase="platform_phase8_offline_convergence",
                tags=("platform", "offline", "convergence", "matrix"),
                packet_builder=build_search_offline_convergence_matrix_packet_wrapper,
                payload_builder=build_search_offline_convergence_matrix_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_offline_convergence_divergence_ledger",
                title="Offline DAG optimize RL divergence ledger",
                packet_family="search_offline_convergence_divergence_ledger.v1",
                phase="platform_phase8_offline_convergence",
                tags=("platform", "offline", "convergence", "divergence"),
                packet_builder=build_search_offline_convergence_divergence_ledger_packet_wrapper,
                payload_builder=build_search_offline_convergence_divergence_ledger_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_offline_convergence_closeout",
                title="Offline DAG optimize RL convergence closeout",
                packet_family="search_offline_convergence_closeout.v1",
                phase="platform_phase8_offline_convergence",
                tags=("platform", "offline", "convergence", "closeout"),
                packet_builder=build_search_offline_convergence_closeout_packet_wrapper,
                payload_builder=build_search_offline_convergence_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_contract_publication",
                title="Search platform contract publication bundle",
                packet_family="search_platform_contract_publication.v1",
                phase="platform_stage_a",
                tags=("platform", "publication", "fixtures"),
                packet_builder=build_search_platform_contract_publication_packet,
                payload_builder=build_search_platform_contract_publication_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_regression_harness",
                title="Search platform deployment regression harness",
                packet_family="search_platform_regression_harness.v1",
                phase="platform_stage_a",
                tags=("platform", "regression", "contracts"),
                packet_builder=build_search_platform_regression_harness_packet,
                payload_builder=build_search_platform_regression_harness_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_fixture_publication",
                title="Search platform reference fixture publication packet",
                packet_family="search_platform_fixture_publication.v1",
                phase="platform_stage_a",
                tags=("platform", "fixtures", "publication"),
                packet_builder=build_search_platform_fixture_publication_packet_wrapper,
                payload_builder=build_search_platform_fixture_publication_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_regression_entrypoint",
                title="Search platform regression entrypoint packet",
                packet_family="search_platform_regression_entrypoint.v1",
                phase="platform_stage_a",
                tags=("platform", "regression", "entrypoint"),
                packet_builder=build_search_platform_regression_entrypoint_packet,
                payload_builder=build_search_platform_regression_entrypoint_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_command_bundle",
                title="Search platform command bundle packet",
                packet_family="search_platform_command_bundle.v1",
                phase="platform_stage_a",
                tags=("platform", "commands", "publication"),
                packet_builder=build_search_platform_command_bundle_packet,
                payload_builder=build_search_platform_command_bundle_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_platform_validator",
                title="Search platform validator packet",
                packet_family="search_platform_validator.v1",
                phase="platform_stage_a",
                tags=("platform", "validator", "regression"),
                packet_builder=build_search_platform_validator_packet_wrapper,
                payload_builder=build_search_platform_validator_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_atp_bundle_publication",
                title="Search ATP bundle publication packet",
                packet_family="search_atp_bundle_publication.v1",
                phase="platform_stage_b",
                tags=("stage_b", "atp", "publication"),
                packet_builder=build_search_atp_bundle_publication_packet_wrapper,
                payload_builder=build_search_atp_bundle_publication_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_atp_production_lane",
                title="Search ATP production lane packet",
                packet_family="search_atp_production_lane.v1",
                phase="platform_stage_b",
                tags=("stage_b", "atp", "production_lane"),
                packet_builder=build_search_atp_production_lane_packet_wrapper,
                payload_builder=build_search_atp_production_lane_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_atp_operator_proof_triage",
                title="Search ATP operator proof triage packet",
                packet_family="search_atp_operator_proof_triage.v1",
                phase="platform_stage_b",
                tags=("stage_b", "atp", "operator", "triage"),
                packet_builder=build_search_atp_operator_proof_triage_packet_wrapper,
                payload_builder=build_search_atp_operator_proof_triage_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_atp_consumer_handoff_stabilization",
                title="Search ATP consumer handoff stabilization packet",
                packet_family="search_atp_consumer_handoff_stabilization.v1",
                phase="platform_stage_b",
                tags=("stage_b", "atp", "handoff", "consumer"),
                packet_builder=build_search_atp_consumer_handoff_stabilization_packet_wrapper,
                payload_builder=build_search_atp_consumer_handoff_stabilization_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_atp_stage_b_closeout",
                title="Search ATP Stage B closeout packet",
                packet_family="search_atp_stage_b_closeout.v1",
                phase="platform_stage_b",
                tags=("stage_b", "atp", "closeout"),
                packet_builder=build_search_atp_stage_b_closeout_packet_wrapper,
                payload_builder=build_search_atp_stage_b_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_optimize_consumerization",
                title="Search Stage C optimize consumerization packet",
                packet_family="search_stage_c_optimize_consumerization.v1",
                phase="platform_stage_c",
                tags=("stage_c", "optimize", "consumer"),
                packet_builder=build_search_stage_c_optimize_consumerization_packet_wrapper,
                payload_builder=build_search_stage_c_optimize_consumerization_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_rl_consumerization",
                title="Search Stage C RL consumerization packet",
                packet_family="search_stage_c_rl_consumerization.v1",
                phase="platform_stage_c",
                tags=("stage_c", "rl", "consumer"),
                packet_builder=build_search_stage_c_rl_consumerization_packet_wrapper,
                payload_builder=build_search_stage_c_rl_consumerization_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_repair_loop_consumer_lane",
                title="Search Stage C repair-loop consumer lane packet",
                packet_family="search_stage_c_repair_loop_consumer_lane.v1",
                phase="platform_stage_c",
                tags=("stage_c", "repair_loop", "consumer"),
                packet_builder=build_search_stage_c_repair_loop_consumer_lane_packet_wrapper,
                payload_builder=build_search_stage_c_repair_loop_consumer_lane_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_consumer_convergence",
                title="Search Stage C consumer convergence packet",
                packet_family="search_stage_c_consumer_convergence.v1",
                phase="platform_stage_c",
                tags=("stage_c", "consumer", "convergence"),
                packet_builder=build_search_stage_c_consumer_convergence_packet_wrapper,
                payload_builder=build_search_stage_c_consumer_convergence_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_repair_loop_containment",
                title="Search Stage C repair-loop containment packet",
                packet_family="search_stage_c_repair_loop_containment.v1",
                phase="platform_stage_c",
                tags=("stage_c", "repair_loop", "containment"),
                packet_builder=build_search_stage_c_repair_loop_containment_packet_wrapper,
                payload_builder=build_search_stage_c_repair_loop_containment_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="search_stage_c_closeout",
                title="Search Stage C closeout packet",
                packet_family="search_stage_c_closeout.v1",
                phase="platform_stage_c",
                tags=("stage_c", "closeout", "consumer"),
                packet_builder=build_search_stage_c_closeout_packet_wrapper,
                payload_builder=build_search_stage_c_closeout_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v3_rsa_replication",
                title="DAG V3 RSA replication packet",
                packet_family="dag_v3_rsa_replication",
                phase="dag_v3",
                tags=("replication", "rsa", "baseline"),
                packet_builder=build_dag_v3_rsa_replication_packet,
                payload_builder=build_dag_v3_rsa_replication_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_replication_v1_got_sorting",
                title="DAG replication V1 GoT sorting packet",
                packet_family="dag_replication_v1_got_sorting",
                phase="replication_v1",
                tags=("replication", "got", "sorting"),
                packet_builder=build_dag_replication_v1_got_sorting_packet,
                payload_builder=build_dag_replication_v1_got_sorting_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v4_bavt",
                title="DAG V4 BAVT packet",
                packet_family="dag_v4_bavt",
                phase="dag_v4",
                tags=("frontier", "adaptive", "budget"),
                packet_builder=build_dag_v4_bavt_packet,
                payload_builder=build_dag_v4_bavt_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_replication_v1_tot_game24",
                title="DAG replication V1 ToT Game of 24 packet",
                packet_family="dag_replication_v1_tot_game24",
                phase="replication_v1",
                tags=("replication", "tot", "game24"),
                packet_builder=build_dag_replication_v1_tot_game24_packet,
                payload_builder=build_dag_replication_v1_tot_game24_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v4_team_of_thoughts",
                title="DAG V4 Team of Thoughts packet",
                packet_family="dag_v4_team_of_thoughts",
                phase="dag_v4",
                tags=("heterogeneity", "team", "roles"),
                packet_builder=build_dag_v4_team_of_thoughts_packet,
                payload_builder=build_dag_v4_team_of_thoughts_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v4_dci",
                title="DAG V4 DCI packet",
                packet_family="dag_v4_dci",
                phase="dag_v4",
                tags=("typed_acts", "shared_workspace", "heterogeneity"),
                packet_builder=build_dag_v4_dci_packet,
                payload_builder=build_dag_v4_dci_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v4_final_adjudication",
                title="DAG V4 final adjudication packet",
                packet_family="dag_v4_final_adjudication",
                phase="dag_v4",
                tags=("closeout", "adjudication", "freeze"),
                packet_builder=build_dag_v4_final_adjudication_packet,
                payload_builder=build_dag_v4_final_adjudication_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_cross_system_deployment_readiness",
                title="DAG V6 cross-system deployment readiness packet",
                packet_family="dag_v6_cross_system_deployment_readiness",
                phase="dag_v6",
                tags=("deployment", "cross_system", "handoff"),
                packet_builder=build_search_cross_system_deployment_readiness_packet,
                payload_builder=build_search_cross_system_deployment_readiness_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_atp_boundary_control_v2",
                title="DAG V6 ATP boundary control v2 packet",
                packet_family="dag_v6_atp_boundary_control_v2",
                phase="dag_v6",
                tags=("deployment", "atp", "boundary_control"),
                packet_builder=build_search_atp_boundary_control_v2_packet,
                payload_builder=build_search_atp_boundary_control_v2_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_atp_deployment_readiness",
                title="DAG V6 ATP deployment readiness packet",
                packet_family="dag_v6_atp_deployment_readiness",
                phase="dag_v6",
                tags=("deployment", "atp", "readiness"),
                packet_builder=build_search_atp_deployment_readiness_packet,
                payload_builder=build_search_atp_deployment_readiness_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_atp_operator_triage",
                title="DAG V6 ATP operator triage packet",
                packet_family="dag_v6_atp_operator_triage",
                phase="dag_v6",
                tags=("deployment", "atp", "operator"),
                packet_builder=build_search_atp_operator_triage_packet,
                payload_builder=build_search_atp_operator_triage_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_optimize_rl_handoff_regression",
                title="DAG V6 optimize/RL handoff regression packet",
                packet_family="dag_v6_optimize_rl_handoff_regression",
                phase="dag_v6",
                tags=("deployment", "consumer", "regression"),
                packet_builder=build_search_optimize_rl_handoff_regression_packet,
                payload_builder=build_search_optimize_rl_handoff_regression_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_repair_loop_deployment_readiness",
                title="DAG V6 repair-loop deployment readiness packet",
                packet_family="dag_v6_repair_loop_deployment_readiness",
                phase="dag_v6",
                tags=("deployment", "repair_loop", "readiness"),
                packet_builder=build_search_repair_loop_deployment_readiness_packet,
                payload_builder=build_search_repair_loop_deployment_readiness_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_optimize_consumer_expansion",
                title="DAG V6 optimize consumer expansion packet",
                packet_family="dag_v6_optimize_consumer_expansion",
                phase="dag_v6",
                tags=("deployment", "optimize", "consumer"),
                packet_builder=build_search_optimize_consumer_expansion_packet,
                payload_builder=build_search_optimize_consumer_expansion_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_rl_consumer_expansion",
                title="DAG V6 RL consumer expansion packet",
                packet_family="dag_v6_rl_consumer_expansion",
                phase="dag_v6",
                tags=("deployment", "rl", "consumer"),
                packet_builder=build_search_rl_consumer_expansion_packet,
                payload_builder=build_search_rl_consumer_expansion_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v6_ctrees_boundary_canary",
                title="DAG V6 C-Trees boundary canary packet",
                packet_family="dag_v6_ctrees_boundary_canary",
                phase="dag_v6",
                tags=("deployment", "ctrees", "boundary_canary"),
                packet_builder=build_search_ctrees_boundary_canary_packet,
                payload_builder=build_search_ctrees_boundary_canary_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v5_atp_domain_pilot",
                title="DAG V5 ATP bounded domain pilot",
                packet_family="dag_v5_atp_domain_pilot",
                phase="dag_v5",
                tags=("domain_pilot", "atp", "bounded"),
                packet_builder=build_search_atp_domain_pilot,
                payload_builder=build_search_atp_domain_pilot_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v5_repair_loop_domain_pilot",
                title="DAG V5 repair-loop bounded domain pilot",
                packet_family="dag_v5_repair_loop_domain_pilot",
                phase="dag_v5",
                tags=("domain_pilot", "repair_loop", "bounded"),
                packet_builder=build_search_repair_loop_domain_pilot,
                payload_builder=build_search_repair_loop_domain_pilot_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v5_tool_planning_tree_control",
                title="DAG V5 tool-planning tree control packet",
                packet_family="dag_v5_tool_planning_tree_control",
                phase="dag_v5",
                tags=("research_control", "tool_planning", "bounded"),
                packet_builder=build_search_tool_planning_tree_control_packet,
                payload_builder=build_search_tool_planning_tree_control_packet_payload,
            ),
            SearchStudyRegistryEntry(
                study_key="dag_v5_general_agent_control",
                title="DAG V5 general-agent control packet",
                packet_family="dag_v5_general_agent_control",
                phase="dag_v5",
                tags=("research_control", "general_agent", "bounded"),
                packet_builder=build_search_general_agent_control_packet,
                payload_builder=build_search_general_agent_control_packet_payload,
            ),
        ]
    )


def run_search_study(
    study_key: str,
    *,
    mode: str = "spec",
    registry: SearchStudyRegistry | None = None,
) -> SearchStudyRunResult:
    return (registry or build_default_search_study_registry()).run(study_key, mode=mode)


def inspect_search_study(
    study_key: str,
    *,
    mode: str = "spec",
    registry: SearchStudyRegistry | None = None,
) -> Dict[str, object]:
    return run_search_study(study_key, mode=mode, registry=registry).inspect()
