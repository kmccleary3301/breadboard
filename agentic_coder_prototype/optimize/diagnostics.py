from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .substrate import ArtifactRef


ALLOWED_DIAGNOSTIC_SEVERITIES = {"info", "warning", "error"}
ALLOWED_DETERMINISM_CLASSES = {
    "deterministic",
    "seeded_stochastic",
    "unseeded_stochastic",
    "environment_volatile",
}
ALLOWED_EVALUATOR_MODES = {"replay", "hybrid", "live"}


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class DiagnosticEntry:
    diagnostic_id: str
    kind: str
    severity: str
    message: str
    evidence_refs: List[ArtifactRef] = field(default_factory=list)
    locus_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic_id", _require_text(self.diagnostic_id, "diagnostic_id"))
        object.__setattr__(self, "kind", _require_text(self.kind, "kind"))
        severity = _require_text(self.severity, "severity").lower()
        if severity not in ALLOWED_DIAGNOSTIC_SEVERITIES:
            raise ValueError(f"severity must be one of: {sorted(ALLOWED_DIAGNOSTIC_SEVERITIES)}")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "message", _require_text(self.message, "message"))
        object.__setattr__(
            self,
            "evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.evidence_refs],
        )
        object.__setattr__(self, "locus_id", str(self.locus_id).strip() if self.locus_id else None)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "diagnostic_id": self.diagnostic_id,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "metadata": dict(self.metadata),
        }
        if self.locus_id:
            payload["locus_id"] = self.locus_id
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "DiagnosticEntry":
        return DiagnosticEntry(
            diagnostic_id=data.get("diagnostic_id") or data.get("id") or "",
            kind=data.get("kind") or "",
            severity=data.get("severity") or "",
            message=data.get("message") or "",
            evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("evidence_refs") or []],
            locus_id=data.get("locus_id"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class DiagnosticBundle:
    bundle_id: str
    evaluation_id: str
    evaluator_mode: str
    determinism_class: str
    entries: List[DiagnosticEntry] = field(default_factory=list)
    cache_identity: Dict[str, Any] = field(default_factory=dict)
    retry_policy_hint: Dict[str, Any] = field(default_factory=dict)
    reproducibility_notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", _require_text(self.bundle_id, "bundle_id"))
        object.__setattr__(self, "evaluation_id", _require_text(self.evaluation_id, "evaluation_id"))
        evaluator_mode = _require_text(self.evaluator_mode, "evaluator_mode").lower()
        if evaluator_mode not in ALLOWED_EVALUATOR_MODES:
            raise ValueError(f"evaluator_mode must be one of: {sorted(ALLOWED_EVALUATOR_MODES)}")
        object.__setattr__(self, "evaluator_mode", evaluator_mode)
        determinism_class = _require_text(self.determinism_class, "determinism_class").lower()
        if determinism_class not in ALLOWED_DETERMINISM_CLASSES:
            raise ValueError(f"determinism_class must be one of: {sorted(ALLOWED_DETERMINISM_CLASSES)}")
        object.__setattr__(self, "determinism_class", determinism_class)
        object.__setattr__(
            self,
            "entries",
            [item if isinstance(item, DiagnosticEntry) else DiagnosticEntry.from_dict(item) for item in self.entries],
        )
        object.__setattr__(self, "cache_identity", _copy_mapping(self.cache_identity))
        object.__setattr__(self, "retry_policy_hint", _copy_mapping(self.retry_policy_hint))
        object.__setattr__(self, "reproducibility_notes", _copy_mapping(self.reproducibility_notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if "key" not in self.cache_identity:
            raise ValueError("cache_identity must include a stable 'key'")
        if "version" not in self.cache_identity:
            raise ValueError("cache_identity must include a stable 'version'")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "evaluation_id": self.evaluation_id,
            "evaluator_mode": self.evaluator_mode,
            "determinism_class": self.determinism_class,
            "entries": [item.to_dict() for item in self.entries],
            "cache_identity": dict(self.cache_identity),
            "retry_policy_hint": dict(self.retry_policy_hint),
            "reproducibility_notes": dict(self.reproducibility_notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "DiagnosticBundle":
        return DiagnosticBundle(
            bundle_id=data.get("bundle_id") or data.get("id") or "",
            evaluation_id=data.get("evaluation_id") or "",
            evaluator_mode=data.get("evaluator_mode") or "",
            determinism_class=data.get("determinism_class") or "",
            entries=[DiagnosticEntry.from_dict(item) for item in data.get("entries") or []],
            cache_identity=dict(data.get("cache_identity") or {}),
            retry_policy_hint=dict(data.get("retry_policy_hint") or {}),
            reproducibility_notes=dict(data.get("reproducibility_notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
