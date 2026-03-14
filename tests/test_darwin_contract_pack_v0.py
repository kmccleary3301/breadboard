from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from breadboard_ext.darwin.contracts import validate_campaign_spec, validate_effective_config
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
