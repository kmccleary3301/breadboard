from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .schema import DatasetExportUnit, EvaluationPackManifest, ExportManifest


def build_export_unit_conformance_view(
    *,
    export_unit: DatasetExportUnit,
    evaluation_pack_manifest: EvaluationPackManifest,
    export_manifest: ExportManifest,
) -> Dict[str, Any]:
    return {
        "export_unit_id": export_unit.export_unit_id,
        "export_kind": export_unit.export_kind,
        "graph_id": export_unit.graph_id,
        "origin_kind": export_unit.rollout_descriptor.origin_kind,
        "evaluation_pack_id": evaluation_pack_manifest.evaluation_pack_id,
        "export_manifest_id": export_manifest.export_manifest_id,
        "split_kind": export_manifest.split_kind,
        "canonicalization_policy": export_manifest.canonicalization_policy,
        "transform_version": export_manifest.transform_version,
        "export_fingerprint": export_manifest.export_fingerprint,
        "fidelity_tier": export_manifest.fidelity_tier,
        "contamination_controls": list(export_manifest.contamination_controls),
        "delayed_evaluation_present": any(item.delayed for item in export_unit.evaluation_annotations),
        "compaction_manifest_count": len(export_unit.compaction_manifests),
        "policy_count": len(export_unit.policy_provenance),
    }


def build_export_conformance_packet(
    *,
    packet_id: str,
    export_units: Sequence[DatasetExportUnit],
    evaluation_pack_manifest: EvaluationPackManifest,
    export_manifest: ExportManifest,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    conformance_views = [
        build_export_unit_conformance_view(
            export_unit=unit,
            evaluation_pack_manifest=evaluation_pack_manifest,
            export_manifest=export_manifest,
        )
        for unit in export_units
    ]
    return {
        "packet_id": packet_id,
        "evaluation_pack_manifest": evaluation_pack_manifest.to_dict(),
        "export_manifest": export_manifest.to_dict(),
        "unit_conformance_views": conformance_views,
        "split_provenance": {
            "split_kind": export_manifest.split_kind,
            "canonicalization_policy": export_manifest.canonicalization_policy,
            "transform_version": export_manifest.transform_version,
            "contamination_controls": list(export_manifest.contamination_controls),
        },
        "summary": {
            "export_unit_count": len(conformance_views),
            "export_kind_counts": {
                kind: sum(1 for item in conformance_views if item["export_kind"] == kind)
                for kind in sorted({item["export_kind"] for item in conformance_views})
            },
            "delayed_evaluation_unit_count": sum(
                1 for item in conformance_views if item["delayed_evaluation_present"]
            ),
            "compaction_manifest_total": sum(item["compaction_manifest_count"] for item in conformance_views),
            "fidelity_tier": export_manifest.fidelity_tier,
        },
        "metadata": dict(metadata or {}),
    }


def build_export_conformance_parity_view(packet: Mapping[str, Any]) -> Dict[str, Any]:
    unit_views = []
    for item in packet.get("unit_conformance_views") or []:
        filtered = dict(item)
        filtered.pop("origin_kind", None)
        filtered.pop("export_manifest_id", None)
        unit_views.append(filtered)
    return {
        "evaluation_pack_manifest": {
            "evaluation_pack_id": packet["evaluation_pack_manifest"]["evaluation_pack_id"],
            "annotation_ids": list(packet["evaluation_pack_manifest"]["annotation_ids"]),
            "channels": list(packet["evaluation_pack_manifest"]["channels"]),
            "rubric_version": packet["evaluation_pack_manifest"]["rubric_version"],
            "visibility_boundary": packet["evaluation_pack_manifest"]["visibility_boundary"],
            "delayed_evaluation_allowed": packet["evaluation_pack_manifest"]["delayed_evaluation_allowed"],
            "reduction_context": packet["evaluation_pack_manifest"].get("reduction_context"),
        },
        "export_manifest": {
            "export_unit_ids": list(packet["export_manifest"]["export_unit_ids"]),
            "export_kinds": list(packet["export_manifest"]["export_kinds"]),
            "source_graph_ids": list(packet["export_manifest"]["source_graph_ids"]),
            "evaluation_pack_id": packet["export_manifest"]["evaluation_pack_id"],
            "split_kind": packet["export_manifest"]["split_kind"],
            "canonicalization_policy": packet["export_manifest"]["canonicalization_policy"],
            "transform_version": packet["export_manifest"]["transform_version"],
            "export_fingerprint": packet["export_manifest"]["export_fingerprint"],
            "contamination_controls": list(packet["export_manifest"]["contamination_controls"]),
            "fidelity_tier": packet["export_manifest"]["fidelity_tier"],
        },
        "unit_conformance_views": unit_views,
        "split_provenance": dict(packet["split_provenance"]),
        "summary": dict(packet["summary"]),
    }
