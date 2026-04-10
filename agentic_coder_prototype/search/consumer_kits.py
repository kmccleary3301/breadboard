from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence

from .examples import (
    build_dag_v4_optimize_consumer_packet,
    build_dag_v4_rl_consumer_packet,
)


def _sorted_unique_texts(values: Iterable[Any]) -> tuple[str, ...]:
    seen = {str(item or "").strip() for item in values}
    return tuple(sorted(text for text in seen if text))


def _sequence_to_tuple(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(item or "").strip() for item in values if str(item or "").strip())


def _lookup_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str,
) -> Dict[str, Dict[str, Any]]:
    return {
        str(row[key]): dict(row)
        for row in rows
        if str(row.get(key) or "").strip()
    }


@dataclass(frozen=True)
class SearchConsumerProofRow:
    target_family: str
    topology_class: str
    selected_candidate_id: str | None
    benchmark_packet: str
    consumer_kind: str
    artifact_kinds: tuple[str, ...]
    handoff_contract: tuple[str, ...]
    measured_shadow_semantics_required: bool
    lost_semantics_count: int
    evidence_sources: tuple[str, ...]
    issue_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "selected_candidate_id": self.selected_candidate_id,
            "benchmark_packet": self.benchmark_packet,
            "consumer_kind": self.consumer_kind,
            "artifact_kinds": list(self.artifact_kinds),
            "handoff_contract": list(self.handoff_contract),
            "measured_shadow_semantics_required": self.measured_shadow_semantics_required,
            "lost_semantics_count": self.lost_semantics_count,
            "evidence_sources": list(self.evidence_sources),
            "issue_locus": self.issue_locus,
        }


@dataclass(frozen=True)
class SearchConsumerSeamDiagnostic:
    diagnostic_id: str
    optimize_seam_labels: tuple[str, ...]
    rl_seam_labels: tuple[str, ...]
    optimize_issue_loci: tuple[str, ...]
    rl_issue_loci: tuple[str, ...]
    combined_issue_loci: tuple[str, ...]
    repeated_shape_gap_detected: bool
    dag_runtime_missing_truth_detected: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "diagnostic_id": self.diagnostic_id,
            "optimize_seam_labels": list(self.optimize_seam_labels),
            "rl_seam_labels": list(self.rl_seam_labels),
            "optimize_issue_loci": list(self.optimize_issue_loci),
            "rl_issue_loci": list(self.rl_issue_loci),
            "combined_issue_loci": list(self.combined_issue_loci),
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "dag_runtime_missing_truth_detected": self.dag_runtime_missing_truth_detected,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchOptimizeHandoffKit:
    kit_id: str
    consumer_kind: str
    consumer_manifest_id: str
    composition_id: str
    source_tranche_note: str
    rows: tuple[SearchConsumerProofRow, ...]
    seam_labels: tuple[str, ...]
    awkwardness_classification: str
    repeated_shape_gap_detected: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "consumer_kind": self.consumer_kind,
            "consumer_manifest_id": self.consumer_manifest_id,
            "composition_id": self.composition_id,
            "source_tranche_note": self.source_tranche_note,
            "rows": [row.to_dict() for row in self.rows],
            "seam_labels": list(self.seam_labels),
            "awkwardness_classification": self.awkwardness_classification,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "final_decision": self.final_decision,
        }


@dataclass(frozen=True)
class SearchOptimizeComparisonKit:
    kit_id: str
    manifest_id: str
    comparison_id: str
    comparison_protocol: str
    objective_keys: tuple[str, ...]
    handoff_preserved_fields: tuple[str, ...]
    helper_only_handoff: bool
    awkwardness_classification: str
    repeated_shape_gap_detected: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "manifest_id": self.manifest_id,
            "comparison_id": self.comparison_id,
            "comparison_protocol": self.comparison_protocol,
            "objective_keys": list(self.objective_keys),
            "handoff_preserved_fields": list(self.handoff_preserved_fields),
            "helper_only_handoff": self.helper_only_handoff,
            "awkwardness_classification": self.awkwardness_classification,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "final_decision": self.final_decision,
        }


@dataclass(frozen=True)
class SearchRLHandoffKit:
    kit_id: str
    consumer_kind: str
    consumer_export_manifest_id: str
    composition_id: str
    rows: tuple[SearchConsumerProofRow, ...]
    seam_labels: tuple[str, ...]
    awkwardness_classification: str
    repeated_shape_gap_detected: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "consumer_kind": self.consumer_kind,
            "consumer_export_manifest_id": self.consumer_export_manifest_id,
            "composition_id": self.composition_id,
            "rows": [row.to_dict() for row in self.rows],
            "seam_labels": list(self.seam_labels),
            "awkwardness_classification": self.awkwardness_classification,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "final_decision": self.final_decision,
        }


