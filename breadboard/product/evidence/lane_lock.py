"""Machine-owned deterministic lane locks and capture preflight."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

from .lanes import REF_NAMES, LaneValidationError, canonical_lane_bytes, resolve_reference, validate_lane
from .workspace import BreadBoardWorkspace

LOCK_SCHEMA_VERSION = "bb.e4.lane_lock.v2"
LOCK_FORMAT = "canonical-json-v2"


class LaneLockError(ValueError):
    """Raised when a lane cannot be resolved into a machine lock."""


class MutableReferenceError(LaneLockError):
    """Raised when a lane ref is mutable or changed after locking."""


class LaneCompatibilityError(LaneLockError):
    """Raised when target, adapter, harness, comparator, or policy disagree."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _file_digest(path: Path) -> tuple[str, int]:
    if path.is_file():
        payload = path.read_bytes()
        return _sha256_bytes(payload), len(payload)
    if path.is_dir():
        rows: list[dict[str, Any]] = []
        total = 0
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            payload = item.read_bytes()
            total += len(payload)
            rows.append({"path": item.relative_to(path).as_posix(), "sha256": _sha256_bytes(payload), "bytes": len(payload)})
        return _sha256_bytes(_canonical(rows)), total
    raise LaneLockError(f"cannot hash missing reference: {path}")


def _display_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise LaneLockError(f"reference is outside checkout: {path}") from exc


