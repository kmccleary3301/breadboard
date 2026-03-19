from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .suites import TransferSliceManifest
from .substrate import ArtifactRef


ALLOWED_SPLIT_VISIBILITY = {"mutation_visible", "comparison_visible", "hidden_hold"}
ALLOWED_STOCHASTICITY_CLASSES = {"deterministic", "seeded_stochastic", "environment_volatile"}
ALLOWED_COMPARISON_OUTCOMES = {
    "win",
    "loss",
    "tie",
    "non_inferior",
    "inconclusive",
    "blocked",
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


@dataclass(frozen=True)
class BenchmarkSplit:
    split_name: str
    sample_ids: List[str]
    visibility: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "split_name", _require_text(self.split_name, "split_name"))
        object.__setattr__(self, "sample_ids", _copy_text_list(self.sample_ids))
        visibility = _require_text(self.visibility, "visibility").lower()
        if visibility not in ALLOWED_SPLIT_VISIBILITY:
            raise ValueError(f"visibility must be one of: {sorted(ALLOWED_SPLIT_VISIBILITY)}")
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.sample_ids:
            raise ValueError("sample_ids must contain at least one sample id")
        if len(self.sample_ids) != len(set(self.sample_ids)):
            raise ValueError("sample_ids contains duplicates")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "split_name": self.split_name,
            "sample_ids": list(self.sample_ids),
            "visibility": self.visibility,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BenchmarkSplit":
        return BenchmarkSplit(
            split_name=data.get("split_name") or data.get("name") or "",
            sample_ids=list(data.get("sample_ids") or []),
            visibility=data.get("visibility") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BenchmarkRunManifest:
    manifest_id: str
    benchmark_kind: str
    target_id: str
    dataset_id: str
    dataset_version: str
    baseline_candidate_id: str
    environment_domain: str
    evaluator_stack: List[str]
    comparison_protocol: str
    splits: List[BenchmarkSplit]
    bucket_tags: Dict[str, List[str]] = field(default_factory=dict)
    stochasticity_class: str = "deterministic"
    rerun_policy: Dict[str, Any] = field(default_factory=dict)
    contamination_notes: List[str] = field(default_factory=list)
    transfer_slices: List[TransferSliceManifest] = field(default_factory=list)
    promotion_relevance: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "benchmark_kind", _require_text(self.benchmark_kind, "benchmark_kind"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "dataset_id", _require_text(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "dataset_version", _require_text(self.dataset_version, "dataset_version"))
        object.__setattr__(self, "baseline_candidate_id", _require_text(self.baseline_candidate_id, "baseline_candidate_id"))
        object.__setattr__(self, "environment_domain", _require_text(self.environment_domain, "environment_domain"))
        object.__setattr__(self, "evaluator_stack", _copy_text_list(self.evaluator_stack))
        object.__setattr__(self, "comparison_protocol", _require_text(self.comparison_protocol, "comparison_protocol"))
        object.__setattr__(
            self,
            "splits",
            [item if isinstance(item, BenchmarkSplit) else BenchmarkSplit.from_dict(item) for item in self.splits],
        )
        object.__setattr__(
            self,
            "bucket_tags",
            {str(key): _copy_text_list(value) for key, value in (self.bucket_tags or {}).items()},
        )
        stochasticity = _require_text(self.stochasticity_class, "stochasticity_class").lower()
        if stochasticity not in ALLOWED_STOCHASTICITY_CLASSES:
            raise ValueError(f"stochasticity_class must be one of: {sorted(ALLOWED_STOCHASTICITY_CLASSES)}")
        object.__setattr__(self, "stochasticity_class", stochasticity)
        object.__setattr__(self, "rerun_policy", _copy_mapping(self.rerun_policy))
        object.__setattr__(self, "contamination_notes", _copy_text_list(self.contamination_notes))
        object.__setattr__(
            self,
            "transfer_slices",
            [
                item if isinstance(item, TransferSliceManifest) else TransferSliceManifest.from_dict(item)
                for item in self.transfer_slices
            ],
        )
        object.__setattr__(self, "promotion_relevance", _copy_mapping(self.promotion_relevance))
        object.__setattr__(
            self,
            "artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.artifact_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.evaluator_stack:
            raise ValueError("evaluator_stack must contain at least one evaluator identifier")
        if not self.splits:
            raise ValueError("splits must contain at least one benchmark split")

        split_names = [split.split_name for split in self.splits]
        if len(split_names) != len(set(split_names)):
            raise ValueError("splits contains duplicate split_name values")

        all_sample_ids: List[str] = []
        for split in self.splits:
            all_sample_ids.extend(split.sample_ids)
        if len(all_sample_ids) != len(set(all_sample_ids)):
            raise ValueError("sample ids may not appear in more than one benchmark split")
        if "train" not in set(split_names):
            raise ValueError("benchmark manifest must include a train split")
        if not any(split.visibility == "hidden_hold" for split in self.splits):
            raise ValueError("benchmark manifest must include at least one hidden_hold split")
        transfer_slice_ids = [item.slice_id for item in self.transfer_slices]
        if len(transfer_slice_ids) != len(set(transfer_slice_ids)):
            raise ValueError("transfer_slices contains duplicate slice_id values")

        unknown_bucket_ids = sorted(set(self.bucket_tags) - set(all_sample_ids))
        if unknown_bucket_ids:
            raise ValueError(f"bucket_tags references unknown sample ids: {unknown_bucket_ids}")

    def sample_ids(self) -> List[str]:
        sample_ids: List[str] = []
        for split in self.splits:
            sample_ids.extend(split.sample_ids)
        return sample_ids

    def hidden_hold_sample_ids(self) -> List[str]:
        sample_ids: List[str] = []
        for split in self.splits:
            if split.visibility == "hidden_hold":
                sample_ids.extend(split.sample_ids)
        return sample_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "benchmark_kind": self.benchmark_kind,
            "target_id": self.target_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "baseline_candidate_id": self.baseline_candidate_id,
            "environment_domain": self.environment_domain,
            "evaluator_stack": list(self.evaluator_stack),
            "comparison_protocol": self.comparison_protocol,
            "splits": [item.to_dict() for item in self.splits],
            "bucket_tags": {key: list(value) for key, value in self.bucket_tags.items()},
            "stochasticity_class": self.stochasticity_class,
            "rerun_policy": dict(self.rerun_policy),
            "contamination_notes": list(self.contamination_notes),
            "transfer_slices": [item.to_dict() for item in self.transfer_slices],
            "promotion_relevance": dict(self.promotion_relevance),
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BenchmarkRunManifest":
        return BenchmarkRunManifest(
            manifest_id=data.get("manifest_id") or data.get("id") or "",
            benchmark_kind=data.get("benchmark_kind") or "",
            target_id=data.get("target_id") or "",
            dataset_id=data.get("dataset_id") or "",
            dataset_version=data.get("dataset_version") or "",
            baseline_candidate_id=data.get("baseline_candidate_id") or "",
            environment_domain=data.get("environment_domain") or "",
            evaluator_stack=list(data.get("evaluator_stack") or []),
            comparison_protocol=data.get("comparison_protocol") or "",
            splits=[BenchmarkSplit.from_dict(item) for item in data.get("splits") or []],
            bucket_tags={str(key): list(value) for key, value in (data.get("bucket_tags") or {}).items()},
            stochasticity_class=data.get("stochasticity_class") or "deterministic",
            rerun_policy=dict(data.get("rerun_policy") or {}),
            contamination_notes=list(data.get("contamination_notes") or []),
            transfer_slices=[TransferSliceManifest.from_dict(item) for item in data.get("transfer_slices") or []],
            promotion_relevance=dict(data.get("promotion_relevance") or {}),
            artifact_refs=[ArtifactRef.from_dict(item) for item in data.get("artifact_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CandidateComparisonResult:
    comparison_id: str
    manifest_id: str
    parent_candidate_id: str
    child_candidate_id: str
    protocol_id: str
    outcome: str
    compared_sample_ids: List[str]
    held_out_sample_ids: List[str]
    trial_count: int
    rationale: str
    evidence_refs: List[ArtifactRef] = field(default_factory=list)
    metric_deltas: Dict[str, Any] = field(default_factory=dict)
    better_candidate_id: Optional[str] = None
    blocked_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison_id", _require_text(self.comparison_id, "comparison_id"))
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "parent_candidate_id", _require_text(self.parent_candidate_id, "parent_candidate_id"))
        object.__setattr__(self, "child_candidate_id", _require_text(self.child_candidate_id, "child_candidate_id"))
        object.__setattr__(self, "protocol_id", _require_text(self.protocol_id, "protocol_id"))
        outcome = _require_text(self.outcome, "outcome").lower()
        if outcome not in ALLOWED_COMPARISON_OUTCOMES:
            raise ValueError(f"outcome must be one of: {sorted(ALLOWED_COMPARISON_OUTCOMES)}")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "compared_sample_ids", _copy_text_list(self.compared_sample_ids))
        object.__setattr__(self, "held_out_sample_ids", _copy_text_list(self.held_out_sample_ids))
        if not self.compared_sample_ids:
            raise ValueError("compared_sample_ids must contain at least one sample id")
        if not set(self.held_out_sample_ids).issubset(set(self.compared_sample_ids)):
            raise ValueError("held_out_sample_ids must be a subset of compared_sample_ids")
        trial_count = int(self.trial_count)
        if trial_count <= 0:
            raise ValueError("trial_count must be positive")
        object.__setattr__(self, "trial_count", trial_count)
        object.__setattr__(self, "rationale", _require_text(self.rationale, "rationale"))
        object.__setattr__(
            self,
            "evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.evidence_refs],
        )
        object.__setattr__(self, "metric_deltas", _copy_mapping(self.metric_deltas))
        better_candidate_id = str(self.better_candidate_id).strip() if self.better_candidate_id else None
        object.__setattr__(self, "better_candidate_id", better_candidate_id)
        blocked_reason = str(self.blocked_reason).strip() if self.blocked_reason else None
        object.__setattr__(self, "blocked_reason", blocked_reason)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if self.outcome in {"win", "loss"} and not self.better_candidate_id:
            raise ValueError("better_candidate_id is required for win/loss outcomes")
        if self.outcome == "blocked" and not self.blocked_reason:
            raise ValueError("blocked_reason is required when outcome is blocked")
        if self.outcome == "tie" and self.better_candidate_id:
            raise ValueError("better_candidate_id must be empty for tie outcomes")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "comparison_id": self.comparison_id,
            "manifest_id": self.manifest_id,
            "parent_candidate_id": self.parent_candidate_id,
            "child_candidate_id": self.child_candidate_id,
            "protocol_id": self.protocol_id,
            "outcome": self.outcome,
            "compared_sample_ids": list(self.compared_sample_ids),
            "held_out_sample_ids": list(self.held_out_sample_ids),
            "trial_count": self.trial_count,
            "rationale": self.rationale,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "metric_deltas": dict(self.metric_deltas),
            "metadata": dict(self.metadata),
        }
        if self.better_candidate_id:
            payload["better_candidate_id"] = self.better_candidate_id
        if self.blocked_reason:
            payload["blocked_reason"] = self.blocked_reason
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CandidateComparisonResult":
        return CandidateComparisonResult(
            comparison_id=data.get("comparison_id") or data.get("id") or "",
            manifest_id=data.get("manifest_id") or "",
            parent_candidate_id=data.get("parent_candidate_id") or "",
            child_candidate_id=data.get("child_candidate_id") or "",
            protocol_id=data.get("protocol_id") or "",
            outcome=data.get("outcome") or "",
            compared_sample_ids=list(data.get("compared_sample_ids") or []),
            held_out_sample_ids=list(data.get("held_out_sample_ids") or []),
            trial_count=int(data.get("trial_count") or 0),
            rationale=data.get("rationale") or "",
            evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("evidence_refs") or []],
            metric_deltas=dict(data.get("metric_deltas") or {}),
            better_candidate_id=data.get("better_candidate_id"),
            blocked_reason=data.get("blocked_reason"),
            metadata=dict(data.get("metadata") or {}),
        )


def build_paired_candidate_comparison(
    manifest: BenchmarkRunManifest,
    *,
    comparison_id: str,
    parent_candidate_id: str,
    child_candidate_id: str,
    outcome: str,
    compared_sample_ids: Sequence[str],
    held_out_sample_ids: Sequence[str],
    trial_count: int,
    rationale: str,
    evidence_refs: Sequence[ArtifactRef | Mapping[str, Any]] | None = None,
    metric_deltas: Mapping[str, Any] | None = None,
    better_candidate_id: Optional[str] = None,
    blocked_reason: Optional[str] = None,
    metadata: Mapping[str, Any] | None = None,
) -> CandidateComparisonResult:
    manifest_sample_ids = set(manifest.sample_ids())
    compared_ids = _copy_text_list(compared_sample_ids)
    unknown_ids = sorted(set(compared_ids) - manifest_sample_ids)
    if unknown_ids:
        raise ValueError(f"comparison references unknown sample ids for manifest {manifest.manifest_id}: {unknown_ids}")
    held_out_ids = _copy_text_list(held_out_sample_ids)
    if not set(held_out_ids).issubset(set(manifest.hidden_hold_sample_ids())):
        raise ValueError("held_out_sample_ids must be drawn from manifest hidden_hold splits")
    return CandidateComparisonResult(
        comparison_id=comparison_id,
        manifest_id=manifest.manifest_id,
        parent_candidate_id=parent_candidate_id,
        child_candidate_id=child_candidate_id,
        protocol_id=manifest.comparison_protocol,
        outcome=outcome,
        compared_sample_ids=compared_ids,
        held_out_sample_ids=held_out_ids,
        trial_count=trial_count,
        rationale=rationale,
        evidence_refs=[
            item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item)
            for item in (evidence_refs or [])
        ],
        metric_deltas=dict(metric_deltas or {}),
        better_candidate_id=better_candidate_id,
        blocked_reason=blocked_reason,
        metadata=dict(metadata or {}),
    )


@dataclass(frozen=True)
class BenchmarkRunResult:
    run_id: str
    manifest_id: str
    candidate_ids: List[str]
    comparison_results: List[CandidateComparisonResult]
    aggregate_metrics: Dict[str, Any] = field(default_factory=dict)
    bucket_outcomes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    variance_summary: Dict[str, Any] = field(default_factory=dict)
    cost_support_evidence_slices: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    promotion_readiness_summary: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text(self.run_id, "run_id"))
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "candidate_ids", _copy_text_list(self.candidate_ids))
        object.__setattr__(
            self,
            "comparison_results",
            [
                item if isinstance(item, CandidateComparisonResult) else CandidateComparisonResult.from_dict(item)
                for item in self.comparison_results
            ],
        )
        object.__setattr__(self, "aggregate_metrics", _copy_mapping(self.aggregate_metrics))
        object.__setattr__(
            self,
            "bucket_outcomes",
            {str(key): dict(value) for key, value in (self.bucket_outcomes or {}).items()},
        )
        object.__setattr__(self, "variance_summary", _copy_mapping(self.variance_summary))
        object.__setattr__(self, "cost_support_evidence_slices", _copy_mapping(self.cost_support_evidence_slices))
        object.__setattr__(
            self,
            "artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.artifact_refs],
        )
        object.__setattr__(self, "promotion_readiness_summary", _copy_mapping(self.promotion_readiness_summary))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.candidate_ids:
            raise ValueError("candidate_ids must contain at least one candidate id")
        if not self.comparison_results:
            raise ValueError("comparison_results must contain at least one comparison result")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate_ids contains duplicate values")
        for comparison in self.comparison_results:
            if comparison.manifest_id != self.manifest_id:
                raise ValueError("comparison_results manifest_id must match the benchmark run result")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "manifest_id": self.manifest_id,
            "candidate_ids": list(self.candidate_ids),
            "comparison_results": [item.to_dict() for item in self.comparison_results],
            "aggregate_metrics": dict(self.aggregate_metrics),
            "bucket_outcomes": {key: dict(value) for key, value in self.bucket_outcomes.items()},
            "variance_summary": dict(self.variance_summary),
            "cost_support_evidence_slices": dict(self.cost_support_evidence_slices),
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "promotion_readiness_summary": dict(self.promotion_readiness_summary),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BenchmarkRunResult":
        return BenchmarkRunResult(
            run_id=data.get("run_id") or data.get("id") or "",
            manifest_id=data.get("manifest_id") or "",
            candidate_ids=list(data.get("candidate_ids") or []),
            comparison_results=[
                CandidateComparisonResult.from_dict(item)
                for item in data.get("comparison_results") or []
            ],
            aggregate_metrics=dict(data.get("aggregate_metrics") or {}),
            bucket_outcomes={str(key): dict(value) for key, value in (data.get("bucket_outcomes") or {}).items()},
            variance_summary=dict(data.get("variance_summary") or {}),
            cost_support_evidence_slices=dict(data.get("cost_support_evidence_slices") or {}),
            artifact_refs=[ArtifactRef.from_dict(item) for item in data.get("artifact_refs") or []],
            promotion_readiness_summary=dict(data.get("promotion_readiness_summary") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BackendComparisonResult:
    comparison_id: str
    backend_ids: List[str]
    manifest_ids: List[str]
    backend_run_ids: Dict[str, List[str]]
    backend_outcome_summary: Dict[str, Dict[str, Any]]
    rationale: str
    winner_backend_id: Optional[str] = None
    evidence_refs: List[ArtifactRef] = field(default_factory=list)
    reproducibility_notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison_id", _require_text(self.comparison_id, "comparison_id"))
        object.__setattr__(self, "backend_ids", _copy_text_list(self.backend_ids))
        object.__setattr__(self, "manifest_ids", _copy_text_list(self.manifest_ids))
        object.__setattr__(
            self,
            "backend_run_ids",
            {str(key): _copy_text_list(value) for key, value in (self.backend_run_ids or {}).items()},
        )
        object.__setattr__(
            self,
            "backend_outcome_summary",
            {str(key): dict(value) for key, value in (self.backend_outcome_summary or {}).items()},
        )
        object.__setattr__(self, "rationale", _require_text(self.rationale, "rationale"))
        winner_backend_id = str(self.winner_backend_id).strip() if self.winner_backend_id else None
        object.__setattr__(self, "winner_backend_id", winner_backend_id)
        object.__setattr__(
            self,
            "evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.evidence_refs],
        )
        object.__setattr__(self, "reproducibility_notes", _copy_mapping(self.reproducibility_notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.backend_ids:
            raise ValueError("backend_ids must contain at least one backend identifier")
        if len(self.backend_ids) != len(set(self.backend_ids)):
            raise ValueError("backend_ids contains duplicate values")
        if not self.manifest_ids:
            raise ValueError("manifest_ids must contain at least one manifest id")
        if len(self.manifest_ids) != len(set(self.manifest_ids)):
            raise ValueError("manifest_ids contains duplicate values")
        unknown_run_keys = sorted(set(self.backend_run_ids) - set(self.backend_ids))
        if unknown_run_keys:
            raise ValueError(f"backend_run_ids references unknown backend ids: {unknown_run_keys}")
        unknown_summary_keys = sorted(set(self.backend_outcome_summary) - set(self.backend_ids))
        if unknown_summary_keys:
            raise ValueError(f"backend_outcome_summary references unknown backend ids: {unknown_summary_keys}")
        if self.winner_backend_id and self.winner_backend_id not in set(self.backend_ids):
            raise ValueError("winner_backend_id must be one of backend_ids when provided")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "comparison_id": self.comparison_id,
            "backend_ids": list(self.backend_ids),
            "manifest_ids": list(self.manifest_ids),
            "backend_run_ids": {key: list(value) for key, value in self.backend_run_ids.items()},
            "backend_outcome_summary": {key: dict(value) for key, value in self.backend_outcome_summary.items()},
            "rationale": self.rationale,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "reproducibility_notes": dict(self.reproducibility_notes),
            "metadata": dict(self.metadata),
        }
        if self.winner_backend_id:
            payload["winner_backend_id"] = self.winner_backend_id
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BackendComparisonResult":
        return BackendComparisonResult(
            comparison_id=data.get("comparison_id") or data.get("id") or "",
            backend_ids=list(data.get("backend_ids") or []),
            manifest_ids=list(data.get("manifest_ids") or []),
            backend_run_ids={str(key): list(value) for key, value in (data.get("backend_run_ids") or {}).items()},
            backend_outcome_summary={
                str(key): dict(value) for key, value in (data.get("backend_outcome_summary") or {}).items()
            },
            rationale=data.get("rationale") or "",
            winner_backend_id=data.get("winner_backend_id"),
            evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("evidence_refs") or []],
            reproducibility_notes=dict(data.get("reproducibility_notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
