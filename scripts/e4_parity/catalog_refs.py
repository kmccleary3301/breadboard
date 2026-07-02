#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agentic_coder_prototype.compilation.primitive_records import canonical_record_bytes, sha256_ref

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = ROOT / "docs" / "conformance" / "e4_artifact_catalog.json"

_ROW_ID_FIELDS = (
    "row_id",
    "feature_id",
    "config_id",
    "claim_id",
    "score_row_id",
    "id",
    "name",
)
_CONTAINER_KEYS = (
    "rows",
    "score_rows",
    "entries",
    "records",
    "items",
    "features",
    "artifacts",
    "claims",
    "e4_configs",
)


def load_catalog(path: Path | str | None = None) -> dict[str, Any]:
    """Load an E4 artifact catalog.

    When *path* is omitted, this reads the generated
    ``docs/conformance/e4_artifact_catalog.json`` from the current checkout.
    """

    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"catalog must be a JSON object: {catalog_path}")
    return payload


def entry(catalog: Mapping[str, Any], role_id: str) -> Mapping[str, Any]:
    """Return the catalog entry with exactly matching ``role_id``.

    Raises ``KeyError(role_id)`` when the catalog has no such entry.
    """

    entries = catalog.get("entries")
    if isinstance(entries, Iterable) and not isinstance(entries, (str, bytes, Mapping)):
        for candidate in entries:
            if isinstance(candidate, Mapping) and candidate.get("role_id") == role_id:
                return candidate
    raise KeyError(role_id)


def hash_ref(catalog: Mapping[str, Any], role_id: str) -> str:
    """Return ``<path>#<sha256>`` for a catalog role.

    ``path`` and ``sha256`` are taken from the catalog entry without touching the
    referenced artifact, so this reflects the generated catalog's single hash
    truth.
    """

    catalog_entry = entry(catalog, role_id)
    path = _required_string(catalog_entry, "path", role_id)
    digest = _required_string(catalog_entry, "sha256", role_id)
    return f"{path}#{digest}"


def row_ref(catalog: Mapping[str, Any], role_id: str, row_id: str) -> str:
    """Return ``<path>#<row_id>#<row_hash>`` for a JSON row in a catalog artifact.

    Lookup is deterministic and intentionally shallow:

    * If the artifact is a list, each mapping item is considered a candidate.
    * If the artifact is an object, direct mapping entries whose key equals
      ``row_id`` are considered first, then common containers named ``rows``,
      ``score_rows``, ``entries``, ``records``, ``items``, ``features``,
      ``artifacts``, ``claims``, and ``e4_configs`` are searched in that order.
    * List containers match mapping rows by the first present common id field in
      this order: ``row_id``, ``feature_id``, ``config_id``, ``claim_id``,
      ``score_row_id``, ``id``, ``name``. Mapping containers match by key before
      applying the same id-field checks to their values.

    The row hash is ``sha256`` over canonical JSON bytes for
    ``{"row_id": row_id, "row": row}`` using sorted keys and compact separators.
    Raises ``KeyError(row_id)`` when no matching row is found.
    """

    catalog_entry = entry(catalog, role_id)
    display_path = _required_string(catalog_entry, "path", role_id)
    artifact = _load_json_artifact(display_path)
    row = _find_row(artifact, row_id)
    if row is None:
        raise KeyError(row_id)
    payload = {"row_id": row_id, "row": row}
    return f"{display_path}#{row_id}#{sha256_ref(canonical_record_bytes(payload))}"


def _required_string(catalog_entry: Mapping[str, Any], field: str, role_id: str) -> str:
    value = catalog_entry.get(field)
    if not isinstance(value, str) or not value:
        raise KeyError(f"{role_id}.{field}")
    return value


def _load_json_artifact(display_path: str) -> Any:
    path = _resolve_artifact_path(display_path)
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_artifact_path(display_path: str) -> Path:
    raw = display_path.split("#", 1)[0]
    path = Path(raw)
    if path.is_absolute():
        return path

    root_path = ROOT / path
    if root_path.exists():
        return root_path

    if raw.startswith("docs_tmp/"):
        return ROOT.parent / path

    checkout_path = ROOT.parent / path
    if raw.startswith(f"{ROOT.name}/") and checkout_path.exists():
        return checkout_path

    return root_path


def _find_row(value: Any, row_id: str) -> Any | None:
    if isinstance(value, list):
        return _find_in_list(value, row_id)
    if not isinstance(value, Mapping):
        return None

    direct = value.get(row_id)
    if direct is not None:
        return direct

    for key in _CONTAINER_KEYS:
        if key in value:
            found = _find_in_container(value[key], row_id)
            if found is not None:
                return found
    return None


def _find_in_container(container: Any, row_id: str) -> Any | None:
    if isinstance(container, list):
        return _find_in_list(container, row_id)
    if isinstance(container, Mapping):
        if row_id in container:
            return container[row_id]
        for candidate in container.values():
            if _row_matches(candidate, row_id):
                return candidate
    return None


def _find_in_list(rows: Iterable[Any], row_id: str) -> Any | None:
    for candidate in rows:
        if _row_matches(candidate, row_id):
            return candidate
    return None


def _row_matches(candidate: Any, row_id: str) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    for field in _ROW_ID_FIELDS:
        value = candidate.get(field)
        if isinstance(value, str) and value == row_id:
            return True
    return False