@dataclass(frozen=True)
class SearchRLReplayParityKit:
    kit_id: str
    workload_family: str
    source_packet_id: str
    live_export_manifest_id: str
    replay_export_manifest_id: str
    graph_parity_equal: bool
    export_manifest_parity_equal: bool
    handoff_preserved_fields: tuple[str, ...]
    awkwardness_classification: str
    repeated_shape_gap_detected: bool
    final_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "kit_id": self.kit_id,
            "workload_family": self.workload_family,
            "source_packet_id": self.source_packet_id,
            "live_export_manifest_id": self.live_export_manifest_id,
            "replay_export_manifest_id": self.replay_export_manifest_id,
            "graph_parity_equal": self.graph_parity_equal,
            "export_manifest_parity_equal": self.export_manifest_parity_equal,
            "handoff_preserved_fields": list(self.handoff_preserved_fields),
            "awkwardness_classification": self.awkwardness_classification,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "final_decision": self.final_decision,
        }


def _build_consumer_rows(packet: Mapping[str, Any]) -> tuple[SearchConsumerProofRow, ...]:
    handoff_lookup = _lookup_rows(
        [item.to_dict() if hasattr(item, "to_dict") else item for item in packet["consumer_handoff_packets"]],
        key="target_family",
    )
    integrity_lookup = _lookup_rows(packet["integrity_rows"], key="target_family")
    issue_lookup = _lookup_rows(packet["consumer_issue_classification"], key="target_family")
    rows = []
    for row in packet["source_rows"]:
        target_family = str(row["target_family"])
        handoff = handoff_lookup[target_family]
        integrity = integrity_lookup[target_family]
        issue = issue_lookup[target_family]
        rows.append(
            SearchConsumerProofRow(
                target_family=target_family,
                topology_class=str(row["topology_class"]),
                selected_candidate_id=row.get("selected_candidate_id"),
                benchmark_packet=str(row["benchmark_packet"]),
                consumer_kind=str(packet["consumer_kind"]),
                artifact_kinds=_sequence_to_tuple(handoff.get("artifact_kinds") or []),
                handoff_contract=_sequence_to_tuple(handoff.get("handoff_contract") or []),
                measured_shadow_semantics_required=bool(integrity["measured_shadow_semantics_required"]),
                lost_semantics_count=int(integrity["lost_semantics_count"]),
                evidence_sources=_sequence_to_tuple(integrity.get("evidence_sources") or []),
                issue_locus=str(issue["locus"]),
            )
        )
    return tuple(rows)


def build_search_optimize_handoff_kit() -> SearchOptimizeHandoffKit:
    from ..optimize import (
        build_next_frontier_dag_to_optimize_composition_packet,
        build_next_frontier_optimize_final_closeout_packet,
    )

    packet = build_dag_v4_optimize_consumer_packet()
    composition = build_next_frontier_dag_to_optimize_composition_packet()
    closeout = build_next_frontier_optimize_final_closeout_packet()
    return SearchOptimizeHandoffKit(
        kit_id="search.consumer.optimize.handoff_kit.v1",
        consumer_kind=str(packet["consumer_kind"]),
        consumer_manifest_id=str(composition["consumer_manifest_id"]),
        composition_id=str(composition["composition_id"]),
        source_tranche_note=str(composition["source_tranche_note"]),
        rows=_build_consumer_rows(packet),
        seam_labels=_sequence_to_tuple(packet["composition_seam_packet"].seam_labels),
        awkwardness_classification=str(composition["composition_report"]["awkwardness_classification"]),
        repeated_shape_gap_detected=bool(composition["composition_report"]["repeated_shape_gap_detected"]),
        final_decision=str(closeout["final_decision"]),
    )


def build_search_optimize_comparison_kit() -> SearchOptimizeComparisonKit:
    from ..optimize import (
        build_next_frontier_dag_to_optimize_composition_packet,
        build_next_frontier_optimize_final_closeout_packet,
        build_next_frontier_optimize_second_cohort_packet,
    )

    cohort = build_next_frontier_optimize_second_cohort_packet()
    composition = build_next_frontier_dag_to_optimize_composition_packet()
    closeout = build_next_frontier_optimize_final_closeout_packet()
    objective_breakdown = cohort["objective_breakdown_result"].to_dict()
    comparison_result = cohort["comparison_result"].to_dict()
    return SearchOptimizeComparisonKit(
        kit_id="search.consumer.optimize.comparison_kit.v1",
        manifest_id=cohort["manifest"].manifest_id,
        comparison_id=str(comparison_result["comparison_id"]),
        comparison_protocol=cohort["manifest"].comparison_protocol,
        objective_keys=_sorted_unique_texts(objective_breakdown.get("aggregate_objectives", {}).keys()),
        handoff_preserved_fields=_sequence_to_tuple(composition["handoff_contract"]["preserved_fields"]),
        helper_only_handoff=bool(composition["handoff_contract"]["helper_only_handoff"]),
        awkwardness_classification=str(composition["composition_report"]["awkwardness_classification"]),
        repeated_shape_gap_detected=bool(composition["composition_report"]["repeated_shape_gap_detected"]),
        final_decision=str(closeout["final_decision"]),
    )


