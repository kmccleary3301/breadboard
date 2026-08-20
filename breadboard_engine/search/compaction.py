from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .schema import SearchCandidate, SearchCarryState, SearchMessage


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
class CompactionOutput:
    summary_payload: Dict[str, Any]
    dropped_fields: List[str] = field(default_factory=list)
    unresolved_gaps: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_payload", _copy_mapping(self.summary_payload))
        object.__setattr__(self, "dropped_fields", _copy_text_list(self.dropped_fields))
        object.__setattr__(self, "unresolved_gaps", _copy_text_list(self.unresolved_gaps))
        if self.confidence is not None:
            confidence = float(self.confidence)
            if confidence < 0.0 or confidence > 1.0:
                raise ValueError("confidence must be within [0.0, 1.0]")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "usage", _copy_mapping(self.usage))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RegisteredCompactionBackend:
    backend_kind: str
    schema_kind: str
    bounded_by: str
    token_budget: int
    max_artifact_refs: int = 4
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        backend_kind = str(self.backend_kind or "").strip()
        schema_kind = str(self.schema_kind or "").strip()
        bounded_by = str(self.bounded_by or "").strip()
        token_budget = int(self.token_budget)
        max_artifact_refs = int(self.max_artifact_refs)
        if not backend_kind:
            raise ValueError("backend_kind must be non-empty")
        if not schema_kind:
            raise ValueError("schema_kind must be non-empty")
        if not bounded_by:
            raise ValueError("bounded_by must be non-empty")
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if max_artifact_refs <= 0:
            raise ValueError("max_artifact_refs must be positive")
        object.__setattr__(self, "backend_kind", backend_kind)
        object.__setattr__(self, "schema_kind", schema_kind)
        object.__setattr__(self, "bounded_by", bounded_by)
        object.__setattr__(self, "token_budget", token_budget)
        object.__setattr__(self, "max_artifact_refs", max_artifact_refs)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


CompactionFn = Callable[[Sequence[SearchCandidate], RegisteredCompactionBackend, Mapping[str, Any]], CompactionOutput]


class SearchCompactionRegistry:
    def __init__(self) -> None:
        self._backends: Dict[str, Tuple[RegisteredCompactionBackend, CompactionFn]] = {}

    def register(self, backend: RegisteredCompactionBackend, handler: CompactionFn) -> None:
        self._backends[backend.backend_kind] = (backend, handler)

    def list_backend_kinds(self) -> List[str]:
        return sorted(self._backends.keys())

    def compact(
        self,
        *,
        backend_kind: str,
        search_id: str,
        carry_state_id: str,
        message_id: str,
        candidates: Sequence[SearchCandidate],
        metadata: Mapping[str, Any] | None = None,
    ) -> Tuple[SearchMessage, SearchCarryState]:
        if backend_kind not in self._backends:
            raise ValueError(f"unknown compaction backend: {backend_kind}")
        if not candidates:
            raise ValueError("candidates must contain at least one candidate")

        backend, handler = self._backends[backend_kind]
        source_candidates = list(candidates)
        output = handler(source_candidates, backend, metadata or {})
        kept_artifact_refs = [item.payload_ref for item in source_candidates[: backend.max_artifact_refs]]
        omitted_artifact_refs = [item.payload_ref for item in source_candidates[backend.max_artifact_refs :]]

        message = SearchMessage(
            message_id=message_id,
            schema_kind=backend.schema_kind,
            source_candidate_ids=[item.candidate_id for item in source_candidates],
            summary_payload=dict(output.summary_payload),
            dropped_fields=list(output.dropped_fields),
            omitted_artifact_refs=omitted_artifact_refs,
            confidence=output.confidence,
            unresolved_gaps=list(output.unresolved_gaps),
            usage=dict(output.usage),
            metadata={
                "backend_kind": backend.backend_kind,
                **dict(backend.metadata),
                **dict(output.metadata),
                **dict(metadata or {}),
            },
        )
        carry_state = SearchCarryState(
            state_id=carry_state_id,
            search_id=search_id,
            message_ids=[message.message_id],
            artifact_refs=kept_artifact_refs,
            bounded_by=backend.bounded_by,
            token_budget=backend.token_budget,
            metadata={
                "backend_kind": backend.backend_kind,
                "schema_kind": backend.schema_kind,
                "candidate_count": len(source_candidates),
                **dict(backend.metadata),
                **dict(metadata or {}),
            },
        )
        return message, carry_state


def _candidate_rollup_backend(
    candidates: Sequence[SearchCandidate],
    backend: RegisteredCompactionBackend,
    metadata: Mapping[str, Any],
) -> CompactionOutput:
    scores = [float(item.score_vector.get("correctness_score", 0.0)) for item in candidates]
    best = max(
        candidates,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
    )
    average_score = sum(scores) / float(len(scores))
    return CompactionOutput(
        summary_payload={
            "candidate_count": len(candidates),
            "best_candidate_id": best.candidate_id,
            "average_correctness_score": round(average_score, 4),
            "frontier_ids": sorted({item.frontier_id for item in candidates}),
        },
        dropped_fields=["full_reasoning_trace", "workspace_snapshot"],
        unresolved_gaps=["verifier_not_run"],
        confidence=0.72,
        usage={"compaction_tokens": 18},
        metadata={"carry_mode": "bounded_rollup", **dict(metadata)},
    )


def build_default_search_compaction_registry() -> SearchCompactionRegistry:
    registry = SearchCompactionRegistry()
    registry.register(
        RegisteredCompactionBackend(
            backend_kind="bounded_candidate_rollup.v1",
            schema_kind="candidate_rollup.v1",
            bounded_by="single_summary_message",
            token_budget=192,
            max_artifact_refs=3,
            metadata={"phase": "dag_v1_phase2", "non_kernel": True},
        ),
        _candidate_rollup_backend,
    )
    return registry
