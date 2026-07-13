from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
EXAMPLE_DIR = ROOT / "contracts" / "kernel" / "examples"
MANIFEST_PATH = EXAMPLE_DIR / "examples_manifest.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _schema_version_for(schema_path: Path, schema: dict[str, Any]) -> str:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema_version = properties.get("schema_version")
        if isinstance(schema_version, dict) and isinstance(schema_version.get("const"), str):
            return schema_version["const"]
    return schema_path.name.removesuffix(".schema.json")


def _format_validation_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


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


def test_examples_manifest_has_one_pair_for_every_kernel_schema() -> None:
    manifest = _load_json(MANIFEST_PATH)
    entries = manifest.get("entries")
    assert manifest.get("schema_version") == "bb.kernel.examples_manifest.v1"
    assert isinstance(entries, list)

    schema_versions = []
    for schema_path in sorted(SCHEMA_DIR.glob("*.json")):
        schema_versions.append(_schema_version_for(schema_path, _load_json(schema_path)))

    manifest_versions = [entry.get("schema_version") for entry in entries]

    assert len(manifest_versions) == len(set(manifest_versions)), "manifest schema_version entries must be unique"
    assert sorted(manifest_versions) == sorted(schema_versions)


def test_examples_manifest_paths_resolve_to_checked_in_files() -> None:
    entries = _load_json(MANIFEST_PATH)["entries"]

    errors: list[str] = []
    for entry in entries:
        schema_path = ROOT / entry["schema_path"]
        example_path = ROOT / entry["example_path"]
        if not schema_path.is_file():
            errors.append(f"missing schema: {entry['schema_path']}")
        if not example_path.is_file():
            errors.append(f"missing example: {entry['example_path']}")

    assert errors == []


def test_manifest_examples_validate_against_paired_schemas() -> None:
    entries = _load_json(MANIFEST_PATH)["entries"]

    errors: list[str] = []
    for entry in entries:
        schema_path = ROOT / entry["schema_path"]
        example_path = ROOT / entry["example_path"]
        schema = _load_json(schema_path)
        example = _load_json(example_path)
        expected_version = _schema_version_for(schema_path, schema)
        if entry["schema_version"] != expected_version:
            errors.append(
                f"{entry['schema_path']}: manifest schema_version {entry['schema_version']!r} != {expected_version!r}"
            )
        for error in sorted(
            _validator(schema).iter_errors(example),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        ):
            errors.append(f"{entry['example_path']} against {entry['schema_path']}: {_format_validation_error(error)}")

    assert errors == []
