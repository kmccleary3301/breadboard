from __future__ import annotations

from breadboard.rl.adapters.benchflow import build_benchflow_fixture_probe_report
from breadboard.rl.adapters.ors import build_ors_fixture_probe_report
from breadboard.rl.adapters.prime_verifiers import build_prime_verifiers_fixture_probe_report
from breadboard.rl.adapters.probe import validate_adapter_probe_report
from breadboard.rl.adapters.verl import build_verl_jsonl_probe_report


def test_required_adapter_probe_reports_validate() -> None:
    reports = [
        build_benchflow_fixture_probe_report(),
        build_ors_fixture_probe_report(),
        build_verl_jsonl_probe_report(),
        build_prime_verifiers_fixture_probe_report(),
    ]

    assert {report.adapter_id for report in reports} == {
        "benchflow.fixture.v1",
        "ors.fixture.v1",
        "verl.jsonl_probe.v1",
        "prime_verifiers.fixture.v1",
    }
    for report in reports:
        assert validate_adapter_probe_report(report) == []
        assert report.claim_boundary == "adapter_probe_not_production_integration"
        assert report.preserved_fields
        assert report.lost_fields or report.unsupported_fields
        assert report.data_boundary == "fixture_or_probe_only"
        assert report.fidelity_notes
        assert report.promotion_requirements
        assert set(report.preserved_fields).issubset(report.field_mapping)


def test_verl_report_points_to_m7_jsonl_not_trainer_support() -> None:
    report = build_verl_jsonl_probe_report()

    assert report.support_level == "jsonl_probe"
    assert "verl_DataProto_object" in report.lost_fields
    assert "ppo_grpo_trainer_execution" in report.unsupported_fields
    assert report.field_mapping["input_ids"] == "VeRLProbeRow.input_ids"
    assert "DataProto" in " ".join(report.promotion_requirements)
