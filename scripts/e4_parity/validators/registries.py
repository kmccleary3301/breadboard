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


def assert_registered(registry_id: str, value: str, *, allow_deprecated: bool = False) -> None:
    """Raise RegistryValidationError when value is not active in the named registry."""
    registry = load_registry(registry_id)
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"registry entries must be a list: {registry_id}")
    by_id: dict[str, Mapping[str, Any]] = {
        str(entry.get("id")): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    }
    entry = by_id.get(value)
    if entry is None:
        raise RegistryValidationError(_unregistered_identifier_error(registry_id, value))
    status = entry.get("status")
    if status == "active" or (allow_deprecated and status == "deprecated"):
        return
    expected = "active or deprecated" if allow_deprecated else "active"
    raise RegistryValidationError(
        _unregistered_identifier_error(
            registry_id,
            value,
            status=str(status) if isinstance(status, str) else "invalid_status",
            expected=expected,
        )
    )
