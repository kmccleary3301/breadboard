from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .schema import (
    DatasetExportUnit,
    DecisionRecord,
    EffectRecord,
    EvaluationAnnotation,
    ObservationSlice,
    TrajectoryGraph,
)


def _observation_ids_for_candidate_ids(
    observations: List[ObservationSlice], candidate_ids: List[str]
) -> List[str]:
    by_candidate_id = {item.candidate_id: item.observation_id for item in observations}
    return [by_candidate_id[item] for item in candidate_ids if item in by_candidate_id]


def _effect_for_decision(graph: TrajectoryGraph, decision: DecisionRecord) -> Optional[EffectRecord]:
    for effect in graph.effects:
        if effect.event_id == decision.event_id:
            return effect
    return None


def _annotations_for_decision(graph: TrajectoryGraph, decision: DecisionRecord) -> List[EvaluationAnnotation]:
    wanted = set(decision.assessment_ids)
    return [item for item in graph.evaluation_annotations if item.annotation_id in wanted]


def build_dataset_export_unit(
    *,
    export_unit_id: str,
    export_kind: str,
    graph: TrajectoryGraph,
    record_payload: Mapping[str, Any],
    metadata: Optional[Mapping[str, Any]] = None,
) -> DatasetExportUnit:
    return DatasetExportUnit(
        export_unit_id=export_unit_id,
        export_kind=export_kind,
        graph_id=graph.graph_id,
        rollout_descriptor=graph.rollout_descriptor,
        environment_descriptor=graph.environment_descriptor,
        policy_provenance=list(graph.policy_provenance),
        evaluation_annotations=list(graph.evaluation_annotations),
        compaction_manifests=list(graph.compaction_manifests),
        record_payload=dict(record_payload),
        metadata={"export_family": "rl_v1_alpha", **dict(metadata or {})},
    )


def export_sft_distillation_unit(graph: TrajectoryGraph) -> DatasetExportUnit:
    target_decision = graph.decisions[-1]
    target_effect = _effect_for_decision(graph, target_decision)
    payload = {
        "observation_ids": _observation_ids_for_candidate_ids(graph.observations, target_decision.input_candidate_ids),
        "target_decision_id": target_decision.decision_id,
        "target_operator_kind": target_decision.operator_kind,
        "target_effect_id": target_effect.effect_id if target_effect else None,
        "output_candidate_ids": list(target_decision.output_candidate_ids),
        "annotation_ids": list(target_decision.assessment_ids),
    }
    return build_dataset_export_unit(
        export_unit_id=f"{graph.graph_id}.export.sft_distillation.v1",
        export_kind="sft_distillation",
        graph=graph,
        record_payload=payload,
        metadata={"flattening_boundary": "edge_only"},
    )


def export_rl_transition_segment_unit(graph: TrajectoryGraph) -> DatasetExportUnit:
    transitions: List[Dict[str, Any]] = []
    for decision in graph.decisions:
        effect = _effect_for_decision(graph, decision)
        annotations = _annotations_for_decision(graph, decision)
        transitions.append(
            {
                "decision_id": decision.decision_id,
                "event_id": decision.event_id,
                "observation_ids": _observation_ids_for_candidate_ids(graph.observations, decision.input_candidate_ids),
                "output_candidate_ids": list(decision.output_candidate_ids),
                "effect_id": effect.effect_id if effect else None,
                "annotation_ids": [item.annotation_id for item in annotations],
            }
        )
    return build_dataset_export_unit(
        export_unit_id=f"{graph.graph_id}.export.rl_transition_segment.v1",
        export_kind="rl_transition_segment",
        graph=graph,
        record_payload={"transitions": transitions},
        metadata={"segment_count": len(transitions)},
    )


def export_verifier_example_unit(graph: TrajectoryGraph) -> DatasetExportUnit:
    verifier_annotations = [
        item
        for item in graph.evaluation_annotations
        if item.channel in {"verify", "execute"} or str(item.metadata.get("backend_kind") or "").startswith("exact_")
    ]
    payload = {
        "verifier_annotations": [
            {
                "annotation_id": item.annotation_id,
                "channel": item.channel,
                "status": item.status,
                "score_value": item.score_value,
                "artifact_refs": list(item.artifact_refs),
            }
            for item in verifier_annotations
        ]
    }
    return build_dataset_export_unit(
        export_unit_id=f"{graph.graph_id}.export.verifier_example.v1",
        export_kind="verifier_example",
        graph=graph,
        record_payload=payload,
        metadata={"verifier_annotation_count": len(verifier_annotations)},
    )


def build_dataset_export_unit_core_view(unit: DatasetExportUnit) -> Dict[str, Any]:
    return {
        "export_kind": unit.export_kind,
        "graph_id": unit.graph_id,
        "rollout_source_ref": unit.rollout_descriptor.source_ref,
        "recipe_kind": unit.rollout_descriptor.recipe_kind,
        "environment_id": unit.environment_descriptor.environment_id,
        "policy_ids": [item.policy_id for item in unit.policy_provenance],
        "evaluation_annotations": [item.to_dict() for item in unit.evaluation_annotations],
        "compaction_manifests": [item.to_dict() for item in unit.compaction_manifests],
        "record_payload": dict(unit.record_payload),
    }
