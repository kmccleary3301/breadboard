from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
SUPPORT_CLAIM_V4_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.support_claim.v4.schema.json"
KERNEL_COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.common.v1.schema.json"
SUPPORT_CLAIMS_DIR = ROOT / "docs" / "conformance" / "support_claims"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.fixture(scope="module")
def support_claim_v4_validator() -> Draft202012Validator:
    assert SUPPORT_CLAIM_V4_SCHEMA_PATH.is_file(), "missing contracts/kernel/schemas/bb.e4.support_claim.v4.schema.json"
    schema = _load_json(SUPPORT_CLAIM_V4_SCHEMA_PATH)
    kernel_common = _load_json(KERNEL_COMMON_SCHEMA_PATH)
    e4_common = _load_json(E4_COMMON_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    store = {
        str(schema["$id"]): schema,
        SUPPORT_CLAIM_V4_SCHEMA_PATH.name: schema,
        str(kernel_common["$id"]): kernel_common,
        KERNEL_COMMON_SCHEMA_PATH.name: kernel_common,
        str(e4_common["$id"]): e4_common,
        E4_COMMON_SCHEMA_PATH.name: e4_common,
    }
    return Draft202012Validator(schema, resolver=RefResolver.from_schema(schema, store=store))


def _schema_errors(validator: Draft202012Validator, payload: dict[str, Any]) -> list[ValidationError]:
    return sorted(
        validator.iter_errors(payload),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )


def _format_errors(errors: list[ValidationError]) -> str:
    return "\n".join(
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    )


def _valid_v4_claim() -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.support_claim.v4",
        "claim_id": "claim_alpha",
        "kind": "target_support",
        "accepted": True,
        "summary": "Fixture C4 support claim for one exact lane scope.",
        "scope": {
            "config_id": "config_alpha",
            "lane_id": "lane_alpha",
            "run_id": "run_alpha",
            "target_family": "oh_my_pi",
            "target_version": "target-alpha",
            "provider_model": "provider-alpha",
            "sandbox_mode": "sandbox-alpha",
        },
        "exclusions": ["No broad target-family support claim is made."],
        "exclusion_facets": {
            "excluded_families": ["all_other_families", "new_harness_family"],
            "excluded_lanes": [],
            "excluded_sandbox_modes": [],
            "excluded_provider_modes": [],
            "excluded_behavior_classes": ["broad_target_parity"],
        },
        "claim_semantics": {
            "asserted_behaviors": [
                {
                    "behavior_id": "behavior_alpha",
                    "description": "the comparator proves the exact scoped behavior",
                    "comparator_assertion_ids": ["scope_match"],
                }
            ]
        },
        "freeze_ref": "config/e4_target_freeze_manifest.yaml#config_alpha#sha256:" + "4" * 64,
        "capture_ref": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json#sha256:" + "5" * 64,
        "replay_ref": "docs/conformance/e4_target_support/lane_alpha/bb_replay_result.json#sha256:" + "6" * 64,
        "comparator_ref": "docs/conformance/e4_target_support/lane_alpha/comparator_report.json#sha256:" + "7" * 64,
        "evidence_manifest_ref": "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
        "ledger_row_refs": ["docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#feat_alpha#sha256:" + "9" * 64],
        "validation_refs": ["artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json#sha256:" + "8" * 64],
        "catalog_binding": {
            "catalog_path": "docs/conformance/e4_artifact_catalog.json",
            "catalog_revision": 7,
            "segment_id": "lane_alpha",
            "segment_hash": "sha256:" + "1" * 64,
            "shared_segment_hash": "sha256:" + "2" * 64,
        },
        "reverify_command": {"argv": ["python", "scripts/validate_e4_c4_chain.py", "--check-only"], "cwd": "."},
        "generated_at_utc": "2026-07-03T00:00:00Z",
    }


def test_support_claim_v4_schema_exists_and_valid_fixture_validates(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    errors = _schema_errors(support_claim_v4_validator, _valid_v4_claim())

    assert errors == [], _format_errors(errors)


def test_support_claim_v4_scope_schema_is_common_e4_scope_ref() -> None:
    schema = _load_json(SUPPORT_CLAIM_V4_SCHEMA_PATH)

    assert schema["properties"]["scope"] == {
        "$ref": "bb.e4.common.v1.schema.json#/$defs/e4_scope"
    }


def test_support_claim_v4_scope_target_family_uses_e4_scope_pattern(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    payload = _valid_v4_claim()
    payload["scope"] = copy.deepcopy(payload["scope"])
    payload["scope"]["target_family"] = "oh-my-pi"

    errors = _schema_errors(support_claim_v4_validator, payload)

    target_family_errors = [
        error
        for error in errors
        if tuple(error.absolute_path) == ("scope", "target_family")
    ]
    assert [error.validator for error in target_family_errors] == ["pattern"], _format_errors(errors)


def test_support_claim_v4_target_family_other_conditional_reads_nested_scope(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    payload = _valid_v4_claim()
    payload["scope"] = copy.deepcopy(payload["scope"])
    payload["scope"]["target_family"] = "other"

    errors = _schema_errors(support_claim_v4_validator, payload)

    target_family_other_errors = [
        error
        for error in errors
        if tuple(error.absolute_path) == () and error.validator == "required"
    ]
    assert any(
        "'target_family_other' is a required property" == error.message
        for error in target_family_other_errors
    ), _format_errors(errors)



def test_support_claim_v4_rejects_root_scope_duplicate_fields(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    payload = _valid_v4_claim()
    payload["lane_id"] = payload["scope"]["lane_id"]

    errors = _schema_errors(support_claim_v4_validator, payload)

    assert errors != []
    formatted = _format_errors(errors)
    assert "lane_id" in formatted
    assert "Additional properties are not allowed" in formatted or "additional properties" in formatted


def test_support_claim_v4_excluded_families_are_registry_identifiers_not_a_closed_enum(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    payload = _valid_v4_claim()
    payload["exclusion_facets"] = copy.deepcopy(payload["exclusion_facets"])
    payload["exclusion_facets"]["excluded_families"] = ["all_other_families", "future_agent_family"]

    errors = _schema_errors(support_claim_v4_validator, payload)

    assert errors == [], _format_errors(errors)


def test_checked_in_support_claim_v4_records_validate(
    support_claim_v4_validator: Draft202012Validator,
) -> None:
    support_claim_paths = sorted(SUPPORT_CLAIMS_DIR.glob("*_support_claim.json"))
    checked_count = 0
    failures: list[str] = []

    for path in support_claim_paths:
        payload = _load_json(path)
        if payload.get("schema_version") != "bb.e4.support_claim.v4":
            continue

        checked_count += 1
        errors = _schema_errors(support_claim_v4_validator, payload)
        if errors:
            failures.append(
                f"{path.relative_to(ROOT)} ({len(errors)} error(s)):\n{_format_errors(errors)}"
            )

    assert failures == [], (
        f"validated {checked_count} checked-in v4 support claim(s) from "
        f"{len(support_claim_paths)} support claim file(s); failures:\n"
        + "\n\n".join(failures)
    )
