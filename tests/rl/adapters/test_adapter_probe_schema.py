from __future__ import annotations

from breadboard.rl.adapters.probe import AdapterProbeReport, validate_adapter_probe_report


def test_adapter_probe_requires_preserved_fields() -> None:
    report = AdapterProbeReport(
        adapter_id="x",
        adapter_kind="fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=[],
    )

    assert "preserved_fields must be non-empty" in validate_adapter_probe_report(report)


def test_supported_report_cannot_have_lost_fields() -> None:
    report = AdapterProbeReport(
        adapter_id="x",
        adapter_kind="fixture",
        support_level="supported",
        workload_family="swe",
        preserved_fields=["task_id"],
        lost_fields=["x"],
        claim_boundary="production_supported",
    )

    assert "support_level=supported requires no lost_fields or unsupported_fields" in validate_adapter_probe_report(report)


def test_production_boundary_requires_supported_level() -> None:
    report = AdapterProbeReport(
        adapter_id="x",
        adapter_kind="fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=["task_id"],
        claim_boundary="production_supported",
    )

    assert "production_supported claim_boundary requires support_level=supported" in validate_adapter_probe_report(report)


def test_probe_report_requires_mapping_and_promotion_notes() -> None:
    report = AdapterProbeReport(
        adapter_id="x",
        adapter_kind="fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=["task_id"],
        lost_fields=["live_runtime"],
        source_artifacts=["fixture.json"],
        field_mapping={},
        fidelity_notes=[],
        promotion_requirements=[],
    )

    errors = validate_adapter_probe_report(report)

    assert "field_mapping must be non-empty" in errors
    assert "field_mapping missing preserved field: task_id" in errors
    assert "non-supported reports must include fidelity_notes" in errors
    assert "non-supported reports must include promotion_requirements" in errors


def test_complete_fixture_probe_report_validates() -> None:
    report = AdapterProbeReport(
        adapter_id="x",
        adapter_kind="fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=["task_id"],
        lost_fields=["live_runtime"],
        source_artifacts=["fixture.json"],
        field_mapping={"task_id": "fixture.task.id"},
        fidelity_notes=["Static fixture only."],
        promotion_requirements=["Run against the real service."],
    )

    assert validate_adapter_probe_report(report) == []
