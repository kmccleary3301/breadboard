from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _copy_text_list(values: Sequence[Any] | None) -> List[str]:
    copied: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text:
            copied.append(text)
    return copied


@dataclass(frozen=True)
class ArtifactRef:
    ref: str
    digest: Optional[str] = None
    media_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", _require_text(self.ref, "ref"))
        object.__setattr__(self, "digest", str(self.digest).strip() if self.digest else None)
        object.__setattr__(self, "media_type", str(self.media_type).strip() if self.media_type else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"ref": self.ref, "metadata": dict(self.metadata)}
        if self.digest:
            payload["digest"] = self.digest
        if self.media_type:
            payload["media_type"] = self.media_type
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ArtifactRef":
        return ArtifactRef(
            ref=data.get("ref", ""),
            digest=data.get("digest"),
            media_type=data.get("media_type"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationInvariant:
    invariant_id: str
    description: str
    severity: str = "error"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "invariant_id", _require_text(self.invariant_id, "invariant_id"))
        object.__setattr__(self, "description", _require_text(self.description, "description"))
        severity = str(self.severity or "error").strip().lower()
        if severity not in {"error", "warning", "info"}:
            raise ValueError("severity must be one of: error, warning, info")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "description": self.description,
            "severity": self.severity,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationInvariant":
        return OptimizationInvariant(
            invariant_id=data.get("invariant_id") or data.get("id") or "",
            description=data.get("description") or "",
            severity=data.get("severity") or "error",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SupportEnvelope:
    tools: List[str] = field(default_factory=list)
    execution_profiles: List[str] = field(default_factory=list)
    environments: List[str] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    assumptions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", _copy_text_list(self.tools))
        object.__setattr__(self, "execution_profiles", _copy_text_list(self.execution_profiles))
        object.__setattr__(self, "environments", _copy_text_list(self.environments))
        object.__setattr__(self, "providers", _copy_text_list(self.providers))
        object.__setattr__(self, "models", _copy_text_list(self.models))
        object.__setattr__(self, "assumptions", _copy_mapping(self.assumptions))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tools": list(self.tools),
            "execution_profiles": list(self.execution_profiles),
            "environments": list(self.environments),
            "providers": list(self.providers),
            "models": list(self.models),
            "assumptions": dict(self.assumptions),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> "SupportEnvelope":
        data = data or {}
        return SupportEnvelope(
            tools=list(data.get("tools") or []),
            execution_profiles=list(data.get("execution_profiles") or data.get("profiles") or []),
            environments=list(data.get("environments") or []),
            providers=list(data.get("providers") or []),
            models=list(data.get("models") or []),
            assumptions=dict(data.get("assumptions") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MutableLocus:
    locus_id: str
    locus_kind: str
    selector: str
    artifact_ref: Optional[str] = None
    mutation_kind: str = "replace"
    constraints: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "locus_id", _require_text(self.locus_id, "locus_id"))
        object.__setattr__(self, "locus_kind", _require_text(self.locus_kind, "locus_kind"))
        object.__setattr__(self, "selector", _require_text(self.selector, "selector"))
        object.__setattr__(self, "artifact_ref", str(self.artifact_ref).strip() if self.artifact_ref else None)
        object.__setattr__(self, "mutation_kind", _require_text(self.mutation_kind, "mutation_kind"))
        object.__setattr__(self, "constraints", _copy_mapping(self.constraints))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "locus_id": self.locus_id,
            "locus_kind": self.locus_kind,
            "selector": self.selector,
            "mutation_kind": self.mutation_kind,
            "constraints": dict(self.constraints),
            "metadata": dict(self.metadata),
        }
        if self.artifact_ref:
            payload["artifact_ref"] = self.artifact_ref
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "MutableLocus":
        return MutableLocus(
            locus_id=data.get("locus_id") or data.get("id") or "",
            locus_kind=data.get("locus_kind") or data.get("kind") or "",
            selector=data.get("selector") or data.get("path") or "",
            artifact_ref=data.get("artifact_ref"),
            mutation_kind=data.get("mutation_kind") or "replace",
            constraints=dict(data.get("constraints") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationTarget:
    target_id: str
    target_kind: str
    baseline_artifact_refs: List[ArtifactRef] = field(default_factory=list)
    mutable_loci: List[MutableLocus] = field(default_factory=list)
    support_envelope: SupportEnvelope = field(default_factory=SupportEnvelope)
    invariants: List[OptimizationInvariant] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "target_kind", _require_text(self.target_kind, "target_kind"))
        object.__setattr__(
            self,
            "baseline_artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.baseline_artifact_refs],
        )
        object.__setattr__(
            self,
            "mutable_loci",
            [item if isinstance(item, MutableLocus) else MutableLocus.from_dict(item) for item in self.mutable_loci],
        )
        object.__setattr__(
            self,
            "support_envelope",
            self.support_envelope if isinstance(self.support_envelope, SupportEnvelope) else SupportEnvelope.from_dict(self.support_envelope),
        )
        object.__setattr__(
            self,
            "invariants",
            [item if isinstance(item, OptimizationInvariant) else OptimizationInvariant.from_dict(item) for item in self.invariants],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.baseline_artifact_refs:
            raise ValueError("baseline_artifact_refs must contain at least one artifact")
        if not self.mutable_loci:
            raise ValueError("mutable_loci must contain at least one locus")

        locus_ids = [locus.locus_id for locus in self.mutable_loci]
        if len(locus_ids) != len(set(locus_ids)):
            raise ValueError("mutable_loci contains duplicate locus_id values")

        invariant_ids = [item.invariant_id for item in self.invariants]
        if len(invariant_ids) != len(set(invariant_ids)):
            raise ValueError("invariants contains duplicate invariant_id values")

    def locus_ids(self) -> List[str]:
        return [locus.locus_id for locus in self.mutable_loci]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "baseline_artifact_refs": [item.to_dict() for item in self.baseline_artifact_refs],
            "mutable_loci": [item.to_dict() for item in self.mutable_loci],
            "support_envelope": self.support_envelope.to_dict(),
            "invariants": [item.to_dict() for item in self.invariants],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationTarget":
        return OptimizationTarget(
            target_id=data.get("target_id") or data.get("id") or "",
            target_kind=data.get("target_kind") or data.get("kind") or "",
            baseline_artifact_refs=[
                ArtifactRef.from_dict(item) for item in data.get("baseline_artifact_refs") or data.get("baseline_artifacts") or []
            ],
            mutable_loci=[MutableLocus.from_dict(item) for item in data.get("mutable_loci") or []],
            support_envelope=SupportEnvelope.from_dict(data.get("support_envelope") or {}),
            invariants=[OptimizationInvariant.from_dict(item) for item in data.get("invariants") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidateChange:
    locus_id: str
    value: Any
    rationale: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "locus_id", _require_text(self.locus_id, "locus_id"))
        object.__setattr__(self, "rationale", str(self.rationale).strip() if self.rationale else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {"locus_id": self.locus_id, "value": self.value, "metadata": dict(self.metadata)}
        if self.rationale:
            payload["rationale"] = self.rationale
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CandidateChange":
        return CandidateChange(
            locus_id=data.get("locus_id") or "",
            value=data.get("value"),
            rationale=data.get("rationale"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidateBundle:
    candidate_id: str
    source_target_id: str
    applied_loci: List[str] = field(default_factory=list)
    changes: List[CandidateChange] = field(default_factory=list)
    change_set_refs: List[ArtifactRef] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source_target_id", _require_text(self.source_target_id, "source_target_id"))
        object.__setattr__(self, "applied_loci", _copy_text_list(self.applied_loci))
        object.__setattr__(
            self,
            "changes",
            [item if isinstance(item, CandidateChange) else CandidateChange.from_dict(item) for item in self.changes],
        )
        object.__setattr__(
            self,
            "change_set_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.change_set_refs],
        )
        object.__setattr__(self, "provenance", _copy_mapping(self.provenance))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.applied_loci:
            raise ValueError("applied_loci must contain at least one locus_id")
        if not self.changes:
            raise ValueError("changes must contain at least one change")
        if len(self.applied_loci) != len(set(self.applied_loci)):
            raise ValueError("applied_loci contains duplicate locus_id values")

        change_loci = [change.locus_id for change in self.changes]
        if len(change_loci) != len(set(change_loci)):
            raise ValueError("changes contains duplicate locus_id values")
        if set(change_loci) != set(self.applied_loci):
            raise ValueError("changes locus ids must exactly match applied_loci")

    def validate_against_target(self, target: OptimizationTarget) -> None:
        if self.source_target_id != target.target_id:
            raise ValueError(
                f"candidate target mismatch: expected {target.target_id}, got {self.source_target_id}"
            )
        unknown_loci = sorted(set(self.applied_loci) - set(target.locus_ids()))
        if unknown_loci:
            raise ValueError(f"candidate references unknown loci: {unknown_loci}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_target_id": self.source_target_id,
            "applied_loci": list(self.applied_loci),
            "changes": [item.to_dict() for item in self.changes],
            "change_set_refs": [item.to_dict() for item in self.change_set_refs],
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CandidateBundle":
        return CandidateBundle(
            candidate_id=data.get("candidate_id") or data.get("id") or "",
            source_target_id=data.get("source_target_id") or data.get("target_id") or "",
            applied_loci=list(data.get("applied_loci") or []),
            changes=[CandidateChange.from_dict(item) for item in data.get("changes") or []],
            change_set_refs=[ArtifactRef.from_dict(item) for item in data.get("change_set_refs") or []],
            provenance=dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MaterializedCandidate:
    candidate_id: str
    source_target_id: str
    applied_loci: List[str]
    effective_artifact: Any
    effective_tool_surface: Dict[str, Any] = field(default_factory=dict)
    support_envelope: SupportEnvelope = field(default_factory=SupportEnvelope)
    evaluation_input_compatibility: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source_target_id", _require_text(self.source_target_id, "source_target_id"))
        object.__setattr__(self, "applied_loci", _copy_text_list(self.applied_loci))
        if not self.applied_loci:
            raise ValueError("applied_loci must contain at least one locus_id")
        object.__setattr__(self, "effective_tool_surface", _copy_mapping(self.effective_tool_surface))
        object.__setattr__(
            self,
            "support_envelope",
            self.support_envelope if isinstance(self.support_envelope, SupportEnvelope) else SupportEnvelope.from_dict(self.support_envelope),
        )
        object.__setattr__(self, "evaluation_input_compatibility", _copy_mapping(self.evaluation_input_compatibility))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_target_id": self.source_target_id,
            "applied_loci": list(self.applied_loci),
            "effective_artifact": self.effective_artifact,
            "effective_tool_surface": dict(self.effective_tool_surface),
            "support_envelope": self.support_envelope.to_dict(),
            "evaluation_input_compatibility": dict(self.evaluation_input_compatibility),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "MaterializedCandidate":
        return MaterializedCandidate(
            candidate_id=data.get("candidate_id") or data.get("id") or "",
            source_target_id=data.get("source_target_id") or data.get("target_id") or "",
            applied_loci=list(data.get("applied_loci") or []),
            effective_artifact=data.get("effective_artifact"),
            effective_tool_surface=dict(data.get("effective_tool_surface") or {}),
            support_envelope=SupportEnvelope.from_dict(data.get("support_envelope") or {}),
            evaluation_input_compatibility=dict(data.get("evaluation_input_compatibility") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


def materialize_candidate(
    target: OptimizationTarget,
    candidate: CandidateBundle,
    *,
    effective_artifact: Any,
    effective_tool_surface: Mapping[str, Any] | None = None,
    support_envelope: SupportEnvelope | Mapping[str, Any] | None = None,
    evaluation_input_compatibility: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MaterializedCandidate:
    candidate.validate_against_target(target)
    envelope = target.support_envelope if support_envelope is None else (
        support_envelope if isinstance(support_envelope, SupportEnvelope) else SupportEnvelope.from_dict(support_envelope)
    )
    return MaterializedCandidate(
        candidate_id=candidate.candidate_id,
        source_target_id=target.target_id,
        applied_loci=list(candidate.applied_loci),
        effective_artifact=effective_artifact,
        effective_tool_surface=dict(effective_tool_surface or {}),
        support_envelope=envelope,
        evaluation_input_compatibility=dict(evaluation_input_compatibility or {}),
        metadata=dict(metadata or {}),
    )