def _load_descriptor(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file() or path.suffix not in (".json", ".yaml", ".yml"):
        return None
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if path.suffix == ".json" or yaml is None else yaml.safe_load(text)
    except OSError as exc:
        raise LaneCompatibilityError(f"cannot read reference descriptor {path}: {exc}") from exc
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


def _values(descriptor: Mapping[str, Any] | None, *keys: str) -> set[str]:
    if descriptor is None:
        return set()
    values: set[str] = set()
    for key in keys:
        value = descriptor.get(key)
        if isinstance(value, str):
            values.add(value)
        elif isinstance(value, (list, tuple, set)):
            values.update(item for item in value if isinstance(item, str))
    return values


def validate_compatibility(document: Mapping[str, Any], *, descriptors: Mapping[str, Mapping[str, Any] | None]) -> None:
    """Check declared identities before a capture adapter is invoked."""
    target = descriptors.get("target")
    target_families = _values(target, "family", "target_family", "target_families", "supported_target_families")
    target_versions = _values(target, "version", "target_version", "target_versions", "supported_target_versions")
    if target is not None and not target_families and not target_versions:
        raise LaneCompatibilityError("target reference does not declare a target identity")
    for name in ("harness", "adapter", "comparator", "policy"):
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue
        families = _values(descriptor, "family", "target_family", "target_families", "supported_target_families")
        versions = _values(descriptor, "version", "target_version", "target_versions", "supported_target_versions")
        if families and target_families and not families.intersection(target_families):
            raise LaneCompatibilityError(f"{name} is incompatible with target family")
        if versions and target_versions and not versions.intersection(target_versions):
            raise LaneCompatibilityError(f"{name} is incompatible with target version")
        config_ids = _values(descriptor, "config_id", "config_ids", "supported_config_ids")
        document_config = document.get("metadata", {}).get("config_id") if isinstance(document.get("metadata"), Mapping) else None
        if config_ids and isinstance(document_config, str) and document_config not in config_ids:
            raise LaneCompatibilityError(f"{name} is incompatible with lane config_id")
    target_config = _values(target, "config_id", "config_ids")
    for name in ("harness", "adapter", "comparator", "policy"):
        config = _values(descriptors.get(name), "config_id", "config_ids")
        if target_config and config and not target_config.intersection(config):
            raise LaneCompatibilityError(f"{name} config is incompatible with target config")


def build_lane_lock(
    document: Mapping[str, Any],
    *,
    root: str | Path,
    manifest_path: str | Path | None = None,
    adapter_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Resolve a candidate and return a byte-stable lock record.

    The resolver is an optional NS06 integration seam.  It enriches adapter
    compatibility checks; it never mutates or consults the E4 adapter registry.
    """
    try:
        lane = validate_lane(document)
    except LaneValidationError as exc:
        raise LaneLockError(str(exc)) from exc
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise LaneLockError(f"lock root must be a directory: {root_path}")
    if lane["status"] not in ("draft", "candidate"):
        raise LaneLockError("only inactive draft/candidate lanes may be locked")
    rows: list[dict[str, Any]] = []
    descriptors: dict[str, Mapping[str, Any] | None] = {}
    references = lane["references"]
    for name in REF_NAMES:
        try:
            resolved = resolve_reference(references[name], root=root_path)
        except LaneValidationError as exc:
            raise LaneLockError(str(exc)) from exc
        digest, size = _file_digest(resolved)
        rows.append({"name": name, "path": _display_path(resolved, root=root_path), "sha256": digest, "bytes": size})
        descriptors[name] = _load_descriptor(resolved)
    if adapter_resolver is not None:
        resolved_adapter = adapter_resolver(references["adapter"])
        if resolved_adapter is not None:
            descriptors["adapter"] = resolved_adapter
    try:
        validate_compatibility(lane, descriptors=descriptors)
    except LaneCompatibilityError:
        raise
    canonical_manifest = canonical_lane_bytes(lane)
    if manifest_path is None:
        manifest_row = {"path": f".breadboard/lanes/{lane['lane_id']}.manifest.json", "sha256": _sha256_bytes(canonical_manifest), "bytes": len(canonical_manifest)}
    else:
        manifest = Path(manifest_path).expanduser().resolve()
        if not manifest.is_file():
            raise LaneLockError(f"manifest does not exist: {manifest}")
        digest, size = _file_digest(manifest)
        manifest_row = {"path": _display_path(manifest, root=root_path), "sha256": digest, "bytes": size}
    rows.sort(key=lambda row: row["name"])
    lock = {
        "schema_version": LOCK_SCHEMA_VERSION,
        "lock_format": LOCK_FORMAT,
        "lane_id": lane["lane_id"],
        "manifest": manifest_row,
        "references": rows,
    }
    lock["lock_sha256"] = _sha256_bytes(_canonical(lock))
    return lock


def lock_lane(
    document: Mapping[str, Any],
    workspace: BreadBoardWorkspace,
    *,
    root: str | Path | None = None,
    adapter_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> Path:
    lane = validate_lane(document)
    workspace.init()
    lock = build_lane_lock(
        lane,
        root=root or workspace.root,
        manifest_path=workspace.lane_manifest_path(lane["lane_id"]) if workspace.lane_manifest_path(lane["lane_id"]).exists() else None,
        adapter_resolver=adapter_resolver,
    )
    return workspace.write_json(Path(".breadboard") / "lanes" / f"{lane['lane_id']}.lock.json", lock)


def validate_before_capture(
    document: Mapping[str, Any],
    lock: Mapping[str, Any],
    *,
    root: str | Path,
) -> None:
    """Fail closed if a candidate is mutable, unlocked, or drifted."""
    lane = validate_lane(document)
    if "capture" not in lane["execute"]:
        raise LaneLockError("capture is not declared in execute stages")
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION or lock.get("lock_format") != LOCK_FORMAT:
        raise LaneLockError("capture requires a bb.e4.lane_lock.v2 lock")
    if lock.get("lane_id") != lane["lane_id"]:
        raise LaneLockError("lane lock lane_id does not match manifest")
    current = build_lane_lock(lane, root=root)
    if lock.get("manifest", {}).get("sha256") != current["manifest"]["sha256"]:
        raise MutableReferenceError("manifest changed after lane lock")
    if lock.get("references") != current["references"]:
        raise MutableReferenceError("lane reference changed after lane lock")
    expected_hash = lock.get("lock_sha256")
    unsigned = dict(lock)
    unsigned.pop("lock_sha256", None)
    if expected_hash != _sha256_bytes(_canonical(unsigned)):
        raise LaneLockError("lane lock digest is invalid")
