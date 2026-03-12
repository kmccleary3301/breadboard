from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .evaluation import EvaluationRecord
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
ALLOWED_GATE_KINDS = {"replay", "conformance", "support_envelope"}
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


def evaluate_promotion_gates(
    *,
    target: OptimizationTarget,
    materialized_candidate: MaterializedCandidate,
    evaluation: EvaluationRecord,
    prior_state: Optional[PromotionRecord] = None,
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
    return [
        evaluate_replay_gate(replay_input),
        evaluate_conformance_gate(replay_input),
        evaluate_support_envelope_gate(support_input),
    ]


def promote_candidate(
    *,
    record_id: str,
    target: OptimizationTarget,
    materialized_candidate: MaterializedCandidate,
    evaluation: EvaluationRecord,
    created_at: str,
    gated_at: str,
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
    )
    blocked = [result.gate_kind for result in gate_results if result.status != "pass" and result.status != "not_applicable"]
    evidence = build_promotion_evidence(
        evidence_id=f"evidence.{materialized_candidate.candidate_id}.gated",
        target_id=target.target_id,
        candidate_id=materialized_candidate.candidate_id,
        evaluations=[evaluation],
        gate_results=gate_results,
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
