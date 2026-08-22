from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .substrate import ArtifactRef


ALLOWED_WRONGNESS_CLASSES = {
    "correctness.result_mismatch",
    "correctness.missing_required_output",
    "policy.support_envelope_violation",
    "policy.invariant_violation",
    "replay.conformance_drift",
    "environment.tool_unavailable",
    "environment.profile_mismatch",
}


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _repair_locus_required(wrongness_class: str) -> bool:
    return wrongness_class in ALLOWED_WRONGNESS_CLASSES


@dataclass(frozen=True)
class WrongnessReport:
    wrongness_id: str
    wrongness_class: str
    failure_locus: str
    explanation: str
    confidence: float
    supporting_evidence_refs: List[ArtifactRef] = field(default_factory=list)
    likely_repair_locus: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "wrongness_id", _require_text(self.wrongness_id, "wrongness_id"))
        wrongness_class = _require_text(self.wrongness_class, "wrongness_class")
        if wrongness_class not in ALLOWED_WRONGNESS_CLASSES:
            raise ValueError(f"wrongness_class must be one of: {sorted(ALLOWED_WRONGNESS_CLASSES)}")
        object.__setattr__(self, "wrongness_class", wrongness_class)
        object.__setattr__(self, "failure_locus", _require_text(self.failure_locus, "failure_locus"))
        object.__setattr__(self, "explanation", _require_text(self.explanation, "explanation"))
        confidence = float(self.confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "supporting_evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.supporting_evidence_refs],
        )
        repair_locus = str(self.likely_repair_locus).strip() if self.likely_repair_locus else None
        if _repair_locus_required(wrongness_class) and not repair_locus:
            raise ValueError("likely_repair_locus must be non-empty for the current wrongness_class")
        object.__setattr__(self, "likely_repair_locus", repair_locus)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "wrongness_id": self.wrongness_id,
            "wrongness_class": self.wrongness_class,
            "failure_locus": self.failure_locus,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "supporting_evidence_refs": [item.to_dict() for item in self.supporting_evidence_refs],
            "metadata": dict(self.metadata),
        }
        if self.likely_repair_locus:
            payload["likely_repair_locus"] = self.likely_repair_locus
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "WrongnessReport":
        return WrongnessReport(
            wrongness_id=data.get("wrongness_id") or data.get("id") or "",
            wrongness_class=data.get("wrongness_class") or "",
            failure_locus=data.get("failure_locus") or "",
            explanation=data.get("explanation") or "",
            confidence=float(data.get("confidence") or 0.0),
            supporting_evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("supporting_evidence_refs") or []],
            likely_repair_locus=data.get("likely_repair_locus"),
            metadata=dict(data.get("metadata") or {}),
        )
