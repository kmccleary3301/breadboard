from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import ValidationError
from scripts.e4_parity.validators.registries import schema_generation_default


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
SCHEMA_VERSION = schema_generation_default("support_claim")
SCHEMA_PATH = SCHEMA_DIR / f"{SCHEMA_VERSION}.schema.json"
SUPPORT_CLAIM_FIXTURE_DIR = ROOT / "docs" / "conformance" / "support_claims"
SUPPORT_CLAIM_FIXTURES = sorted(SUPPORT_CLAIM_FIXTURE_DIR.glob("*_c4_support_claim.json"))
REQUIRED_SCOPE_KEYS = (
    "config_id",
    "lane_id",
    "provider_model",
    "run_id",
    "target_family",
    "sandbox_mode",
    "target_version",
)


requires_support_claim_schema = pytest.mark.skipif(
    not SCHEMA_PATH.is_file(),
    reason=f"{SCHEMA_VERSION} dedicated schema is not present",
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _load_schema() -> dict[str, Any]:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return schema

def _local_schema_store() -> dict[str, dict[str, Any]]:
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.schema.json"):
        schema = _load_json(schema_path)
        store[schema_path.name] = schema
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = schema
    return store


def _reject_remote_ref(uri: str) -> Any:
    raise AssertionError(f"schema validation attempted network access: {uri}")


@pytest.fixture(scope="module")
def support_claim_validator() -> Draft202012Validator:
    schema = _load_schema()
    resolver = RefResolver.from_schema(
        schema,
        store=_local_schema_store(),
        handlers={"http": _reject_remote_ref, "https": _reject_remote_ref},
    )
    return Draft202012Validator(schema, resolver=resolver)


def _fixture_payload() -> dict[str, Any]:
    assert SUPPORT_CLAIM_FIXTURES, "expected at least one checked-in v4 support-claim fixture"
    payload = _load_json(SUPPORT_CLAIM_FIXTURES[0])
    assert payload.get("schema_version") == SCHEMA_VERSION
    return payload




def _schema_errors(
    validator: Draft202012Validator,
    payload: dict[str, Any],
) -> list[ValidationError]:
    return sorted(
        validator.iter_errors(payload),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )


def _format_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def _assert_schema_valid(validator: Draft202012Validator, payload: dict[str, Any]) -> None:
    errors = _schema_errors(validator, payload)
    assert errors == [], "\n".join(_format_error(error) for error in errors)


def _assert_schema_rejects(
    validator: Draft202012Validator,
    payload: dict[str, Any],
    *needles: str,
) -> None:
    errors = _schema_errors(validator, payload)
    assert errors != []
    formatted = "\n".join(_format_error(error) for error in errors)
    for needle in needles:
        assert needle in formatted


def test_dedicated_support_claim_schema_exists_and_is_a_valid_json_schema() -> None:
    """Support claims have their own contract, not an implicit validator-only shape."""
    assert SCHEMA_PATH.is_file(), f"missing contracts/kernel/schemas/{SCHEMA_PATH.name}"
    _load_schema()


@requires_support_claim_schema
def test_checked_in_support_claim_fixtures_validate_against_dedicated_schema(
    support_claim_validator: Draft202012Validator,
) -> None:
    """Accepted v4 support-claim evidence already in the repo remains schema-valid."""
    assert SUPPORT_CLAIM_FIXTURES, "expected checked-in v4 support-claim fixtures"

    errors: list[str] = []
    for fixture_path in SUPPORT_CLAIM_FIXTURES:
        payload = _load_json(fixture_path)
        if payload.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{fixture_path.relative_to(ROOT)}: expected {SCHEMA_VERSION}")
            continue
        for error in _schema_errors(support_claim_validator, payload):
            errors.append(f"{fixture_path.relative_to(ROOT)}: {_format_error(error)}")

    assert errors == []


@requires_support_claim_schema
@pytest.mark.parametrize("scope_key", REQUIRED_SCOPE_KEYS)
def test_scope_requires_each_semantic_anchor(
    support_claim_validator: Draft202012Validator,
    scope_key: str,
) -> None:
    """A support claim is not portable evidence unless its scope pins the exact lane/run/config surface."""
    payload = _fixture_payload()
    scope = copy.deepcopy(payload["scope"])
    assert isinstance(scope, dict)
    scope.pop(scope_key)
    payload["scope"] = scope

    _assert_schema_rejects(support_claim_validator, payload, "scope", scope_key)


@requires_support_claim_schema
@pytest.mark.parametrize(
    "mutation, expected_needles",
    [
        (lambda payload: payload.pop("ledger_row_refs"), ("ledger_row_refs",)),
        (lambda payload: payload.__setitem__("ledger_row_refs", []), ("ledger_row_refs",)),
        (
            lambda payload: payload.__setitem__(
                "ledger_row_refs",
                ["docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#sha256:" + "0" * 64],
            ),
            ("ledger_row_refs",),
        ),
    ],
)
def test_accepted_support_claim_requires_row_scoped_ledger_refs(
    support_claim_validator: Draft202012Validator,
    mutation: Any,
    expected_needles: tuple[str, ...],
) -> None:
    """An accepted claim must cite atomic-feature rows as path#feature_id#sha256, not a file-level ledger."""
    payload = _fixture_payload()
    assert payload["accepted"] is True
    mutation(payload)

    _assert_schema_rejects(support_claim_validator, payload, *expected_needles)


@requires_support_claim_schema
@pytest.mark.parametrize(
    "mutation, expected_needles",
    [
        (lambda payload: payload.pop("evidence_manifest_ref"), ("evidence_manifest_ref",)),
        (lambda payload: payload.__setitem__("evidence_manifest_ref", ""), ("evidence_manifest_ref",)),
        (lambda payload: payload.pop("validation_refs"), ("validation_refs",)),
        (lambda payload: payload.__setitem__("validation_refs", []), ("validation_refs",)),
    ],
)
def test_accepted_support_claim_requires_evidence_manifest_and_validation_refs(
    support_claim_validator: Draft202012Validator,
    mutation: Any,
    expected_needles: tuple[str, ...],
) -> None:
    """Accepted support claims must point at packet evidence and validator output before promotion."""
    payload = _fixture_payload()
    assert payload["accepted"] is True
    mutation(payload)

    _assert_schema_rejects(support_claim_validator, payload, *expected_needles)
