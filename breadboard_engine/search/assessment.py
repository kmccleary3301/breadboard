from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from .schema import SearchAssessment, SearchCandidate


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
class AssessmentOutput:
    verdict: str
    score_vector: Dict[str, Any]
    summary_payload: Dict[str, Any]
    artifact_refs: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        verdict = str(self.verdict or "").strip().lower()
        if not verdict:
            raise ValueError("verdict must be non-empty")
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "score_vector", _copy_mapping(self.score_vector))
        object.__setattr__(self, "summary_payload", _copy_mapping(self.summary_payload))
        object.__setattr__(self, "artifact_refs", _copy_text_list(self.artifact_refs))
        object.__setattr__(self, "usage", _copy_mapping(self.usage))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RegisteredAssessmentBackend:
    backend_kind: str
    assessment_kind: str
    schema_kind: str
    subject_arity: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        backend_kind = str(self.backend_kind or "").strip()
        assessment_kind = str(self.assessment_kind or "").strip().lower()
        schema_kind = str(self.schema_kind or "").strip()
        subject_arity = str(self.subject_arity or "").strip().lower()
        if not backend_kind:
            raise ValueError("backend_kind must be non-empty")
        if not assessment_kind:
            raise ValueError("assessment_kind must be non-empty")
        if not schema_kind:
            raise ValueError("schema_kind must be non-empty")
        if subject_arity not in {"one", "pair", "set"}:
            raise ValueError("subject_arity must be one of: ['one', 'pair', 'set']")
        object.__setattr__(self, "backend_kind", backend_kind)
        object.__setattr__(self, "assessment_kind", assessment_kind)
        object.__setattr__(self, "schema_kind", schema_kind)
        object.__setattr__(self, "subject_arity", subject_arity)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


AssessmentFn = Callable[[Sequence[SearchCandidate], RegisteredAssessmentBackend, Mapping[str, Any]], AssessmentOutput]


class SearchAssessmentRegistry:
    def __init__(self) -> None:
        self._backends: Dict[str, Tuple[RegisteredAssessmentBackend, AssessmentFn]] = {}

    def register(self, backend: RegisteredAssessmentBackend, handler: AssessmentFn) -> None:
        self._backends[backend.backend_kind] = (backend, handler)

    def list_backend_kinds(self) -> List[str]:
        return sorted(self._backends.keys())

    def get_backend(self, backend_kind: str) -> RegisteredAssessmentBackend:
        if backend_kind not in self._backends:
            raise ValueError(f"unknown assessment backend: {backend_kind}")
        return self._backends[backend_kind][0]

    def assess(
        self,
        *,
        backend_kind: str,
        assessment_id: str,
        search_id: str,
        frontier_id: str,
        round_index: int,
        candidates: Sequence[SearchCandidate],
        metadata: Mapping[str, Any] | None = None,
    ) -> SearchAssessment:
        if backend_kind not in self._backends:
            raise ValueError(f"unknown assessment backend: {backend_kind}")
        if not candidates:
            raise ValueError("candidates must contain at least one candidate")

        backend, handler = self._backends[backend_kind]
        if backend.subject_arity == "one" and len(candidates) != 1:
            raise ValueError("one-arity assessment requires exactly one candidate")
        if backend.subject_arity == "pair" and len(candidates) != 2:
            raise ValueError("pair-arity assessment requires exactly two candidates")

        output = handler(list(candidates), backend, metadata or {})
        return SearchAssessment(
            assessment_id=assessment_id,
            search_id=search_id,
            frontier_id=frontier_id,
            round_index=round_index,
            assessment_kind=backend.assessment_kind,
            backend_kind=backend.backend_kind,
            schema_kind=backend.schema_kind,
            subject_candidate_ids=[item.candidate_id for item in candidates],
            verdict=output.verdict,
            score_vector=dict(output.score_vector),
            summary_payload=dict(output.summary_payload),
            artifact_refs=list(output.artifact_refs),
            usage=dict(output.usage),
            metadata={**dict(backend.metadata), **dict(output.metadata), **dict(metadata or {})},
        )


def _exact_tests_backend(
    candidates: Sequence[SearchCandidate],
    backend: RegisteredAssessmentBackend,
    metadata: Mapping[str, Any],
) -> AssessmentOutput:
    candidate = candidates[0]
    score = float(candidate.score_vector.get("correctness_score", 0.0))
    verdict = "pass" if score >= 0.6 else "fail"
    return AssessmentOutput(
        verdict=verdict,
        score_vector={"correctness_score": score, "test_pass_rate": 1.0 if verdict == "pass" else 0.0},
        summary_payload={
            "summary": "Exact verifier result",
            "candidate_id": candidate.candidate_id,
            "verdict": verdict,
        },
        artifact_refs=[f"artifacts/search/{candidate.search_id}/exact_tests_report.json"],
        usage={"assessment_tokens": 0},
        metadata={"backend_kind": backend.backend_kind, **dict(metadata)},
    )


def _judge_pairwise_backend(
    candidates: Sequence[SearchCandidate],
    backend: RegisteredAssessmentBackend,
    metadata: Mapping[str, Any],
) -> AssessmentOutput:
    ordered = sorted(
        candidates,
        key=lambda item: (
            float(item.score_vector.get("correctness_score", 0.0)),
            -int(item.round_index),
            item.candidate_id,
        ),
        reverse=True,
    )
    preferred = ordered[0]
    rejected = ordered[1]
    return AssessmentOutput(
        verdict="prefer_a",
        score_vector={
            "preferred_score": float(preferred.score_vector.get("correctness_score", 0.0)),
            "rejected_score": float(rejected.score_vector.get("correctness_score", 0.0)),
        },
        summary_payload={
            "summary": "Pairwise judge verdict",
            "preferred_candidate_id": preferred.candidate_id,
            "rejected_candidate_id": rejected.candidate_id,
        },
        artifact_refs=[f"artifacts/search/{preferred.search_id}/judge_pairwise_report.json"],
        usage={"assessment_tokens": 48},
        metadata={"backend_kind": backend.backend_kind, **dict(metadata)},
    )


def build_default_search_assessment_registry() -> SearchAssessmentRegistry:
    registry = SearchAssessmentRegistry()
    registry.register(
        RegisteredAssessmentBackend(
            backend_kind="exact_tests.v1",
            assessment_kind="verify",
            schema_kind="code.test_report.v1",
            subject_arity="one",
            metadata={"phase": "dag_v2_phase1", "non_kernel": True},
        ),
        _exact_tests_backend,
    )
    registry.register(
        RegisteredAssessmentBackend(
            backend_kind="judge_pairwise.v1",
            assessment_kind="judge",
            schema_kind="judge.verdict.v1",
            subject_arity="pair",
            metadata={"phase": "dag_v2_phase1", "non_kernel": True},
        ),
        _judge_pairwise_backend,
    )
    return registry
