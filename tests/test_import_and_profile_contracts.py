from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


@pytest.mark.parametrize(
    "schema_path, fixture_paths",
    [
        (
            "docs/contracts/import/import_ir_v1.schema.json",
            ["tests/fixtures/contracts/import_ir/sample_import_ir_v1.json"],
        ),
        (
            "docs/contracts/import/import_run_manifest_v1.schema.json",
            ["tests/fixtures/contracts/import_ir/sample_import_run_manifest_v1.json"],
        ),
        (
            "docs/contracts/emulation_profile/emulation_profile_manifest_v1.schema.json",
            ["tests/fixtures/contracts/emulation_profile/sample_manifest_v1.json"],
        ),
        (
            "docs/contracts/provider_plans/provider_policy_manifest_v1.schema.json",
            [
                "docs/provider_plans/policy_manifests/openai_codex_chatgpt_plan.json",
                "docs/provider_plans/policy_manifests/anthropic_consumer_subscription.json",
            ],
        ),
    ],
)
def test_schema_is_valid_and_fixture_conforms(schema_path: str, fixture_paths: list[str]) -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for rel in fixture_paths:
        fixture = json.loads((root / rel).read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(fixture))
        assert not errors, f"{rel} failed schema validation: {errors}"
