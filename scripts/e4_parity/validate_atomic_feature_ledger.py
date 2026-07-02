#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.atomic_feature_ledger.v1.schema.json"

C2_SOURCE_TIERS = {"C2", "C3", "C4"}
C4_REQUIRED_FIXTURE_KINDS = {"freeze", "capture", "replay", "comparator"}
PRODUCT_PRIMITIVE_NAMES = {
    "pi",
    "omp",
    "claude",
    "codex",
    "opencode",
    "open_code",
    "oh_my_opencode",
    "oh-my-opencode",
    "breadboard",
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|refresh[_-]?token|authorization|cookie)\b\s*[:=]\s*['\"]?(?!hash:|sha256:|ref:|redacted\b)[A-Za-z0-9][A-Za-z0-9._/+=-]{11,}"
    ),
    re.compile(r"(?i)\bbearer\s+(?!redacted\b)[A-Za-z0-9._/+=-]{20,}"),
)


class AtomicFeatureLedgerValidationError(ValueError):
    """Raised when an atomic feature ledger row fails schema or promotion validation."""


def load_schema(schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(Path(schema_path).read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _validator_for_schema(schema_path: str) -> Draft202012Validator:
    schema = load_schema(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _format_jsonschema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"schema.{path}: " if path else "schema: "
    return f"{prefix}{error.message}"


def _ref_kind(ref: Any) -> str | None:
    if not isinstance(ref, str):
        return None
    marker = ref.split(":", 1)[0].strip().lower()
    return marker or None


def _fixture_kinds(row: Mapping[str, Any]) -> set[str]:
    refs = row.get("fixture_refs")
    if not isinstance(refs, list):
        return set()
    return {kind for kind in (_ref_kind(ref) for ref in refs) if kind is not None}


def _normalise_product_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_strings(child, f"{path}[{index}]")


def _looks_like_raw_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _promotion_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    evidence_tier = row.get("evidence_tier")
    source_refs = row.get("source_refs")
    if evidence_tier in C2_SOURCE_TIERS and not source_refs:
        errors.append(f"{evidence_tier} evidence requires at least one source_ref")

    if evidence_tier == "C4":
        missing = sorted(C4_REQUIRED_FIXTURE_KINDS - _fixture_kinds(row))
        if missing:
            errors.append(f"C4 evidence requires fixture_refs for: {', '.join(missing)}")

    mapping = row.get("breadboard_mapping")
    if isinstance(mapping, Mapping):
        primitive = mapping.get("primitive")
        if isinstance(primitive, str) and _normalise_product_name(primitive) in PRODUCT_PRIMITIVE_NAMES:
            errors.append("breadboard_mapping.primitive must not be a product name")

        if (
            mapping.get("truth_scope") == "kernel_truth"
            and (row.get("family") == "projection" or row.get("claim_type") == "host_projection")
        ):
            errors.append("projection or host_projection claims must not be promoted as kernel truth")

    if row.get("claim_type") == "registry_discovery" and row.get("model_visible") is True:
        errors.append("registry discovery must not be recorded as model-visible exposure")

    for path, value in _iter_strings(row):
        if _looks_like_raw_secret(value):
            errors.append(f"{path} appears to contain a raw secret; store a hash or redacted reference instead")

    return errors


def collect_atomic_feature_ledger_errors(
    row: Mapping[str, Any], schema_path: Path | str = DEFAULT_SCHEMA_PATH
) -> list[str]:
    """Return schema and promotion-rule errors for one atomic feature ledger row."""
    validator = _validator_for_schema(str(Path(schema_path)))
    schema_errors = [
        _format_jsonschema_error(error)
        for error in sorted(
            validator.iter_errors(row),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]
    return schema_errors + _promotion_errors(row)


def validate_atomic_feature_ledger_row(
    row: Mapping[str, Any], schema_path: Path | str = DEFAULT_SCHEMA_PATH
) -> None:
    """Raise if one atomic feature ledger row is not schema- and promotion-valid."""
    errors = collect_atomic_feature_ledger_errors(row, schema_path=schema_path)
    if errors:
        raise AtomicFeatureLedgerValidationError("\n".join(errors))


def _rows_from_json_payload(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    else:
        rows = [payload]

    typed_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AtomicFeatureLedgerValidationError("ledger payload must contain object rows")
        typed_rows.append(row)
    return typed_rows


def iter_rows_from_path(path: Path | str) -> list[Mapping[str, Any]]:
    """Load ledger rows from a JSON object, JSON array, {rows: [...]}, or JSONL file."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".jsonl":
        rows: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, Mapping):
                raise AtomicFeatureLedgerValidationError(f"{source}:{line_number}: JSONL row must be an object")
            rows.append(row)
        return rows
    return _rows_from_json_payload(json.loads(text))


def validate_paths(paths: Iterable[Path | str], schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> list[str]:
    """Return labeled validation errors for all rows loaded from the supplied paths."""
    errors: list[str] = []
    for path in paths:
        source = Path(path)
        try:
            rows = iter_rows_from_path(source)
        except Exception as exc:  # pragma: no cover - CLI defensive path
            errors.append(f"{source}: unable to load ledger rows: {exc}")
            continue
        for index, row in enumerate(rows, start=1):
            for error in collect_atomic_feature_ledger_errors(row, schema_path=schema_path):
                errors.append(f"{source} row {index}: {error}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate bb.atomic_feature_ledger.v1 rows and promotion rules.")
    parser.add_argument("paths", nargs="+", help="JSON, JSON array, {rows: [...]}, or JSONL ledger path(s)")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Schema path to use for row validation")
    args = parser.parse_args(argv)

    errors = validate_paths(args.paths, schema_path=args.schema)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
