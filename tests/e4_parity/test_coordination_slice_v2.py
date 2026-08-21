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
EXAMPLE_DIR = ROOT / "contracts" / "kernel" / "examples"

SCHEMA_PATH = SCHEMA_DIR / "bb.coordination_slice.v2.schema.json"
EXAMPLE_PATH = EXAMPLE_DIR / "coordination_slice_v2_minimal.json"


requires_coordination_slice_v2_schema = pytest.mark.skipif(
    not SCHEMA_PATH.is_file(),
    reason="bb.coordination_slice.v2 dedicated schema is not present yet",
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _load_schema() -> dict[str, Any]:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return schema


def _schema_store() -> dict[str, dict[str, Any]]:
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.json"):
        schema = _load_json(schema_path)
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = schema
        store[schema_path.name] = schema
    return store


@pytest.fixture(scope="module")
def coordination_slice_validator() -> Draft202012Validator:
    schema = _load_schema()
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=schema,
        store=_schema_store(),
    )
    return Draft202012Validator(schema, resolver=resolver)


def _example_payload() -> dict[str, Any]:
    return _load_json(EXAMPLE_PATH)


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


def test_coordination_slice_v2_schema_exists_and_is_valid_draft_2020_12() -> None:
    """W3 coordination slices have a dedicated Draft 2020-12 JSON Schema contract."""
    assert SCHEMA_PATH.is_file(), "missing contracts/kernel/schemas/bb.coordination_slice.v2.schema.json"
    schema = _load_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@requires_coordination_slice_v2_schema
def test_minimal_coordination_slice_v2_example_validates(
    coordination_slice_validator: Draft202012Validator,
) -> None:
    """The checked-in minimal W3 coordination slice is valid against the dedicated schema."""
    _assert_schema_valid(coordination_slice_validator, _example_payload())


@requires_coordination_slice_v2_schema
def test_coordination_slice_v2_rejects_unknown_top_level_property(
    coordination_slice_validator: Draft202012Validator,
) -> None:
    """The root slice object is closed so misspelled or stray top-level fields cannot pass."""
    payload = _example_payload()
    payload["unexpected_top_level"] = True

    _assert_schema_rejects(coordination_slice_validator, payload, "unexpected_top_level")


@requires_coordination_slice_v2_schema
def test_coordination_slice_v2_rejects_embedded_signal_missing_required_field(
    coordination_slice_validator: Draft202012Validator,
) -> None:
    """Embedded bb.signal.v1 records still enforce their required signal fields inside a slice."""
    payload = _example_payload()
    invalid_signal = copy.deepcopy(payload["records"]["signals"][0])
    invalid_signal.pop("source")
    payload["records"]["signals"][0] = invalid_signal

    _assert_schema_rejects(coordination_slice_validator, payload, "records.signals.0", "source")
