from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from scripts.e4_parity.validators.gate_errors import GateError

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_DIR = REPO_ROOT / "contracts" / "kernel" / "registries"


class RegistryValidationError(ValueError):
    """Exception wrapper for registry GateError semantics."""

    def __init__(self, gate_error: GateError) -> None:
        self.gate_error = gate_error
        super().__init__(gate_error.message or gate_error.code)


@lru_cache(maxsize=None)
def _load_registry_from_dir(registry_dir: str, registry_id: str) -> dict[str, Any]:
    path = Path(registry_dir) / f"{registry_id}.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"registry root must be an object: {path}")
    if payload.get("registry_id") != registry_id:
        raise ValueError(f"registry_id mismatch in {path}: {payload.get('registry_id')!r}")
    return payload


def load_registry(registry_id: str) -> dict[str, Any]:
    """Load a kernel identifier registry by registry_id from contracts/kernel/registries."""
    if not registry_id or "/" in registry_id or ".." in registry_id:
        raise ValueError(f"invalid registry_id: {registry_id!r}")
    return _load_registry_from_dir(str(REGISTRY_DIR), registry_id)


load_registry.cache_clear = _load_registry_from_dir.cache_clear  # type: ignore[attr-defined]

def schema_generation_default(family: str) -> str:
    """Return the single active generation-default schema for a lifecycle family."""
    if not family:
        raise ValueError("schema lifecycle family must be non-empty")
    registry = load_registry("schema_lifecycle")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise ValueError("schema_lifecycle entries must be a list")
    defaults = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("family") == family
        and entry.get("default_for_generation") is True
    ]
    if len(defaults) != 1:
        raise ValueError(f"schema lifecycle family {family!r} has {len(defaults)} generation defaults")
    schema_id = defaults[0].get("schema_id")
    if defaults[0].get("lifecycle") != "active_production" or not isinstance(schema_id, str):
        raise ValueError(f"schema lifecycle family {family!r} generation default is not active")
    return schema_id


def _unregistered_identifier_error(
    registry_id: str,
    value: str,
    *,
    status: str | None = None,
    expected: str = "active",
) -> GateError:
    got = status or "missing"
    return GateError(
        code="unregistered_identifier",
        gate="c4_chain",
        klass="semantic",
        subject={"registry_id": registry_id, "value": value},
        expected=expected,
        got=got,
        remedy=f"Add an active {value!r} row to contracts/kernel/registries/{registry_id}.v1.json or correct the identifier.",
        blame=(),
        message=f"{registry_id}: unregistered identifier {value!r} (expected {expected}, got {got})",
    )


def _registry_entry(registry_id: str, value: str) -> Mapping[str, Any] | None:
    registry = load_registry(registry_id)
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"registry entries must be a list: {registry_id}")
    by_id: dict[str, Mapping[str, Any]] = {
        str(entry.get("id")): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    }
    return by_id.get(value)


def assert_registered(
    registry_id: str,
    value: str,
    *,
    allow_deprecated: bool = False,
    expected_kind: str | None = None,
) -> None:
    """Raise RegistryValidationError when value is not active in the named registry."""
    entry = _registry_entry(registry_id, value)
    if entry is None:
        raise RegistryValidationError(_unregistered_identifier_error(registry_id, value))
    status = entry.get("status")
    if status != "active" and not (allow_deprecated and status == "deprecated"):
        expected = "active or deprecated" if allow_deprecated else "active"
        raise RegistryValidationError(
            _unregistered_identifier_error(
                registry_id,
                value,
                status=str(status) if isinstance(status, str) else "invalid_status",
                expected=expected,
            )
        )
    if expected_kind is None:
        return
    metadata = entry.get("metadata")
    actual_kind = metadata.get("kind") if isinstance(metadata, Mapping) else None
    if actual_kind != expected_kind:
        raise RegistryValidationError(
            GateError(
                code="wrong_registry_kind",
                gate="c4_chain",
                klass="semantic",
                subject={"registry_id": registry_id, "value": value},
                expected=f"active {expected_kind}",
                got=str(actual_kind) if isinstance(actual_kind, str) else "missing_kind",
                remedy=f"Correct {value!r} metadata.kind in contracts/kernel/registries/{registry_id}.v1.json or use an id of kind {expected_kind!r}.",
                blame=(),
                message=f"{registry_id}: identifier {value!r} has wrong kind (expected {expected_kind!r}, got {actual_kind!r})",
            )
        )
