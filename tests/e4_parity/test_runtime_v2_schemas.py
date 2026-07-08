from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
EXAMPLE_DIR = ROOT / "contracts" / "kernel" / "examples"

RUNTIME_SCHEMAS = {
    "bb.kernel_event.v2": "kernel_event_v2_minimal.json",
    "bb.tool_call.v2": "tool_call_v2_minimal.json",
    "bb.tool_execution_outcome.v2": "tool_execution_outcome_v2_minimal.json",
    "bb.tool_model_render.v2": "tool_model_render_v2_minimal.json",
    "bb.tool_spec.v2": "tool_spec_v2_minimal.json",
    "bb.session_transcript.v2": "session_transcript_v2_minimal.json",
}

COMMON_REFS = {
    "bb.kernel.common.v1.schema.json#/$defs/metadata",
    "bb.kernel.common.v1.schema.json#/$defs/actor_ref",
    "bb.kernel.common.v1.schema.json#/$defs/visibility",
    "bb.kernel.common.v1.schema.json#/$defs/evidence_refs",
    "bb.kernel.common.v1.schema.json#/$defs/timestamp_utc",
    "bb.kernel.common.v1.schema.json#/$defs/identifier",
    "bb.kernel.common.v1.schema.json#/$defs/nullable_digest_sha256",
}

OPEN_PAYLOAD_POINTERS = {
    "bb.kernel_event.v2": {"$/properties/payload"},
    "bb.tool_call.v2": {"$/properties/args"},
    "bb.tool_execution_outcome.v2": {"$/properties/result"},
    "bb.tool_model_render.v2": {"$/properties/parts/items/properties/content"},
    "bb.tool_spec.v2": {"$/properties/input_schema", "$/properties/output_schema"},
    "bb.session_transcript.v2": {"$/properties/items/items/properties/content"},
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _schema_path(schema_version: str) -> Path:
    return SCHEMA_DIR / f"{schema_version}.schema.json"


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.json"):
        loaded = _load_json(schema_path)
        schema_id = loaded.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = loaded
        store[schema_path.name] = loaded
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=schema,
        store=store,
    )
    return Draft202012Validator(schema, resolver=resolver)


def _runtime_schema_and_example(schema_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _load_json(_schema_path(schema_version)), _load_json(EXAMPLE_DIR / RUNTIME_SCHEMAS[schema_version])


def _validation_messages(schema: dict[str, Any], instance: dict[str, Any]) -> list[str]:
    return [error.message for error in sorted(_validator(schema).iter_errors(instance), key=str)]


def _object_nodes(node: Any, pointer: str = "$") -> list[tuple[str, dict[str, Any]]]:
    if isinstance(node, bool) or not isinstance(node, dict):
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    if node.get("type") == "object" or "properties" in node:
        found.append((pointer, node))
    for key, value in node.items():
        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            for child_name, child in value.items():
                found.extend(_object_nodes(child, f"{pointer}/{key}/{child_name}"))
        elif key in {"items", "additionalProperties", "contains", "if", "then", "else", "not"}:
            found.extend(_object_nodes(value, f"{pointer}/{key}"))
        elif key in {"allOf", "anyOf", "oneOf"} and isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(_object_nodes(child, f"{pointer}/{key}/{index}"))
    return found


def test_runtime_v2_examples_validate_against_schemas() -> None:
    errors: list[str] = []
    for schema_version in RUNTIME_SCHEMAS:
        schema, example = _runtime_schema_and_example(schema_version)
        errors.extend(
            f"{schema_version}: {message}" for message in _validation_messages(schema, example)
        )
    assert errors == []


def test_runtime_v2_roots_are_closed_and_versioned() -> None:
    for schema_version in RUNTIME_SCHEMAS:
        schema = _load_json(_schema_path(schema_version))
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"]["const"] == schema_version
        assert "schema_version" in schema["required"]


def test_runtime_v2_object_literals_are_closed_except_declared_open_payloads() -> None:
    failures: list[str] = []
    for schema_version in RUNTIME_SCHEMAS:
        schema = _load_json(_schema_path(schema_version))
        allowed_open = OPEN_PAYLOAD_POINTERS[schema_version]
        for pointer, node in _object_nodes(schema):
            if node.get("$ref") in COMMON_REFS:
                continue
            if pointer in allowed_open:
                continue
            if node.get("additionalProperties") is not False:
                failures.append(f"{schema_version}:{pointer}")
    assert failures == []


def test_runtime_v2_unknown_top_level_keys_are_rejected() -> None:
    for schema_version in RUNTIME_SCHEMAS:
        schema, example = _runtime_schema_and_example(schema_version)
        invalid = copy.deepcopy(example)
        invalid["unexpected_top_level"] = True
        assert _validation_messages(schema, invalid), schema_version


def test_runtime_v2_bad_visibility_shape_is_rejected() -> None:
    for schema_version in RUNTIME_SCHEMAS:
        schema, example = _runtime_schema_and_example(schema_version)
        invalid = copy.deepcopy(example)
        if "visibility" in schema.get("properties", {}):
            invalid["visibility"] = {"model_visible": True}
        else:
            invalid.setdefault("items", [{}])[0]["visibility"] = {"model_visible": True}
        assert _validation_messages(schema, invalid), schema_version


def test_runtime_v2_tool_call_bad_state_is_rejected() -> None:
    schema, example = _runtime_schema_and_example("bb.tool_call.v2")
    invalid = copy.deepcopy(example)
    invalid["state"] = "requested"
    assert _validation_messages(schema, invalid)


def test_runtime_v2_outcome_bad_terminal_state_is_rejected() -> None:
    schema, example = _runtime_schema_and_example("bb.tool_execution_outcome.v2")
    invalid = copy.deepcopy(example)
    invalid["terminal_state"] = "unknown"
    assert _validation_messages(schema, invalid)
