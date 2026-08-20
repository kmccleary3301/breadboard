from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .diagnostics import DiagnosticBundle
from .substrate import ArtifactRef, SupportEnvelope
from .wrongness import WrongnessReport


ALLOWED_EVALUATION_STATUSES = {"pending", "running", "completed", "failed", "error"}
ALLOWED_EVALUATION_OUTCOMES = {"passed", "failed", "inconclusive", "error", "rejected_by_gate"}


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    target_id: str
    candidate_id: str
    dataset_id: str
    dataset_version: str
    sample_id: str
    evaluator_id: str
    evaluator_version: str
    status: str
    outcome: str
    started_at: str
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    raw_evidence_refs: List[ArtifactRef] = field(default_factory=list)
    normalized_diagnostics: List[DiagnosticBundle] = field(default_factory=list)
    wrongness_reports: List[WrongnessReport] = field(default_factory=list)
    support_envelope_snapshot: SupportEnvelope = field(default_factory=SupportEnvelope)
    evaluation_input_compatibility: Dict[str, Any] = field(default_factory=dict)
    gate_results: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_id", _require_text(self.evaluation_id, "evaluation_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "dataset_id", _require_text(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "dataset_version", _require_text(self.dataset_version, "dataset_version"))
        object.__setattr__(self, "sample_id", _require_text(self.sample_id, "sample_id"))
        object.__setattr__(self, "evaluator_id", _require_text(self.evaluator_id, "evaluator_id"))
        object.__setattr__(self, "evaluator_version", _require_text(self.evaluator_version, "evaluator_version"))
        status = _require_text(self.status, "status").lower()
        if status not in ALLOWED_EVALUATION_STATUSES:
            raise ValueError(f"status must be one of: {sorted(ALLOWED_EVALUATION_STATUSES)}")
        object.__setattr__(self, "status", status)
        outcome = _require_text(self.outcome, "outcome").lower()
        if outcome not in ALLOWED_EVALUATION_OUTCOMES:
            raise ValueError(f"outcome must be one of: {sorted(ALLOWED_EVALUATION_OUTCOMES)}")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "started_at", _require_text(self.started_at, "started_at"))
        object.__setattr__(self, "completed_at", str(self.completed_at).strip() if self.completed_at else None)
        if self.duration_ms is not None and int(self.duration_ms) < 0:
            raise ValueError("duration_ms must be non-negative")
        object.__setattr__(self, "duration_ms", int(self.duration_ms) if self.duration_ms is not None else None)
        object.__setattr__(
            self,
            "raw_evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.raw_evidence_refs],
        )
        object.__setattr__(
            self,
            "normalized_diagnostics",
            [item if isinstance(item, DiagnosticBundle) else DiagnosticBundle.from_dict(item) for item in self.normalized_diagnostics],
        )
        object.__setattr__(
            self,
            "wrongness_reports",
            [item if isinstance(item, WrongnessReport) else WrongnessReport.from_dict(item) for item in self.wrongness_reports],
        )
        object.__setattr__(
            self,
            "support_envelope_snapshot",
            self.support_envelope_snapshot if isinstance(self.support_envelope_snapshot, SupportEnvelope) else SupportEnvelope.from_dict(self.support_envelope_snapshot),
        )
        object.__setattr__(self, "evaluation_input_compatibility", _copy_mapping(self.evaluation_input_compatibility))
        object.__setattr__(self, "gate_results", _copy_mapping(self.gate_results))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if self.status in {"completed", "failed", "error"} and not self.completed_at:
            raise ValueError("completed_at must be non-empty when evaluation status is terminal")
        if self.completed_at and self.duration_ms is None:
            raise ValueError("duration_ms must be set when completed_at is present")
        if not self.raw_evidence_refs:
            raise ValueError("raw_evidence_refs must contain at least one evidence ref")
        if not self.normalized_diagnostics:
            raise ValueError("normalized_diagnostics must contain at least one diagnostic bundle")

        for bundle in self.normalized_diagnostics:
            if bundle.evaluation_id != self.evaluation_id:
                raise ValueError("diagnostic bundle evaluation_id must match the evaluation record")

        if self.outcome == "passed" and self.wrongness_reports:
            raise ValueError("passed evaluations may not carry wrongness reports")

    def aggregate_outcome(self) -> str:
        if self.outcome == "error" or self.status == "error":
            return "error"
        if any(result is False for result in self.gate_results.values()):
            return "rejected_by_gate"
        if self.wrongness_reports:
            return "failed"
        severities = {entry.severity for bundle in self.normalized_diagnostics for entry in bundle.entries}
        if "error" in severities and self.outcome != "passed":
            return "failed"
        if self.outcome == "inconclusive":
            return "inconclusive"
        return "passed"

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "evaluation_id": self.evaluation_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "sample_id": self.sample_id,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "status": self.status,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "raw_evidence_refs": [item.to_dict() for item in self.raw_evidence_refs],
            "normalized_diagnostics": [item.to_dict() for item in self.normalized_diagnostics],
            "wrongness_reports": [item.to_dict() for item in self.wrongness_reports],
            "support_envelope_snapshot": self.support_envelope_snapshot.to_dict(),
            "evaluation_input_compatibility": dict(self.evaluation_input_compatibility),
            "gate_results": dict(self.gate_results),
            "metadata": dict(self.metadata),
        }
        if self.completed_at:
            payload["completed_at"] = self.completed_at
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EvaluationRecord":
        return EvaluationRecord(
            evaluation_id=data.get("evaluation_id") or data.get("id") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            dataset_id=data.get("dataset_id") or "",
            dataset_version=data.get("dataset_version") or "",
            sample_id=data.get("sample_id") or "",
            evaluator_id=data.get("evaluator_id") or "",
            evaluator_version=data.get("evaluator_version") or "",
            status=data.get("status") or "",
            outcome=data.get("outcome") or "",
            started_at=data.get("started_at") or "",
            completed_at=data.get("completed_at"),
            duration_ms=data.get("duration_ms"),
            raw_evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("raw_evidence_refs") or []],
            normalized_diagnostics=[DiagnosticBundle.from_dict(item) for item in data.get("normalized_diagnostics") or []],
            wrongness_reports=[WrongnessReport.from_dict(item) for item in data.get("wrongness_reports") or []],
            support_envelope_snapshot=SupportEnvelope.from_dict(data.get("support_envelope_snapshot") or {}),
            evaluation_input_compatibility=dict(data.get("evaluation_input_compatibility") or {}),
            gate_results=dict(data.get("gate_results") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
