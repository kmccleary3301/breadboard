#!/usr/bin/env python3
"""Validate candidate public contract sources without activating them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "contracts" / "public"
SCHEMA_DIR = PUBLIC_DIR / "schemas"
KERNEL_SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
SURFACES = ("bbh", "openapi", "python_sdk", "typescript_sdk", "tui", "docs")
FROZEN_SHA256 = "sha256:72817b7b1bc5e5d10f752acb48157491aaeb3eb268337461a4fd6f0bd10cbfe0"
AXIS_MANIFEST_SHA256 = "sha256:dff057633730b1bbb28ebd4fceff3060227f5532b6caabb0f3ed2a325d437db0"
RECORD_ROLE_PROJECTION_SHA256 = "sha256:98e8cf84a312ad4b28f347d89ad3146873e88f290cba9a9295655966661d896b"

class ContractValidationError(ValueError):
    """A candidate public contract violates its frozen contract."""

def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path}: root must be an object")
    return value

def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"

def load_frozen_surface(public_dir: Path = PUBLIC_DIR) -> dict[str, Any]:
    path = public_dir / "frozen_public_surface.v1.json"
    frozen = load_json(path)
    canonical = canonical_bytes(frozen)
    if path.read_bytes() != canonical or _sha256(canonical) != FROZEN_SHA256:
        raise ContractValidationError(f"{path}: canonical frozen public-surface hash mismatch")
    if frozen.get("contract_id") != "bb.north_star.public_surface.v1":
        raise ContractValidationError(f"{path}: unexpected frozen contract_id")
    return frozen

def frozen_operation_ids(frozen: dict[str, Any]) -> frozenset[str]:
    return frozenset(item for group in frozen["canonical_operations"].values() for item in group)

def load_schema(path: Path) -> dict[str, Any]:
    schema = load_json(path)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractValidationError(f"{path}: invalid Draft 2020-12 schema: {exc.message}") from exc
    return schema
def sync_record_schemas(public_dir: Path = PUBLIC_DIR, *, write: bool = False) -> None:
    source_path = public_dir / "record_schemas.v1.json"
    source = load_json(source_path)
    if source.get("contract_id") != "bb.public_record_schema_source.v1" or source.get("status") != "candidate":
        raise ContractValidationError(f"{source_path}: invalid candidate record-schema source")
    source_bytes = canonical_bytes(source)
    if source_path.read_bytes() != source_bytes:
        raise ContractValidationError(f"{source_path}: semantic source must use canonical bytes")
    source_hash = _sha256(source_bytes)
    generated_by = "scripts/quality/validate_public_contracts.py --write-record-schemas"
    expected: set[Path] = set()
    for schema_id, body in sorted(source["schemas"].items()):
        path = public_dir / "schemas" / f"{schema_id}.schema.json"
        schema = {**body, "x-generated-by": generated_by, "x-source-sha256": source_hash}
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ContractValidationError(f"{source_path}:{schema_id}: invalid Draft 2020-12 schema: {exc.message}") from exc
        if schema.get("$id") != f"https://breadboard.dev/contracts/public/schemas/{path.name}":
            raise ContractValidationError(f"{source_path}:{schema_id}: non-canonical $id")
        expected.add(path)
        content = canonical_bytes(schema)
        if write:
            path.write_bytes(content)
        elif not path.is_file() or path.read_bytes() != content:
            raise ContractValidationError(f"{path}: generated record schema is stale; run --write-record-schemas")
    extras = {path for path in (public_dir / "schemas").glob("*.schema.json") if load_json(path).get("x-generated-by") == generated_by} - expected
    if extras:
        raise ContractValidationError(f"generated record schemas absent from source: {sorted(map(str, extras))}")

def _schema_errors(instance: Any, schema_name: str, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    schema = load_schema(schema_dir / schema_name)
    errors = Draft202012Validator(schema).iter_errors(instance)
    return [
        f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in sorted(errors, key=lambda item: (tuple(map(str, item.absolute_path)), item.message))
    ]

def _raise_if_errors(errors: Iterable[str]) -> None:
    items = list(errors)
    if items:
        raise ContractValidationError("\n".join(items))

def _reject_inline_models(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            if key in {"inline_model", "inline_schema", "model"} and isinstance(child, dict):
                raise ContractValidationError(f"{'.'.join(child_path)}: inline record models are forbidden")
            if key.endswith("_schema") and child is not None and not isinstance(child, str):
                raise ContractValidationError(f"{'.'.join(child_path)}: schema binding must be a schema ID")
            _reject_inline_models(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_inline_models(child, (*path, str(index)))

def validate_catalog(
    catalog: dict[str, Any], schema_dir: Path = SCHEMA_DIR, frozen: dict[str, Any] | None = None,
) -> None:
    _raise_if_errors(_schema_errors(catalog, "bb.public_operation_catalog.v1.schema.json", schema_dir))
    _reject_inline_models(catalog)
    rows = catalog["operations"]
    operation_ids = [row["operation_id"] for row in rows]
    duplicates = sorted({item for item in operation_ids if operation_ids.count(item) > 1})
    if duplicates:
        raise ContractValidationError(f"duplicate operation IDs: {', '.join(duplicates)}")
    frozen = frozen or load_frozen_surface()
    expected, actual = frozen_operation_ids(frozen), set(operation_ids)
    if expected != actual:
        raise ContractValidationError(f"frozen operation set mismatch; missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    identity_fields = {"bbh": ("command",), "openapi": ("method", "path"), "python_sdk": ("method",), "typescript_sdk": ("method",), "tui": ("action_id",), "docs": ("slug",)}
    seen: dict[str, dict[tuple[str, ...], str]] = {surface: {} for surface in SURFACES}
    for row in rows:
        for surface in SURFACES:
            binding = row["bindings"][surface]
            if surface == "openapi" and binding["operation_id"] != row["operation_id"]:
                raise ContractValidationError(f"{row['operation_id']}.openapi: operation_id must equal the canonical operation ID")
            identity = tuple(binding[field] for field in identity_fields[surface])
            if identity in seen[surface]:
                raise ContractValidationError(f"duplicate {surface} binding identity for {seen[surface][identity]} and {row['operation_id']}: {identity}")
            seen[surface][identity] = row["operation_id"]
    by_id = {row["operation_id"]: row for row in rows}
    for operation_id, command in frozen["surface_bindings"]["bbh"]["examples"].items():
        if by_id[operation_id]["bindings"]["bbh"]["command"] != command:
            raise ContractValidationError(f"{operation_id}.bbh: command differs from frozen example {command}")

def _schema_index(schema_dir: Path, kernel_schema_dir: Path) -> dict[str, Path]:
    by_id: dict[str, Path] = {}
    by_uri: dict[str, Path] = {}
    for root, namespace in ((schema_dir, "public"), (kernel_schema_dir, "kernel")):
        for path in sorted(root.glob("*.schema.json")):
            schema = load_schema(path)
            uri = schema.get("$id")
            if not isinstance(uri, str):
                raise ContractValidationError(f"{path}: missing string $id")
            if uri in by_uri:
                raise ContractValidationError(f"{path}: duplicate schema $id also declared by {by_uri[uri]}")
            by_uri[uri] = path
            expected_uri = f"https://breadboard.dev/contracts/{namespace}/schemas/{path.name}"
            if uri != expected_uri:
                raise ContractValidationError(f"{path}: $id must equal {expected_uri}")
            schema_id = path.name.removesuffix(".schema.json")
            if schema_id in by_id:
                raise ContractValidationError(f"{path}: duplicate schema identity also declared by {by_id[schema_id]}")
            by_id[schema_id] = path
    return by_id

def validate_record_surface(
    record_surface: dict[str, Any], schema_dir: Path = SCHEMA_DIR,
    kernel_schema_dir: Path = KERNEL_SCHEMA_DIR, frozen: dict[str, Any] | None = None,
) -> None:
    _raise_if_errors(_schema_errors(record_surface, "bb.public_record_surface.v1.schema.json", schema_dir))
    _reject_inline_models(record_surface)
    roles = record_surface["roles"]
    role_ids = [row["role_id"] for row in roles]
    if len(role_ids) != len(set(role_ids)):
        raise ContractValidationError("duplicate public record role IDs")
    expected_roles = {
        (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), label)
        for label in (frozen or load_frozen_surface())["required_record_roles"]
    }
    actual_roles = {(row["role_id"], row["label"]) for row in roles}
    if actual_roles != expected_roles:
        raise ContractValidationError(f"frozen public record roles mismatch; missing={sorted(expected_roles-actual_roles)}, extra={sorted(actual_roles-expected_roles)}")
    schema_ids = [schema_id for row in roles for schema_id in row["schema_ids"]]
    duplicates = sorted({item for item in schema_ids if schema_ids.count(item) > 1})
    if duplicates:
        raise ContractValidationError(f"record schemas assigned to multiple roles: {', '.join(duplicates)}")
    schema_index = _schema_index(schema_dir, kernel_schema_dir)
    for schema_id in schema_ids:
        if schema_id not in schema_index:
            raise ContractValidationError(f"{schema_id}: schema ID is absent from candidate/kernel roots")
    projection = [{"label": row["label"], "schema_ids": row["schema_ids"]} for row in roles]
    if _sha256(canonical_bytes(projection)) != RECORD_ROLE_PROJECTION_SHA256:
        raise ContractValidationError("record role semantic mapping differs from the frozen projection")

def validate_axis_manifest(manifest: dict[str, Any], schema_dir: Path = SCHEMA_DIR) -> None:
    _raise_if_errors(_schema_errors(manifest, "bb.public_axis_smoke_manifest.v1.schema.json", schema_dir))
    if _sha256(canonical_bytes(manifest)) != AXIS_MANIFEST_SHA256:
        raise ContractValidationError("axis smoke manifest differs from the frozen projection hash")

def validate_public_contracts(public_dir: Path = PUBLIC_DIR) -> None:
    schema_dir = public_dir / "schemas"
    kernel_schema_dir = public_dir.parent / "kernel" / "schemas"
    sync_record_schemas(public_dir)
    frozen = load_frozen_surface(public_dir)
    validate_catalog(load_json(public_dir / "operations.v1.json"), schema_dir, frozen)
    validate_record_surface(load_json(public_dir / "record_surface.v1.json"), schema_dir, kernel_schema_dir, frozen)
    validate_axis_manifest(load_json(public_dir / "axis_smoke.v1.json"), schema_dir)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", type=Path, default=PUBLIC_DIR)
    parser.add_argument("--write-record-schemas", action="store_true")
    args = parser.parse_args(argv)
    if args.write_record_schemas:
        sync_record_schemas(args.public_dir, write=True)
    try:
        validate_public_contracts(args.public_dir)
    except ContractValidationError as exc:
        print(f"public contract validation failed: {exc}")
        return 1
    print("public candidate contracts valid: 45 operations, 6 bindings each, non-active")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
