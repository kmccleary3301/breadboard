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


def _copy_nested_int_mapping(value: Mapping[str, Any] | None) -> Dict[str, int]:
    copied: Dict[str, int] = {}
    for key, raw in (value or {}).items():
        copied[str(key)] = int(raw or 0)
    return copied


@dataclass(frozen=True)
class RolloutDescriptor:
    rollout_id: str
    source_kind: str
    source_ref: str
    recipe_kind: str
    origin_kind: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rollout_id", _require_text(self.rollout_id, "rollout_id"))
        object.__setattr__(self, "source_kind", _require_text(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_ref", _require_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "recipe_kind", _require_text(self.recipe_kind, "recipe_kind"))
        origin_kind = _require_text(self.origin_kind, "origin_kind").lower()
        if origin_kind not in {"live", "replay"}:
            raise ValueError("origin_kind must be one of: ['live', 'replay']")
        object.__setattr__(self, "origin_kind", origin_kind)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rollout_id": self.rollout_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "recipe_kind": self.recipe_kind,
            "origin_kind": self.origin_kind,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RolloutDescriptor":
        return RolloutDescriptor(
            rollout_id=data.get("rollout_id") or "",
            source_kind=data.get("source_kind") or "",
            source_ref=data.get("source_ref") or "",
            recipe_kind=data.get("recipe_kind") or "",
            origin_kind=data.get("origin_kind") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class EnvironmentDescriptor:
    environment_id: str
    environment_kind: str
    workspace_mode: str
    tool_names: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment_id", _require_text(self.environment_id, "environment_id"))
        object.__setattr__(self, "environment_kind", _require_text(self.environment_kind, "environment_kind"))
        object.__setattr__(self, "workspace_mode", _require_text(self.workspace_mode, "workspace_mode"))
        object.__setattr__(self, "tool_names", _copy_text_list(self.tool_names))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment_id": self.environment_id,
            "environment_kind": self.environment_kind,
            "workspace_mode": self.workspace_mode,
            "tool_names": list(self.tool_names),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EnvironmentDescriptor":
        return EnvironmentDescriptor(
            environment_id=data.get("environment_id") or "",
            environment_kind=data.get("environment_kind") or "",
            workspace_mode=data.get("workspace_mode") or "",
            tool_names=list(data.get("tool_names") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PolicyProvenance:
    policy_id: str
    policy_kind: str
    provider: str
    model_name: str
    sequential_track_id: Optional[str] = None
    prompt_ref: Optional[str] = None
    config_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        object.__setattr__(self, "policy_kind", _require_text(self.policy_kind, "policy_kind"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "model_name", _require_text(self.model_name, "model_name"))
        object.__setattr__(
            self,
            "sequential_track_id",
            str(self.sequential_track_id).strip() if self.sequential_track_id else None,
        )
        object.__setattr__(self, "prompt_ref", str(self.prompt_ref).strip() if self.prompt_ref else None)
        object.__setattr__(self, "config_ref", str(self.config_ref).strip() if self.config_ref else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "policy_id": self.policy_id,
            "policy_kind": self.policy_kind,
            "provider": self.provider,
            "model_name": self.model_name,
            "metadata": dict(self.metadata),
        }
        if self.sequential_track_id:
            payload["sequential_track_id"] = self.sequential_track_id
        if self.prompt_ref:
            payload["prompt_ref"] = self.prompt_ref
        if self.config_ref:
            payload["config_ref"] = self.config_ref
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PolicyProvenance":
        return PolicyProvenance(
            policy_id=data.get("policy_id") or "",
            policy_kind=data.get("policy_kind") or "",
            provider=data.get("provider") or "",
            model_name=data.get("model_name") or "",
            sequential_track_id=data.get("sequential_track_id"),
            prompt_ref=data.get("prompt_ref"),
            config_ref=data.get("config_ref"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class EvaluationAnnotation:
    annotation_id: str
    subject_id: str
    subject_kind: str
    channel: str
    status: str
    score_value: Optional[float] = None
    text_feedback: Optional[str] = None
    artifact_refs: List[str] = field(default_factory=list)
    delayed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "annotation_id", _require_text(self.annotation_id, "annotation_id"))
        object.__setattr__(self, "subject_id", _require_text(self.subject_id, "subject_id"))
        object.__setattr__(self, "subject_kind", _require_text(self.subject_kind, "subject_kind"))
        object.__setattr__(self, "channel", _require_text(self.channel, "channel"))
        object.__setattr__(self, "status", _require_text(self.status, "status"))
        object.__setattr__(
            self,
            "score_value",
            float(self.score_value) if self.score_value is not None else None,
        )
        object.__setattr__(
            self,
            "text_feedback",
            str(self.text_feedback).strip() if self.text_feedback else None,
        )
        object.__setattr__(self, "artifact_refs", _copy_text_list(self.artifact_refs))
        object.__setattr__(self, "delayed", bool(self.delayed))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "annotation_id": self.annotation_id,
            "subject_id": self.subject_id,
            "subject_kind": self.subject_kind,
            "channel": self.channel,
            "status": self.status,
            "artifact_refs": list(self.artifact_refs),
            "delayed": self.delayed,
            "metadata": dict(self.metadata),
        }
        if self.score_value is not None:
            payload["score_value"] = self.score_value
        if self.text_feedback:
            payload["text_feedback"] = self.text_feedback
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EvaluationAnnotation":
        return EvaluationAnnotation(
            annotation_id=data.get("annotation_id") or "",
            subject_id=data.get("subject_id") or "",
            subject_kind=data.get("subject_kind") or "",
            channel=data.get("channel") or "",
            status=data.get("status") or "",
            score_value=data.get("score_value"),
            text_feedback=data.get("text_feedback"),
            artifact_refs=list(data.get("artifact_refs") or []),
            delayed=bool(data.get("delayed")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CostLedger:
    ledger_id: str
    token_counts: Dict[str, int]
    cost_usd: float
    wallclock_seconds: float
    tool_call_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ledger_id", _require_text(self.ledger_id, "ledger_id"))
        object.__setattr__(self, "token_counts", _copy_nested_int_mapping(self.token_counts))
        object.__setattr__(self, "cost_usd", float(self.cost_usd))
        object.__setattr__(self, "wallclock_seconds", float(self.wallclock_seconds))
        tool_call_count = int(self.tool_call_count)
        if tool_call_count < 0:
            raise ValueError("tool_call_count must be >= 0")
        object.__setattr__(self, "tool_call_count", tool_call_count)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "token_counts": dict(self.token_counts),
            "cost_usd": self.cost_usd,
            "wallclock_seconds": self.wallclock_seconds,
            "tool_call_count": self.tool_call_count,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CostLedger":
        return CostLedger(
            ledger_id=data.get("ledger_id") or "",
            token_counts=dict(data.get("token_counts") or {}),
            cost_usd=float(data.get("cost_usd") or 0.0),
            wallclock_seconds=float(data.get("wallclock_seconds") or 0.0),
            tool_call_count=int(data.get("tool_call_count") or 0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CompactionManifest:
    manifest_id: str
    source_message_ids: List[str]
    bounded_by: str
    dropped_fields: List[str] = field(default_factory=list)
    omitted_artifact_refs: List[str] = field(default_factory=list)
    fidelity_tier: str = "bounded_summary"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "source_message_ids", _copy_text_list(self.source_message_ids))
        object.__setattr__(self, "bounded_by", _require_text(self.bounded_by, "bounded_by"))
        object.__setattr__(self, "dropped_fields", _copy_text_list(self.dropped_fields))
        object.__setattr__(self, "omitted_artifact_refs", _copy_text_list(self.omitted_artifact_refs))
        object.__setattr__(self, "fidelity_tier", _require_text(self.fidelity_tier, "fidelity_tier"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.source_message_ids:
            raise ValueError("source_message_ids must contain at least one message id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "source_message_ids": list(self.source_message_ids),
            "bounded_by": self.bounded_by,
            "dropped_fields": list(self.dropped_fields),
            "omitted_artifact_refs": list(self.omitted_artifact_refs),
            "fidelity_tier": self.fidelity_tier,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CompactionManifest":
        return CompactionManifest(
            manifest_id=data.get("manifest_id") or "",
            source_message_ids=list(data.get("source_message_ids") or []),
            bounded_by=data.get("bounded_by") or "",
            dropped_fields=list(data.get("dropped_fields") or []),
            omitted_artifact_refs=list(data.get("omitted_artifact_refs") or []),
            fidelity_tier=data.get("fidelity_tier") or "bounded_summary",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AdapterCapabilities:
    adapter_id: str
    adapter_kind: str
    supports_graph_trajectory: bool
    supports_async: bool
    supports_training_feedback: bool
    export_formats: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _require_text(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "adapter_kind", _require_text(self.adapter_kind, "adapter_kind"))
        object.__setattr__(self, "supports_graph_trajectory", bool(self.supports_graph_trajectory))
        object.__setattr__(self, "supports_async", bool(self.supports_async))
        object.__setattr__(self, "supports_training_feedback", bool(self.supports_training_feedback))
        object.__setattr__(self, "export_formats", _copy_text_list(self.export_formats))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "supports_graph_trajectory": self.supports_graph_trajectory,
            "supports_async": self.supports_async,
            "supports_training_feedback": self.supports_training_feedback,
            "export_formats": list(self.export_formats),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "AdapterCapabilities":
        return AdapterCapabilities(
            adapter_id=data.get("adapter_id") or "",
            adapter_kind=data.get("adapter_kind") or "",
            supports_graph_trajectory=bool(data.get("supports_graph_trajectory")),
            supports_async=bool(data.get("supports_async")),
            supports_training_feedback=bool(data.get("supports_training_feedback")),
            export_formats=list(data.get("export_formats") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TrackRecord:
    track_id: str
    source_kind: str
    source_ref: str
    parent_track_id: Optional[str] = None
    candidate_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", _require_text(self.track_id, "track_id"))
        object.__setattr__(self, "source_kind", _require_text(self.source_kind, "source_kind"))
        object.__setattr__(self, "source_ref", _require_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "parent_track_id", str(self.parent_track_id).strip() if self.parent_track_id else None)
        object.__setattr__(self, "candidate_ids", _copy_text_list(self.candidate_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "track_id": self.track_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "candidate_ids": list(self.candidate_ids),
            "metadata": dict(self.metadata),
        }
        if self.parent_track_id:
            payload["parent_track_id"] = self.parent_track_id
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TrackRecord":
        return TrackRecord(
            track_id=data.get("track_id") or "",
            source_kind=data.get("source_kind") or "",
            source_ref=data.get("source_ref") or "",
            parent_track_id=data.get("parent_track_id"),
            candidate_ids=list(data.get("candidate_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ObservationSlice:
    observation_id: str
    track_id: str
    candidate_id: str
    payload_ref: str
    workspace_ref: Optional[str] = None
    message_ref: Optional[str] = None
    visible_artifact_refs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _require_text(self.observation_id, "observation_id"))
        object.__setattr__(self, "track_id", _require_text(self.track_id, "track_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "payload_ref", _require_text(self.payload_ref, "payload_ref"))
        object.__setattr__(self, "workspace_ref", str(self.workspace_ref).strip() if self.workspace_ref else None)
        object.__setattr__(self, "message_ref", str(self.message_ref).strip() if self.message_ref else None)
        object.__setattr__(self, "visible_artifact_refs", _copy_text_list(self.visible_artifact_refs))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "observation_id": self.observation_id,
            "track_id": self.track_id,
            "candidate_id": self.candidate_id,
            "payload_ref": self.payload_ref,
            "visible_artifact_refs": list(self.visible_artifact_refs),
            "metadata": dict(self.metadata),
        }
        if self.workspace_ref:
            payload["workspace_ref"] = self.workspace_ref
        if self.message_ref:
            payload["message_ref"] = self.message_ref
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ObservationSlice":
        return ObservationSlice(
            observation_id=data.get("observation_id") or "",
            track_id=data.get("track_id") or "",
            candidate_id=data.get("candidate_id") or "",
            payload_ref=data.get("payload_ref") or "",
            workspace_ref=data.get("workspace_ref"),
            message_ref=data.get("message_ref"),
            visible_artifact_refs=list(data.get("visible_artifact_refs") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    track_id: str
    event_id: str
    operator_kind: str
    policy_id: str
    input_candidate_ids: List[str]
    output_candidate_ids: List[str] = field(default_factory=list)
    assessment_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_text(self.decision_id, "decision_id"))
        object.__setattr__(self, "track_id", _require_text(self.track_id, "track_id"))
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(self, "operator_kind", _require_text(self.operator_kind, "operator_kind"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        object.__setattr__(self, "input_candidate_ids", _copy_text_list(self.input_candidate_ids))
        object.__setattr__(self, "output_candidate_ids", _copy_text_list(self.output_candidate_ids))
        object.__setattr__(self, "assessment_ids", _copy_text_list(self.assessment_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.input_candidate_ids:
            raise ValueError("input_candidate_ids must contain at least one candidate id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "track_id": self.track_id,
            "event_id": self.event_id,
            "operator_kind": self.operator_kind,
            "policy_id": self.policy_id,
            "input_candidate_ids": list(self.input_candidate_ids),
            "output_candidate_ids": list(self.output_candidate_ids),
            "assessment_ids": list(self.assessment_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "DecisionRecord":
        return DecisionRecord(
            decision_id=data.get("decision_id") or "",
            track_id=data.get("track_id") or "",
            event_id=data.get("event_id") or "",
            operator_kind=data.get("operator_kind") or "",
            policy_id=data.get("policy_id") or "",
            input_candidate_ids=list(data.get("input_candidate_ids") or []),
            output_candidate_ids=list(data.get("output_candidate_ids") or []),
            assessment_ids=list(data.get("assessment_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    track_id: str
    event_id: str
    effect_kind: str
    produced_candidate_ids: List[str] = field(default_factory=list)
    artifact_refs: List[str] = field(default_factory=list)
    workspace_snapshot_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_id", _require_text(self.effect_id, "effect_id"))
        object.__setattr__(self, "track_id", _require_text(self.track_id, "track_id"))
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(self, "effect_kind", _require_text(self.effect_kind, "effect_kind"))
        object.__setattr__(self, "produced_candidate_ids", _copy_text_list(self.produced_candidate_ids))
        object.__setattr__(self, "artifact_refs", _copy_text_list(self.artifact_refs))
        object.__setattr__(self, "workspace_snapshot_ids", _copy_text_list(self.workspace_snapshot_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "track_id": self.track_id,
            "event_id": self.event_id,
            "effect_kind": self.effect_kind,
            "produced_candidate_ids": list(self.produced_candidate_ids),
            "artifact_refs": list(self.artifact_refs),
            "workspace_snapshot_ids": list(self.workspace_snapshot_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EffectRecord":
        return EffectRecord(
            effect_id=data.get("effect_id") or "",
            track_id=data.get("track_id") or "",
            event_id=data.get("event_id") or "",
            effect_kind=data.get("effect_kind") or "",
            produced_candidate_ids=list(data.get("produced_candidate_ids") or []),
            artifact_refs=list(data.get("artifact_refs") or []),
            workspace_snapshot_ids=list(data.get("workspace_snapshot_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CausalEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_kind: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_id", _require_text(self.edge_id, "edge_id"))
        object.__setattr__(self, "source_id", _require_text(self.source_id, "source_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "edge_kind", _require_text(self.edge_kind, "edge_kind"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_kind": self.edge_kind,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CausalEdge":
        return CausalEdge(
            edge_id=data.get("edge_id") or "",
            source_id=data.get("source_id") or "",
            target_id=data.get("target_id") or "",
            edge_kind=data.get("edge_kind") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TrajectoryGraph:
    graph_id: str
    rollout_descriptor: RolloutDescriptor
    environment_descriptor: EnvironmentDescriptor
    policy_provenance: List[PolicyProvenance]
    tracks: List[TrackRecord]
    observations: List[ObservationSlice]
    decisions: List[DecisionRecord]
    effects: List[EffectRecord]
    causal_edges: List[CausalEdge]
    evaluation_annotations: List[EvaluationAnnotation] = field(default_factory=list)
    cost_ledger: Optional[CostLedger] = None
    compaction_manifests: List[CompactionManifest] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "graph_id", _require_text(self.graph_id, "graph_id"))
        object.__setattr__(
            self,
            "rollout_descriptor",
            self.rollout_descriptor
            if isinstance(self.rollout_descriptor, RolloutDescriptor)
            else RolloutDescriptor.from_dict(self.rollout_descriptor),
        )
        object.__setattr__(
            self,
            "environment_descriptor",
            self.environment_descriptor
            if isinstance(self.environment_descriptor, EnvironmentDescriptor)
            else EnvironmentDescriptor.from_dict(self.environment_descriptor),
        )
        object.__setattr__(
            self,
            "policy_provenance",
            [
                item if isinstance(item, PolicyProvenance) else PolicyProvenance.from_dict(item)
                for item in self.policy_provenance
            ],
        )
        object.__setattr__(
            self,
            "tracks",
            [item if isinstance(item, TrackRecord) else TrackRecord.from_dict(item) for item in self.tracks],
        )
        object.__setattr__(
            self,
            "observations",
            [
                item if isinstance(item, ObservationSlice) else ObservationSlice.from_dict(item)
                for item in self.observations
            ],
        )
        object.__setattr__(
            self,
            "decisions",
            [item if isinstance(item, DecisionRecord) else DecisionRecord.from_dict(item) for item in self.decisions],
        )
        object.__setattr__(
            self,
            "effects",
            [item if isinstance(item, EffectRecord) else EffectRecord.from_dict(item) for item in self.effects],
        )
        object.__setattr__(
            self,
            "causal_edges",
            [item if isinstance(item, CausalEdge) else CausalEdge.from_dict(item) for item in self.causal_edges],
        )
        object.__setattr__(
            self,
            "evaluation_annotations",
            [
                item if isinstance(item, EvaluationAnnotation) else EvaluationAnnotation.from_dict(item)
                for item in self.evaluation_annotations
            ],
        )
        object.__setattr__(
            self,
            "cost_ledger",
            self.cost_ledger if isinstance(self.cost_ledger, CostLedger) or self.cost_ledger is None else CostLedger.from_dict(self.cost_ledger),
        )
        object.__setattr__(
            self,
            "compaction_manifests",
            [
                item if isinstance(item, CompactionManifest) else CompactionManifest.from_dict(item)
                for item in self.compaction_manifests
            ],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.policy_provenance:
            raise ValueError("policy_provenance must contain at least one policy record")
        if not self.tracks:
            raise ValueError("tracks must contain at least one track")
        if not self.observations:
            raise ValueError("observations must contain at least one observation")
        if not self.decisions:
            raise ValueError("decisions must contain at least one decision")
        if not self.effects:
            raise ValueError("effects must contain at least one effect")
        if not self.causal_edges:
            raise ValueError("causal_edges must contain at least one edge")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "graph_id": self.graph_id,
            "rollout_descriptor": self.rollout_descriptor.to_dict(),
            "environment_descriptor": self.environment_descriptor.to_dict(),
            "policy_provenance": [item.to_dict() for item in self.policy_provenance],
            "tracks": [item.to_dict() for item in self.tracks],
            "observations": [item.to_dict() for item in self.observations],
            "decisions": [item.to_dict() for item in self.decisions],
            "effects": [item.to_dict() for item in self.effects],
            "causal_edges": [item.to_dict() for item in self.causal_edges],
            "evaluation_annotations": [item.to_dict() for item in self.evaluation_annotations],
            "compaction_manifests": [item.to_dict() for item in self.compaction_manifests],
            "metadata": dict(self.metadata),
        }
        if self.cost_ledger is not None:
            payload["cost_ledger"] = self.cost_ledger.to_dict()
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TrajectoryGraph":
        return TrajectoryGraph(
            graph_id=data.get("graph_id") or "",
            rollout_descriptor=RolloutDescriptor.from_dict(data.get("rollout_descriptor") or {}),
            environment_descriptor=EnvironmentDescriptor.from_dict(data.get("environment_descriptor") or {}),
            policy_provenance=[PolicyProvenance.from_dict(item) for item in data.get("policy_provenance") or []],
            tracks=[TrackRecord.from_dict(item) for item in data.get("tracks") or []],
            observations=[ObservationSlice.from_dict(item) for item in data.get("observations") or []],
            decisions=[DecisionRecord.from_dict(item) for item in data.get("decisions") or []],
            effects=[EffectRecord.from_dict(item) for item in data.get("effects") or []],
            causal_edges=[CausalEdge.from_dict(item) for item in data.get("causal_edges") or []],
            evaluation_annotations=[
                EvaluationAnnotation.from_dict(item) for item in data.get("evaluation_annotations") or []
            ],
            cost_ledger=CostLedger.from_dict(data["cost_ledger"]) if data.get("cost_ledger") else None,
            compaction_manifests=[CompactionManifest.from_dict(item) for item in data.get("compaction_manifests") or []],
            metadata=dict(data.get("metadata") or {}),
        )
