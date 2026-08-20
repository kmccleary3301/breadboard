from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .context import (
    OptimizationRuntimeContext,
    RuntimeCompatibilityIssue,
    RuntimeCompatibilityResult,
)
from .dataset import OptimizationDataset
from .evaluation import EvaluationRecord
from .suites import (
    EvaluationSuiteManifest,
    FamilyCompositionManifest,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
)
from .substrate import (
    CandidateBundle,
    CandidateChange,
    MaterializedCandidate,
    MutableLocus,
    OptimizationInvariant,
    OptimizationTarget,
    SupportEnvelope,
    materialize_candidate,
)


OBJECTIVE_DIRECTIONS: Dict[str, str] = {
    "correctness_score": "maximize",
    "wrongness_penalty": "minimize",
    "mutation_cost": "minimize",
    "instability_penalty": "minimize",
}


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


def _json_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _candidate_fingerprint(candidate: CandidateBundle) -> str:
    payload = {
        "source_target_id": candidate.source_target_id,
        "changes": [change.to_dict() for change in candidate.changes],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _find_sample(dataset: OptimizationDataset, sample_id: str) -> Any:
    for sample in dataset.samples:
        if sample.sample_id == sample_id:
            return sample
    raise ValueError(f"dataset does not contain sample_id {sample_id}")


def _find_locus(target: OptimizationTarget, locus_id: str) -> MutableLocus:
    for locus in target.mutable_loci:
        if locus.locus_id == locus_id:
            return locus
    raise ValueError(f"unknown locus_id: {locus_id}")


def _validate_candidate_against_search_space(
    candidate: CandidateBundle,
    search_space: "SearchSpaceManifest",
) -> None:
    allowed_loci = set(search_space.allowed_loci)
    candidate_loci = set(candidate.applied_loci)
    unknown_loci = sorted(candidate_loci - allowed_loci)
    if unknown_loci:
        raise ValueError(f"candidate uses loci outside the declared search space: {unknown_loci}")
    for change in candidate.changes:
        allowed_mutation_kinds = set(search_space.mutation_kinds_by_locus.get(change.locus_id) or [])
        if "replace" not in allowed_mutation_kinds:
            raise ValueError(f"search space does not allow replace mutations for locus {change.locus_id}")


def _envelope_widens(baseline: SupportEnvelope, candidate: SupportEnvelope) -> bool:
    for field_name in ("tools", "execution_profiles", "environments", "providers", "models"):
        baseline_values = set(getattr(baseline, field_name))
        candidate_values = set(getattr(candidate, field_name))
        if candidate_values - baseline_values:
            return True
    baseline_assumptions = dict(baseline.assumptions)
    candidate_assumptions = dict(candidate.assumptions)
    if set(candidate_assumptions) - set(baseline_assumptions):
        return True
    for key, value in candidate_assumptions.items():
        if baseline_assumptions.get(key) != value:
            return True
    return False


def _supports_overlay_only(candidate: CandidateBundle) -> None:
    for change in candidate.changes:
        if not isinstance(change.value, Mapping):
            raise ValueError("candidate changes must remain narrow overlay mappings")


def _artifact_count_for_candidate(target: OptimizationTarget, candidate: CandidateBundle) -> int:
    artifact_refs = {
        locus.artifact_ref
        for locus in target.mutable_loci
        if locus.locus_id in set(candidate.applied_loci) and locus.artifact_ref
    }
    return len(artifact_refs)


def _payload_size_for_candidate(candidate: CandidateBundle) -> int:
    return sum(_json_size(change.value) for change in candidate.changes)


def _normalized_payload_cost(candidate: CandidateBundle, bounds: "MutationBounds") -> float:
    if bounds.max_total_value_bytes <= 0:
        return 1.0
    return min(1.0, _payload_size_for_candidate(candidate) / float(bounds.max_total_value_bytes))


def _outcome_score(outcome: str) -> float:
    return {
        "passed": 1.0,
        "failed": 0.0,
        "rejected_by_gate": 0.1,
        "inconclusive": 0.5,
        "error": 0.0,
    }.get(outcome, 0.0)


def _wrongness_penalty(evaluations: Sequence[EvaluationRecord]) -> float:
    if not evaluations:
        return 1.0
    weighted_total = 0.0
    count = 0
    for evaluation in evaluations:
        for report in evaluation.wrongness_reports:
            class_penalty = {
                "correctness.result_mismatch": 0.85,
                "correctness.missing_required_output": 0.95,
                "policy.support_envelope_violation": 1.0,
                "policy.invariant_violation": 1.0,
                "replay.conformance_drift": 0.9,
                "environment.tool_unavailable": 0.8,
                "environment.profile_mismatch": 0.75,
            }.get(report.wrongness_class, 0.8)
            weighted_total += class_penalty * report.confidence
            count += 1
    if not count:
        return 0.0
    return min(1.0, weighted_total / count)


def _instability_penalty(evaluations: Sequence[EvaluationRecord]) -> float:
    if len(evaluations) <= 1:
        return 0.0
    outcomes = {evaluation.aggregate_outcome() for evaluation in evaluations}
    if len(outcomes) <= 1:
        return 0.0
    return min(1.0, (len(outcomes) - 1) / max(1, len(evaluations)))


def _validate_builtin_invariants(
    target: OptimizationTarget,
    candidate: CandidateBundle,
    materialized: Optional[MaterializedCandidate],
) -> None:
    for invariant in target.invariants:
        _validate_single_invariant(target, candidate, materialized, invariant)


def _validate_single_invariant(
    target: OptimizationTarget,
    candidate: CandidateBundle,
    materialized: Optional[MaterializedCandidate],
    invariant: OptimizationInvariant,
) -> None:
    invariant_id = invariant.invariant_id
    if invariant_id in {"bounded-overlay-only", "keep-tool-surface-bounded"}:
        candidate.validate_against_target(target)
        _supports_overlay_only(candidate)
        return
    if invariant_id == "support-envelope-preserved":
        if materialized is None:
            return
        if _envelope_widens(target.support_envelope, materialized.support_envelope):
            raise ValueError("candidate may not silently widen the support envelope")
        return


def validate_bounded_candidate(
    target: OptimizationTarget,
    candidate: CandidateBundle,
    bounds: "MutationBounds",
    *,
    materialized: Optional[MaterializedCandidate] = None,
) -> Dict[str, int]:
    candidate.validate_against_target(target)
    _supports_overlay_only(candidate)
    blast_radius = {
        "changed_loci_count": len(candidate.applied_loci),
        "changed_artifact_count": _artifact_count_for_candidate(target, candidate),
        "total_value_bytes": _payload_size_for_candidate(candidate),
    }
    if blast_radius["changed_loci_count"] > bounds.max_changed_loci:
        raise ValueError("candidate blast radius exceeds max_changed_loci")
    if blast_radius["changed_artifact_count"] > bounds.max_changed_artifacts:
        raise ValueError("candidate blast radius exceeds max_changed_artifacts")
    if blast_radius["total_value_bytes"] > bounds.max_total_value_bytes:
        raise ValueError("candidate blast radius exceeds max_total_value_bytes")
    _validate_builtin_invariants(target, candidate, materialized)
    return blast_radius


@dataclass(frozen=True)
class MutationBounds:
    max_changed_loci: int = 2
    max_changed_artifacts: int = 1
    max_total_value_bytes: int = 1200
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.max_changed_loci) <= 0:
            raise ValueError("max_changed_loci must be positive")
        if int(self.max_changed_artifacts) <= 0:
            raise ValueError("max_changed_artifacts must be positive")
        if int(self.max_total_value_bytes) <= 0:
            raise ValueError("max_total_value_bytes must be positive")
        object.__setattr__(self, "max_changed_loci", int(self.max_changed_loci))
        object.__setattr__(self, "max_changed_artifacts", int(self.max_changed_artifacts))
        object.__setattr__(self, "max_total_value_bytes", int(self.max_total_value_bytes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_changed_loci": self.max_changed_loci,
            "max_changed_artifacts": self.max_changed_artifacts,
            "max_total_value_bytes": self.max_total_value_bytes,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> "MutationBounds":
        data = data or {}
        return MutationBounds(
            max_changed_loci=int(data.get("max_changed_loci") or 2),
            max_changed_artifacts=int(data.get("max_changed_artifacts") or 1),
            max_total_value_bytes=int(data.get("max_total_value_bytes") or 1200),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationExecutionContext:
    target_id: str
    sample_id: str
    runtime_context: OptimizationRuntimeContext
    evaluation_input_compatibility: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "sample_id", _require_text(self.sample_id, "sample_id"))
        object.__setattr__(
            self,
            "runtime_context",
            self.runtime_context
            if isinstance(self.runtime_context, OptimizationRuntimeContext)
            else OptimizationRuntimeContext.from_dict(self.runtime_context),
        )
        object.__setattr__(self, "evaluation_input_compatibility", _copy_mapping(self.evaluation_input_compatibility))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "sample_id": self.sample_id,
            "runtime_context": self.runtime_context.to_dict(),
            "evaluation_input_compatibility": dict(self.evaluation_input_compatibility),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationExecutionContext":
        return OptimizationExecutionContext(
            target_id=data.get("target_id") or "",
            sample_id=data.get("sample_id") or "",
            runtime_context=OptimizationRuntimeContext.from_dict(data.get("runtime_context") or {}),
            evaluation_input_compatibility=dict(data.get("evaluation_input_compatibility") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ObjectiveVector:
    correctness_score: float
    wrongness_penalty: float
    mutation_cost: float
    instability_penalty: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("correctness_score", "wrongness_penalty", "mutation_cost", "instability_penalty"):
            value = float(getattr(self, field_name))
            if value < 0.0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correctness_score": self.correctness_score,
            "wrongness_penalty": self.wrongness_penalty,
            "mutation_cost": self.mutation_cost,
            "instability_penalty": self.instability_penalty,
            "objective_directions": dict(OBJECTIVE_DIRECTIONS),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ObjectiveVector":
        return ObjectiveVector(
            correctness_score=float(data.get("correctness_score") or 0.0),
            wrongness_penalty=float(data.get("wrongness_penalty") or 0.0),
            mutation_cost=float(data.get("mutation_cost") or 0.0),
            instability_penalty=float(data.get("instability_penalty") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )

    def dominates(self, other: "ObjectiveVector", *, tolerance: float = 1e-9) -> bool:
        comparisons: List[bool] = []
        strict = False
        for field_name, direction in OBJECTIVE_DIRECTIONS.items():
            ours = float(getattr(self, field_name))
            theirs = float(getattr(other, field_name))
            if direction == "maximize":
                if ours + tolerance < theirs:
                    return False
                comparisons.append(ours > theirs + tolerance)
            else:
                if ours > theirs + tolerance:
                    return False
                comparisons.append(ours + tolerance < theirs)
        strict = any(comparisons)
        return strict

    def effectively_equal(self, other: "ObjectiveVector", *, tolerance: float = 1e-9) -> bool:
        return all(abs(float(getattr(self, name)) - float(getattr(other, name))) <= tolerance for name in OBJECTIVE_DIRECTIONS)


@dataclass(frozen=True)
class ReflectionFinding:
    wrongness_id: str
    wrongness_class: str
    failure_locus: str
    suggested_repair_locus: Optional[str]
    confidence: float
    blocked_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "wrongness_id", _require_text(self.wrongness_id, "wrongness_id"))
        object.__setattr__(self, "wrongness_class", _require_text(self.wrongness_class, "wrongness_class"))
        object.__setattr__(self, "failure_locus", _require_text(self.failure_locus, "failure_locus"))
        repair_locus = str(self.suggested_repair_locus).strip() if self.suggested_repair_locus else None
        object.__setattr__(self, "suggested_repair_locus", repair_locus)
        confidence = float(self.confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "blocked_reason", str(self.blocked_reason).strip() if self.blocked_reason else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "wrongness_id": self.wrongness_id,
            "wrongness_class": self.wrongness_class,
            "failure_locus": self.failure_locus,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }
        if self.suggested_repair_locus:
            payload["suggested_repair_locus"] = self.suggested_repair_locus
        if self.blocked_reason:
            payload["blocked_reason"] = self.blocked_reason
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReflectionFinding":
        return ReflectionFinding(
            wrongness_id=data.get("wrongness_id") or "",
            wrongness_class=data.get("wrongness_class") or "",
            failure_locus=data.get("failure_locus") or "",
            suggested_repair_locus=data.get("suggested_repair_locus"),
            confidence=float(data.get("confidence") or 0.0),
            blocked_reason=data.get("blocked_reason"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReflectionDecision:
    decision_id: str
    target_candidate_id: str
    should_mutate: bool
    recommended_loci: List[str] = field(default_factory=list)
    findings: List[ReflectionFinding] = field(default_factory=list)
    declined_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_text(self.decision_id, "decision_id"))
        object.__setattr__(self, "target_candidate_id", _require_text(self.target_candidate_id, "target_candidate_id"))
        object.__setattr__(self, "should_mutate", bool(self.should_mutate))
        object.__setattr__(self, "recommended_loci", _copy_text_list(self.recommended_loci))
        object.__setattr__(
            self,
            "findings",
            [item if isinstance(item, ReflectionFinding) else ReflectionFinding.from_dict(item) for item in self.findings],
        )
        object.__setattr__(self, "declined_reason", str(self.declined_reason).strip() if self.declined_reason else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if self.should_mutate and not self.recommended_loci:
            raise ValueError("recommended_loci must be non-empty when should_mutate is true")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "decision_id": self.decision_id,
            "target_candidate_id": self.target_candidate_id,
            "should_mutate": self.should_mutate,
            "recommended_loci": list(self.recommended_loci),
            "findings": [finding.to_dict() for finding in self.findings],
            "metadata": dict(self.metadata),
        }
        if self.declined_reason:
            payload["declined_reason"] = self.declined_reason
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReflectionDecision":
        return ReflectionDecision(
            decision_id=data.get("decision_id") or "",
            target_candidate_id=data.get("target_candidate_id") or "",
            should_mutate=bool(data.get("should_mutate")),
            recommended_loci=list(data.get("recommended_loci") or []),
            findings=[ReflectionFinding.from_dict(item) for item in data.get("findings") or []],
            declined_reason=data.get("declined_reason"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MutationProposal:
    proposal_id: str
    policy_id: str
    candidate: CandidateBundle
    blast_radius: Dict[str, Any]
    rationale_summary: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _require_text(self.proposal_id, "proposal_id"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        object.__setattr__(
            self,
            "candidate",
            self.candidate if isinstance(self.candidate, CandidateBundle) else CandidateBundle.from_dict(self.candidate),
        )
        object.__setattr__(self, "blast_radius", _copy_mapping(self.blast_radius))
        object.__setattr__(self, "rationale_summary", _require_text(self.rationale_summary, "rationale_summary"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "policy_id": self.policy_id,
            "candidate": self.candidate.to_dict(),
            "blast_radius": dict(self.blast_radius),
            "rationale_summary": self.rationale_summary,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "MutationProposal":
        return MutationProposal(
            proposal_id=data.get("proposal_id") or "",
            policy_id=data.get("policy_id") or "",
            candidate=CandidateBundle.from_dict(data.get("candidate") or {}),
            blast_radius=dict(data.get("blast_radius") or {}),
            rationale_summary=data.get("rationale_summary") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PortfolioEntry:
    candidate: CandidateBundle
    materialized_candidate: MaterializedCandidate
    objective_vector: ObjectiveVector
    evidence_lineage: List[str] = field(default_factory=list)
    score_kind: str = "observed"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate",
            self.candidate if isinstance(self.candidate, CandidateBundle) else CandidateBundle.from_dict(self.candidate),
        )
        object.__setattr__(
            self,
            "materialized_candidate",
            self.materialized_candidate
            if isinstance(self.materialized_candidate, MaterializedCandidate)
            else MaterializedCandidate.from_dict(self.materialized_candidate),
        )
        object.__setattr__(
            self,
            "objective_vector",
            self.objective_vector if isinstance(self.objective_vector, ObjectiveVector) else ObjectiveVector.from_dict(self.objective_vector),
        )
        object.__setattr__(self, "evidence_lineage", _copy_text_list(self.evidence_lineage))
        score_kind = _require_text(self.score_kind, "score_kind").lower()
        if score_kind not in {"observed", "predicted"}:
            raise ValueError("score_kind must be one of: observed, predicted")
        object.__setattr__(self, "score_kind", score_kind)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def fingerprint(self) -> str:
        return _candidate_fingerprint(self.candidate)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "materialized_candidate": self.materialized_candidate.to_dict(),
            "objective_vector": self.objective_vector.to_dict(),
            "evidence_lineage": list(self.evidence_lineage),
            "score_kind": self.score_kind,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PortfolioEntry":
        return PortfolioEntry(
            candidate=CandidateBundle.from_dict(data.get("candidate") or {}),
            materialized_candidate=MaterializedCandidate.from_dict(data.get("materialized_candidate") or {}),
            objective_vector=ObjectiveVector.from_dict(data.get("objective_vector") or {}),
            evidence_lineage=list(data.get("evidence_lineage") or []),
            score_kind=data.get("score_kind") or "observed",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidatePortfolio:
    entries: List[PortfolioEntry] = field(default_factory=list)
    comparison_protocol: str = "pareto_objective_vector_v1"
    dominance_tolerance: float = 1e-9
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entries",
            [item if isinstance(item, PortfolioEntry) else PortfolioEntry.from_dict(item) for item in self.entries],
        )
        object.__setattr__(self, "comparison_protocol", _require_text(self.comparison_protocol, "comparison_protocol"))
        object.__setattr__(self, "dominance_tolerance", float(self.dominance_tolerance))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def compare(self, left: PortfolioEntry, right: PortfolioEntry) -> str:
        if left.objective_vector.dominates(right.objective_vector, tolerance=self.dominance_tolerance):
            return "dominates"
        if right.objective_vector.dominates(left.objective_vector, tolerance=self.dominance_tolerance):
            return "dominated"
        if left.objective_vector.effectively_equal(right.objective_vector, tolerance=self.dominance_tolerance):
            return "equal"
        return "tradeoff"

    def retain_entry(self, new_entry: PortfolioEntry) -> "CandidatePortfolio":
        retained: List[PortfolioEntry] = []
        replaced_duplicate = False

        for current in self.entries:
            if current.fingerprint() == new_entry.fingerprint():
                relation = self.compare(new_entry, current)
                if relation == "dominates":
                    retained.append(new_entry)
                elif relation == "equal":
                    retained.append(min(current, new_entry, key=lambda item: item.candidate.candidate_id))
                else:
                    retained.append(current)
                replaced_duplicate = True
                continue

            relation = self.compare(new_entry, current)
            if relation == "dominated":
                return self
            if relation == "dominates":
                continue
            retained.append(current)

        if not replaced_duplicate:
            retained.append(new_entry)
        retained.sort(key=lambda item: item.candidate.candidate_id)
        return CandidatePortfolio(
            entries=retained,
            comparison_protocol=self.comparison_protocol,
            dominance_tolerance=self.dominance_tolerance,
            metadata=self.metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "comparison_protocol": self.comparison_protocol,
            "dominance_tolerance": self.dominance_tolerance,
            "objective_directions": dict(OBJECTIVE_DIRECTIONS),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CandidatePortfolio":
        return CandidatePortfolio(
            entries=[PortfolioEntry.from_dict(item) for item in data.get("entries") or []],
            comparison_protocol=data.get("comparison_protocol") or "pareto_objective_vector_v1",
            dominance_tolerance=float(data.get("dominance_tolerance") or 1e-9),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReflectiveParetoBackendRequest:
    request_id: str
    target: OptimizationTarget
    baseline_candidate: CandidateBundle
    baseline_materialized_candidate: MaterializedCandidate
    dataset: OptimizationDataset
    evaluations: List[EvaluationRecord] = field(default_factory=list)
    active_sample_id: Optional[str] = None
    execution_context: Optional[OptimizationExecutionContext] = None
    mutation_bounds: MutationBounds = field(default_factory=MutationBounds)
    max_proposals: int = 3
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_text(self.request_id, "request_id"))
        object.__setattr__(self, "target", self.target if isinstance(self.target, OptimizationTarget) else OptimizationTarget.from_dict(self.target))
        object.__setattr__(
            self,
            "baseline_candidate",
            self.baseline_candidate if isinstance(self.baseline_candidate, CandidateBundle) else CandidateBundle.from_dict(self.baseline_candidate),
        )
        object.__setattr__(
            self,
            "baseline_materialized_candidate",
            self.baseline_materialized_candidate
            if isinstance(self.baseline_materialized_candidate, MaterializedCandidate)
            else MaterializedCandidate.from_dict(self.baseline_materialized_candidate),
        )
        object.__setattr__(self, "dataset", self.dataset if isinstance(self.dataset, OptimizationDataset) else OptimizationDataset.from_dict(self.dataset))
        object.__setattr__(
            self,
            "evaluations",
            [item if isinstance(item, EvaluationRecord) else EvaluationRecord.from_dict(item) for item in self.evaluations],
        )
        object.__setattr__(self, "active_sample_id", str(self.active_sample_id).strip() if self.active_sample_id else None)
        object.__setattr__(
            self,
            "execution_context",
            None
            if self.execution_context is None
            else (
                self.execution_context
                if isinstance(self.execution_context, OptimizationExecutionContext)
                else OptimizationExecutionContext.from_dict(self.execution_context)
            ),
        )
        object.__setattr__(
            self,
            "mutation_bounds",
            self.mutation_bounds if isinstance(self.mutation_bounds, MutationBounds) else MutationBounds.from_dict(self.mutation_bounds),
        )
        if int(self.max_proposals) <= 0:
            raise ValueError("max_proposals must be positive")
        object.__setattr__(self, "max_proposals", int(self.max_proposals))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        self.baseline_candidate.validate_against_target(self.target)
        if self.baseline_materialized_candidate.candidate_id != self.baseline_candidate.candidate_id:
            raise ValueError("baseline candidate and materialized candidate must share candidate_id")
        resolved_sample_id = self.active_sample_id or (self.evaluations[0].sample_id if self.evaluations else None)
        if resolved_sample_id is None:
            if len(self.dataset.samples) != 1:
                raise ValueError("active_sample_id must be provided when dataset contains multiple samples and no evaluations are present")
            resolved_sample_id = self.dataset.samples[0].sample_id
        sample = _find_sample(self.dataset, resolved_sample_id)
        if sample.target_id != self.target.target_id:
            raise ValueError("active sample target_id must match the optimization target")
        object.__setattr__(self, "active_sample_id", resolved_sample_id)
        if self.execution_context is None:
            object.__setattr__(
                self,
                "execution_context",
                OptimizationExecutionContext(
                    target_id=self.target.target_id,
                    sample_id=sample.sample_id,
                    runtime_context=sample.runtime_context(),
                    evaluation_input_compatibility=self.baseline_materialized_candidate.evaluation_input_compatibility,
                    metadata={
                        "dataset_id": self.dataset.dataset_id,
                        "dataset_version": self.dataset.dataset_version,
                        "from_sample": sample.sample_id,
                    },
                ),
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "target": self.target.to_dict(),
            "baseline_candidate": self.baseline_candidate.to_dict(),
            "baseline_materialized_candidate": self.baseline_materialized_candidate.to_dict(),
            "dataset": self.dataset.to_dict(),
            "evaluations": [item.to_dict() for item in self.evaluations],
            "active_sample_id": self.active_sample_id,
            "execution_context": None if self.execution_context is None else self.execution_context.to_dict(),
            "mutation_bounds": self.mutation_bounds.to_dict(),
            "max_proposals": self.max_proposals,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReflectiveParetoBackendRequest":
        return ReflectiveParetoBackendRequest(
            request_id=data.get("request_id") or "",
            target=OptimizationTarget.from_dict(data.get("target") or {}),
            baseline_candidate=CandidateBundle.from_dict(data.get("baseline_candidate") or {}),
            baseline_materialized_candidate=MaterializedCandidate.from_dict(data.get("baseline_materialized_candidate") or {}),
            dataset=OptimizationDataset.from_dict(data.get("dataset") or {}),
            evaluations=[EvaluationRecord.from_dict(item) for item in data.get("evaluations") or []],
            active_sample_id=data.get("active_sample_id"),
            execution_context=None
            if not data.get("execution_context")
            else OptimizationExecutionContext.from_dict(data.get("execution_context") or {}),
            mutation_bounds=MutationBounds.from_dict(data.get("mutation_bounds") or {}),
            max_proposals=int(data.get("max_proposals") or 3),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class StagePlanStep:
    stage_id: str
    stage_kind: str
    allowed_loci: List[str]
    primary_objective_channels: List[str]
    allowed_split_visibilities: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", _require_text(self.stage_id, "stage_id"))
        object.__setattr__(self, "stage_kind", _require_text(self.stage_kind, "stage_kind"))
        object.__setattr__(self, "allowed_loci", _copy_text_list(self.allowed_loci))
        object.__setattr__(self, "primary_objective_channels", _copy_text_list(self.primary_objective_channels))
        object.__setattr__(self, "allowed_split_visibilities", _copy_text_list(self.allowed_split_visibilities))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.allowed_loci:
            raise ValueError("allowed_loci must contain at least one locus id")
        if not self.primary_objective_channels:
            raise ValueError("primary_objective_channels must contain at least one channel id")
        if not self.allowed_split_visibilities:
            raise ValueError("allowed_split_visibilities must contain at least one visibility class")
        if "hidden_hold" in set(self.allowed_split_visibilities):
            raise ValueError("staged optimizer may not optimize directly against hidden_hold splits")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage_kind": self.stage_kind,
            "allowed_loci": list(self.allowed_loci),
            "primary_objective_channels": list(self.primary_objective_channels),
            "allowed_split_visibilities": list(self.allowed_split_visibilities),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "StagePlanStep":
        return StagePlanStep(
            stage_id=data.get("stage_id") or data.get("id") or "",
            stage_kind=data.get("stage_kind") or "",
            allowed_loci=list(data.get("allowed_loci") or []),
            primary_objective_channels=list(data.get("primary_objective_channels") or []),
            allowed_split_visibilities=list(data.get("allowed_split_visibilities") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PrivateSearchPolicyDecision:
    stage_id: str
    policy_id: str
    policy_kind: str
    selected_candidate_id: str
    selected_proposal_id: str
    model_tier: str
    escalation_considered: bool
    escalation_triggered: bool
    escalation_reason: Optional[str] = None
    blocked_components: List[str] = field(default_factory=list)
    uncertainty_penalties: Dict[str, float] = field(default_factory=dict)
    score_table: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", _require_text(self.stage_id, "stage_id"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        object.__setattr__(self, "policy_kind", _require_text(self.policy_kind, "policy_kind"))
        object.__setattr__(self, "selected_candidate_id", _require_text(self.selected_candidate_id, "selected_candidate_id"))
        object.__setattr__(self, "selected_proposal_id", _require_text(self.selected_proposal_id, "selected_proposal_id"))
        object.__setattr__(self, "model_tier", _require_text(self.model_tier, "model_tier"))
        object.__setattr__(self, "escalation_considered", bool(self.escalation_considered))
        object.__setattr__(self, "escalation_triggered", bool(self.escalation_triggered))
        object.__setattr__(self, "escalation_reason", str(self.escalation_reason).strip() if self.escalation_reason else None)
        object.__setattr__(self, "blocked_components", _copy_text_list(self.blocked_components))
        object.__setattr__(
            self,
            "uncertainty_penalties",
            {str(key): float(value) for key, value in (self.uncertainty_penalties or {}).items()},
        )
        object.__setattr__(
            self,
            "score_table",
            {str(key): float(value) for key, value in (self.score_table or {}).items()},
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "stage_id": self.stage_id,
            "policy_id": self.policy_id,
            "policy_kind": self.policy_kind,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_proposal_id": self.selected_proposal_id,
            "model_tier": self.model_tier,
            "escalation_considered": self.escalation_considered,
            "escalation_triggered": self.escalation_triggered,
            "blocked_components": list(self.blocked_components),
            "uncertainty_penalties": dict(self.uncertainty_penalties),
            "score_table": dict(self.score_table),
            "metadata": dict(self.metadata),
        }
        if self.escalation_reason:
            payload["escalation_reason"] = self.escalation_reason
        return payload


@dataclass(frozen=True)
class StagedOptimizerRequest:
    request_id: str
    backend_request: ReflectiveParetoBackendRequest
    evaluation_suite: EvaluationSuiteManifest
    objective_suite: ObjectiveSuiteManifest
    search_space: SearchSpaceManifest
    target_family: Optional[TargetFamilyManifest] = None
    family_composition: Optional[FamilyCompositionManifest] = None
    stage_strategy: str = "family_risk_split_v1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_text(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "backend_request",
            self.backend_request
            if isinstance(self.backend_request, ReflectiveParetoBackendRequest)
            else ReflectiveParetoBackendRequest.from_dict(self.backend_request),
        )
        object.__setattr__(
            self,
            "evaluation_suite",
            self.evaluation_suite
            if isinstance(self.evaluation_suite, EvaluationSuiteManifest)
            else EvaluationSuiteManifest.from_dict(self.evaluation_suite),
        )
        object.__setattr__(
            self,
            "objective_suite",
            self.objective_suite
            if isinstance(self.objective_suite, ObjectiveSuiteManifest)
            else ObjectiveSuiteManifest.from_dict(self.objective_suite),
        )
        object.__setattr__(
            self,
            "target_family",
            None
            if self.target_family is None
            else (
                self.target_family
                if isinstance(self.target_family, TargetFamilyManifest)
                else TargetFamilyManifest.from_dict(self.target_family)
            ),
        )
        object.__setattr__(
            self,
            "family_composition",
            None
            if self.family_composition is None
            else (
                self.family_composition
                if isinstance(self.family_composition, FamilyCompositionManifest)
                else FamilyCompositionManifest.from_dict(self.family_composition)
            ),
        )
        object.__setattr__(
            self,
            "search_space",
            self.search_space
            if isinstance(self.search_space, SearchSpaceManifest)
            else SearchSpaceManifest.from_dict(self.search_space),
        )
        object.__setattr__(self, "stage_strategy", _require_text(self.stage_strategy, "stage_strategy"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        target = self.backend_request.target
        family = self.target_family
        composition = self.family_composition
        search_space = self.search_space
        if family is None and composition is None:
            raise ValueError("either target_family or family_composition must be provided")
        if family is not None and composition is not None:
            raise ValueError("target_family and family_composition are mutually exclusive")
        target_loci = set(target.locus_ids())
        if family is not None:
            if target.target_id not in set(family.target_ids):
                raise ValueError("backend request target must belong to the declared target family")
            family_loci = set(family.mutable_loci_ids)
            if not family_loci.issubset(target_loci):
                raise ValueError("target family mutable loci must be declared on the optimization target")
            if search_space.family_id != family.family_id:
                raise ValueError("search space family_id must match the target family")
            if family.evaluation_suite_id != self.evaluation_suite.suite_id:
                raise ValueError("target family evaluation_suite_id must match the evaluation suite")
            if family.objective_suite_id != self.objective_suite.suite_id:
                raise ValueError("target family objective_suite_id must match the objective suite")
            if set(search_space.allowed_loci) != family_loci:
                raise ValueError("search space allowed_loci must match the target family mutable loci")
        if composition is not None:
            if search_space.composition_id != composition.composition_id:
                raise ValueError("search space composition_id must match the family composition")
            if composition.evaluation_suite_id != self.evaluation_suite.suite_id:
                raise ValueError("family composition evaluation_suite_id must match the evaluation suite")
            if composition.objective_suite_id != self.objective_suite.suite_id:
                raise ValueError("family composition objective_suite_id must match the objective suite")
            if composition.search_space_id != search_space.search_space_id:
                raise ValueError("family composition search_space_id must match the search space")
            if not set(search_space.allowed_loci).issubset(target_loci):
                raise ValueError("composition search space allowed_loci must be declared on the optimization target")
        if self.objective_suite.evaluation_suite_id != self.evaluation_suite.suite_id:
            raise ValueError("objective suite must bind to the declared evaluation suite")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "backend_request": self.backend_request.to_dict(),
            "evaluation_suite": self.evaluation_suite.to_dict(),
            "objective_suite": self.objective_suite.to_dict(),
            "search_space": self.search_space.to_dict(),
            "stage_strategy": self.stage_strategy,
            "metadata": dict(self.metadata),
        }
        if self.target_family is not None:
            payload["target_family"] = self.target_family.to_dict()
        if self.family_composition is not None:
            payload["family_composition"] = self.family_composition.to_dict()
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "StagedOptimizerRequest":
        return StagedOptimizerRequest(
            request_id=data.get("request_id") or "",
            backend_request=ReflectiveParetoBackendRequest.from_dict(data.get("backend_request") or {}),
            evaluation_suite=EvaluationSuiteManifest.from_dict(data.get("evaluation_suite") or {}),
            objective_suite=ObjectiveSuiteManifest.from_dict(data.get("objective_suite") or {}),
            target_family=None
            if not data.get("target_family")
            else TargetFamilyManifest.from_dict(data.get("target_family") or {}),
            family_composition=None
            if not data.get("family_composition")
            else FamilyCompositionManifest.from_dict(data.get("family_composition") or {}),
            search_space=SearchSpaceManifest.from_dict(data.get("search_space") or {}),
            stage_strategy=data.get("stage_strategy") or "family_risk_split_v1",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReflectiveParetoBackendResult:
    request_id: str
    backend_id: str
    execution_context: OptimizationExecutionContext
    reflection_decision: ReflectionDecision
    proposals: List[MutationProposal] = field(default_factory=list)
    portfolio: CandidatePortfolio = field(default_factory=CandidatePortfolio)
    compatibility_results: List[RuntimeCompatibilityResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _require_text(self.request_id, "request_id"))
        object.__setattr__(self, "backend_id", _require_text(self.backend_id, "backend_id"))
        object.__setattr__(
            self,
            "execution_context",
            self.execution_context
            if isinstance(self.execution_context, OptimizationExecutionContext)
            else OptimizationExecutionContext.from_dict(self.execution_context),
        )
        object.__setattr__(
            self,
            "reflection_decision",
            self.reflection_decision
            if isinstance(self.reflection_decision, ReflectionDecision)
            else ReflectionDecision.from_dict(self.reflection_decision),
        )
        object.__setattr__(
            self,
            "proposals",
            [item if isinstance(item, MutationProposal) else MutationProposal.from_dict(item) for item in self.proposals],
        )
        object.__setattr__(
            self,
            "portfolio",
            self.portfolio if isinstance(self.portfolio, CandidatePortfolio) else CandidatePortfolio.from_dict(self.portfolio),
        )
        object.__setattr__(
            self,
            "compatibility_results",
            [
                item if isinstance(item, RuntimeCompatibilityResult) else RuntimeCompatibilityResult.from_dict(item)
                for item in self.compatibility_results
            ],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "backend_id": self.backend_id,
            "execution_context": self.execution_context.to_dict(),
            "reflection_decision": self.reflection_decision.to_dict(),
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "portfolio": self.portfolio.to_dict(),
            "compatibility_results": [item.to_dict() for item in self.compatibility_results],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReflectiveParetoBackendResult":
        return ReflectiveParetoBackendResult(
            request_id=data.get("request_id") or "",
            backend_id=data.get("backend_id") or "",
            execution_context=OptimizationExecutionContext.from_dict(data.get("execution_context") or {}),
            reflection_decision=ReflectionDecision.from_dict(data.get("reflection_decision") or {}),
            proposals=[MutationProposal.from_dict(item) for item in data.get("proposals") or []],
            portfolio=CandidatePortfolio.from_dict(data.get("portfolio") or {}),
            compatibility_results=[RuntimeCompatibilityResult.from_dict(item) for item in data.get("compatibility_results") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReflectionPolicyInput:
    target: OptimizationTarget
    baseline_candidate: CandidateBundle
    baseline_materialized_candidate: MaterializedCandidate
    dataset: OptimizationDataset
    evaluations: List[EvaluationRecord]
    mutation_bounds: MutationBounds
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MutationPolicyInput:
    target: OptimizationTarget
    baseline_candidate: CandidateBundle
    baseline_materialized_candidate: MaterializedCandidate
    reflection_decision: ReflectionDecision
    mutation_bounds: MutationBounds
    metadata: Dict[str, Any] = field(default_factory=dict)


class WrongnessGuidedReflectionPolicy:
    policy_id = "wrongness_guided_reflection_v1"

    def __init__(self, *, min_confidence: float = 0.6) -> None:
        self.min_confidence = float(min_confidence)

    def reflect(self, policy_input: ReflectionPolicyInput) -> ReflectionDecision:
        findings: List[ReflectionFinding] = []
        recommended: List[str] = []
        target_loci = set(policy_input.target.locus_ids())
        relevant_evaluations = [
            evaluation
            for evaluation in policy_input.evaluations
            if evaluation.candidate_id == policy_input.baseline_candidate.candidate_id
        ]

        for evaluation in relevant_evaluations:
            for report in evaluation.wrongness_reports:
                blocked_reason: Optional[str] = None
                repair_locus = report.likely_repair_locus
                if not repair_locus:
                    blocked_reason = "wrongness report did not identify a likely repair locus"
                elif repair_locus not in target_loci:
                    blocked_reason = "repair locus is outside the declared optimization target"
                elif report.confidence < self.min_confidence:
                    blocked_reason = "wrongness confidence is too low for bounded mutation"
                else:
                    recommended.append(repair_locus)
                findings.append(
                    ReflectionFinding(
                        wrongness_id=report.wrongness_id,
                        wrongness_class=report.wrongness_class,
                        failure_locus=report.failure_locus,
                        suggested_repair_locus=repair_locus,
                        confidence=report.confidence,
                        blocked_reason=blocked_reason,
                    )
                )

        deduped_recommended = list(dict.fromkeys(recommended))
        if not findings:
            return ReflectionDecision(
                decision_id=f"decision.{policy_input.baseline_candidate.candidate_id}",
                target_candidate_id=policy_input.baseline_candidate.candidate_id,
                should_mutate=False,
                findings=[],
                declined_reason="no structured wrongness evidence was available",
                metadata={"policy_id": self.policy_id},
            )
        if not deduped_recommended:
            return ReflectionDecision(
                decision_id=f"decision.{policy_input.baseline_candidate.candidate_id}",
                target_candidate_id=policy_input.baseline_candidate.candidate_id,
                should_mutate=False,
                findings=findings,
                declined_reason="reflection could not identify a safe in-scope repair locus",
                metadata={"policy_id": self.policy_id},
            )
        if len(deduped_recommended) > policy_input.mutation_bounds.max_changed_loci:
            return ReflectionDecision(
                decision_id=f"decision.{policy_input.baseline_candidate.candidate_id}",
                target_candidate_id=policy_input.baseline_candidate.candidate_id,
                should_mutate=False,
                findings=findings,
                declined_reason="repair set exceeds the configured blast-radius limit",
                metadata={"policy_id": self.policy_id},
            )
        return ReflectionDecision(
            decision_id=f"decision.{policy_input.baseline_candidate.candidate_id}",
            target_candidate_id=policy_input.baseline_candidate.candidate_id,
            should_mutate=True,
            recommended_loci=deduped_recommended,
            findings=findings,
            metadata={"policy_id": self.policy_id},
        )


class TypedOverlayMutationPolicy:
    policy_id = "typed_overlay_mutation_v1"

    def propose(self, policy_input: MutationPolicyInput, *, max_proposals: int) -> List[MutationProposal]:
        if not policy_input.reflection_decision.should_mutate:
            return []
        recommended = list(policy_input.reflection_decision.recommended_loci)
        proposals: List[MutationProposal] = []
        primary_locus = recommended[0]
        variant_sets: List[List[str]] = [[primary_locus]]

        companion_locus = self._companion_guidance_locus(policy_input.target, primary_locus)
        if companion_locus and companion_locus not in variant_sets[0]:
            variant_sets.append([primary_locus, companion_locus])

        for index, loci in enumerate(variant_sets[:max_proposals], start=1):
            candidate = self._build_candidate(policy_input, loci, variant=index)
            blast_radius = self._blast_radius(policy_input.target, candidate)
            self._validate_candidate(policy_input.target, candidate, policy_input.mutation_bounds, blast_radius)
            proposals.append(
                MutationProposal(
                    proposal_id=f"proposal.{candidate.candidate_id}",
                    policy_id=self.policy_id,
                    candidate=candidate,
                    blast_radius=blast_radius,
                    rationale_summary="bounded overlay mutation derived from structured wrongness and repair loci",
                    metadata={"intended_repair_loci": list(loci)},
                )
            )
        return proposals[:max_proposals]

    def _companion_guidance_locus(self, target: OptimizationTarget, primary_locus: str) -> Optional[str]:
        if primary_locus == "prompt.section.optimization_guidance":
            return None
        for locus in target.mutable_loci:
            if locus.locus_id == "prompt.section.optimization_guidance":
                return locus.locus_id
        return None

    def _build_candidate(self, policy_input: MutationPolicyInput, loci: Sequence[str], *, variant: int) -> CandidateBundle:
        changes: List[CandidateChange] = []
        for locus_id in loci:
            locus = _find_locus(policy_input.target, locus_id)
            changes.append(
                CandidateChange(
                    locus_id=locus.locus_id,
                    value=self._render_overlay_value(locus, include_guidance_boost=variant > 1),
                    rationale=f"repair {locus.locus_id} using structured wrongness-guided overlay mutation",
                    metadata={
                        "policy_id": self.policy_id,
                        "locus_kind": locus.locus_kind,
                        "overlay_only": True,
                    },
                )
            )
        return CandidateBundle(
            candidate_id=f"{policy_input.baseline_candidate.candidate_id}.repair.{variant:02d}",
            source_target_id=policy_input.target.target_id,
            applied_loci=list(loci),
            changes=changes,
            provenance={
                "policy_id": self.policy_id,
                "reflection_decision_id": policy_input.reflection_decision.decision_id,
                "overlay_only": True,
                "intended_repair_loci": list(loci),
                "baseline_candidate_id": policy_input.baseline_candidate.candidate_id,
            },
            metadata={"variant": variant},
        )

    def _render_overlay_value(self, locus: MutableLocus, *, include_guidance_boost: bool) -> Dict[str, Any]:
        if locus.locus_kind == "tool_description":
            description = "Run shell commands within the declared support envelope and summarize results clearly."
            if include_guidance_boost:
                description = "Run shell commands and summarize results clearly without expanding supported tools, profiles, or environments."
            return {"description": description}
        if "prompt" in locus.locus_kind:
            text = "Prefer narrow overlays, preserve replay-safe behavior, and do not widen support claims."
            if include_guidance_boost:
                text = "Prefer narrow overlays, preserve replay-safe behavior, and state support boundaries explicitly when editing tool-facing wording."
            return {"text": text}
        return {
            "text": "Refine only this declared locus while preserving the current support envelope and invariants."
        }

    def _blast_radius(self, target: OptimizationTarget, candidate: CandidateBundle) -> Dict[str, Any]:
        return {
            "changed_loci_count": len(candidate.applied_loci),
            "changed_artifact_count": _artifact_count_for_candidate(target, candidate),
            "total_value_bytes": _payload_size_for_candidate(candidate),
        }

    def _validate_candidate(
        self,
        target: OptimizationTarget,
        candidate: CandidateBundle,
        bounds: MutationBounds,
        blast_radius: Mapping[str, Any],
    ) -> None:
        validated = validate_bounded_candidate(target, candidate, bounds)
        if dict(validated) != dict(blast_radius):
            raise ValueError("blast radius metadata must match computed bounded-candidate validation")


class ReflectiveParetoBackend:
    backend_id = "reflective_pareto_backend_v1"

    def __init__(
        self,
        *,
        reflection_policy: Optional[WrongnessGuidedReflectionPolicy] = None,
        mutation_policy: Optional[TypedOverlayMutationPolicy] = None,
    ) -> None:
        self.reflection_policy = reflection_policy or WrongnessGuidedReflectionPolicy()
        self.mutation_policy = mutation_policy or TypedOverlayMutationPolicy()

    def evaluate_runtime_compatibility(
        self,
        *,
        execution_context: OptimizationExecutionContext,
        support_envelope: SupportEnvelope,
        candidate_id: str,
    ) -> RuntimeCompatibilityResult:
        issues: List[RuntimeCompatibilityIssue] = []
        runtime_context = execution_context.runtime_context

        required_tools = set(runtime_context.required_tool_names())
        available_tools = set(support_envelope.tools)
        missing_tools = sorted(required_tools - available_tools)
        if missing_tools:
            issues.append(
                RuntimeCompatibilityIssue(
                    issue_id=f"compat.{candidate_id}.tools",
                    category="tool_pack",
                    message=f"candidate support envelope is missing required tools: {missing_tools}",
                    metadata={"missing_tools": missing_tools},
                )
            )

        expected_environments = {
            runtime_context.environment_selector.profile,
            runtime_context.environment_selector.environment_kind,
        }
        if not expected_environments & set(support_envelope.environments):
            issues.append(
                RuntimeCompatibilityIssue(
                    issue_id=f"compat.{candidate_id}.environment",
                    category="environment_selector",
                    message="candidate support envelope does not expose the required environment selector/profile",
                    metadata={"expected_environment": sorted(expected_environments)},
                )
            )

        sandbox = runtime_context.sandbox_context
        if sandbox is not None:
            if sandbox.filesystem_mode not in support_envelope.environments:
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.sandbox_filesystem",
                        category="sandbox",
                        message="candidate support envelope does not expose the required sandbox filesystem mode",
                        metadata={"filesystem_mode": sandbox.filesystem_mode},
                    )
                )
            if sandbox.network_access and not bool(support_envelope.assumptions.get("network_access")):
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.sandbox_network",
                        category="sandbox",
                        message="candidate support envelope does not allow required sandbox network access",
                        metadata={"network_access_required": True},
                    )
                )

        declared_services = dict(support_envelope.assumptions.get("service_selectors") or {})
        for service in runtime_context.service_contexts:
            if declared_services.get(service.service_id) != service.selector:
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.service.{service.service_id}",
                        category="service",
                        message="candidate support envelope does not satisfy the required service selector",
                        metadata={"service_id": service.service_id, "selector": service.selector},
                    )
                )
            if service.requires_network_access and not bool(support_envelope.assumptions.get("network_access")):
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.service_network.{service.service_id}",
                        category="service",
                        message="candidate support envelope does not provide required network access for a service selector",
                        metadata={"service_id": service.service_id},
                    )
                )

        mcp_context = runtime_context.mcp_context
        if mcp_context is not None and mcp_context.server_names:
            declared_mcp_servers = set(support_envelope.assumptions.get("mcp_servers") or [])
            missing_servers = sorted(set(mcp_context.server_names) - declared_mcp_servers)
            if missing_servers:
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.mcp",
                        category="mcp",
                        message=f"candidate support envelope does not expose required MCP servers: {missing_servers}",
                        metadata={"missing_servers": missing_servers},
                    )
                )
            if mcp_context.requires_network_access and not bool(support_envelope.assumptions.get("network_access")):
                issues.append(
                    RuntimeCompatibilityIssue(
                        issue_id=f"compat.{candidate_id}.mcp_network",
                        category="mcp",
                        message="candidate support envelope does not allow required MCP network access",
                        metadata={"requires_network_access": True},
                    )
                )

        status = "incompatible" if issues else "compatible"
        return RuntimeCompatibilityResult(
            context_id=execution_context.runtime_context.context_id,
            target_id=execution_context.target_id,
            candidate_id=candidate_id,
            status=status,
            issues=issues,
            metadata={"sample_id": execution_context.sample_id},
        )

    def run(self, request: ReflectiveParetoBackendRequest) -> ReflectiveParetoBackendResult:
        assert request.execution_context is not None
        baseline_compatibility = self.evaluate_runtime_compatibility(
            execution_context=request.execution_context,
            support_envelope=request.baseline_materialized_candidate.support_envelope,
            candidate_id=request.baseline_candidate.candidate_id,
        )
        compatibility_results: List[RuntimeCompatibilityResult] = [baseline_compatibility]
        if baseline_compatibility.status != "compatible":
            return ReflectiveParetoBackendResult(
                request_id=request.request_id,
                backend_id=self.backend_id,
                execution_context=request.execution_context,
                reflection_decision=ReflectionDecision(
                    decision_id=f"decision.{request.baseline_candidate.candidate_id}",
                    target_candidate_id=request.baseline_candidate.candidate_id,
                    should_mutate=False,
                    findings=[],
                    declined_reason="runtime context is incompatible with the current candidate support envelope",
                    metadata={"backend_id": self.backend_id},
                ),
                proposals=[],
                portfolio=CandidatePortfolio(
                    entries=[
                        PortfolioEntry(
                            candidate=request.baseline_candidate,
                            materialized_candidate=request.baseline_materialized_candidate,
                            objective_vector=self._observed_objective_vector(
                                request.baseline_candidate,
                                request.evaluations,
                                request.mutation_bounds,
                            ),
                            evidence_lineage=[evaluation.evaluation_id for evaluation in request.evaluations],
                            score_kind="observed",
                            metadata={
                                "role": "baseline",
                                "runtime_compatibility": baseline_compatibility.to_dict(),
                            },
                        )
                    ],
                    metadata={"backend_id": self.backend_id},
                ),
                compatibility_results=compatibility_results,
                metadata={"objective_directions": dict(OBJECTIVE_DIRECTIONS)},
            )
        reflection_decision = self.reflection_policy.reflect(
            ReflectionPolicyInput(
                target=request.target,
                baseline_candidate=request.baseline_candidate,
                baseline_materialized_candidate=request.baseline_materialized_candidate,
                dataset=request.dataset,
                evaluations=list(request.evaluations),
                mutation_bounds=request.mutation_bounds,
                metadata={"backend_id": self.backend_id},
            )
        )

        proposals = self.mutation_policy.propose(
            MutationPolicyInput(
                target=request.target,
                baseline_candidate=request.baseline_candidate,
                baseline_materialized_candidate=request.baseline_materialized_candidate,
                reflection_decision=reflection_decision,
                mutation_bounds=request.mutation_bounds,
                metadata={"backend_id": self.backend_id},
            ),
            max_proposals=request.max_proposals,
        )

        portfolio = CandidatePortfolio(metadata={"backend_id": self.backend_id})
        baseline_entry = PortfolioEntry(
            candidate=request.baseline_candidate,
            materialized_candidate=request.baseline_materialized_candidate,
            objective_vector=self._observed_objective_vector(request.baseline_candidate, request.evaluations, request.mutation_bounds),
            evidence_lineage=[evaluation.evaluation_id for evaluation in request.evaluations],
            score_kind="observed",
            metadata={"role": "baseline", "runtime_compatibility": baseline_compatibility.to_dict()},
        )
        portfolio = portfolio.retain_entry(baseline_entry)

        for proposal in proposals:
            materialized = self._materialize_proposal(request, proposal)
            _validate_builtin_invariants(request.target, proposal.candidate, materialized)
            proposal_compatibility = self.evaluate_runtime_compatibility(
                execution_context=request.execution_context,
                support_envelope=materialized.support_envelope,
                candidate_id=proposal.candidate.candidate_id,
            )
            compatibility_results.append(proposal_compatibility)
            if proposal_compatibility.status != "compatible":
                continue
            proposal_entry = PortfolioEntry(
                candidate=proposal.candidate,
                materialized_candidate=materialized,
                objective_vector=self._predicted_objective_vector(
                    baseline=baseline_entry.objective_vector,
                    proposal=proposal,
                    reflection_decision=reflection_decision,
                    mutation_bounds=request.mutation_bounds,
                ),
                evidence_lineage=[evaluation.evaluation_id for evaluation in request.evaluations],
                score_kind="predicted",
                metadata={
                    "proposal_id": proposal.proposal_id,
                    "runtime_compatibility": proposal_compatibility.to_dict(),
                },
            )
            portfolio = portfolio.retain_entry(proposal_entry)

        return ReflectiveParetoBackendResult(
            request_id=request.request_id,
            backend_id=self.backend_id,
            execution_context=request.execution_context,
            reflection_decision=reflection_decision,
            proposals=proposals,
            portfolio=portfolio,
            compatibility_results=compatibility_results,
            metadata={"objective_directions": dict(OBJECTIVE_DIRECTIONS)},
        )

    def _materialize_proposal(
        self,
        request: ReflectiveParetoBackendRequest,
        proposal: MutationProposal,
    ) -> MaterializedCandidate:
        effective_artifact = {
            "baseline_effective_artifact": request.baseline_materialized_candidate.effective_artifact,
            "overlay_by_locus": {change.locus_id: change.value for change in proposal.candidate.changes},
        }
        materialized = materialize_candidate(
            request.target,
            proposal.candidate,
            effective_artifact=effective_artifact,
            effective_tool_surface=request.baseline_materialized_candidate.effective_tool_surface,
            support_envelope=request.target.support_envelope,
            evaluation_input_compatibility=request.baseline_materialized_candidate.evaluation_input_compatibility,
            metadata={"proposal_id": proposal.proposal_id, "backend_id": self.backend_id},
        )
        validate_bounded_candidate(
            request.target,
            proposal.candidate,
            request.mutation_bounds,
            materialized=materialized,
        )
        return materialized

    def _observed_objective_vector(
        self,
        candidate: CandidateBundle,
        evaluations: Sequence[EvaluationRecord],
        bounds: MutationBounds,
    ) -> ObjectiveVector:
        relevant = [evaluation for evaluation in evaluations if evaluation.candidate_id == candidate.candidate_id]
        correctness_scores = [_outcome_score(evaluation.aggregate_outcome()) for evaluation in relevant]
        correctness = sum(correctness_scores) / len(correctness_scores) if correctness_scores else 0.0
        return ObjectiveVector(
            correctness_score=correctness,
            wrongness_penalty=_wrongness_penalty(relevant),
            mutation_cost=min(
                1.0,
                (len(candidate.applied_loci) / float(bounds.max_changed_loci)) * 0.6
                + _normalized_payload_cost(candidate, bounds) * 0.4,
            ),
            instability_penalty=_instability_penalty(relevant),
            metadata={"score_kind": "observed"},
        )

    def _predicted_objective_vector(
        self,
        *,
        baseline: ObjectiveVector,
        proposal: MutationProposal,
        reflection_decision: ReflectionDecision,
        mutation_bounds: MutationBounds,
    ) -> ObjectiveVector:
        recommended = set(reflection_decision.recommended_loci)
        proposal_loci = set(proposal.candidate.applied_loci)
        coverage = len(proposal_loci & recommended) / float(len(recommended) or 1)
        guidance_bonus = max(0.0, len(proposal_loci) - 1) * 0.08
        correctness = min(1.0, baseline.correctness_score + 0.45 * coverage + guidance_bonus)
        wrongness = max(0.0, baseline.wrongness_penalty - 0.45 * coverage - guidance_bonus)
        mutation_cost = min(
            1.0,
            (len(proposal.candidate.applied_loci) / float(mutation_bounds.max_changed_loci)) * 0.6
            + _normalized_payload_cost(proposal.candidate, mutation_bounds) * 0.4,
        )
        instability = max(0.0, baseline.instability_penalty - 0.05 * coverage + 0.05 * max(0, len(proposal_loci) - 1))
        return ObjectiveVector(
            correctness_score=correctness,
            wrongness_penalty=wrongness,
            mutation_cost=mutation_cost,
            instability_penalty=instability,
            metadata={"score_kind": "predicted", "coverage": coverage},
        )


def run_reflective_pareto_backend(request: ReflectiveParetoBackendRequest) -> ReflectiveParetoBackendResult:
    backend = ReflectiveParetoBackend()
    return backend.run(request)


class SingleLocusGreedyBackend(ReflectiveParetoBackend):
    """A narrower backend family that only keeps the first proposed repair."""

    backend_id = "single_locus_greedy.v1"

    def __init__(
        self,
        *,
        reflection_policy: Optional[WrongnessGuidedReflectionPolicy] = None,
        mutation_policy: Optional[TypedOverlayMutationPolicy] = None,
    ) -> None:
        super().__init__(
            reflection_policy=reflection_policy,
            mutation_policy=mutation_policy,
        )

    def run(self, request: ReflectiveParetoBackendRequest) -> ReflectiveParetoBackendResult:
        limited_request = replace(
            request,
            max_proposals=1,
            metadata={**request.metadata, "backend_family": self.backend_id, "proposal_limit": 1},
        )
        result = super().run(limited_request)
        return replace(
            result,
            metadata={**result.metadata, "backend_family": self.backend_id, "proposal_limit": 1},
        )


def run_single_locus_greedy_backend(request: ReflectiveParetoBackendRequest) -> ReflectiveParetoBackendResult:
    backend = SingleLocusGreedyBackend()
    return backend.run(request)


class StagedOptimizer(ReflectiveParetoBackend):
    """A backend-only staged optimizer over declared families and search spaces."""

    backend_id = "staged_optimizer.v1"

    def _private_transfer_slice_status(
        self,
        request: StagedOptimizerRequest,
    ) -> Dict[str, Dict[str, Any]]:
        raw_status = request.metadata.get("transfer_slice_status") or {}
        if not isinstance(raw_status, Mapping):
            return {}
        return {
            str(slice_id): dict(payload)
            for slice_id, payload in raw_status.items()
            if isinstance(payload, Mapping)
        }

    def _private_transfer_slice_penalties(
        self,
        request: StagedOptimizerRequest,
    ) -> Dict[str, float]:
        penalties: Dict[str, float] = {}
        for slice_id, payload in self._private_transfer_slice_status(request).items():
            status = str(payload.get("status") or "").strip().lower()
            if status == "blocked":
                penalties[f"slice_blocked:{slice_id}"] = 0.22
            elif status == "inconclusive":
                penalties[f"slice_inconclusive:{slice_id}"] = 0.11
            elif status == "missing":
                penalties[f"slice_missing:{slice_id}"] = 0.14
        model_tier_audit = request.metadata.get("model_tier_audit") or {}
        if isinstance(model_tier_audit, Mapping) and bool(model_tier_audit.get("triggered")):
            penalties["model_tier_audit_active"] = 0.03
        return penalties

    def _private_transfer_cohort_status(
        self,
        request: StagedOptimizerRequest,
    ) -> Dict[str, Dict[str, Any]]:
        raw_status = request.metadata.get("transfer_cohort_status") or {}
        if not isinstance(raw_status, Mapping):
            return {}
        return {
            str(cohort_id): dict(payload)
            for cohort_id, payload in raw_status.items()
            if isinstance(payload, Mapping)
        }

    def _private_transfer_cohort_penalties(
        self,
        request: StagedOptimizerRequest,
    ) -> Dict[str, float]:
        penalties: Dict[str, float] = {}
        for cohort_id, payload in self._private_transfer_cohort_status(request).items():
            status = str(payload.get("status") or "").strip().lower()
            if status == "blocked":
                penalties[f"cohort_blocked:{cohort_id}"] = 0.25
            elif status == "inconclusive":
                penalties[f"cohort_inconclusive:{cohort_id}"] = 0.14
            elif status in {"referenced", "missing"}:
                penalties[f"cohort_unproven:{cohort_id}"] = 0.1
        cohort_rollup = request.metadata.get("cohort_rollup") or {}
        if isinstance(cohort_rollup, Mapping) and bool(cohort_rollup.get("mini_audit_triggered")):
            penalties["cohort_mini_audit_active"] = 0.02
        return penalties

    def _private_live_cell_penalties(
        self,
        request: StagedOptimizerRequest,
    ) -> Dict[str, float]:
        penalties: Dict[str, float] = {}
        if not bool(request.metadata.get("live_cell_context")):
            return penalties
        if bool(request.metadata.get("blocked_semantic_channels_dominant")):
            penalties["blocked_semantic_channels_dominate"] = 0.18
        if bool(request.metadata.get("credible_pattern_absent")):
            penalties["no_credible_nano_pattern"] = 0.12
        if bool(request.metadata.get("mini_audit_no_status_change")):
            penalties["mini_audit_no_status_change"] = 0.08
        return penalties

    def _private_candidate_bonus(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
        entry: "PortfolioEntry",
        *,
        escalation_triggered: bool,
    ) -> float:
        if not escalation_triggered:
            return 0.0
        if request.family_composition is None:
            return 0.0
        if entry.metadata.get("role") == "baseline":
            return 0.0
        applied_count = len(entry.candidate.applied_loci)
        if applied_count <= 1:
            return 0.0
        stage_plan = self.build_stage_plan(request)
        is_final_stage = bool(stage_plan) and stage.stage_id == stage_plan[-1].stage_id
        if not is_final_stage:
            return 0.0
        return 0.04

    def _private_uncertainty_penalties(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
    ) -> Dict[str, float]:
        penalties: Dict[str, float] = {}
        uncertainty_policy = dict(request.objective_suite.uncertainty_policy)
        if uncertainty_policy.get("blocked_when_missing_hidden_hold"):
            penalties["hidden_hold_deferred"] = 0.15
        if uncertainty_policy.get("blocked_when_missing_regression_bucket"):
            penalties["regression_deferred"] = 0.1
        if request.evaluation_suite.stochasticity_class != "deterministic":
            penalties["stochasticity_penalty"] = 0.1
        review_class = (
            request.target_family.review_class
            if request.target_family is not None
            else request.family_composition.review_class
        )
        if review_class in {"support_honesty", "support_sensitive_coding_overlay"}:
            penalties["review_sensitive_penalty"] = 0.12
        if len(stage.allowed_loci) > 1:
            penalties["multi_locus_penalty"] = 0.05
        penalties.update(self._private_transfer_slice_penalties(request))
        penalties.update(self._private_transfer_cohort_penalties(request))
        penalties.update(self._private_live_cell_penalties(request))
        return penalties

    def _private_blocked_components(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
    ) -> List[str]:
        blocked: List[str] = []
        if request.objective_suite.uncertainty_policy.get("blocked_when_missing_hidden_hold"):
            blocked.append("hidden_hold_deferred")
        if request.objective_suite.uncertainty_policy.get("blocked_when_missing_regression_bucket"):
            blocked.append("regression_deferred")
        if request.family_composition is not None and request.search_space.cross_family_constraints:
            blocked.append("cross_family_constraints_active")
        review_class = (
            request.target_family.review_class
            if request.target_family is not None
            else request.family_composition.review_class
        )
        if review_class in {"support_honesty", "support_sensitive_coding_overlay"}:
            blocked.append("review_required")
        if stage.allowed_split_visibilities == ["comparison_visible"]:
            blocked.append("mutation_visibility_closed")
        for slice_id, payload in self._private_transfer_slice_status(request).items():
            status = str(payload.get("status") or "").strip().lower()
            if status in {"blocked", "inconclusive", "missing"}:
                blocked.append(f"transfer_slice:{slice_id}:{status}")
        for cohort_id, payload in self._private_transfer_cohort_status(request).items():
            status = str(payload.get("status") or "").strip().lower()
            if status in {"blocked", "inconclusive", "referenced", "missing"}:
                blocked.append(f"transfer_cohort:{cohort_id}:{status}")
        if bool(request.metadata.get("optimistic_scope_blocked")):
            blocked.append("optimistic_scope_blocked")
        if bool(request.metadata.get("blocked_semantic_channels_dominant")):
            blocked.append("blocked_semantic_channels_dominate")
        if bool(request.metadata.get("credible_pattern_absent")):
            blocked.append("no_credible_nano_pattern")
        if bool(request.metadata.get("mini_audit_no_status_change")):
            blocked.append("mini_audit_no_status_change")
        return blocked

    def _private_early_stop_state(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
        *,
        escalation_considered: bool,
        escalation_triggered: bool,
    ) -> tuple[bool, Optional[str]]:
        stage_plan = self.build_stage_plan(request)
        is_final_stage = bool(stage_plan) and stage.stage_id == stage_plan[-1].stage_id
        cohort_status = self._private_transfer_cohort_status(request)
        unsupported_statuses = {"blocked", "inconclusive", "referenced", "missing"}
        if cohort_status and all(
            str(payload.get("status") or "").strip().lower() in unsupported_statuses
            for payload in cohort_status.values()
        ):
            return True, "unsupported_transfer_cohort_status"
        stopping_policy = str(request.evaluation_suite.metadata.get("stopping_policy") or "").strip().lower()
        if (
            is_final_stage
            and stopping_policy == "stop_if_mini_audit_does_not_change_transfer_status"
            and escalation_considered
            and not escalation_triggered
        ):
            return True, "mini_audit_not_needed"
        if bool(request.metadata.get("live_cell_context")):
            observed_pairs = int(request.metadata.get("nano_pairs_observed") or 0)
            planned_pairs = int(request.metadata.get("max_nano_pairs") or 0)
            if planned_pairs > 0 and observed_pairs >= planned_pairs and bool(request.metadata.get("credible_pattern_absent")):
                return True, "no_credible_nano_pattern"
            if bool(request.metadata.get("blocked_semantic_channels_dominant")):
                return True, "blocked_semantic_channels_dominate"
            if bool(request.metadata.get("mini_audit_no_status_change")):
                return True, "mini_audit_no_status_change"
        return False, None

    def _private_model_tier_for_stage(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
    ) -> tuple[str, bool, bool, Optional[str]]:
        rerun_policy = dict(request.evaluation_suite.rerun_policy)
        default_model = str(rerun_policy.get("default_model") or rerun_policy.get("model_policy") or "gpt-5.4-nano")
        escalation_model = str(rerun_policy.get("escalation_model") or "").strip()
        considered = bool(escalation_model)
        triggered = False
        reason: Optional[str] = None
        signal = str(request.metadata.get("search_policy_signal") or "").strip()
        stage_plan = self.build_stage_plan(request)
        is_final_stage = bool(stage_plan) and stage.stage_id == stage_plan[-1].stage_id
        if considered and is_final_stage and signal in {"ambiguous_hidden_hold", "close_margin"}:
            triggered = True
            reason = signal
        elif considered:
            reason = "not_triggered"
        model_tier = escalation_model if triggered and escalation_model else default_model
        return model_tier, considered, triggered, reason

    def _policy_score_for_entry(
        self,
        entry: "PortfolioEntry",
        penalties: Mapping[str, float],
        candidate_bonus: float = 0.0,
    ) -> float:
        vector = entry.objective_vector
        score = float(vector.correctness_score)
        score -= float(vector.wrongness_penalty)
        score -= float(vector.mutation_cost)
        score -= float(vector.instability_penalty)
        score -= sum(float(value) for value in penalties.values())
        score += float(candidate_bonus)
        return score

    def _select_stage_winner(
        self,
        request: StagedOptimizerRequest,
        stage: StagePlanStep,
        compatible_entries: Sequence["PortfolioEntry"],
        current_baseline_entry: "PortfolioEntry",
    ) -> Optional[PrivateSearchPolicyDecision]:
        penalties = self._private_uncertainty_penalties(request, stage)
        blocked_components = self._private_blocked_components(request, stage)
        model_tier, considered, triggered, reason = self._private_model_tier_for_stage(request, stage)
        early_stop, early_stop_reason = self._private_early_stop_state(
            request,
            stage,
            escalation_considered=considered,
            escalation_triggered=triggered,
        )
        def _entry_score(entry: "PortfolioEntry", *, triggered_now: bool) -> float:
            return self._policy_score_for_entry(
                entry,
                penalties,
                candidate_bonus=self._private_candidate_bonus(
                    request,
                    stage,
                    entry,
                    escalation_triggered=triggered_now,
                ),
            )

        if not compatible_entries:
            return PrivateSearchPolicyDecision(
                stage_id=stage.stage_id,
                policy_id=f"search_policy.{stage.stage_id}",
                policy_kind="bounded_composed_scalarization_v1",
                selected_candidate_id=current_baseline_entry.candidate.candidate_id,
                selected_proposal_id=current_baseline_entry.candidate.candidate_id,
                model_tier=model_tier,
                escalation_considered=considered,
                escalation_triggered=triggered,
                escalation_reason=reason,
                blocked_components=blocked_components + ["no_compatible_proposals"],
                uncertainty_penalties=penalties,
                score_table={current_baseline_entry.candidate.candidate_id: _entry_score(current_baseline_entry, triggered_now=triggered)},
                metadata={
                    "search_space_id": request.search_space.search_space_id,
                    "evaluation_suite_id": request.evaluation_suite.suite_id,
                    "objective_suite_id": request.objective_suite.suite_id,
                    "fallback_to_baseline": True,
                    "transfer_slice_status": self._private_transfer_slice_status(request),
                    "slice_penalties": self._private_transfer_slice_penalties(request),
                    "transfer_cohort_status": self._private_transfer_cohort_status(request),
                    "cohort_penalties": self._private_transfer_cohort_penalties(request),
                    "unfair_mixed_tier_backend_comparison_forbidden": bool(
                        request.evaluation_suite.rerun_policy.get("mixed_tier_backend_comparison_forbidden")
                    ),
                    "escalation_changed_order": False,
                    "early_stop": early_stop,
                    "early_stop_reason": early_stop_reason,
                },
            )
        pre_escalation_best = max(compatible_entries, key=lambda entry: _entry_score(entry, triggered_now=False))
        best_entry = max(compatible_entries, key=lambda entry: _entry_score(entry, triggered_now=triggered))
        best_score = _entry_score(best_entry, triggered_now=triggered)
        proposal_id = str(best_entry.metadata.get("proposal_id") or best_entry.candidate.candidate_id)
        return PrivateSearchPolicyDecision(
            stage_id=stage.stage_id,
            policy_id=f"search_policy.{stage.stage_id}",
            policy_kind="bounded_composed_scalarization_v1",
            selected_candidate_id=best_entry.candidate.candidate_id,
            selected_proposal_id=proposal_id,
            model_tier=model_tier,
            escalation_considered=considered,
            escalation_triggered=triggered,
            escalation_reason=reason,
            blocked_components=blocked_components,
            uncertainty_penalties=penalties,
            score_table={
                entry.candidate.candidate_id: _entry_score(entry, triggered_now=triggered)
                for entry in compatible_entries
            },
            metadata={
                "search_space_id": request.search_space.search_space_id,
                "evaluation_suite_id": request.evaluation_suite.suite_id,
                "objective_suite_id": request.objective_suite.suite_id,
                "transfer_slice_status": self._private_transfer_slice_status(request),
                "slice_penalties": self._private_transfer_slice_penalties(request),
                "transfer_cohort_status": self._private_transfer_cohort_status(request),
                "cohort_penalties": self._private_transfer_cohort_penalties(request),
                "unfair_mixed_tier_backend_comparison_forbidden": bool(
                    request.evaluation_suite.rerun_policy.get("mixed_tier_backend_comparison_forbidden")
                ),
                "escalation_changed_order": (
                    triggered and pre_escalation_best.candidate.candidate_id != best_entry.candidate.candidate_id
                ),
                "selected_score": best_score,
                "early_stop": early_stop,
                "early_stop_reason": early_stop_reason,
            },
        )

    def build_stage_plan(self, request: StagedOptimizerRequest) -> List[StagePlanStep]:
        family = request.target_family
        composition = request.family_composition
        objective_suite = request.objective_suite
        split_visibility = request.evaluation_suite.split_visibility
        scope_id = family.family_id if family is not None else composition.composition_id
        review_class = family.review_class if family is not None else composition.review_class
        if request.search_space.stage_partitions:
            stage_plan: List[StagePlanStep] = []
            allowed_visibilities = ["mutation_visible", "comparison_visible"]
            for index, (partition_id, loci) in enumerate(request.search_space.stage_partitions.items(), start=1):
                primary_channels = list(objective_suite.frontier_dimensions[: max(1, min(2, len(objective_suite.frontier_dimensions)))])
                stage_plan.append(
                    StagePlanStep(
                        stage_id=f"stage.{scope_id}.{index:02d}",
                        stage_kind=partition_id,
                        allowed_loci=list(loci),
                        primary_objective_channels=primary_channels,
                        allowed_split_visibilities=allowed_visibilities if index == 1 else ["comparison_visible"],
                        metadata={
                            "scope_id": scope_id,
                            "review_class": review_class,
                            "composition_id": None if composition is None else composition.composition_id,
                            "member_family_ids": [] if composition is None else list(composition.member_family_ids),
                        },
                    )
                )
            return stage_plan
        assert family is not None
        primary_locus = family.mutable_loci_ids[0]
        primary_channel = objective_suite.frontier_dimensions[0]
        comparison_visible_splits = [
            split_name
            for split_name, visibility in split_visibility.items()
            if visibility == "comparison_visible"
        ]
        stage_one = StagePlanStep(
            stage_id=f"stage.{scope_id}.01",
            stage_kind="seed_primary_locus",
            allowed_loci=[primary_locus],
            primary_objective_channels=[primary_channel],
            allowed_split_visibilities=["mutation_visible", "comparison_visible"],
            metadata={
                "family_id": scope_id,
                "review_class": review_class,
                "split_names": ["train"] + comparison_visible_splits,
            },
        )
        stage_two = StagePlanStep(
            stage_id=f"stage.{scope_id}.02",
            stage_kind="family_bounded_expansion",
            allowed_loci=list(family.mutable_loci_ids),
            primary_objective_channels=list(objective_suite.frontier_dimensions[: max(2, len(objective_suite.frontier_dimensions))]),
            allowed_split_visibilities=["comparison_visible"],
            metadata={
                "family_id": scope_id,
                "review_class": review_class,
                "split_names": comparison_visible_splits,
            },
        )
        return [stage_one, stage_two]

    def run(self, request: StagedOptimizerRequest) -> ReflectiveParetoBackendResult:
        base_request = request.backend_request
        stage_plan = self.build_stage_plan(request)
        assert base_request.execution_context is not None
        scope_id = (
            request.target_family.family_id
            if request.target_family is not None
            else request.family_composition.composition_id
        )
        review_class = (
            request.target_family.review_class
            if request.target_family is not None
            else request.family_composition.review_class
        )
        search_scope_metadata = {
            "scope_id": scope_id,
            "family_id": None if request.target_family is None else request.target_family.family_id,
            "composition_id": None if request.family_composition is None else request.family_composition.composition_id,
        }

        baseline_compatibility = self.evaluate_runtime_compatibility(
            execution_context=base_request.execution_context,
            support_envelope=base_request.baseline_materialized_candidate.support_envelope,
            candidate_id=base_request.baseline_candidate.candidate_id,
        )
        compatibility_results: List[RuntimeCompatibilityResult] = [baseline_compatibility]
        if baseline_compatibility.status != "compatible":
            return ReflectiveParetoBackendResult(
                request_id=request.request_id,
                backend_id=self.backend_id,
                execution_context=base_request.execution_context,
                reflection_decision=ReflectionDecision(
                    decision_id=f"decision.{base_request.baseline_candidate.candidate_id}",
                    target_candidate_id=base_request.baseline_candidate.candidate_id,
                    should_mutate=False,
                    findings=[],
                    declined_reason="runtime context is incompatible with the current candidate support envelope",
                    metadata={"backend_id": self.backend_id, "stage_strategy": request.stage_strategy},
                ),
                proposals=[],
                portfolio=CandidatePortfolio(
                    entries=[
                        PortfolioEntry(
                            candidate=base_request.baseline_candidate,
                            materialized_candidate=base_request.baseline_materialized_candidate,
                            objective_vector=self._observed_objective_vector(
                                base_request.baseline_candidate,
                                base_request.evaluations,
                                base_request.mutation_bounds,
                            ),
                            evidence_lineage=[evaluation.evaluation_id for evaluation in base_request.evaluations],
                            score_kind="observed",
                            metadata={
                                "role": "baseline",
                                "runtime_compatibility": baseline_compatibility.to_dict(),
                                **search_scope_metadata,
                            },
                        )
                    ],
                    metadata={"backend_id": self.backend_id, **search_scope_metadata},
                ),
                compatibility_results=compatibility_results,
                metadata={
                    "objective_directions": dict(OBJECTIVE_DIRECTIONS),
                    "stage_plan": [item.to_dict() for item in stage_plan],
                    "stage_strategy": request.stage_strategy,
                    "search_policy_trace": [],
                    "final_selected_candidate_id": base_request.baseline_candidate.candidate_id,
                    **search_scope_metadata,
                    "search_space_id": request.search_space.search_space_id,
                    "evaluation_suite_id": request.evaluation_suite.suite_id,
                    "objective_suite_id": request.objective_suite.suite_id,
                    "review_class": review_class,
                },
            )

        reflection_decision = self.reflection_policy.reflect(
            ReflectionPolicyInput(
                target=base_request.target,
                baseline_candidate=base_request.baseline_candidate,
                baseline_materialized_candidate=base_request.baseline_materialized_candidate,
                dataset=base_request.dataset,
                evaluations=list(base_request.evaluations),
                mutation_bounds=base_request.mutation_bounds,
                metadata={
                    "backend_id": self.backend_id,
                    **search_scope_metadata,
                    "review_class": review_class,
                },
            )
        )

        portfolio = CandidatePortfolio(
            metadata={
                "backend_id": self.backend_id,
                **search_scope_metadata,
                "objective_suite_id": request.objective_suite.suite_id,
            }
        )
        baseline_entry = PortfolioEntry(
            candidate=base_request.baseline_candidate,
            materialized_candidate=base_request.baseline_materialized_candidate,
            objective_vector=self._observed_objective_vector(
                base_request.baseline_candidate,
                base_request.evaluations,
                base_request.mutation_bounds,
            ),
            evidence_lineage=[evaluation.evaluation_id for evaluation in base_request.evaluations],
            score_kind="observed",
            metadata={
                "role": "baseline",
                "runtime_compatibility": baseline_compatibility.to_dict(),
                **search_scope_metadata,
                "search_space_id": request.search_space.search_space_id,
            },
        )
        portfolio = portfolio.retain_entry(baseline_entry)

        proposals: List[MutationProposal] = []
        search_policy_trace: List[PrivateSearchPolicyDecision] = []
        current_request = base_request
        current_baseline_entry = baseline_entry
        for stage in stage_plan:
            stage_request = replace(
                current_request,
                max_proposals=min(current_request.max_proposals, max(1, len(stage.allowed_loci))),
                metadata={
                    **current_request.metadata,
                    "backend_family": self.backend_id,
                    "family_id": None if request.target_family is None else request.target_family.family_id,
                    "composition_id": None if request.family_composition is None else request.family_composition.composition_id,
                    "stage_id": stage.stage_id,
                    "primary_objective_channels": list(stage.primary_objective_channels),
                },
            )
            stage_proposals = self.mutation_policy.propose(
                MutationPolicyInput(
                    target=stage_request.target,
                    baseline_candidate=stage_request.baseline_candidate,
                    baseline_materialized_candidate=stage_request.baseline_materialized_candidate,
                    reflection_decision=reflection_decision,
                    mutation_bounds=stage_request.mutation_bounds,
                    metadata={"backend_id": self.backend_id, "stage_id": stage.stage_id},
                ),
                max_proposals=stage_request.max_proposals,
            )

            compatible_entries: List[PortfolioEntry] = []
            for proposal in stage_proposals:
                proposal_loci = set(proposal.candidate.applied_loci)
                if not proposal_loci.issubset(set(stage.allowed_loci)):
                    continue
                _validate_candidate_against_search_space(proposal.candidate, request.search_space)
                proposal = replace(
                    proposal,
                    metadata={
                        **proposal.metadata,
                        "stage_id": stage.stage_id,
                        "stage_kind": stage.stage_kind,
                        **search_scope_metadata,
                        "search_space_id": request.search_space.search_space_id,
                        "evaluation_suite_id": request.evaluation_suite.suite_id,
                        "objective_suite_id": request.objective_suite.suite_id,
                        "primary_objective_channels": list(stage.primary_objective_channels),
                    },
                )
                materialized = self._materialize_proposal(stage_request, proposal)
                proposal_compatibility = self.evaluate_runtime_compatibility(
                    execution_context=stage_request.execution_context,
                    support_envelope=materialized.support_envelope,
                    candidate_id=proposal.candidate.candidate_id,
                )
                compatibility_results.append(proposal_compatibility)
                proposals.append(proposal)
                if proposal_compatibility.status != "compatible":
                    continue
                proposal_entry = PortfolioEntry(
                    candidate=proposal.candidate,
                    materialized_candidate=materialized,
                    objective_vector=self._predicted_objective_vector(
                        baseline=current_baseline_entry.objective_vector,
                        proposal=proposal,
                        reflection_decision=reflection_decision,
                        mutation_bounds=stage_request.mutation_bounds,
                    ),
                    evidence_lineage=[evaluation.evaluation_id for evaluation in stage_request.evaluations],
                    score_kind="predicted",
                    metadata={
                        "proposal_id": proposal.proposal_id,
                        "runtime_compatibility": proposal_compatibility.to_dict(),
                        "stage_id": stage.stage_id,
                        **search_scope_metadata,
                        "search_space_id": request.search_space.search_space_id,
                        "evaluation_suite_id": request.evaluation_suite.suite_id,
                        "objective_suite_id": request.objective_suite.suite_id,
                    },
                )
                portfolio = portfolio.retain_entry(proposal_entry)
                compatible_entries.append(proposal_entry)

            decision = self._select_stage_winner(request, stage, compatible_entries, current_baseline_entry)
            if decision is None:
                continue
            search_policy_trace.append(decision)
            if compatible_entries:
                selected_entry = next(
                    entry for entry in compatible_entries if entry.candidate.candidate_id == decision.selected_candidate_id
                )
            else:
                selected_entry = current_baseline_entry
            current_baseline_entry = selected_entry
            current_request = replace(
                current_request,
                baseline_candidate=selected_entry.candidate,
                baseline_materialized_candidate=selected_entry.materialized_candidate,
                metadata={
                    **current_request.metadata,
                    "selected_stage_candidate_id": selected_entry.candidate.candidate_id,
                    "selected_stage_model_tier": decision.model_tier,
                    "search_policy_trace_count": len(search_policy_trace),
                },
            )
            if bool(decision.metadata.get("early_stop")):
                break

        return ReflectiveParetoBackendResult(
            request_id=request.request_id,
            backend_id=self.backend_id,
            execution_context=base_request.execution_context,
            reflection_decision=reflection_decision,
            proposals=proposals,
            portfolio=portfolio,
            compatibility_results=compatibility_results,
            metadata={
                "objective_directions": dict(OBJECTIVE_DIRECTIONS),
                "backend_family": self.backend_id,
                "stage_plan": [item.to_dict() for item in stage_plan],
                "stage_strategy": request.stage_strategy,
                "search_policy_trace": [item.to_dict() for item in search_policy_trace],
                "final_selected_candidate_id": current_baseline_entry.candidate.candidate_id,
                "early_stopped": bool(search_policy_trace and search_policy_trace[-1].metadata.get("early_stop")),
                "early_stop_reason": (
                    search_policy_trace[-1].metadata.get("early_stop_reason")
                    if search_policy_trace and search_policy_trace[-1].metadata.get("early_stop")
                    else None
                ),
                **search_scope_metadata,
                "search_space_id": request.search_space.search_space_id,
                "evaluation_suite_id": request.evaluation_suite.suite_id,
                "objective_suite_id": request.objective_suite.suite_id,
                "review_class": review_class,
            },
        )


def run_staged_optimizer(request: StagedOptimizerRequest) -> ReflectiveParetoBackendResult:
    backend = StagedOptimizer()
    return backend.run(request)
