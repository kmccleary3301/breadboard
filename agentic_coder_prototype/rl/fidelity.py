from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Sequence

from .export import build_dataset_export_unit_core_view
from .schema import DatasetExportUnit, EvaluationAnnotation, EvaluationPackManifest, ExportManifest, TrajectoryGraph


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_evaluation_pack_manifest(
    *,
    evaluation_pack_id: str,
    annotations: Sequence[EvaluationAnnotation],
    rubric_version: str,
    visibility_boundary: str,
    reduction_context: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> EvaluationPackManifest:
    channels = list(dict.fromkeys(item.channel for item in annotations))
    return EvaluationPackManifest(
        evaluation_pack_id=evaluation_pack_id,
        annotation_ids=[item.annotation_id for item in annotations],
        channels=channels,
        rubric_version=rubric_version,
        visibility_boundary=visibility_boundary,
        delayed_evaluation_allowed=any(item.delayed for item in annotations),
        reduction_context=reduction_context,
        metadata=dict(metadata or {}),
    )


def build_export_fingerprint(
    *,
    export_units: Sequence[DatasetExportUnit],
    evaluation_pack_manifest: EvaluationPackManifest,
    split_kind: str,
    canonicalization_policy: str,
    transform_version: str,
    contamination_controls: Optional[Sequence[str]] = None,
) -> str:
    unit_views: Dict[str, Any] = {}
    for unit in sorted(export_units, key=lambda item: item.export_unit_id):
        core_view = build_dataset_export_unit_core_view(unit)
        core_view.pop("origin_kind", None)
        unit_views[unit.export_unit_id] = core_view
    payload = {
        "export_units": unit_views,
        "evaluation_pack_id": evaluation_pack_manifest.evaluation_pack_id,
        "annotation_ids": list(evaluation_pack_manifest.annotation_ids),
        "channels": list(evaluation_pack_manifest.channels),
        "rubric_version": evaluation_pack_manifest.rubric_version,
        "visibility_boundary": evaluation_pack_manifest.visibility_boundary,
        "reduction_context": evaluation_pack_manifest.reduction_context,
        "split_kind": split_kind,
        "canonicalization_policy": canonicalization_policy,
        "transform_version": transform_version,
        "contamination_controls": sorted(str(item) for item in (contamination_controls or [])),
    }
    return hashlib.sha256(_stable_json(payload).encode("ascii")).hexdigest()


def build_export_manifest(
    *,
    export_manifest_id: str,
    export_units: Sequence[DatasetExportUnit],
    evaluation_pack_manifest: EvaluationPackManifest,
    split_kind: str,
    canonicalization_policy: str,
    transform_version: str,
    contamination_controls: Optional[Sequence[str]] = None,
    fidelity_tier: str = "replay_stable_contract",
    metadata: Optional[Mapping[str, Any]] = None,
) -> ExportManifest:
    controls = [str(item) for item in contamination_controls or []]
    return ExportManifest(
        export_manifest_id=export_manifest_id,
        export_unit_ids=[unit.export_unit_id for unit in export_units],
        export_kinds=sorted({unit.export_kind for unit in export_units}),
        source_graph_ids=sorted({unit.graph_id for unit in export_units}),
        evaluation_pack_id=evaluation_pack_manifest.evaluation_pack_id,
        split_kind=split_kind,
        canonicalization_policy=canonicalization_policy,
        transform_version=transform_version,
        export_fingerprint=build_export_fingerprint(
            export_units=export_units,
            evaluation_pack_manifest=evaluation_pack_manifest,
            split_kind=split_kind,
            canonicalization_policy=canonicalization_policy,
            transform_version=transform_version,
            contamination_controls=controls,
        ),
        contamination_controls=controls,
        origin_kinds=sorted({unit.rollout_descriptor.origin_kind for unit in export_units}),
        fidelity_tier=fidelity_tier,
        metadata={
            "export_unit_count": len(export_units),
            "channel_count": len(evaluation_pack_manifest.channels),
            **dict(metadata or {}),
        },
    )


def build_export_manifest_parity_view(manifest: ExportManifest) -> Dict[str, Any]:
    return {
        "export_unit_ids": list(manifest.export_unit_ids),
        "export_kinds": list(manifest.export_kinds),
        "source_graph_ids": list(manifest.source_graph_ids),
        "evaluation_pack_id": manifest.evaluation_pack_id,
        "split_kind": manifest.split_kind,
        "canonicalization_policy": manifest.canonicalization_policy,
        "transform_version": manifest.transform_version,
        "export_fingerprint": manifest.export_fingerprint,
        "contamination_controls": list(manifest.contamination_controls),
        "fidelity_tier": manifest.fidelity_tier,
    }


def build_compaction_fidelity_report(
    *,
    graph: TrajectoryGraph,
    export_unit: DatasetExportUnit,
    evaluation_pack_manifest: EvaluationPackManifest,
    export_manifest: ExportManifest,
) -> Dict[str, Any]:
    graph_manifest_ids = [item.manifest_id for item in graph.compaction_manifests]
    export_manifest_ids = [item.manifest_id for item in export_unit.compaction_manifests]
    return {
        "graph_id": graph.graph_id,
        "export_unit_id": export_unit.export_unit_id,
        "evaluation_pack_id": evaluation_pack_manifest.evaluation_pack_id,
        "export_manifest_id": export_manifest.export_manifest_id,
        "graph_compaction_manifest_ids": graph_manifest_ids,
        "export_compaction_manifest_ids": export_manifest_ids,
        "all_compaction_refs_preserved": graph_manifest_ids == export_manifest_ids,
        "lossy_policy_view": any(
            item.dropped_fields or item.omitted_artifact_refs for item in graph.compaction_manifests
        ),
        "fidelity_tiers": [item.fidelity_tier for item in export_unit.compaction_manifests],
    }


def build_delayed_evaluation_fidelity_report(
    *,
    graph: TrajectoryGraph,
    evaluation_pack_manifest: EvaluationPackManifest,
    export_manifest: ExportManifest,
) -> Dict[str, Any]:
    delayed_annotations = [item for item in graph.evaluation_annotations if item.delayed]
    available_markers = {
        item.annotation_id: item.metadata.get("available_at") for item in delayed_annotations
    }
    visibility_markers = {
        item.annotation_id: item.metadata.get("visibility_boundary") for item in delayed_annotations
    }
    return {
        "graph_id": graph.graph_id,
        "evaluation_pack_id": evaluation_pack_manifest.evaluation_pack_id,
        "export_manifest_id": export_manifest.export_manifest_id,
        "delayed_annotation_ids": [item.annotation_id for item in delayed_annotations],
        "delayed_annotation_count": len(delayed_annotations),
        "all_available_at_explicit": all(bool(value) for value in available_markers.values()),
        "policy_view_safe": all(value == "policy_view" for value in visibility_markers.values()),
        "available_at": available_markers,
    }
