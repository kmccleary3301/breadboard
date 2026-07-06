from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SUPPORT_LEVELS = {"not_started", "fixture_probe", "jsonl_probe", "live_probe", "supported"}


@dataclass(frozen=True)
class AdapterProbeReport:
    adapter_id: str
    adapter_kind: str
    support_level: str
    workload_family: str
    preserved_fields: list[str]
    lost_fields: list[str] = field(default_factory=list)
    unsupported_fields: list[str] = field(default_factory=list)
    source_artifacts: list[str] = field(default_factory=list)
    claim_boundary: str = "adapter_probe_not_production_integration"
    data_boundary: str = "fixture_or_probe_only"
    field_mapping: dict[str, str] = field(default_factory=dict)
    fidelity_notes: list[str] = field(default_factory=list)
    promotion_requirements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "support_level": self.support_level,
            "workload_family": self.workload_family,
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "unsupported_fields": list(self.unsupported_fields),
            "source_artifacts": list(self.source_artifacts),
            "claim_boundary": self.claim_boundary,
            "data_boundary": self.data_boundary,
            "field_mapping": dict(self.field_mapping),
            "fidelity_notes": list(self.fidelity_notes),
            "promotion_requirements": list(self.promotion_requirements),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "AdapterProbeReport":
        return AdapterProbeReport(
            adapter_id=str(data.get("adapter_id") or ""),
            adapter_kind=str(data.get("adapter_kind") or ""),
            support_level=str(data.get("support_level") or ""),
            workload_family=str(data.get("workload_family") or ""),
            preserved_fields=[str(item) for item in data.get("preserved_fields") or []],
            lost_fields=[str(item) for item in data.get("lost_fields") or []],
            unsupported_fields=[str(item) for item in data.get("unsupported_fields") or []],
            source_artifacts=[str(item) for item in data.get("source_artifacts") or []],
            claim_boundary=str(data.get("claim_boundary") or "adapter_probe_not_production_integration"),
            data_boundary=str(data.get("data_boundary") or "fixture_or_probe_only"),
            field_mapping={str(key): str(value) for key, value in dict(data.get("field_mapping") or {}).items()},
            fidelity_notes=[str(item) for item in data.get("fidelity_notes") or []],
            promotion_requirements=[str(item) for item in data.get("promotion_requirements") or []],
            metadata=dict(data.get("metadata") or {}),
        )


def validate_adapter_probe_report(report: AdapterProbeReport) -> list[str]:
    errors: list[str] = []
    for field_name in ["adapter_id", "adapter_kind", "support_level", "workload_family"]:
        if not str(getattr(report, field_name) or "").strip():
            errors.append(f"{field_name} must be non-empty")
    if report.support_level not in SUPPORT_LEVELS:
        errors.append(f"support_level must be one of {sorted(SUPPORT_LEVELS)}")
    if not report.preserved_fields:
        errors.append("preserved_fields must be non-empty")
    if not report.source_artifacts:
        errors.append("source_artifacts must be non-empty")
    if not report.data_boundary.strip():
        errors.append("data_boundary must be non-empty")
    if not report.field_mapping:
        errors.append("field_mapping must be non-empty")
    for preserved_field in report.preserved_fields:
        if preserved_field not in report.field_mapping:
            errors.append(f"field_mapping missing preserved field: {preserved_field}")
    if report.support_level == "supported" and (report.lost_fields or report.unsupported_fields):
        errors.append("support_level=supported requires no lost_fields or unsupported_fields")
    if report.support_level == "supported" and report.claim_boundary != "production_supported":
        errors.append("support_level=supported requires production_supported claim_boundary")
    if report.support_level != "supported" and report.claim_boundary == "production_supported":
        errors.append("production_supported claim_boundary requires support_level=supported")
    if report.support_level != "supported":
        if not (report.lost_fields or report.unsupported_fields):
            errors.append("non-supported reports must name lost_fields or unsupported_fields")
        if not report.fidelity_notes:
            errors.append("non-supported reports must include fidelity_notes")
        if not report.promotion_requirements:
            errors.append("non-supported reports must include promotion_requirements")
    return errors
