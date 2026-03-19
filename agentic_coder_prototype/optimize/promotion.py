from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .benchmark import BenchmarkRunManifest, CandidateComparisonResult
from .evaluation import EvaluationRecord
from .suites import (
    EvaluationSuiteManifest,
    FamilyCompositionManifest,
    ObjectiveBreakdownResult,
    ObjectiveSuiteManifest,
    SearchSpaceManifest,
    TargetFamilyManifest,
    TransferSliceManifest,
)
from .substrate import ArtifactRef, MaterializedCandidate, OptimizationTarget, SupportEnvelope


PROMOTION_STATES = {
    "draft",
    "evaluated",
    "frontier",
    "gated",
    "promotable",
    "promoted",
    "rejected",
    "archived",
}
TERMINAL_PROMOTION_STATES = {"promoted", "rejected", "archived"}
ALLOWED_GATE_STATUSES = {"pass", "fail", "not_applicable", "insufficient_evidence"}
ALLOWED_GATE_KINDS = {"replay", "conformance", "support_envelope", "comparison", "family_promotion"}
ALLOWED_TRANSITIONS = {
    "draft": {"evaluated", "archived"},
    "evaluated": {"frontier", "rejected", "archived"},
    "frontier": {"gated", "rejected", "archived"},
    "gated": {"promotable", "frontier", "rejected", "archived"},
    "promotable": {"promoted", "rejected", "archived"},
    "promoted": {"archived"},
    "rejected": {"archived"},
    "archived": set(),
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


def _infer_transfer_slice_kind(slice_id: str) -> str:
    prefix = str(slice_id).split(".", 1)[0]
    return {
        "package": "package",
        "model_tier": "model_tier",
        "provider_model": "provider_model",
        "environment": "environment",
        "tool_pack": "tool_pack",
        "repo_family": "repo_family",
    }.get(prefix, "package")


def _envelope_expansion_details(
    baseline: SupportEnvelope,
    candidate: SupportEnvelope,
) -> Dict[str, List[str]]:
    details: Dict[str, List[str]] = {}
    for field_name in ("tools", "execution_profiles", "environments", "providers", "models"):
        expanded = sorted(set(getattr(candidate, field_name)) - set(getattr(baseline, field_name)))
        if expanded:
            details[field_name] = expanded

    assumption_expansion: List[str] = []
    baseline_assumptions = dict(baseline.assumptions)
    candidate_assumptions = dict(candidate.assumptions)
    for key in sorted(set(candidate_assumptions) - set(baseline_assumptions)):
        assumption_expansion.append(f"{key}=new")
    for key in sorted(set(candidate_assumptions) & set(baseline_assumptions)):
        if baseline_assumptions[key] != candidate_assumptions[key]:
            assumption_expansion.append(f"{key}=changed")
    if assumption_expansion:
        details["assumptions"] = assumption_expansion
    return details


def _find_diagnostic_evidence(
    evaluation: EvaluationRecord,
    diagnostic_kind: str,
) -> List[ArtifactRef]:
    refs: List[ArtifactRef] = []
    for bundle in evaluation.normalized_diagnostics:
        for entry in bundle.entries:
            if entry.kind == diagnostic_kind:
                refs.extend(entry.evidence_refs)
    return refs


@dataclass(frozen=True)
class PromotionEvidence:
    evidence_id: str
    target_id: str
    candidate_id: str
    evaluation_ids: List[str] = field(default_factory=list)
    raw_evidence_refs: List[ArtifactRef] = field(default_factory=list)
    gate_evidence_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _require_text(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "evaluation_ids", _copy_text_list(self.evaluation_ids))
        object.__setattr__(
            self,
            "raw_evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.raw_evidence_refs],
        )
        object.__setattr__(
            self,
            "gate_evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.gate_evidence_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "evaluation_ids": list(self.evaluation_ids),
            "raw_evidence_refs": [item.to_dict() for item in self.raw_evidence_refs],
            "gate_evidence_refs": [item.to_dict() for item in self.gate_evidence_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PromotionEvidence":
        return PromotionEvidence(
            evidence_id=data.get("evidence_id") or data.get("id") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            evaluation_ids=list(data.get("evaluation_ids") or []),
            raw_evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("raw_evidence_refs") or []],
            gate_evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("gate_evidence_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PromotionEvidenceSummary:
    summary_id: str
    candidate_id: str
    comparison_ids: List[str] = field(default_factory=list)
    manifest_ids: List[str] = field(default_factory=list)
    held_out_sample_ids: List[str] = field(default_factory=list)
    regression_sample_ids: List[str] = field(default_factory=list)
    compared_regression_sample_ids: List[str] = field(default_factory=list)
    stochasticity_class: Optional[str] = None
    minimum_required_trials: Optional[int] = None
    observed_trial_count: Optional[int] = None
    outcome_counts: Dict[str, int] = field(default_factory=dict)
    evaluation_suite_ids: List[str] = field(default_factory=list)
    objective_suite_ids: List[str] = field(default_factory=list)
    target_family_ids: List[str] = field(default_factory=list)
    composition_ids: List[str] = field(default_factory=list)
    member_family_ids: List[str] = field(default_factory=list)
    search_space_ids: List[str] = field(default_factory=list)
    objective_breakdown_result_ids: List[str] = field(default_factory=list)
    family_bucket_ids: List[str] = field(default_factory=list)
    hidden_hold_bucket_ids: List[str] = field(default_factory=list)
    regression_bucket_ids: List[str] = field(default_factory=list)
    applicability_scope: Dict[str, Any] = field(default_factory=dict)
    family_risk_summary: Dict[str, Any] = field(default_factory=dict)
    member_family_coverage: Dict[str, Any] = field(default_factory=dict)
    coupling_risk_summary: Dict[str, Any] = field(default_factory=dict)
    transfer_slice_ids: List[str] = field(default_factory=list)
    transfer_slices: List[TransferSliceManifest] = field(default_factory=list)
    model_tier_audit: Dict[str, Any] = field(default_factory=dict)
    review_class: Optional[str] = None
    objective_breakdown_status: Optional[str] = None
    review_required: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _require_text(self.summary_id, "summary_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "comparison_ids", _copy_text_list(self.comparison_ids))
        object.__setattr__(self, "manifest_ids", _copy_text_list(self.manifest_ids))
        object.__setattr__(self, "held_out_sample_ids", _copy_text_list(self.held_out_sample_ids))
        object.__setattr__(self, "regression_sample_ids", _copy_text_list(self.regression_sample_ids))
        object.__setattr__(self, "compared_regression_sample_ids", _copy_text_list(self.compared_regression_sample_ids))
        object.__setattr__(self, "stochasticity_class", str(self.stochasticity_class).strip() if self.stochasticity_class else None)
        object.__setattr__(self, "evaluation_suite_ids", _copy_text_list(self.evaluation_suite_ids))
        object.__setattr__(self, "objective_suite_ids", _copy_text_list(self.objective_suite_ids))
        object.__setattr__(self, "target_family_ids", _copy_text_list(self.target_family_ids))
        object.__setattr__(self, "composition_ids", _copy_text_list(self.composition_ids))
        object.__setattr__(self, "member_family_ids", _copy_text_list(self.member_family_ids))
        object.__setattr__(self, "search_space_ids", _copy_text_list(self.search_space_ids))
        object.__setattr__(self, "objective_breakdown_result_ids", _copy_text_list(self.objective_breakdown_result_ids))
        object.__setattr__(self, "family_bucket_ids", _copy_text_list(self.family_bucket_ids))
        object.__setattr__(self, "hidden_hold_bucket_ids", _copy_text_list(self.hidden_hold_bucket_ids))
        object.__setattr__(self, "regression_bucket_ids", _copy_text_list(self.regression_bucket_ids))
        object.__setattr__(self, "applicability_scope", _copy_mapping(self.applicability_scope))
        object.__setattr__(self, "family_risk_summary", _copy_mapping(self.family_risk_summary))
        object.__setattr__(self, "member_family_coverage", _copy_mapping(self.member_family_coverage))
        object.__setattr__(self, "coupling_risk_summary", _copy_mapping(self.coupling_risk_summary))
        object.__setattr__(self, "transfer_slice_ids", _copy_text_list(self.transfer_slice_ids))
        object.__setattr__(
            self,
            "transfer_slices",
            [
                item if isinstance(item, TransferSliceManifest) else TransferSliceManifest.from_dict(item)
                for item in self.transfer_slices
            ],
        )
        object.__setattr__(self, "model_tier_audit", _copy_mapping(self.model_tier_audit))
        object.__setattr__(self, "review_class", str(self.review_class).strip() if self.review_class else None)
        object.__setattr__(
            self,
            "objective_breakdown_status",
            str(self.objective_breakdown_status).strip() if self.objective_breakdown_status else None,
        )
        if self.minimum_required_trials is not None and int(self.minimum_required_trials) <= 0:
            raise ValueError("minimum_required_trials must be positive when provided")
        if self.observed_trial_count is not None and int(self.observed_trial_count) <= 0:
            raise ValueError("observed_trial_count must be positive when provided")
        object.__setattr__(
            self,
            "minimum_required_trials",
            int(self.minimum_required_trials) if self.minimum_required_trials is not None else None,
        )
        object.__setattr__(
            self,
            "observed_trial_count",
            int(self.observed_trial_count) if self.observed_trial_count is not None else None,
        )
        outcome_counts = {str(key): int(value) for key, value in (self.outcome_counts or {}).items()}
        object.__setattr__(self, "outcome_counts", outcome_counts)
        object.__setattr__(self, "review_required", bool(self.review_required))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "summary_id": self.summary_id,
            "candidate_id": self.candidate_id,
            "comparison_ids": list(self.comparison_ids),
            "manifest_ids": list(self.manifest_ids),
            "held_out_sample_ids": list(self.held_out_sample_ids),
            "regression_sample_ids": list(self.regression_sample_ids),
            "compared_regression_sample_ids": list(self.compared_regression_sample_ids),
            "outcome_counts": dict(self.outcome_counts),
            "evaluation_suite_ids": list(self.evaluation_suite_ids),
            "objective_suite_ids": list(self.objective_suite_ids),
            "target_family_ids": list(self.target_family_ids),
            "composition_ids": list(self.composition_ids),
            "member_family_ids": list(self.member_family_ids),
            "search_space_ids": list(self.search_space_ids),
            "objective_breakdown_result_ids": list(self.objective_breakdown_result_ids),
            "family_bucket_ids": list(self.family_bucket_ids),
            "hidden_hold_bucket_ids": list(self.hidden_hold_bucket_ids),
            "regression_bucket_ids": list(self.regression_bucket_ids),
            "applicability_scope": dict(self.applicability_scope),
            "family_risk_summary": dict(self.family_risk_summary),
            "member_family_coverage": dict(self.member_family_coverage),
            "coupling_risk_summary": dict(self.coupling_risk_summary),
            "transfer_slice_ids": list(self.transfer_slice_ids),
            "transfer_slices": [item.to_dict() for item in self.transfer_slices],
            "model_tier_audit": dict(self.model_tier_audit),
            "review_class": self.review_class,
            "objective_breakdown_status": self.objective_breakdown_status,
            "review_required": self.review_required,
            "metadata": dict(self.metadata),
        }
        if self.stochasticity_class:
            payload["stochasticity_class"] = self.stochasticity_class
        if self.minimum_required_trials is not None:
            payload["minimum_required_trials"] = self.minimum_required_trials
        if self.observed_trial_count is not None:
            payload["observed_trial_count"] = self.observed_trial_count
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PromotionEvidenceSummary":
        return PromotionEvidenceSummary(
            summary_id=data.get("summary_id") or data.get("id") or "",
            candidate_id=data.get("candidate_id") or "",
            comparison_ids=list(data.get("comparison_ids") or []),
            manifest_ids=list(data.get("manifest_ids") or []),
            held_out_sample_ids=list(data.get("held_out_sample_ids") or []),
            regression_sample_ids=list(data.get("regression_sample_ids") or []),
            compared_regression_sample_ids=list(data.get("compared_regression_sample_ids") or []),
            stochasticity_class=data.get("stochasticity_class"),
            minimum_required_trials=data.get("minimum_required_trials"),
            observed_trial_count=data.get("observed_trial_count"),
            outcome_counts=dict(data.get("outcome_counts") or {}),
            evaluation_suite_ids=list(data.get("evaluation_suite_ids") or []),
            objective_suite_ids=list(data.get("objective_suite_ids") or []),
            target_family_ids=list(data.get("target_family_ids") or []),
            composition_ids=list(data.get("composition_ids") or []),
            member_family_ids=list(data.get("member_family_ids") or []),
            search_space_ids=list(data.get("search_space_ids") or []),
            objective_breakdown_result_ids=list(data.get("objective_breakdown_result_ids") or []),
            family_bucket_ids=list(data.get("family_bucket_ids") or []),
            hidden_hold_bucket_ids=list(data.get("hidden_hold_bucket_ids") or []),
            regression_bucket_ids=list(data.get("regression_bucket_ids") or []),
            applicability_scope=dict(data.get("applicability_scope") or {}),
            family_risk_summary=dict(data.get("family_risk_summary") or {}),
            member_family_coverage=dict(data.get("member_family_coverage") or {}),
            coupling_risk_summary=dict(data.get("coupling_risk_summary") or {}),
            transfer_slice_ids=list(data.get("transfer_slice_ids") or []),
            transfer_slices=[TransferSliceManifest.from_dict(item) for item in data.get("transfer_slices") or []],
            model_tier_audit=dict(data.get("model_tier_audit") or {}),
            review_class=data.get("review_class"),
            objective_breakdown_status=data.get("objective_breakdown_status"),
            review_required=bool(data.get("review_required")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    gate_kind: str
    status: str
    target_id: str
    candidate_id: str
    reason: str
    evidence_refs: List[ArtifactRef] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _require_text(self.gate_id, "gate_id"))
        gate_kind = _require_text(self.gate_kind, "gate_kind")
        if gate_kind not in ALLOWED_GATE_KINDS:
            raise ValueError(f"gate_kind must be one of: {sorted(ALLOWED_GATE_KINDS)}")
        object.__setattr__(self, "gate_kind", gate_kind)
        status = _require_text(self.status, "status")
        if status not in ALLOWED_GATE_STATUSES:
            raise ValueError(f"status must be one of: {sorted(ALLOWED_GATE_STATUSES)}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(
            self,
            "evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.evidence_refs],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_kind": self.gate_kind,
            "status": self.status,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "GateResult":
        return GateResult(
            gate_id=data.get("gate_id") or data.get("id") or "",
            gate_kind=data.get("gate_kind") or "",
            status=data.get("status") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            reason=data.get("reason") or "",
            evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("evidence_refs") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReplayConformanceGateInput:
    target: OptimizationTarget
    materialized_candidate: MaterializedCandidate
    evaluation: EvaluationRecord
    prior_state: Optional["PromotionRecord"] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupportEnvelopeGateInput:
    target: OptimizationTarget
    materialized_candidate: MaterializedCandidate
    evaluation: EvaluationRecord
    allow_narrowing: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromotionDecision:
    decision_id: str
    target_id: str
    candidate_id: str
    previous_state: str
    next_state: str
    reason: str
    event_label: str
    gate_results: List[GateResult] = field(default_factory=list)
    evidence: PromotionEvidence = field(default_factory=lambda: PromotionEvidence(evidence_id="placeholder", target_id="placeholder", candidate_id="placeholder"))
    blocked_by_gate_kinds: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_text(self.decision_id, "decision_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        previous_state = _require_text(self.previous_state, "previous_state")
        next_state = _require_text(self.next_state, "next_state")
        if previous_state not in PROMOTION_STATES:
            raise ValueError(f"previous_state must be one of: {sorted(PROMOTION_STATES)}")
        if next_state not in PROMOTION_STATES:
            raise ValueError(f"next_state must be one of: {sorted(PROMOTION_STATES)}")
        object.__setattr__(self, "previous_state", previous_state)
        object.__setattr__(self, "next_state", next_state)
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(self, "event_label", _require_text(self.event_label, "event_label"))
        object.__setattr__(
            self,
            "gate_results",
            [item if isinstance(item, GateResult) else GateResult.from_dict(item) for item in self.gate_results],
        )
        object.__setattr__(
            self,
            "evidence",
            self.evidence if isinstance(self.evidence, PromotionEvidence) else PromotionEvidence.from_dict(self.evidence),
        )
        object.__setattr__(self, "blocked_by_gate_kinds", _copy_text_list(self.blocked_by_gate_kinds))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "reason": self.reason,
            "event_label": self.event_label,
            "gate_results": [item.to_dict() for item in self.gate_results],
            "evidence": self.evidence.to_dict(),
            "blocked_by_gate_kinds": list(self.blocked_by_gate_kinds),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PromotionDecision":
        return PromotionDecision(
            decision_id=data.get("decision_id") or data.get("id") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            previous_state=data.get("previous_state") or "",
            next_state=data.get("next_state") or "",
            reason=data.get("reason") or "",
            event_label=data.get("event_label") or "",
            gate_results=[GateResult.from_dict(item) for item in data.get("gate_results") or []],
            evidence=PromotionEvidence.from_dict(data.get("evidence") or {}),
            blocked_by_gate_kinds=list(data.get("blocked_by_gate_kinds") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class PromotionRecord:
    record_id: str
    target_id: str
    candidate_id: str
    state: str
    created_at: str
    updated_at: str
    transition_event: str
    transition_reason: str
    gate_results: List[GateResult] = field(default_factory=list)
    evidence: PromotionEvidence = field(default_factory=lambda: PromotionEvidence(evidence_id="placeholder", target_id="placeholder", candidate_id="placeholder"))
    state_history: List[str] = field(default_factory=list)
    blocked_by_gate_kinds: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _require_text(self.record_id, "record_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        state = _require_text(self.state, "state")
        if state not in PROMOTION_STATES:
            raise ValueError(f"state must be one of: {sorted(PROMOTION_STATES)}")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _require_text(self.updated_at, "updated_at"))
        object.__setattr__(self, "transition_event", _require_text(self.transition_event, "transition_event"))
        object.__setattr__(self, "transition_reason", _require_text(self.transition_reason, "transition_reason"))
        object.__setattr__(
            self,
            "gate_results",
            [item if isinstance(item, GateResult) else GateResult.from_dict(item) for item in self.gate_results],
        )
        object.__setattr__(
            self,
            "evidence",
            self.evidence if isinstance(self.evidence, PromotionEvidence) else PromotionEvidence.from_dict(self.evidence),
        )
        history = _copy_text_list(self.state_history)
        if not history:
            history = [state]
        object.__setattr__(self, "state_history", history)
        object.__setattr__(self, "blocked_by_gate_kinds", _copy_text_list(self.blocked_by_gate_kinds))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if self.state_history[-1] != self.state:
            raise ValueError("state_history must end with the current state")
        if self.state in TERMINAL_PROMOTION_STATES and self.blocked_by_gate_kinds and self.state == "promoted":
            raise ValueError("promoted state may not remain blocked by gate kinds")

    def transition(
        self,
        *,
        next_state: str,
        transitioned_at: str,
        event_label: str,
        reason: str,
        gate_results: Optional[Sequence[GateResult]] = None,
        evidence: Optional[PromotionEvidence] = None,
        blocked_by_gate_kinds: Optional[Sequence[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "PromotionRecord":
        next_state = _require_text(next_state, "next_state")
        if next_state not in ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"illegal promotion transition: {self.state} -> {next_state}")
        merged_gate_results = list(gate_results) if gate_results is not None else list(self.gate_results)
        merged_evidence = evidence if evidence is not None else self.evidence
        merged_blocked = (
            _copy_text_list(blocked_by_gate_kinds)
            if blocked_by_gate_kinds is not None
            else list(self.blocked_by_gate_kinds)
        )
        merged_metadata = dict(self.metadata)
        merged_metadata.update(dict(metadata or {}))
        return PromotionRecord(
            record_id=self.record_id,
            target_id=self.target_id,
            candidate_id=self.candidate_id,
            state=next_state,
            created_at=self.created_at,
            updated_at=_require_text(transitioned_at, "transitioned_at"),
            transition_event=event_label,
            transition_reason=reason,
            gate_results=merged_gate_results,
            evidence=merged_evidence,
            state_history=self.state_history + [next_state],
            blocked_by_gate_kinds=merged_blocked,
            metadata=merged_metadata,
        )

    def mark_evaluated(self, *, transitioned_at: str, reason: str, evidence: PromotionEvidence) -> "PromotionRecord":
        return self.transition(
            next_state="evaluated",
            transitioned_at=transitioned_at,
            event_label="evaluation_attached",
            reason=reason,
            evidence=evidence,
        )

    def move_to_frontier(self, *, transitioned_at: str, reason: str) -> "PromotionRecord":
        return self.transition(
            next_state="frontier",
            transitioned_at=transitioned_at,
            event_label="retained_on_frontier",
            reason=reason,
        )

    def move_to_gated(
        self,
        *,
        transitioned_at: str,
        reason: str,
        gate_results: Sequence[GateResult],
        blocked_by_gate_kinds: Optional[Sequence[str]] = None,
    ) -> "PromotionRecord":
        return self.transition(
            next_state="gated",
            transitioned_at=transitioned_at,
            event_label="gates_executed",
            reason=reason,
            gate_results=gate_results,
            blocked_by_gate_kinds=blocked_by_gate_kinds or [],
        )

    def move_to_promotable(self, *, transitioned_at: str, reason: str, gate_results: Sequence[GateResult]) -> "PromotionRecord":
        return self.transition(
            next_state="promotable",
            transitioned_at=transitioned_at,
            event_label="gates_passed",
            reason=reason,
            gate_results=gate_results,
            blocked_by_gate_kinds=[],
        )

    def reject(self, *, transitioned_at: str, reason: str, gate_results: Optional[Sequence[GateResult]] = None) -> "PromotionRecord":
        blocked = [result.gate_kind for result in gate_results or self.gate_results if result.status != "pass"]
        return self.transition(
            next_state="rejected",
            transitioned_at=transitioned_at,
            event_label="promotion_rejected",
            reason=reason,
            gate_results=gate_results,
            blocked_by_gate_kinds=blocked,
        )

    def promote(self, *, transitioned_at: str, reason: str) -> "PromotionRecord":
        return self.transition(
            next_state="promoted",
            transitioned_at=transitioned_at,
            event_label="candidate_promoted",
            reason=reason,
            blocked_by_gate_kinds=[],
        )

    def archive(self, *, transitioned_at: str, reason: str) -> "PromotionRecord":
        return self.transition(
            next_state="archived",
            transitioned_at=transitioned_at,
            event_label="candidate_archived",
            reason=reason,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transition_event": self.transition_event,
            "transition_reason": self.transition_reason,
            "gate_results": [item.to_dict() for item in self.gate_results],
            "evidence": self.evidence.to_dict(),
            "state_history": list(self.state_history),
            "blocked_by_gate_kinds": list(self.blocked_by_gate_kinds),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PromotionRecord":
        return PromotionRecord(
            record_id=data.get("record_id") or data.get("id") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            state=data.get("state") or "",
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
            transition_event=data.get("transition_event") or "",
            transition_reason=data.get("transition_reason") or "",
            gate_results=[GateResult.from_dict(item) for item in data.get("gate_results") or []],
            evidence=PromotionEvidence.from_dict(data.get("evidence") or {}),
            state_history=list(data.get("state_history") or []),
            blocked_by_gate_kinds=list(data.get("blocked_by_gate_kinds") or []),
            metadata=dict(data.get("metadata") or {}),
        )


def build_promotion_evidence(
    *,
    evidence_id: str,
    target_id: str,
    candidate_id: str,
    evaluations: Sequence[EvaluationRecord],
    gate_results: Sequence[GateResult],
    metadata: Optional[Mapping[str, Any]] = None,
) -> PromotionEvidence:
    raw_refs: List[ArtifactRef] = []
    for evaluation in evaluations:
        raw_refs.extend(evaluation.raw_evidence_refs)
    gate_refs: List[ArtifactRef] = []
    for result in gate_results:
        gate_refs.extend(result.evidence_refs)
    return PromotionEvidence(
        evidence_id=evidence_id,
        target_id=target_id,
        candidate_id=candidate_id,
        evaluation_ids=[evaluation.evaluation_id for evaluation in evaluations],
        raw_evidence_refs=raw_refs,
        gate_evidence_refs=gate_refs,
        metadata=dict(metadata or {}),
    )


def build_promotion_evidence_summary(
    *,
    summary_id: str,
    candidate_id: str,
    benchmark_manifest: Optional[BenchmarkRunManifest] = None,
    comparison_results: Sequence[CandidateComparisonResult] | None = None,
    evaluation_suite: Optional[EvaluationSuiteManifest] = None,
    objective_suite: Optional[ObjectiveSuiteManifest] = None,
    target_family: Optional[TargetFamilyManifest] = None,
    family_composition: Optional[FamilyCompositionManifest] = None,
    search_space: Optional[SearchSpaceManifest] = None,
    objective_breakdown_results: Sequence[ObjectiveBreakdownResult] | None = None,
    review_required: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
) -> PromotionEvidenceSummary:
    comparison_results = list(comparison_results or [])
    objective_breakdown_results = list(objective_breakdown_results or [])
    manifests = [benchmark_manifest] if benchmark_manifest is not None else []
    outcome_counts: Dict[str, int] = {}
    comparison_ids: List[str] = []
    held_out_sample_ids: List[str] = []
    observed_trial_count = 0
    for comparison in comparison_results:
        comparison_ids.append(comparison.comparison_id)
        held_out_sample_ids.extend(comparison.held_out_sample_ids)
        outcome_counts[comparison.outcome] = outcome_counts.get(comparison.outcome, 0) + 1
        observed_trial_count = max(observed_trial_count, int(comparison.trial_count))
    regression_ids: List[str] = []
    compared_regression_ids: List[str] = []
    minimum_required_trials: Optional[int] = None
    stochasticity_class: Optional[str] = None
    manifest_ids: List[str] = []
    family_bucket_ids: List[str] = []
    hidden_hold_bucket_ids: List[str] = []
    regression_bucket_ids: List[str] = []
    for manifest in manifests:
        manifest_ids.append(manifest.manifest_id)
        stochasticity_class = manifest.stochasticity_class
        minimum_required_trials = int(manifest.rerun_policy.get("max_trials") or 1)
        for bucket_values in manifest.bucket_tags.values():
            family_bucket_ids.extend(bucket_values)
        for split in manifest.splits:
            if split.split_name == "regression":
                regression_ids.extend(split.sample_ids)
                for sample_id in split.sample_ids:
                    regression_bucket_ids.extend(manifest.bucket_tags.get(sample_id) or [])
            if split.visibility == "hidden_hold":
                for sample_id in split.sample_ids:
                    hidden_hold_bucket_ids.extend(manifest.bucket_tags.get(sample_id) or [])
        for comparison in comparison_results:
            compared_regression_ids.extend(
                sample_id for sample_id in comparison.compared_sample_ids if sample_id in set(regression_ids)
            )
    objective_breakdown_status = "complete" if objective_breakdown_results else None
    if any(result.blocked_components for result in objective_breakdown_results):
        objective_breakdown_status = "partial"
    member_family_coverage: Dict[str, Any] = {}
    composition_ids: List[str] = []
    member_family_ids: List[str] = []
    coupling_risk_summary: Dict[str, Any] = {}
    transfer_slices: List[TransferSliceManifest] = []
    if benchmark_manifest is not None:
        transfer_slices.extend(list(benchmark_manifest.transfer_slices))
        legacy_ids = _copy_text_list((benchmark_manifest.metadata or {}).get("transfer_slice_ids"))
        legacy_ids.extend(_copy_text_list((benchmark_manifest.promotion_relevance or {}).get("transfer_slice_ids")))
        seen_slice_ids = {item.slice_id for item in transfer_slices}
        for slice_id in legacy_ids:
            if slice_id in seen_slice_ids:
                continue
            transfer_slices.append(
                TransferSliceManifest(
                    slice_id=slice_id,
                    slice_kind=_infer_transfer_slice_kind(slice_id),
                    selector={"legacy_transfer_slice_id": slice_id},
                    promotion_role="claim_supporting",
                    visibility="comparison_visible",
                    metadata={"legacy_bridge": True},
                )
            )
    transfer_slice_ids = sorted({item.slice_id for item in transfer_slices})
    if family_composition is not None:
        composition_ids = [family_composition.composition_id]
        member_family_ids = list(family_composition.member_family_ids)
        member_family_breakdown_keys = {
            family_id
            for result in objective_breakdown_results
            for family_id in result.member_family_breakdowns
        }
        for family_id in member_family_ids:
            member_data: Dict[str, Any] = {"present": family_id in member_family_breakdown_keys}
            for result in objective_breakdown_results:
                if family_id in result.member_family_breakdowns:
                    member_data["objective_keys"] = sorted(result.member_family_breakdowns[family_id].keys())
                    break
            member_family_coverage[family_id] = member_data
        coupling_risk_summary = {
            "composition_kind": family_composition.composition_kind,
            "cross_family_invariants": list(family_composition.cross_family_invariants),
            "coupled_loci_groups": sorted((search_space.coupled_loci_groups or {}).keys()) if search_space is not None else [],
            "stage_partitions": sorted((search_space.stage_partitions or {}).keys()) if search_space is not None else [],
            "review_class": family_composition.review_class,
        }
    return PromotionEvidenceSummary(
        summary_id=summary_id,
        candidate_id=candidate_id,
        comparison_ids=comparison_ids,
        manifest_ids=manifest_ids,
        held_out_sample_ids=held_out_sample_ids,
        regression_sample_ids=regression_ids,
        compared_regression_sample_ids=compared_regression_ids,
        stochasticity_class=stochasticity_class,
        minimum_required_trials=minimum_required_trials,
        observed_trial_count=observed_trial_count or None,
        outcome_counts=outcome_counts,
        evaluation_suite_ids=[evaluation_suite.suite_id] if evaluation_suite is not None else [],
        objective_suite_ids=[objective_suite.suite_id] if objective_suite is not None else [],
        target_family_ids=[target_family.family_id] if target_family is not None else [],
        composition_ids=composition_ids,
        member_family_ids=member_family_ids,
        search_space_ids=[search_space.search_space_id] if search_space is not None else [],
        objective_breakdown_result_ids=[result.result_id for result in objective_breakdown_results],
        family_bucket_ids=family_bucket_ids,
        hidden_hold_bucket_ids=hidden_hold_bucket_ids,
        regression_bucket_ids=regression_bucket_ids,
        applicability_scope={
            "manifest_ids": manifest_ids,
            "family_ids": [target_family.family_id] if target_family is not None else [],
            "composition_ids": composition_ids,
            "member_family_ids": member_family_ids,
            "target_ids": list(target_family.target_ids) if target_family is not None else [],
            "mutable_loci_ids": list(target_family.mutable_loci_ids) if target_family is not None else [],
        },
        family_risk_summary={
            "review_required": review_required,
            "review_class": (
                target_family.review_class
                if target_family is not None
                else (family_composition.review_class if family_composition is not None else None)
            ),
            "multi_locus_change": bool(target_family and len(target_family.mutable_loci_ids) > 1),
            "composed_family": family_composition is not None,
            "member_family_count": len(member_family_ids),
            "hidden_hold_covered": bool(held_out_sample_ids),
            "regression_covered": bool(regression_ids and compared_regression_ids),
            "transfer_slice_ids": transfer_slice_ids,
        },
        member_family_coverage=member_family_coverage,
        coupling_risk_summary=coupling_risk_summary,
        transfer_slice_ids=transfer_slice_ids,
        transfer_slices=transfer_slices,
        model_tier_audit=_copy_mapping(
            (
                ((benchmark_manifest.promotion_relevance or {}).get("mini_escalation_audit"))
                if benchmark_manifest is not None
                else {}
            )
        ),
        review_class=(
            target_family.review_class
            if target_family is not None
            else (family_composition.review_class if family_composition is not None else None)
        ),
        objective_breakdown_status=objective_breakdown_status,
        review_required=review_required,
        metadata=dict(metadata or {}),
    )


def create_promotion_record(
    *,
    record_id: str,
    target_id: str,
    candidate_id: str,
    created_at: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> PromotionRecord:
    return PromotionRecord(
        record_id=record_id,
        target_id=target_id,
        candidate_id=candidate_id,
        state="draft",
        created_at=created_at,
        updated_at=created_at,
        transition_event="record_created",
        transition_reason="candidate entered the promotion workflow",
        evidence=PromotionEvidence(
            evidence_id=f"evidence.{candidate_id}.draft",
            target_id=target_id,
            candidate_id=candidate_id,
        ),
        metadata=dict(metadata or {}),
    )


def evaluate_replay_gate(gate_input: ReplayConformanceGateInput) -> GateResult:
    target = gate_input.target
    evaluation = gate_input.evaluation
    requires_replay = bool(target.support_envelope.assumptions.get("requires_replay_gate"))
    evidence_refs = _find_diagnostic_evidence(evaluation, "replay_gate")
    compatibility = dict(evaluation.evaluation_input_compatibility)
    evaluator_modes = {bundle.evaluator_mode for bundle in evaluation.normalized_diagnostics}
    if not requires_replay:
        return GateResult(
            gate_id=f"gate.replay.{evaluation.evaluation_id}",
            gate_kind="replay",
            status="not_applicable",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="target does not require replay gating for promotion",
            evidence_refs=evidence_refs,
            metadata={"requires_replay_gate": False},
        )
    if compatibility.get("replay") is not True:
        return GateResult(
            gate_id=f"gate.replay.{evaluation.evaluation_id}",
            gate_kind="replay",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="replay gating is required but evaluation_input_compatibility.replay is not true",
            evidence_refs=evidence_refs,
            metadata={"requires_replay_gate": True},
        )
    if "replay" not in evaluator_modes and "hybrid" not in evaluator_modes:
        return GateResult(
            gate_id=f"gate.replay.{evaluation.evaluation_id}",
            gate_kind="replay",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="replay gating is required but no replay-capable evaluator mode was recorded",
            evidence_refs=evidence_refs,
            metadata={"evaluator_modes": sorted(evaluator_modes)},
        )
    replay_entry_status = evaluation.gate_results.get("replay_gate_green")
    if replay_entry_status is True:
        return GateResult(
            gate_id=f"gate.replay.{evaluation.evaluation_id}",
            gate_kind="replay",
            status="pass",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="replay gate evidence is present and the recorded replay gate result is green",
            evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
            metadata={"evaluation_mode": sorted(evaluator_modes)},
        )
    if replay_entry_status is False:
        return GateResult(
            gate_id=f"gate.replay.{evaluation.evaluation_id}",
            gate_kind="replay",
            status="fail",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="replay gate evidence is present and the recorded replay gate result is negative",
            evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
            metadata={"evaluation_mode": sorted(evaluator_modes)},
        )
    return GateResult(
        gate_id=f"gate.replay.{evaluation.evaluation_id}",
        gate_kind="replay",
        status="insufficient_evidence",
        target_id=target.target_id,
        candidate_id=evaluation.candidate_id,
        reason="replay gating is required but replay gate outcome was not recorded",
        evidence_refs=evidence_refs,
        metadata={"evaluation_mode": sorted(evaluator_modes)},
    )


def evaluate_conformance_gate(gate_input: ReplayConformanceGateInput) -> GateResult:
    target = gate_input.target
    evaluation = gate_input.evaluation
    compatibility = dict(evaluation.evaluation_input_compatibility)
    requires_conformance = bool(
        target.support_envelope.assumptions.get("requires_replay_gate")
        or compatibility.get("conformance_bundle")
        or compatibility.get("schema") is not None
    )
    evidence_refs = _find_diagnostic_evidence(evaluation, "conformance_gate")
    if not requires_conformance:
        return GateResult(
            gate_id=f"gate.conformance.{evaluation.evaluation_id}",
            gate_kind="conformance",
            status="not_applicable",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="no conformance bundle or schema compatibility requirement is declared",
            evidence_refs=evidence_refs,
            metadata={"requires_conformance": False},
        )
    if compatibility.get("schema") is None or not compatibility.get("conformance_bundle"):
        return GateResult(
            gate_id=f"gate.conformance.{evaluation.evaluation_id}",
            gate_kind="conformance",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="conformance gating is required but schema or conformance bundle metadata is missing",
            evidence_refs=evidence_refs,
            metadata=compatibility,
        )
    result = evaluation.gate_results.get("conformance_gate_green")
    if result is True:
        return GateResult(
            gate_id=f"gate.conformance.{evaluation.evaluation_id}",
            gate_kind="conformance",
            status="pass",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="conformance requirements are declared and the recorded conformance gate is green",
            evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
            metadata=compatibility,
        )
    if result is False:
        return GateResult(
            gate_id=f"gate.conformance.{evaluation.evaluation_id}",
            gate_kind="conformance",
            status="fail",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="conformance requirements are declared and the recorded conformance gate is negative",
            evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
            metadata=compatibility,
        )
    return GateResult(
        gate_id=f"gate.conformance.{evaluation.evaluation_id}",
        gate_kind="conformance",
        status="insufficient_evidence",
        target_id=target.target_id,
        candidate_id=evaluation.candidate_id,
        reason="conformance gating is required but no conformance gate outcome was recorded",
        evidence_refs=evidence_refs,
        metadata=compatibility,
    )


def evaluate_support_envelope_gate(gate_input: SupportEnvelopeGateInput) -> GateResult:
    target = gate_input.target
    evaluation = gate_input.evaluation
    materialized = gate_input.materialized_candidate
    expansion = _envelope_expansion_details(target.support_envelope, materialized.support_envelope)
    evidence_refs = _find_diagnostic_evidence(evaluation, "support_envelope_diff")
    if expansion:
        return GateResult(
            gate_id=f"gate.support_envelope.{evaluation.evaluation_id}",
            gate_kind="support_envelope",
            status="fail",
            target_id=target.target_id,
            candidate_id=evaluation.candidate_id,
            reason="candidate widened the declared support envelope and cannot be promoted",
            evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
            metadata={"expansion": expansion, "allow_narrowing": gate_input.allow_narrowing},
        )
    return GateResult(
        gate_id=f"gate.support_envelope.{evaluation.evaluation_id}",
        gate_kind="support_envelope",
        status="pass",
        target_id=target.target_id,
        candidate_id=evaluation.candidate_id,
        reason="candidate preserved the declared support envelope for promotion purposes",
        evidence_refs=evidence_refs or list(evaluation.raw_evidence_refs),
        metadata={"allow_narrowing": gate_input.allow_narrowing},
    )


def evaluate_comparison_gate(
    *,
    target: OptimizationTarget,
    candidate_id: str,
    benchmark_manifest: BenchmarkRunManifest,
    comparison_results: Sequence[CandidateComparisonResult],
) -> GateResult:
    comparison_results = [item for item in comparison_results if item.child_candidate_id == candidate_id]
    if not comparison_results:
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="no benchmark comparison evidence was recorded for this candidate",
            evidence_refs=[],
            metadata={"manifest_id": benchmark_manifest.manifest_id},
        )
    evidence_refs: List[ArtifactRef] = []
    compared_ids: set[str] = set()
    outcomes: set[str] = set()
    max_trial_count = 0
    for comparison in comparison_results:
        evidence_refs.extend(comparison.evidence_refs)
        compared_ids.update(comparison.compared_sample_ids)
        outcomes.add(comparison.outcome)
        max_trial_count = max(max_trial_count, comparison.trial_count)

    regression_ids = {
        sample_id
        for split in benchmark_manifest.splits
        if split.split_name == "regression"
        for sample_id in split.sample_ids
    }
    missing_regressions = sorted(regression_ids - compared_ids)
    minimum_required_trials = int(benchmark_manifest.rerun_policy.get("max_trials") or 1)
    metadata = {
        "manifest_id": benchmark_manifest.manifest_id,
        "stochasticity_class": benchmark_manifest.stochasticity_class,
        "minimum_required_trials": minimum_required_trials,
        "observed_trial_count": max_trial_count,
        "missing_regression_sample_ids": missing_regressions,
        "outcomes": sorted(outcomes),
    }
    if missing_regressions:
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="comparison evidence is missing one or more regression samples required for promotion",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if benchmark_manifest.stochasticity_class != "deterministic" and max_trial_count < minimum_required_trials:
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="stochastic benchmark evidence did not satisfy the minimum rerun policy for promotion",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if "blocked" in outcomes:
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="comparison evidence is blocked and does not yet support promotion",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if "inconclusive" in outcomes or "tie" in outcomes:
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="comparison evidence is inconclusive for promotion purposes",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    for comparison in comparison_results:
        if comparison.outcome == "loss" or (
            comparison.outcome == "win" and comparison.better_candidate_id != candidate_id
        ):
            return GateResult(
                gate_id=f"gate.comparison.{candidate_id}",
                gate_kind="comparison",
                status="fail",
                target_id=target.target_id,
                candidate_id=candidate_id,
                reason="comparison evidence indicates the candidate is not better than its parent baseline",
                evidence_refs=evidence_refs,
                metadata=metadata,
            )
    if any(comparison.outcome in {"win", "non_inferior"} for comparison in comparison_results):
        return GateResult(
            gate_id=f"gate.comparison.{candidate_id}",
            gate_kind="comparison",
            status="pass",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="comparison evidence supports promotion on held-out and regression coverage",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    return GateResult(
        gate_id=f"gate.comparison.{candidate_id}",
        gate_kind="comparison",
        status="insufficient_evidence",
        target_id=target.target_id,
        candidate_id=candidate_id,
        reason="comparison evidence did not produce a promotable outcome",
        evidence_refs=evidence_refs,
        metadata=metadata,
    )


def evaluate_family_promotion_gate(
    *,
    target: OptimizationTarget,
    candidate_id: str,
    benchmark_manifest: BenchmarkRunManifest,
    comparison_results: Sequence[CandidateComparisonResult],
    evaluation_suite: Optional[EvaluationSuiteManifest] = None,
    objective_suite: Optional[ObjectiveSuiteManifest] = None,
    target_family: Optional[TargetFamilyManifest] = None,
    family_composition: Optional[FamilyCompositionManifest] = None,
    search_space: Optional[SearchSpaceManifest] = None,
    objective_breakdown_results: Sequence[ObjectiveBreakdownResult] | None = None,
) -> GateResult:
    objective_breakdown_results = list(objective_breakdown_results or [])
    evidence_refs: List[ArtifactRef] = []
    for comparison in comparison_results:
        evidence_refs.extend(comparison.evidence_refs)
    for result in objective_breakdown_results:
        evidence_refs.extend(result.artifact_refs)
    metadata = {
        "manifest_id": benchmark_manifest.manifest_id,
        "evaluation_suite_id": evaluation_suite.suite_id if evaluation_suite is not None else None,
        "objective_suite_id": objective_suite.suite_id if objective_suite is not None else None,
        "target_family_id": target_family.family_id if target_family is not None else None,
        "composition_id": family_composition.composition_id if family_composition is not None else None,
        "search_space_id": search_space.search_space_id if search_space is not None else None,
    }
    if evaluation_suite is None or objective_suite is None or search_space is None or (
        target_family is None and family_composition is None
    ):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level promotion requires declared suite and family or composition manifests",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if target_family is not None and target.target_id not in set(target_family.target_ids):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="fail",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level applicability scope does not include the optimization target",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if target_family is not None and search_space.family_id != target_family.family_id:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="fail",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="search space does not match the declared target family",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if family_composition is not None:
        if search_space.composition_id != family_composition.composition_id:
            return GateResult(
                gate_id=f"gate.family_promotion.{candidate_id}",
                gate_kind="family_promotion",
                status="fail",
                target_id=target.target_id,
                candidate_id=candidate_id,
                reason="search space does not match the declared family composition",
                evidence_refs=evidence_refs,
                metadata=metadata,
            )
        if benchmark_manifest.metadata.get("composition_id") != family_composition.composition_id:
            return GateResult(
                gate_id=f"gate.family_promotion.{candidate_id}",
                gate_kind="family_promotion",
                status="fail",
                target_id=target.target_id,
                candidate_id=candidate_id,
                reason="benchmark manifest does not match the declared family composition",
                evidence_refs=evidence_refs,
                metadata=metadata,
            )
    if objective_suite.evaluation_suite_id != evaluation_suite.suite_id:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="fail",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="objective suite does not match the declared evaluation suite",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    candidate_breakdowns = [result for result in objective_breakdown_results if result.candidate_id == candidate_id]
    if not candidate_breakdowns:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level promotion requires candidate objective breakdown evidence",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if any(result.blocked_components for result in candidate_breakdowns):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="objective breakdown evidence is partially blocked for the candidate",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if any(result.cross_family_blocked_components for result in candidate_breakdowns):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="cross-family objective breakdown evidence is partially blocked for the candidate",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if family_composition is not None:
        covered_member_ids = {
            family_id
            for result in candidate_breakdowns
            for family_id in result.member_family_breakdowns
        }
        missing_member_ids = sorted(set(family_composition.member_family_ids) - covered_member_ids)
        metadata["missing_member_family_ids"] = missing_member_ids
        if missing_member_ids:
            return GateResult(
                gate_id=f"gate.family_promotion.{candidate_id}",
                gate_kind="family_promotion",
                status="insufficient_evidence",
                target_id=target.target_id,
                candidate_id=candidate_id,
                reason="family composition promotion requires explicit member-family coverage",
                evidence_refs=evidence_refs,
                metadata=metadata,
            )
    candidate_comparisons = [item for item in comparison_results if item.child_candidate_id == candidate_id]
    compared_ids = {
        sample_id
        for comparison in candidate_comparisons
        for sample_id in comparison.compared_sample_ids
    }
    regression_ids = {
        sample_id
        for split in benchmark_manifest.splits
        if split.split_name == "regression"
        for sample_id in split.sample_ids
    }
    hidden_hold_ids = set(benchmark_manifest.hidden_hold_sample_ids())
    if not regression_ids.issubset(compared_ids):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level promotion is missing regression coverage",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if not hidden_hold_ids.issubset(compared_ids):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level promotion is missing hidden-hold coverage",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    outcomes = {comparison.outcome for comparison in candidate_comparisons}
    if "blocked" in outcomes:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level comparison outcome is blocked",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if "inconclusive" in outcomes or "tie" in outcomes:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level comparison outcome is inconclusive",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if any(comparison.outcome == "loss" for comparison in candidate_comparisons):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="fail",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level comparison evidence indicates a loss against the baseline",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    review_class = (
        target_family.review_class
        if target_family is not None
        else (family_composition.review_class if family_composition is not None else None)
    )
    review_required = bool((benchmark_manifest.promotion_relevance or {}).get("requires_support_sensitive_review"))
    if review_class in {"support_honesty", "support_sensitive_coding_overlay"} or review_required:
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="insufficient_evidence",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="review-heavy family or composition still requires explicit review for promotion",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    if any(comparison.outcome in {"win", "non_inferior"} for comparison in candidate_comparisons):
        return GateResult(
            gate_id=f"gate.family_promotion.{candidate_id}",
            gate_kind="family_promotion",
            status="pass",
            target_id=target.target_id,
            candidate_id=candidate_id,
            reason="family-level comparison and objective breakdown evidence support promotion",
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
    return GateResult(
        gate_id=f"gate.family_promotion.{candidate_id}",
        gate_kind="family_promotion",
        status="insufficient_evidence",
        target_id=target.target_id,
        candidate_id=candidate_id,
        reason="family-level evidence did not produce a promotable outcome",
        evidence_refs=evidence_refs,
        metadata=metadata,
    )


def evaluate_promotion_gates(
    *,
    target: OptimizationTarget,
    materialized_candidate: MaterializedCandidate,
    evaluation: EvaluationRecord,
    prior_state: Optional[PromotionRecord] = None,
    benchmark_manifest: Optional[BenchmarkRunManifest] = None,
    comparison_results: Optional[Sequence[CandidateComparisonResult]] = None,
    evaluation_suite: Optional[EvaluationSuiteManifest] = None,
    objective_suite: Optional[ObjectiveSuiteManifest] = None,
    target_family: Optional[TargetFamilyManifest] = None,
    family_composition: Optional[FamilyCompositionManifest] = None,
    search_space: Optional[SearchSpaceManifest] = None,
    objective_breakdown_results: Optional[Sequence[ObjectiveBreakdownResult]] = None,
) -> List[GateResult]:
    replay_input = ReplayConformanceGateInput(
        target=target,
        materialized_candidate=materialized_candidate,
        evaluation=evaluation,
        prior_state=prior_state,
    )
    support_input = SupportEnvelopeGateInput(
        target=target,
        materialized_candidate=materialized_candidate,
        evaluation=evaluation,
    )
    gate_results = [
        evaluate_replay_gate(replay_input),
        evaluate_conformance_gate(replay_input),
        evaluate_support_envelope_gate(support_input),
    ]
    if benchmark_manifest is not None:
        gate_results.append(
            evaluate_comparison_gate(
                target=target,
                candidate_id=materialized_candidate.candidate_id,
                benchmark_manifest=benchmark_manifest,
                comparison_results=comparison_results or [],
            )
        )
    if benchmark_manifest is not None and (
        evaluation_suite is not None
        or objective_suite is not None
        or target_family is not None
        or search_space is not None
        or objective_breakdown_results
    ):
        gate_results.append(
            evaluate_family_promotion_gate(
                target=target,
                candidate_id=materialized_candidate.candidate_id,
                benchmark_manifest=benchmark_manifest,
                comparison_results=comparison_results or [],
                evaluation_suite=evaluation_suite,
                objective_suite=objective_suite,
                target_family=target_family,
                family_composition=family_composition,
                search_space=search_space,
                objective_breakdown_results=objective_breakdown_results or [],
            )
        )
    return gate_results


def promote_candidate(
    *,
    record_id: str,
    target: OptimizationTarget,
    materialized_candidate: MaterializedCandidate,
    evaluation: EvaluationRecord,
    created_at: str,
    gated_at: str,
    benchmark_manifest: Optional[BenchmarkRunManifest] = None,
    comparison_results: Optional[Sequence[CandidateComparisonResult]] = None,
    evaluation_suite: Optional[EvaluationSuiteManifest] = None,
    objective_suite: Optional[ObjectiveSuiteManifest] = None,
    target_family: Optional[TargetFamilyManifest] = None,
    family_composition: Optional[FamilyCompositionManifest] = None,
    search_space: Optional[SearchSpaceManifest] = None,
    objective_breakdown_results: Optional[Sequence[ObjectiveBreakdownResult]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[PromotionRecord, PromotionDecision]:
    record = create_promotion_record(
        record_id=record_id,
        target_id=target.target_id,
        candidate_id=materialized_candidate.candidate_id,
        created_at=created_at,
        metadata=metadata,
    )
    seed_evidence = build_promotion_evidence(
        evidence_id=f"evidence.{materialized_candidate.candidate_id}.evaluated",
        target_id=target.target_id,
        candidate_id=materialized_candidate.candidate_id,
        evaluations=[evaluation],
        gate_results=[],
    )
    record = record.mark_evaluated(
        transitioned_at=created_at,
        reason="evaluation evidence attached to candidate",
        evidence=seed_evidence,
    )
    record = record.move_to_frontier(
        transitioned_at=gated_at,
        reason="candidate retained on the reflective Pareto frontier pending safety gates",
    )
    gate_results = evaluate_promotion_gates(
        target=target,
        materialized_candidate=materialized_candidate,
        evaluation=evaluation,
        prior_state=record,
        benchmark_manifest=benchmark_manifest,
        comparison_results=comparison_results,
        evaluation_suite=evaluation_suite,
        objective_suite=objective_suite,
        target_family=target_family,
        family_composition=family_composition,
        search_space=search_space,
        objective_breakdown_results=objective_breakdown_results,
    )
    evidence_summary = None
    if benchmark_manifest is not None:
        evidence_summary = build_promotion_evidence_summary(
            summary_id=f"evidence_summary.{materialized_candidate.candidate_id}",
            candidate_id=materialized_candidate.candidate_id,
            benchmark_manifest=benchmark_manifest,
            comparison_results=comparison_results or [],
            evaluation_suite=evaluation_suite,
            objective_suite=objective_suite,
            target_family=target_family,
            family_composition=family_composition,
            search_space=search_space,
            objective_breakdown_results=objective_breakdown_results or [],
            review_required=bool((benchmark_manifest.promotion_relevance or {}).get("requires_support_sensitive_review")),
            metadata={"phase": "v1_5" if family_composition is None else "v3"},
        )
    blocked = [result.gate_kind for result in gate_results if result.status != "pass" and result.status != "not_applicable"]
    evidence = build_promotion_evidence(
        evidence_id=f"evidence.{materialized_candidate.candidate_id}.gated",
        target_id=target.target_id,
        candidate_id=materialized_candidate.candidate_id,
        evaluations=[evaluation],
        gate_results=gate_results,
        metadata=(
            {"evidence_summary": evidence_summary.to_dict()}
            if evidence_summary is not None
            else {}
        ),
    )
    record = record.move_to_gated(
        transitioned_at=gated_at,
        reason="promotion gates executed for candidate",
        gate_results=gate_results,
        blocked_by_gate_kinds=blocked,
    )
    if blocked:
        next_state = "frontier" if all(result.status == "insufficient_evidence" for result in gate_results if result.gate_kind in blocked) else "rejected"
        reason = (
            "candidate remains on the frontier because required promotion evidence is incomplete"
            if next_state == "frontier"
            else "candidate failed one or more required promotion gates"
        )
        record = record.transition(
            next_state=next_state,
            transitioned_at=gated_at,
            event_label="promotion_blocked",
            reason=reason,
            gate_results=gate_results,
            evidence=evidence,
            blocked_by_gate_kinds=blocked,
        )
    else:
        record = record.move_to_promotable(
            transitioned_at=gated_at,
            reason="candidate passed replay, conformance, and support-envelope gates",
            gate_results=gate_results,
        )
        record = replace(record, evidence=evidence)

    decision = PromotionDecision(
        decision_id=f"decision.{materialized_candidate.candidate_id}",
        target_id=target.target_id,
        candidate_id=materialized_candidate.candidate_id,
        previous_state="gated",
        next_state=record.state,
        reason=record.transition_reason,
        event_label=record.transition_event,
        gate_results=gate_results,
        evidence=evidence,
        blocked_by_gate_kinds=blocked,
        metadata=dict(metadata or {}),
    )
    return record, decision
