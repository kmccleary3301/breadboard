from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from breadboard_ext.darwin.contracts import (
    validate_campaign_spec,
    validate_component_ref,
    validate_decision_record,
    validate_effective_config,
    validate_effective_policy,
    validate_evolution_ledger,
    validate_evaluator_pack,
)
from scripts.validate_darwin_contract_pack_v0 import SCHEMA_CASES, build_report


@pytest.mark.parametrize("schema_path,fixture_paths", SCHEMA_CASES)
def test_darwin_schema_is_valid_and_fixture_conforms(schema_path: str, fixture_paths: list[str]) -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for rel in fixture_paths:
        fixture = json.loads((root / rel).read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(fixture))
        assert not errors, f"{rel} failed schema validation: {errors}"


def test_darwin_contract_pack_report_is_green() -> None:
    report = build_report()
    assert report["ok"] is True


def test_validate_campaign_spec_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.campaign_spec.v0",
        "campaign_id": "camp",
    }
    issues = validate_campaign_spec(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_effective_config_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.effective_config.v0",
        "campaign_id": "camp",
    }
    issues = validate_effective_config(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_effective_policy_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.effective_policy.v0",
        "campaign_id": "camp",
    }
    issues = validate_effective_policy(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_evaluator_pack_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.evaluator_pack.v0",
        "campaign_id": "camp",
    }
    issues = validate_evaluator_pack(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_component_ref_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.component_ref.v0",
        "component_id": "cmp",
    }
    issues = validate_component_ref(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_decision_record_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.decision_record.v0",
        "decision_id": "decision",
    }
    issues = validate_decision_record(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)


def test_validate_evolution_ledger_reports_missing_required_field() -> None:
    payload = {
        "schema": "breadboard.darwin.evolution_ledger.v0",
        "generated_at": "2026-03-18T00:00:00+00:00",
    }
    issues = validate_evolution_ledger(payload)
    assert issues
    assert any(issue.path == "$" for issue in issues)
