from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

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
