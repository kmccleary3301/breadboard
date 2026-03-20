from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


ALLOWED_OPERATOR_KINDS = {
    "expand",
    "compact",
    "aggregate",
    "verify",
    "select",
    "execute",
    "terminate",
}
ALLOWED_CANDIDATE_STATUSES = {"seeded", "active", "selected", "discarded", "verified", "terminated"}
ALLOWED_FRONTIER_STATUSES = {"active", "completed", "terminated"}


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


def _copy_nested_mapping(value: Mapping[str, Mapping[str, Any]] | None) -> Dict[str, Dict[str, Any]]:
    return {str(key): dict(inner) for key, inner in (value or {}).items()}


@dataclass(frozen=True)
class SearchCandidate:
    candidate_id: str
    search_id: str
    frontier_id: str
    parent_ids: List[str]
    round_index: int
    depth: int
    payload_ref: str
    message_ref: Optional[str] = None
    workspace_ref: Optional[str] = None
    score_vector: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    reasoning_trace_ref: Optional[str] = None
    reasoning_summary_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "search_id", _require_text(self.search_id, "search_id"))
        object.__setattr__(self, "frontier_id", _require_text(self.frontier_id, "frontier_id"))
        object.__setattr__(self, "parent_ids", _copy_text_list(self.parent_ids))
        round_index = int(self.round_index)
        depth = int(self.depth)
        if round_index < 0:
            raise ValueError("round_index must be >= 0")
        if depth < 0:
            raise ValueError("depth must be >= 0")
        status = _require_text(self.status, "status").lower()
        if status not in ALLOWED_CANDIDATE_STATUSES:
            raise ValueError(f"status must be one of: {sorted(ALLOWED_CANDIDATE_STATUSES)}")
        object.__setattr__(self, "round_index", round_index)
        object.__setattr__(self, "depth", depth)
        object.__setattr__(self, "payload_ref", _require_text(self.payload_ref, "payload_ref"))
        object.__setattr__(self, "message_ref", str(self.message_ref).strip() if self.message_ref else None)
        object.__setattr__(self, "workspace_ref", str(self.workspace_ref).strip() if self.workspace_ref else None)
        object.__setattr__(self, "score_vector", _copy_mapping(self.score_vector))
        object.__setattr__(self, "usage", _copy_mapping(self.usage))
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "reasoning_trace_ref",
            str(self.reasoning_trace_ref).strip() if self.reasoning_trace_ref else None,
        )
        object.__setattr__(
            self,
            "reasoning_summary_ref",
            str(self.reasoning_summary_ref).strip() if self.reasoning_summary_ref else None,
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if len(self.parent_ids) != len(set(self.parent_ids)):
            raise ValueError("parent_ids contains duplicate values")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "search_id": self.search_id,
            "frontier_id": self.frontier_id,
            "parent_ids": list(self.parent_ids),
            "round_index": self.round_index,
            "depth": self.depth,
            "payload_ref": self.payload_ref,
            "score_vector": dict(self.score_vector),
            "usage": dict(self.usage),
            "status": self.status,
            "metadata": dict(self.metadata),
        }
        if self.message_ref:
            payload["message_ref"] = self.message_ref
        if self.workspace_ref:
            payload["workspace_ref"] = self.workspace_ref
        if self.reasoning_trace_ref:
            payload["reasoning_trace_ref"] = self.reasoning_trace_ref
        if self.reasoning_summary_ref:
            payload["reasoning_summary_ref"] = self.reasoning_summary_ref
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchCandidate":
        return SearchCandidate(
            candidate_id=data.get("candidate_id") or "",
            search_id=data.get("search_id") or "",
            frontier_id=data.get("frontier_id") or "",
            parent_ids=list(data.get("parent_ids") or []),
            round_index=int(data.get("round_index") or 0),
            depth=int(data.get("depth") or 0),
            payload_ref=data.get("payload_ref") or "",
            message_ref=data.get("message_ref"),
            workspace_ref=data.get("workspace_ref"),
            score_vector=dict(data.get("score_vector") or {}),
            usage=dict(data.get("usage") or {}),
            status=data.get("status") or "active",
            reasoning_trace_ref=data.get("reasoning_trace_ref"),
            reasoning_summary_ref=data.get("reasoning_summary_ref"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchMessage:
    message_id: str
    schema_kind: str
    source_candidate_ids: List[str]
    summary_payload: Dict[str, Any]
    dropped_fields: List[str] = field(default_factory=list)
    omitted_artifact_refs: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    unresolved_gaps: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _require_text(self.message_id, "message_id"))
        object.__setattr__(self, "schema_kind", _require_text(self.schema_kind, "schema_kind"))
        object.__setattr__(self, "source_candidate_ids", _copy_text_list(self.source_candidate_ids))
        object.__setattr__(self, "summary_payload", _copy_mapping(self.summary_payload))
        object.__setattr__(self, "dropped_fields", _copy_text_list(self.dropped_fields))
        object.__setattr__(self, "omitted_artifact_refs", _copy_text_list(self.omitted_artifact_refs))
        if self.confidence is not None:
            confidence = float(self.confidence)
            if confidence < 0.0 or confidence > 1.0:
                raise ValueError("confidence must be within [0.0, 1.0]")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "unresolved_gaps", _copy_text_list(self.unresolved_gaps))
        object.__setattr__(self, "usage", _copy_mapping(self.usage))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.source_candidate_ids:
            raise ValueError("source_candidate_ids must contain at least one candidate id")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "message_id": self.message_id,
            "schema_kind": self.schema_kind,
            "source_candidate_ids": list(self.source_candidate_ids),
            "summary_payload": dict(self.summary_payload),
            "dropped_fields": list(self.dropped_fields),
            "omitted_artifact_refs": list(self.omitted_artifact_refs),
            "unresolved_gaps": list(self.unresolved_gaps),
            "usage": dict(self.usage),
            "metadata": dict(self.metadata),
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchMessage":
        return SearchMessage(
            message_id=data.get("message_id") or "",
            schema_kind=data.get("schema_kind") or "",
            source_candidate_ids=list(data.get("source_candidate_ids") or []),
            summary_payload=dict(data.get("summary_payload") or {}),
            dropped_fields=list(data.get("dropped_fields") or []),
            omitted_artifact_refs=list(data.get("omitted_artifact_refs") or []),
            confidence=data.get("confidence"),
            unresolved_gaps=list(data.get("unresolved_gaps") or []),
            usage=dict(data.get("usage") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchCarryState:
    state_id: str
    search_id: str
    message_ids: List[str]
    artifact_refs: List[str]
    bounded_by: str
    token_budget: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_id", _require_text(self.state_id, "state_id"))
        object.__setattr__(self, "search_id", _require_text(self.search_id, "search_id"))
        object.__setattr__(self, "message_ids", _copy_text_list(self.message_ids))
        object.__setattr__(self, "artifact_refs", _copy_text_list(self.artifact_refs))
        object.__setattr__(self, "bounded_by", _require_text(self.bounded_by, "bounded_by"))
        token_budget = int(self.token_budget)
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        object.__setattr__(self, "token_budget", token_budget)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.message_ids:
            raise ValueError("message_ids must contain at least one message id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_id": self.state_id,
            "search_id": self.search_id,
            "message_ids": list(self.message_ids),
            "artifact_refs": list(self.artifact_refs),
            "bounded_by": self.bounded_by,
            "token_budget": self.token_budget,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchCarryState":
        return SearchCarryState(
            state_id=data.get("state_id") or "",
            search_id=data.get("search_id") or "",
            message_ids=list(data.get("message_ids") or []),
            artifact_refs=list(data.get("artifact_refs") or []),
            bounded_by=data.get("bounded_by") or "",
            token_budget=int(data.get("token_budget") or 0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchFrontier:
    frontier_id: str
    search_id: str
    round_index: int
    candidate_ids: List[str]
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontier_id", _require_text(self.frontier_id, "frontier_id"))
        object.__setattr__(self, "search_id", _require_text(self.search_id, "search_id"))
        round_index = int(self.round_index)
        if round_index < 0:
            raise ValueError("round_index must be >= 0")
        status = _require_text(self.status, "status").lower()
        if status not in ALLOWED_FRONTIER_STATUSES:
            raise ValueError(f"status must be one of: {sorted(ALLOWED_FRONTIER_STATUSES)}")
        object.__setattr__(self, "round_index", round_index)
        object.__setattr__(self, "candidate_ids", _copy_text_list(self.candidate_ids))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.candidate_ids:
            raise ValueError("candidate_ids must contain at least one candidate id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frontier_id": self.frontier_id,
            "search_id": self.search_id,
            "round_index": self.round_index,
            "candidate_ids": list(self.candidate_ids),
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchFrontier":
        return SearchFrontier(
            frontier_id=data.get("frontier_id") or "",
            search_id=data.get("search_id") or "",
            round_index=int(data.get("round_index") or 0),
            candidate_ids=list(data.get("candidate_ids") or []),
            status=data.get("status") or "active",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchEvent:
    event_id: str
    search_id: str
    frontier_id: str
    round_index: int
    operator_kind: str
    input_candidate_ids: List[str]
    output_candidate_ids: List[str] = field(default_factory=list)
    message_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(self, "search_id", _require_text(self.search_id, "search_id"))
        object.__setattr__(self, "frontier_id", _require_text(self.frontier_id, "frontier_id"))
        round_index = int(self.round_index)
        if round_index < 0:
            raise ValueError("round_index must be >= 0")
        operator_kind = _require_text(self.operator_kind, "operator_kind").lower()
        if operator_kind not in ALLOWED_OPERATOR_KINDS:
            raise ValueError(f"operator_kind must be one of: {sorted(ALLOWED_OPERATOR_KINDS)}")
        object.__setattr__(self, "round_index", round_index)
        object.__setattr__(self, "operator_kind", operator_kind)
        object.__setattr__(self, "input_candidate_ids", _copy_text_list(self.input_candidate_ids))
        object.__setattr__(self, "output_candidate_ids", _copy_text_list(self.output_candidate_ids))
        object.__setattr__(self, "message_ids", _copy_text_list(self.message_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.input_candidate_ids:
            raise ValueError("input_candidate_ids must contain at least one candidate id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "search_id": self.search_id,
            "frontier_id": self.frontier_id,
            "round_index": self.round_index,
            "operator_kind": self.operator_kind,
            "input_candidate_ids": list(self.input_candidate_ids),
            "output_candidate_ids": list(self.output_candidate_ids),
            "message_ids": list(self.message_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchEvent":
        return SearchEvent(
            event_id=data.get("event_id") or "",
            search_id=data.get("search_id") or "",
            frontier_id=data.get("frontier_id") or "",
            round_index=int(data.get("round_index") or 0),
            operator_kind=data.get("operator_kind") or "",
            input_candidate_ids=list(data.get("input_candidate_ids") or []),
            output_candidate_ids=list(data.get("output_candidate_ids") or []),
            message_ids=list(data.get("message_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchMetrics:
    aggregability_gap: float
    diversity_decay: float
    mixing_rate: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregability_gap", float(self.aggregability_gap))
        object.__setattr__(self, "diversity_decay", float(self.diversity_decay))
        object.__setattr__(self, "mixing_rate", float(self.mixing_rate))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aggregability_gap": self.aggregability_gap,
            "diversity_decay": self.diversity_decay,
            "mixing_rate": self.mixing_rate,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchMetrics":
        return SearchMetrics(
            aggregability_gap=float(data.get("aggregability_gap") or 0.0),
            diversity_decay=float(data.get("diversity_decay") or 0.0),
            mixing_rate=float(data.get("mixing_rate") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchRun:
    search_id: str
    recipe_kind: str
    candidates: List[SearchCandidate]
    frontiers: List[SearchFrontier]
    events: List[SearchEvent]
    messages: List[SearchMessage] = field(default_factory=list)
    carry_states: List[SearchCarryState] = field(default_factory=list)
    metrics: Optional[SearchMetrics] = None
    selected_candidate_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_id", _require_text(self.search_id, "search_id"))
        object.__setattr__(self, "recipe_kind", _require_text(self.recipe_kind, "recipe_kind"))
        object.__setattr__(
            self,
            "candidates",
            [item if isinstance(item, SearchCandidate) else SearchCandidate.from_dict(item) for item in self.candidates],
        )
        object.__setattr__(
            self,
            "frontiers",
            [item if isinstance(item, SearchFrontier) else SearchFrontier.from_dict(item) for item in self.frontiers],
        )
        object.__setattr__(
            self,
            "events",
            [item if isinstance(item, SearchEvent) else SearchEvent.from_dict(item) for item in self.events],
        )
        object.__setattr__(
            self,
            "messages",
            [item if isinstance(item, SearchMessage) else SearchMessage.from_dict(item) for item in self.messages],
        )
        object.__setattr__(
            self,
            "carry_states",
            [item if isinstance(item, SearchCarryState) else SearchCarryState.from_dict(item) for item in self.carry_states],
        )
        object.__setattr__(
            self,
            "metrics",
            self.metrics if isinstance(self.metrics, SearchMetrics) or self.metrics is None else SearchMetrics.from_dict(self.metrics),
        )
        object.__setattr__(
            self,
            "selected_candidate_id",
            str(self.selected_candidate_id).strip() if self.selected_candidate_id else None,
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.candidates:
            raise ValueError("candidates must contain at least one candidate")
        if not self.frontiers:
            raise ValueError("frontiers must contain at least one frontier")
        if not self.events:
            raise ValueError("events must contain at least one event")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "search_id": self.search_id,
            "recipe_kind": self.recipe_kind,
            "candidates": [item.to_dict() for item in self.candidates],
            "frontiers": [item.to_dict() for item in self.frontiers],
            "events": [item.to_dict() for item in self.events],
            "messages": [item.to_dict() for item in self.messages],
            "carry_states": [item.to_dict() for item in self.carry_states],
            "metadata": dict(self.metadata),
        }
        if self.metrics is not None:
            payload["metrics"] = self.metrics.to_dict()
        if self.selected_candidate_id:
            payload["selected_candidate_id"] = self.selected_candidate_id
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchRun":
        return SearchRun(
            search_id=data.get("search_id") or "",
            recipe_kind=data.get("recipe_kind") or "",
            candidates=[SearchCandidate.from_dict(item) for item in data.get("candidates") or []],
            frontiers=[SearchFrontier.from_dict(item) for item in data.get("frontiers") or []],
            events=[SearchEvent.from_dict(item) for item in data.get("events") or []],
            messages=[SearchMessage.from_dict(item) for item in data.get("messages") or []],
            carry_states=[SearchCarryState.from_dict(item) for item in data.get("carry_states") or []],
            metrics=SearchMetrics.from_dict(data["metrics"]) if data.get("metrics") else None,
            selected_candidate_id=data.get("selected_candidate_id"),
            metadata=dict(data.get("metadata") or {}),
        )
