from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from .study import SearchStudyRegistry, build_default_search_study_registry, run_search_study


@dataclass(frozen=True)
class SearchStudyControlTemplate:
    control_key: str
    status: str
    evidence_key: str | None
    summary: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "control_key": self.control_key,
            "status": self.status,
            "evidence_key": self.evidence_key,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SearchStudyArtifactContract:
    summary_json: bool
    summary_txt: bool
    inspect_supported: bool
    compare_supported: bool
    open_artifact_supported: bool
    artifact_ref_count: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "summary_json": self.summary_json,
            "summary_txt": self.summary_txt,
            "inspect_supported": self.inspect_supported,
            "compare_supported": self.compare_supported,
            "open_artifact_supported": self.open_artifact_supported,
            "artifact_ref_count": self.artifact_ref_count,
        }


@dataclass(frozen=True)
class SearchStudyKit:
    study_key: str
    title: str
    packet_family: str
    phase: str
    tags: tuple[str, ...]
    packet_keys: tuple[str, ...]
    required_controls: tuple[str, ...]
    control_templates: tuple[SearchStudyControlTemplate, ...]
    artifact_contract: SearchStudyArtifactContract
    top_level_metrics: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "study_key": self.study_key,
            "title": self.title,
            "packet_family": self.packet_family,
            "phase": self.phase,
            "tags": list(self.tags),
            "packet_keys": list(self.packet_keys),
            "required_controls": list(self.required_controls),
            "control_templates": [item.to_dict() for item in self.control_templates],
            "artifact_contract": self.artifact_contract.to_dict(),
            "top_level_metrics": dict(self.top_level_metrics),
        }


def _has_key(packet: Mapping[str, object], key: str) -> bool:
    return key in packet


def _build_control_templates(packet: Mapping[str, object]) -> tuple[SearchStudyControlTemplate, ...]:
    controls: List[SearchStudyControlTemplate] = []
    controls.append(
        SearchStudyControlTemplate(
            control_key="compute_normalization",
            status="ready" if _has_key(packet, "compute_ledger") else "missing",
            evidence_key="compute_ledger" if _has_key(packet, "compute_ledger") else None,
            summary="Compute budget normalization is standardized through a compute ledger.",
        )
    )
    controls.append(
        SearchStudyControlTemplate(
            control_key="baseline_discipline",
            status="ready" if _has_key(packet, "baseline_packet") else "missing",
            evidence_key="baseline_packet" if _has_key(packet, "baseline_packet") else None,
            summary="Every canonical study kit should expose a bounded baseline comparison packet.",
        )
    )
    evaluator_ready = _has_key(packet, "benchmark_control_packet")
    controls.append(
        SearchStudyControlTemplate(
            control_key="evaluator_control",
            status="ready" if evaluator_ready else "standardized_followup",
            evidence_key="benchmark_control_packet" if evaluator_ready else None,
            summary=(
                "Benchmark/evaluator control is embedded directly in the packet."
                if evaluator_ready
                else "This family uses the canonical evaluator-strength and flattened-control template rather than an embedded benchmark control packet."
            ),
        )
    )
    replay_ready = _has_key(packet, "replay_export_integrity_packet") or _has_key(packet, "replay_audit") or _has_key(packet, "run")
    controls.append(
        SearchStudyControlTemplate(
            control_key="artifact_integrity",
            status="ready" if replay_ready else "missing",
            evidence_key=(
                "replay_export_integrity_packet"
                if _has_key(packet, "replay_export_integrity_packet")
                else "replay_audit"
                if _has_key(packet, "replay_audit")
                else "run"
                if _has_key(packet, "run")
                else None
            ),
            summary="Artifact integrity is evaluated through replay/export integrity, replay audit, or run-linked artifact refs.",
        )
    )
    return tuple(controls)


def build_search_study_kit(
    study_key: str,
    *,
    registry: SearchStudyRegistry | None = None,
) -> SearchStudyKit:
    result = run_search_study(study_key, mode="spec", registry=registry)
    control_templates = _build_control_templates(result.packet)
    return SearchStudyKit(
        study_key=result.registry_entry.study_key,
        title=result.registry_entry.title,
        packet_family=result.registry_entry.packet_family,
        phase=result.registry_entry.phase,
        tags=result.registry_entry.tags,
        packet_keys=tuple(sorted(result.packet.keys())),
        required_controls=tuple(item.control_key for item in control_templates),
        control_templates=control_templates,
        artifact_contract=SearchStudyArtifactContract(
            summary_json=True,
            summary_txt=True,
            inspect_supported=True,
            compare_supported=True,
            open_artifact_supported=bool(result.artifact_refs),
            artifact_ref_count=len(result.artifact_refs),
        ),
        top_level_metrics=dict(result.summary.top_level_metrics),
    )


def build_default_search_study_kits(
    *,
    registry: SearchStudyRegistry | None = None,
) -> List[SearchStudyKit]:
    active_registry = registry or build_default_search_study_registry()
    return [build_search_study_kit(entry.study_key, registry=active_registry) for entry in active_registry.list_entries()]