def build_search_rl_handoff_kit() -> SearchRLHandoffKit:
    from ..rl import (
        build_next_frontier_dag_to_rl_composition_packet,
        build_next_frontier_rl_final_closeout_packet,
    )

    packet = build_dag_v4_rl_consumer_packet()
    composition = build_next_frontier_dag_to_rl_composition_packet()
    closeout = build_next_frontier_rl_final_closeout_packet()
    return SearchRLHandoffKit(
        kit_id="search.consumer.rl.handoff_kit.v1",
        consumer_kind=str(packet["consumer_kind"]),
        consumer_export_manifest_id=str(composition["consumer_export_manifest_id"]),
        composition_id=str(composition["composition_id"]),
        rows=_build_consumer_rows(packet),
        seam_labels=_sequence_to_tuple(packet["composition_seam_packet"].seam_labels),
        awkwardness_classification=str(composition["composition_report"]["awkwardness_classification"]),
        repeated_shape_gap_detected=bool(composition["composition_report"]["repeated_shape_gap_detected"]),
        final_decision=str(closeout["final_decision"]),
    )


def build_search_rl_replay_parity_kit() -> SearchRLReplayParityKit:
    from ..rl import (
        build_next_frontier_dag_to_rl_composition_packet,
        build_next_frontier_rl_final_closeout_packet,
        build_next_frontier_rl_replay_live_parity_packet,
    )

    parity = build_next_frontier_rl_replay_live_parity_packet()
    composition = build_next_frontier_dag_to_rl_composition_packet()
    closeout = build_next_frontier_rl_final_closeout_packet()
    return SearchRLReplayParityKit(
        kit_id="search.consumer.rl.replay_parity_kit.v1",
        workload_family=str(parity["workload_family"]),
        source_packet_id=str(parity["source_packet_id"]),
        live_export_manifest_id=parity["live_export_manifest"].export_manifest_id,
        replay_export_manifest_id=parity["replay_export_manifest"].export_manifest_id,
        graph_parity_equal=parity["live_parity_view"] == parity["replay_parity_view"],
        export_manifest_parity_equal=(
            parity["live_export_manifest_parity_view"] == parity["replay_export_manifest_parity_view"]
        ),
        handoff_preserved_fields=_sequence_to_tuple(composition["handoff_contract"]["preserved_fields"]),
        awkwardness_classification=str(composition["composition_report"]["awkwardness_classification"]),
        repeated_shape_gap_detected=bool(composition["composition_report"]["repeated_shape_gap_detected"]),
        final_decision=str(closeout["final_decision"]),
    )


def build_search_consumer_seam_diagnostic() -> SearchConsumerSeamDiagnostic:
    from ..optimize import (
        build_next_frontier_dag_to_optimize_composition_packet,
        build_next_frontier_optimize_final_closeout_packet,
    )
    from ..rl import (
        build_next_frontier_dag_to_rl_composition_packet,
        build_next_frontier_rl_final_closeout_packet,
    )

    optimize_packet = build_dag_v4_optimize_consumer_packet()
    rl_packet = build_dag_v4_rl_consumer_packet()
    optimize_composition = build_next_frontier_dag_to_optimize_composition_packet()
    rl_composition = build_next_frontier_dag_to_rl_composition_packet()
    optimize_closeout = build_next_frontier_optimize_final_closeout_packet()
    rl_closeout = build_next_frontier_rl_final_closeout_packet()

    optimize_issue_loci = _sorted_unique_texts(
        item["locus"] for item in optimize_packet["consumer_issue_classification"]
    )
    rl_issue_loci = _sorted_unique_texts(
        item["locus"] for item in rl_packet["consumer_issue_classification"]
    )
    combined_issue_loci = _sorted_unique_texts(optimize_issue_loci + rl_issue_loci)
    repeated_shape_gap_detected = any(
        (
            optimize_composition["composition_report"]["repeated_shape_gap_detected"],
            rl_composition["composition_report"]["repeated_shape_gap_detected"],
            optimize_closeout["repeated_shape_gap_detected"],
            rl_closeout["repeated_shape_gap_detected"],
        )
    )
    dag_runtime_missing_truth_detected = bool(
        repeated_shape_gap_detected
        or optimize_packet["repeated_shape_update"]["dag_kernel_change_required"]
        or rl_packet["repeated_shape_update"]["dag_kernel_change_required"]
    )
    return SearchConsumerSeamDiagnostic(
        diagnostic_id="search.consumer.seam_diagnostic.v1",
        optimize_seam_labels=_sequence_to_tuple(optimize_packet["composition_seam_packet"].seam_labels),
        rl_seam_labels=_sequence_to_tuple(rl_packet["composition_seam_packet"].seam_labels),
        optimize_issue_loci=optimize_issue_loci,
        rl_issue_loci=rl_issue_loci,
        combined_issue_loci=combined_issue_loci,
        repeated_shape_gap_detected=repeated_shape_gap_detected,
        dag_runtime_missing_truth_detected=dag_runtime_missing_truth_detected,
        final_classification=(
            "consumer_and_helper_only"
            if combined_issue_loci == ("consumer_local_only", "helper_level_only")
            or combined_issue_loci == ("adapter_local_only", "consumer_local_only", "helper_level_only")
            else "mixed_or_unexpected"
        ),
    )
