#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
COVERAGE_DIR = WORKSPACE / "docs_tmp" / "phase_16" / "coverage"
SCHEMA_PATH_V1 = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.target_coverage.v1.schema.json"
SCHEMA_PATH_V2 = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.target_coverage.v2.schema.json"
SCHEMA_PATH = SCHEMA_PATH_V2
SUPPORT_CLAIMS_DIR = ROOT / "docs" / "conformance" / "support_claims"
REGISTRY_ID = "target_families"
REGISTRY_PATH = ROOT / "contracts" / "kernel" / "registries" / "target_families.v1.json"

COMMON_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.kernel.common.v1.schema.json"

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE))
    except ValueError:
        return str(path)


def resolve_ref(ref: str) -> Path:
    raw = ref.split("#", 1)[0]
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if raw.startswith("docs_tmp/") or raw.startswith(f"{ROOT.name}/"):
        return (WORKSPACE / raw).resolve()
    return (ROOT / raw).resolve()


def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return f"schema.{path}: {error.message}" if path else f"schema: {error.message}"


def _coverage_validator(schema_path: Path) -> Draft202012Validator:
    schema = load_json(schema_path)
    common_schema = load_json(COMMON_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    store = {
        str(schema.get("$id")): schema,
        schema_path.name: schema,
        schema_path.as_uri(): schema,
        str(common_schema.get("$id")): common_schema,
        COMMON_SCHEMA_PATH.name: common_schema,
        COMMON_SCHEMA_PATH.as_uri(): common_schema,
    }
    resolver = RefResolver(base_uri=schema_path.parent.resolve().as_uri() + "/", referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver)


def _load_target_families_registry() -> Mapping[str, Any]:
    try:
        from scripts.e4_parity.validators.registries import load_registry
    except (ImportError, ModuleNotFoundError):
        payload = load_json(REGISTRY_PATH)
    else:
        payload = load_registry(REGISTRY_ID)
    if not isinstance(payload, Mapping):
        raise TypeError(f"{REGISTRY_ID} registry must be a JSON object")
    return payload


def _registry_entry_id(entry: Mapping[str, Any]) -> str | None:
    for key in ("value", "id", "identifier", "target_family", "family_id", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _registry_entries(registry: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    for key in ("entries", "items", "values", "target_families", "families"):
        raw_entries = registry.get(key)
        if isinstance(raw_entries, list):
            entries = tuple(entry for entry in raw_entries if isinstance(entry, Mapping) and _registry_entry_id(entry))
            if entries:
                return entries
        if isinstance(raw_entries, Mapping):
            entries = []
            for value, entry in raw_entries.items():
                if isinstance(entry, Mapping):
                    entry_with_id = dict(entry)
                    entry_with_id.setdefault("value", str(value))
                    entries.append(entry_with_id)
            if entries:
                return tuple(entries)
    return ()


def _entry_metadata(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = entry.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _entry_is_coverage_enabled(entry: Mapping[str, Any]) -> bool:
    metadata = _entry_metadata(entry)
    status = str(entry.get("status") or metadata.get("status") or "").lower()
    if status in {"deprecated", "removed", "retired"}:
        return False
    if bool(entry.get("deprecated")) or bool(metadata.get("deprecated")):
        return False
    enabled = entry.get("coverage_enabled", metadata.get("coverage_enabled", True))
    return enabled is not False


def _registered_target_families() -> tuple[str, ...]:
    seen: set[str] = set()
    families: list[str] = []
    for entry in _registry_entries(_load_target_families_registry()):
        family = _registry_entry_id(entry)
        if family is None or family in seen or not _entry_is_coverage_enabled(entry):
            continue
        seen.add(family)
        families.append(family)
    if not families:
        raise ValueError(f"{REGISTRY_ID} registry has no coverage-enabled target families")
    return tuple(families)


def _claim_index() -> dict[str, Mapping[str, Any]]:
    claims: dict[str, Mapping[str, Any]] = {}
    for path in sorted(SUPPORT_CLAIMS_DIR.glob("*_support_claim.json")):
        payload = load_json(path)
        if not isinstance(payload, Mapping):
            continue
        claims[display(path)] = payload
        claims[str(payload.get("claim_id"))] = payload
    return claims


def validate(paths: Sequence[Path] | None = None) -> dict[str, Any]:
    explicit_paths = paths is not None
    schemas = {
        "bb.e4.target_coverage.v1": load_json(SCHEMA_PATH_V1),
        "bb.e4.target_coverage.v2": load_json(SCHEMA_PATH_V2),
    }
    validators = {
        "bb.e4.target_coverage.v1": _coverage_validator(SCHEMA_PATH_V1),
        "bb.e4.target_coverage.v2": _coverage_validator(SCHEMA_PATH_V2),
    }
    paths = list(paths or sorted(COVERAGE_DIR.glob("*_target_coverage.json")))
    registered_families = set(_registered_target_families())
    claims = _claim_index()
    errors: list[str] = []
    seen_families: set[str] = set()
    seen_versions: set[str] = set()
    for path in paths:
        payload = load_json(path)
        if not isinstance(payload, Mapping):
            errors.append(f"{display(path)}: root must be an object")
            continue
        schema_version = str(payload.get("schema_version"))
        schema = schemas.get(schema_version)
        validator = validators.get(schema_version)
        if schema is None or validator is None:
            errors.append(f"{display(path)}: schema.schema_version: unsupported target coverage schema {payload.get('schema_version')!r}")
            continue
        seen_versions.add(schema_version)
        errors.extend(
            f"{display(path)}: {_format_schema_error(error)}"
            for error in sorted(validator.iter_errors(payload), key=lambda item: (list(item.absolute_path), item.message))
        )
        family = payload.get("target_family")
        if isinstance(family, str):
            seen_families.add(family)
            if family not in registered_families:
                errors.append(f"{display(path)}: unregistered target_family {family!r}")
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        represented = {row.get("behavior_family") for row in rows if isinstance(row, Mapping)}
        behavior_family_schema = schema["properties"]["rows"]["items"]["properties"]["behavior_family"]
        missing_behavior_families = sorted(set(behavior_family_schema["enum"]) - represented)
        if missing_behavior_families:
            errors.append(f"{display(path)}: missing behavior_family rows: {', '.join(missing_behavior_families)}")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            status = row.get("status")
            claim_refs = row.get("claim_refs") if isinstance(row.get("claim_refs"), list) else []
            if status == "c4_accepted" and not claim_refs:
                errors.append(f"{display(path)}.rows[{index}]: c4_accepted requires claim_refs")
            for claim_ref in claim_refs:
                if not isinstance(claim_ref, str) or not claim_ref:
                    errors.append(f"{display(path)}.rows[{index}].claim_refs contains non-string ref")
                    continue
                claim = claims.get(claim_ref)
                if claim is None:
                    claim_path = resolve_ref(claim_ref)
                    if claim_path.exists():
                        loaded = load_json(claim_path)
                        claim = loaded if isinstance(loaded, Mapping) else None
                if claim is None:
                    errors.append(f"{display(path)}.rows[{index}].claim_ref unresolved: {claim_ref}")
                    continue
                if claim.get("schema_version") != "bb.e4.support_claim.v2" or claim.get("accepted") is not True:
                    errors.append(f"{display(path)}.rows[{index}].claim_ref is not an accepted support_claim.v2: {claim_ref}")
                if family and claim.get("target_family") != family:
                    errors.append(f"{display(path)}.rows[{index}].claim_ref family mismatch: {claim_ref}")
                behavior_ids = {
                    behavior.get("behavior_id")
                    for behavior in (claim.get("claim_semantics") or {}).get("asserted_behaviors", [])
                    if isinstance(behavior, Mapping)
                }
                if row.get("behavior_id") not in behavior_ids:
                    errors.append(f"{display(path)}.rows[{index}].behavior_id not asserted by claim_ref {claim_ref}: {row.get('behavior_id')}")
    if not explicit_paths:
        if "bb.e4.target_coverage.v2" in seen_versions:
            expected_families = registered_families
        else:
            v1_family_schema = schemas["bb.e4.target_coverage.v1"]["properties"]["target_family"]
            expected_families = set(v1_family_schema.get("enum", ()))
        missing_files = sorted(expected_families - seen_families)
        if missing_files:
            errors.append(f"missing target_family coverage files: {', '.join(missing_files)}")
    return {
        "schema_version": "bb.e4.coverage_validation_report.v1",
        "coverage_dir": display(COVERAGE_DIR),
        "file_count": len(paths),
        "ok": not errors,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate E4 target coverage matrices.")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    paths = [resolve_ref(path) for path in args.paths] if args.paths else None
    report = validate(paths)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
