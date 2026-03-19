from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .benchmark import ALLOWED_SPLIT_VISIBILITY, ALLOWED_STOCHASTICITY_CLASSES
from .substrate import ArtifactRef


ALLOWED_VERIFIER_EXPERIMENT_OUTCOMES = {"accepted", "rejected", "blocked", "inconclusive"}


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
class EvaluationSuiteManifest:
    suite_id: str
    suite_kind: str
    evaluator_stack: List[str]
    split_visibility: Dict[str, str]
    stochasticity_class: str
    rerun_policy: Dict[str, Any] = field(default_factory=dict)
    capture_requirements: List[str] = field(default_factory=list)
    adjudication_requirements: Dict[str, Any] = field(default_factory=dict)
    comparison_protocol_defaults: Dict[str, Any] = field(default_factory=dict)
    artifact_requirements: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _require_text(self.suite_id, "suite_id"))
        object.__setattr__(self, "suite_kind", _require_text(self.suite_kind, "suite_kind"))
        object.__setattr__(self, "evaluator_stack", _copy_text_list(self.evaluator_stack))
        split_visibility = {str(key): _require_text(value, "split_visibility value").lower() for key, value in (self.split_visibility or {}).items()}
        invalid_visibilities = sorted(set(split_visibility.values()) - ALLOWED_SPLIT_VISIBILITY)
        if invalid_visibilities:
            raise ValueError(f"split_visibility contains invalid values: {invalid_visibilities}")
        stochasticity_class = _require_text(self.stochasticity_class, "stochasticity_class").lower()
        if stochasticity_class not in ALLOWED_STOCHASTICITY_CLASSES:
            raise ValueError(f"stochasticity_class must be one of: {sorted(ALLOWED_STOCHASTICITY_CLASSES)}")
        object.__setattr__(self, "split_visibility", split_visibility)
        object.__setattr__(self, "stochasticity_class", stochasticity_class)
        object.__setattr__(self, "rerun_policy", _copy_mapping(self.rerun_policy))
        object.__setattr__(self, "capture_requirements", _copy_text_list(self.capture_requirements))
        object.__setattr__(self, "adjudication_requirements", _copy_mapping(self.adjudication_requirements))
        object.__setattr__(self, "comparison_protocol_defaults", _copy_mapping(self.comparison_protocol_defaults))
        object.__setattr__(self, "artifact_requirements", _copy_text_list(self.artifact_requirements))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.evaluator_stack:
            raise ValueError("evaluator_stack must contain at least one evaluator id")
        if not self.split_visibility:
            raise ValueError("split_visibility must declare at least one split")
        if "train" not in self.split_visibility:
            raise ValueError("split_visibility must include train")
        if "hidden_hold" not in set(self.split_visibility.values()):
            raise ValueError("split_visibility must include at least one hidden_hold split")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_kind": self.suite_kind,
            "evaluator_stack": list(self.evaluator_stack),
            "split_visibility": dict(self.split_visibility),
            "stochasticity_class": self.stochasticity_class,
            "rerun_policy": dict(self.rerun_policy),
            "capture_requirements": list(self.capture_requirements),
            "adjudication_requirements": dict(self.adjudication_requirements),
            "comparison_protocol_defaults": dict(self.comparison_protocol_defaults),
            "artifact_requirements": list(self.artifact_requirements),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EvaluationSuiteManifest":
        return EvaluationSuiteManifest(
            suite_id=data.get("suite_id") or data.get("id") or "",
            suite_kind=data.get("suite_kind") or "",
            evaluator_stack=list(data.get("evaluator_stack") or []),
            split_visibility={str(key): str(value) for key, value in (data.get("split_visibility") or {}).items()},
            stochasticity_class=data.get("stochasticity_class") or "",
            rerun_policy=dict(data.get("rerun_policy") or {}),
            capture_requirements=list(data.get("capture_requirements") or []),
            adjudication_requirements=dict(data.get("adjudication_requirements") or {}),
            comparison_protocol_defaults=dict(data.get("comparison_protocol_defaults") or {}),
            artifact_requirements=list(data.get("artifact_requirements") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ObjectiveSuiteManifest:
    suite_id: str
    evaluation_suite_id: str
    objective_channels: Dict[str, Dict[str, Any]]
    penalties: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    aggregation_rules: Dict[str, Any] = field(default_factory=dict)
    uncertainty_policy: Dict[str, Any] = field(default_factory=dict)
    frontier_dimensions: List[str] = field(default_factory=list)
    promotion_annotations: Dict[str, Any] = field(default_factory=dict)
    visibility_annotations: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _require_text(self.suite_id, "suite_id"))
        object.__setattr__(self, "evaluation_suite_id", _require_text(self.evaluation_suite_id, "evaluation_suite_id"))
        object.__setattr__(self, "objective_channels", _copy_nested_mapping(self.objective_channels))
        object.__setattr__(self, "penalties", _copy_nested_mapping(self.penalties))
        object.__setattr__(self, "aggregation_rules", _copy_mapping(self.aggregation_rules))
        object.__setattr__(self, "uncertainty_policy", _copy_mapping(self.uncertainty_policy))
        object.__setattr__(self, "frontier_dimensions", _copy_text_list(self.frontier_dimensions))
        object.__setattr__(self, "promotion_annotations", _copy_mapping(self.promotion_annotations))
        object.__setattr__(self, "visibility_annotations", _copy_mapping(self.visibility_annotations))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.objective_channels:
            raise ValueError("objective_channels must contain at least one channel")
        if not self.frontier_dimensions:
            raise ValueError("frontier_dimensions must contain at least one channel id")
        unknown_frontier_dimensions = sorted(set(self.frontier_dimensions) - set(self.objective_channels))
        if unknown_frontier_dimensions:
            raise ValueError(
                f"frontier_dimensions references unknown objective channels: {unknown_frontier_dimensions}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "evaluation_suite_id": self.evaluation_suite_id,
            "objective_channels": {key: dict(value) for key, value in self.objective_channels.items()},
            "penalties": {key: dict(value) for key, value in self.penalties.items()},
            "aggregation_rules": dict(self.aggregation_rules),
            "uncertainty_policy": dict(self.uncertainty_policy),
            "frontier_dimensions": list(self.frontier_dimensions),
            "promotion_annotations": dict(self.promotion_annotations),
            "visibility_annotations": dict(self.visibility_annotations),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ObjectiveSuiteManifest":
        return ObjectiveSuiteManifest(
            suite_id=data.get("suite_id") or data.get("id") or "",
            evaluation_suite_id=data.get("evaluation_suite_id") or "",
            objective_channels=_copy_nested_mapping(data.get("objective_channels") or {}),
            penalties=_copy_nested_mapping(data.get("penalties") or {}),
            aggregation_rules=dict(data.get("aggregation_rules") or {}),
            uncertainty_policy=dict(data.get("uncertainty_policy") or {}),
            frontier_dimensions=list(data.get("frontier_dimensions") or []),
            promotion_annotations=dict(data.get("promotion_annotations") or {}),
            visibility_annotations=dict(data.get("visibility_annotations") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ObjectiveBreakdownResult:
    result_id: str
    objective_suite_id: str
    manifest_id: str
    candidate_id: str
    per_sample_components: Dict[str, Dict[str, Any]]
    per_bucket_components: Dict[str, Dict[str, Any]]
    aggregate_objectives: Dict[str, Any]
    uncertainty_summary: Dict[str, Any] = field(default_factory=dict)
    blocked_components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_id", _require_text(self.result_id, "result_id"))
        object.__setattr__(self, "objective_suite_id", _require_text(self.objective_suite_id, "objective_suite_id"))
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "per_sample_components", _copy_nested_mapping(self.per_sample_components))
        object.__setattr__(self, "per_bucket_components", _copy_nested_mapping(self.per_bucket_components))
        object.__setattr__(self, "aggregate_objectives", _copy_mapping(self.aggregate_objectives))
        object.__setattr__(self, "uncertainty_summary", _copy_mapping(self.uncertainty_summary))
        object.__setattr__(self, "blocked_components", _copy_nested_mapping(self.blocked_components))
        object.__setattr__(
            self,
            "artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.artifact_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.per_sample_components:
            raise ValueError("per_sample_components must contain at least one sample entry")
        if not self.aggregate_objectives:
            raise ValueError("aggregate_objectives must contain at least one objective")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "objective_suite_id": self.objective_suite_id,
            "manifest_id": self.manifest_id,
            "candidate_id": self.candidate_id,
            "per_sample_components": {key: dict(value) for key, value in self.per_sample_components.items()},
            "per_bucket_components": {key: dict(value) for key, value in self.per_bucket_components.items()},
            "aggregate_objectives": dict(self.aggregate_objectives),
            "uncertainty_summary": dict(self.uncertainty_summary),
            "blocked_components": {key: dict(value) for key, value in self.blocked_components.items()},
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ObjectiveBreakdownResult":
        return ObjectiveBreakdownResult(
            result_id=data.get("result_id") or data.get("id") or "",
            objective_suite_id=data.get("objective_suite_id") or "",
            manifest_id=data.get("manifest_id") or "",
            candidate_id=data.get("candidate_id") or "",
            per_sample_components=_copy_nested_mapping(data.get("per_sample_components") or {}),
            per_bucket_components=_copy_nested_mapping(data.get("per_bucket_components") or {}),
            aggregate_objectives=dict(data.get("aggregate_objectives") or {}),
            uncertainty_summary=dict(data.get("uncertainty_summary") or {}),
            blocked_components=_copy_nested_mapping(data.get("blocked_components") or {}),
            artifact_refs=[ArtifactRef.from_dict(item) for item in data.get("artifact_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TargetFamilyManifest:
    family_id: str
    family_kind: str
    target_ids: List[str]
    family_scope: str
    mutable_loci_ids: List[str]
    evaluation_suite_id: str
    objective_suite_id: str
    review_class: str
    runtime_context_assumptions: Dict[str, Any] = field(default_factory=dict)
    promotion_class: str = ""
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "family_id", _require_text(self.family_id, "family_id"))
        object.__setattr__(self, "family_kind", _require_text(self.family_kind, "family_kind"))
        object.__setattr__(self, "target_ids", _copy_text_list(self.target_ids))
        object.__setattr__(self, "family_scope", _require_text(self.family_scope, "family_scope"))
        object.__setattr__(self, "mutable_loci_ids", _copy_text_list(self.mutable_loci_ids))
        object.__setattr__(self, "evaluation_suite_id", _require_text(self.evaluation_suite_id, "evaluation_suite_id"))
        object.__setattr__(self, "objective_suite_id", _require_text(self.objective_suite_id, "objective_suite_id"))
        object.__setattr__(self, "review_class", _require_text(self.review_class, "review_class"))
        object.__setattr__(self, "runtime_context_assumptions", _copy_mapping(self.runtime_context_assumptions))
        object.__setattr__(self, "promotion_class", _require_text(self.promotion_class, "promotion_class"))
        object.__setattr__(
            self,
            "artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.artifact_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.target_ids:
            raise ValueError("target_ids must contain at least one target id")
        if not self.mutable_loci_ids:
            raise ValueError("mutable_loci_ids must contain at least one mutable locus id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_kind": self.family_kind,
            "target_ids": list(self.target_ids),
            "family_scope": self.family_scope,
            "mutable_loci_ids": list(self.mutable_loci_ids),
            "evaluation_suite_id": self.evaluation_suite_id,
            "objective_suite_id": self.objective_suite_id,
            "review_class": self.review_class,
            "runtime_context_assumptions": dict(self.runtime_context_assumptions),
            "promotion_class": self.promotion_class,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TargetFamilyManifest":
        return TargetFamilyManifest(
            family_id=data.get("family_id") or data.get("id") or "",
            family_kind=data.get("family_kind") or "",
            target_ids=list(data.get("target_ids") or []),
            family_scope=data.get("family_scope") or "",
            mutable_loci_ids=list(data.get("mutable_loci_ids") or []),
            evaluation_suite_id=data.get("evaluation_suite_id") or "",
            objective_suite_id=data.get("objective_suite_id") or "",
            review_class=data.get("review_class") or "",
            runtime_context_assumptions=dict(data.get("runtime_context_assumptions") or {}),
            promotion_class=data.get("promotion_class") or "",
            artifact_refs=[ArtifactRef.from_dict(item) for item in data.get("artifact_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchSpaceManifest:
    search_space_id: str
    family_id: str
    allowed_loci: List[str]
    mutation_kinds_by_locus: Dict[str, List[str]]
    value_domains_by_locus: Dict[str, Dict[str, Any]]
    semantic_constraints: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    invariants: List[str] = field(default_factory=list)
    unsafe_expansion_notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "search_space_id", _require_text(self.search_space_id, "search_space_id"))
        object.__setattr__(self, "family_id", _require_text(self.family_id, "family_id"))
        object.__setattr__(self, "allowed_loci", _copy_text_list(self.allowed_loci))
        object.__setattr__(
            self,
            "mutation_kinds_by_locus",
            {str(key): _copy_text_list(value) for key, value in (self.mutation_kinds_by_locus or {}).items()},
        )
        object.__setattr__(self, "value_domains_by_locus", _copy_nested_mapping(self.value_domains_by_locus))
        object.__setattr__(self, "semantic_constraints", _copy_nested_mapping(self.semantic_constraints))
        object.__setattr__(self, "invariants", _copy_text_list(self.invariants))
        object.__setattr__(self, "unsafe_expansion_notes", _copy_text_list(self.unsafe_expansion_notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.allowed_loci:
            raise ValueError("allowed_loci must contain at least one locus id")
        unknown_mutation_loci = sorted(set(self.mutation_kinds_by_locus) - set(self.allowed_loci))
        if unknown_mutation_loci:
            raise ValueError(f"mutation_kinds_by_locus references unknown loci: {unknown_mutation_loci}")
        unknown_value_domain_loci = sorted(set(self.value_domains_by_locus) - set(self.allowed_loci))
        if unknown_value_domain_loci:
            raise ValueError(f"value_domains_by_locus references unknown loci: {unknown_value_domain_loci}")
        unknown_constraint_loci = sorted(set(self.semantic_constraints) - set(self.allowed_loci))
        if unknown_constraint_loci:
            raise ValueError(f"semantic_constraints references unknown loci: {unknown_constraint_loci}")
        missing_mutation_kinds = sorted(set(self.allowed_loci) - set(self.mutation_kinds_by_locus))
        if missing_mutation_kinds:
            raise ValueError(f"allowed_loci missing mutation kind declarations: {missing_mutation_kinds}")
        missing_value_domains = sorted(set(self.allowed_loci) - set(self.value_domains_by_locus))
        if missing_value_domains:
            raise ValueError(f"allowed_loci missing value domain declarations: {missing_value_domains}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "search_space_id": self.search_space_id,
            "family_id": self.family_id,
            "allowed_loci": list(self.allowed_loci),
            "mutation_kinds_by_locus": {key: list(value) for key, value in self.mutation_kinds_by_locus.items()},
            "value_domains_by_locus": {key: dict(value) for key, value in self.value_domains_by_locus.items()},
            "semantic_constraints": {key: dict(value) for key, value in self.semantic_constraints.items()},
            "invariants": list(self.invariants),
            "unsafe_expansion_notes": list(self.unsafe_expansion_notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchSpaceManifest":
        return SearchSpaceManifest(
            search_space_id=data.get("search_space_id") or data.get("id") or "",
            family_id=data.get("family_id") or "",
            allowed_loci=list(data.get("allowed_loci") or []),
            mutation_kinds_by_locus={
                str(key): list(value) for key, value in (data.get("mutation_kinds_by_locus") or {}).items()
            },
            value_domains_by_locus=_copy_nested_mapping(data.get("value_domains_by_locus") or {}),
            semantic_constraints=_copy_nested_mapping(data.get("semantic_constraints") or {}),
            invariants=list(data.get("invariants") or []),
            unsafe_expansion_notes=list(data.get("unsafe_expansion_notes") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class VerifierAugmentedExperimentResult:
    experiment_id: str
    experiment_kind: str
    evaluation_suite_id: str
    objective_suite_id: str
    target_family_id: str
    search_space_id: str
    baseline_candidate_id: str
    refined_candidate_id: str
    verifier_stack: List[str]
    focus_sample_ids: List[str]
    comparison_result_id: str
    objective_breakdown_result_id: str
    outcome: str
    rationale: str
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _require_text(self.experiment_id, "experiment_id"))
        object.__setattr__(self, "experiment_kind", _require_text(self.experiment_kind, "experiment_kind"))
        object.__setattr__(self, "evaluation_suite_id", _require_text(self.evaluation_suite_id, "evaluation_suite_id"))
        object.__setattr__(self, "objective_suite_id", _require_text(self.objective_suite_id, "objective_suite_id"))
        object.__setattr__(self, "target_family_id", _require_text(self.target_family_id, "target_family_id"))
        object.__setattr__(self, "search_space_id", _require_text(self.search_space_id, "search_space_id"))
        object.__setattr__(self, "baseline_candidate_id", _require_text(self.baseline_candidate_id, "baseline_candidate_id"))
        object.__setattr__(self, "refined_candidate_id", _require_text(self.refined_candidate_id, "refined_candidate_id"))
        object.__setattr__(self, "verifier_stack", _copy_text_list(self.verifier_stack))
        object.__setattr__(self, "focus_sample_ids", _copy_text_list(self.focus_sample_ids))
        object.__setattr__(self, "comparison_result_id", _require_text(self.comparison_result_id, "comparison_result_id"))
        object.__setattr__(
            self,
            "objective_breakdown_result_id",
            _require_text(self.objective_breakdown_result_id, "objective_breakdown_result_id"),
        )
        outcome = _require_text(self.outcome, "outcome").lower()
        if outcome not in ALLOWED_VERIFIER_EXPERIMENT_OUTCOMES:
            raise ValueError(
                f"outcome must be one of: {sorted(ALLOWED_VERIFIER_EXPERIMENT_OUTCOMES)}"
            )
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "rationale", _require_text(self.rationale, "rationale"))
        object.__setattr__(
            self,
            "artifact_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.artifact_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.verifier_stack:
            raise ValueError("verifier_stack must contain at least one verifier id")
        if not self.focus_sample_ids:
            raise ValueError("focus_sample_ids must contain at least one sample id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_kind": self.experiment_kind,
            "evaluation_suite_id": self.evaluation_suite_id,
            "objective_suite_id": self.objective_suite_id,
            "target_family_id": self.target_family_id,
            "search_space_id": self.search_space_id,
            "baseline_candidate_id": self.baseline_candidate_id,
            "refined_candidate_id": self.refined_candidate_id,
            "verifier_stack": list(self.verifier_stack),
            "focus_sample_ids": list(self.focus_sample_ids),
            "comparison_result_id": self.comparison_result_id,
            "objective_breakdown_result_id": self.objective_breakdown_result_id,
            "outcome": self.outcome,
            "rationale": self.rationale,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "VerifierAugmentedExperimentResult":
        return VerifierAugmentedExperimentResult(
            experiment_id=data.get("experiment_id") or data.get("id") or "",
            experiment_kind=data.get("experiment_kind") or "",
            evaluation_suite_id=data.get("evaluation_suite_id") or "",
            objective_suite_id=data.get("objective_suite_id") or "",
            target_family_id=data.get("target_family_id") or "",
            search_space_id=data.get("search_space_id") or "",
            baseline_candidate_id=data.get("baseline_candidate_id") or "",
            refined_candidate_id=data.get("refined_candidate_id") or "",
            verifier_stack=list(data.get("verifier_stack") or []),
            focus_sample_ids=list(data.get("focus_sample_ids") or []),
            comparison_result_id=data.get("comparison_result_id") or "",
            objective_breakdown_result_id=data.get("objective_breakdown_result_id") or "",
            outcome=data.get("outcome") or "",
            rationale=data.get("rationale") or "",
            artifact_refs=[ArtifactRef.from_dict(item) for item in data.get("artifact_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )
