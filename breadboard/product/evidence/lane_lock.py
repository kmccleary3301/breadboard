from __future__ import annotations
import hashlib, json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]
from .lanes import (
    LANE_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION, REF_NAMES, LaneValidationError,
    MutableReferenceError, _validate_ref, canonical_lane_bytes, load_lane, resolve_reference, validate_lane,
)
from .workspace import BreadBoardWorkspace
LOCK_SCHEMA_VERSION = "bb.e4.lane_lock.v2"
LOCK_FORMAT = "canonical-json-v2"
class LaneLockError(ValueError): pass
class LaneResolutionError(LaneLockError): pass
class LaneCompatibilityError(LaneLockError): pass
def _sha256_bytes(value: bytes) -> str: return "sha256:" + hashlib.sha256(value).hexdigest()
def _canonical(value: Any) -> bytes: return (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
def _file_digest(path: Path) -> tuple[str, int]:
    if path.is_file():
        payload = path.read_bytes(); return _sha256_bytes(payload), len(payload)
    if path.is_dir():
        rows: list[dict[str, Any]] = []; total = 0; items = tuple(sorted(path.rglob("*")))
        if any(item.is_symlink() for item in items): raise LaneLockError(f"directory reference contains a symlink: {path}")
        for item in items:
            if not item.is_file(): continue
            payload = item.read_bytes(); total += len(payload); rows.append({"path": item.relative_to(path).as_posix(), "sha256": _sha256_bytes(payload), "bytes": len(payload)})
        return _sha256_bytes(_canonical(rows)), total
    raise LaneLockError(f"cannot hash missing reference: {path}")
def _display_path(path: Path, *, root: Path) -> str:
    try:
        return _validate_ref("lock path", path.resolve().relative_to(root.resolve()).as_posix())
    except ValueError as exc:
        raise LaneLockError(f"lock path is outside checkout or invalid: {path}") from exc
def _load_descriptor(path: Path, *, required: bool) -> Mapping[str, Any] | None:
    if not path.is_file() or path.suffix not in (".json", ".yaml", ".yml"): return None
    try:
        text = path.read_text(encoding="utf-8"); value = json.loads(text) if path.suffix == ".json" or yaml is None else yaml.safe_load(text)
    except OSError as exc: raise LaneCompatibilityError(f"cannot read reference descriptor {path}: {exc}") from exc
    except (ValueError,) + ((yaml.YAMLError,) if yaml is not None else ()) as exc:
        if required: raise LaneCompatibilityError(f"invalid reference descriptor {path}: {exc}") from exc
        return None
    if required and not isinstance(value, Mapping): raise LaneCompatibilityError(f"reference descriptor must be an object: {path}")
    return value if isinstance(value, Mapping) else None
def _values(descriptor: Mapping[str, Any] | None, *keys: str) -> set[str]:
    if descriptor is None: return set()
    values: set[str] = set()
    for key in keys:
        value = descriptor.get(key)
        if isinstance(value, str): values.add(value)
        elif isinstance(value, (list, tuple, set)):
            if any(not isinstance(item, str) for item in value): raise LaneCompatibilityError(f"compatibility field {key} must contain strings")
            values.update(value)
        elif value is not None: raise LaneCompatibilityError(f"compatibility field {key} must be a string or array")
    return values
def validate_compatibility(document: Mapping[str, Any], *, descriptors: Mapping[str, Mapping[str, Any] | None]) -> None:
    target, adapter = descriptors.get("target"), descriptors.get("adapter")
    if target is None: raise LaneCompatibilityError("target reference must be a structured descriptor")
    if adapter is None: raise LaneCompatibilityError("adapter reference must be a structured descriptor")
    family_keys = ("family", "target_family", "target_families", "supported_target_families"); version_keys = ("version", "target_version", "target_versions", "supported_target_versions"); config_keys = ("config_id", "config_ids", "supported_config_ids")
    target_families, target_versions, target_config = _values(target, *family_keys), _values(target, *version_keys), _values(target, *config_keys)
    adapter_families, adapter_versions, adapter_config = _values(adapter, *family_keys), _values(adapter, *version_keys), _values(adapter, *config_keys)
    if not target_families and not target_versions: raise LaneCompatibilityError("target reference does not declare a target identity")
    if not adapter_families and not adapter_versions and not adapter_config: raise LaneCompatibilityError("adapter reference does not declare target compatibility")
    document_config = document.get("metadata", {}).get("config_id") if isinstance(document.get("metadata"), Mapping) else None
    if target_config and isinstance(document_config, str) and document_config not in target_config: raise LaneCompatibilityError("target is incompatible with lane config_id")
    for name in ("harness", "adapter", "comparator", "policy"):
        descriptor = descriptors.get(name)
        if descriptor is None: continue
        families, versions, config_ids = _values(descriptor, *family_keys), _values(descriptor, *version_keys), _values(descriptor, *config_keys)
        if families and target_families and not families.intersection(target_families): raise LaneCompatibilityError(f"{name} is incompatible with target family")
        if versions and target_versions and not versions.intersection(target_versions): raise LaneCompatibilityError(f"{name} is incompatible with target version")
        if config_ids and isinstance(document_config, str) and document_config not in config_ids: raise LaneCompatibilityError(f"{name} is incompatible with lane config_id")
        if target_config and config_ids and not target_config.intersection(config_ids): raise LaneCompatibilityError(f"{name} config is incompatible with target config")
    if any(left and (not right or not left.intersection(right)) for left, right in ((target_families, adapter_families), (target_versions, adapter_versions), (target_config, adapter_config))): raise LaneCompatibilityError("adapter does not share a compatible target identity")
def build_lane_lock(document: Mapping[str, Any], *, root: str | Path, manifest_path: str | Path | None = None, adapter_resolver: Callable[[str], Mapping[str, Any] | None] | None = None) -> dict[str, Any]:
    try: lane = validate_lane(document)
    except MutableReferenceError: raise
    except LaneValidationError as exc: raise LaneLockError(str(exc)) from exc
    if lane.get("schema_version") not in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION): raise LaneLockError("legacy lanes are read-only and cannot be locked")
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir(): raise LaneLockError(f"lock root must be a directory: {root_path}")
    if lane["status"] not in ("draft", "candidate"): raise LaneLockError("only inactive draft/candidate lanes may be locked")
    rows: list[dict[str, Any]] = []; descriptors: dict[str, Mapping[str, Any] | None] = {}; references = lane["references"]
    for name in REF_NAMES:
        try: resolved = resolve_reference(references[name], root=root_path)
        except LaneValidationError as exc: raise LaneResolutionError(str(exc)) from exc
        digest, size = _file_digest(resolved); rows.append({"name": name, "path": _display_path(resolved, root=root_path), "sha256": digest, "bytes": size}); descriptors[name] = _load_descriptor(resolved, required=name in ("target", "adapter"))
    if adapter_resolver is not None and (resolved_adapter := adapter_resolver(references["adapter"])) is not None: descriptors["adapter"] = resolved_adapter
    validate_compatibility(lane, descriptors=descriptors); canonical_manifest = canonical_lane_bytes(lane)
    if manifest_path is None: raise LaneLockError("candidate lane manifest path is required")
    manifest = Path(manifest_path).expanduser().absolute()
    if not manifest.is_relative_to(root_path) or manifest.is_symlink() or any(parent.is_symlink() for parent in manifest.parents if parent.is_relative_to(root_path)): raise LaneLockError(f"manifest contains a symlink or is outside checkout: {manifest}")
    manifest = manifest.resolve()
    if not manifest.is_file(): raise LaneLockError(f"manifest does not exist: {manifest}")
    if canonical_lane_bytes(load_lane(manifest)) != canonical_manifest: raise MutableReferenceError("manifest content does not match the lane document")
    manifest_row = {"path": _display_path(manifest, root=root_path), "sha256": _sha256_bytes(payload := manifest.read_bytes()), "bytes": len(payload)}
    rows.sort(key=lambda row: row["name"]); lock = {"schema_version": LOCK_SCHEMA_VERSION, "lock_format": LOCK_FORMAT, "lane_id": lane["lane_id"], "manifest": manifest_row, "references": rows}; lock["lock_sha256"] = _sha256_bytes(_canonical(lock))
    return lock
def lock_lane(document: Mapping[str, Any], workspace: BreadBoardWorkspace, *, root: str | Path | None = None, manifest_path: str | Path | None = None, adapter_resolver: Callable[[str], Mapping[str, Any] | None] | None = None) -> Path:
    lane = validate_lane(document); root_path = Path(root or workspace.root).expanduser().resolve(); candidates = tuple(sorted((*workspace.lanes_root.glob("*.manifest.json"), *workspace.lanes_root.glob("*.manifest.yaml"), *workspace.lanes_root.glob("*.manifest.yml")))); existing = tuple(path for path in candidates if load_lane(path)["lane_id"] == lane["lane_id"])
    if len(existing) > 1: raise LaneLockError(f"multiple lane manifests found for {lane['lane_id']}")
    selected_manifest = manifest_path or (existing[0] if existing else None)
    if selected_manifest is None: raise LaneLockError("candidate lane manifest must exist in the workspace or be supplied")
    lock = build_lane_lock(lane, root=root_path, manifest_path=selected_manifest, adapter_resolver=adapter_resolver)
    destination = Path(".breadboard") / "lanes" / f"{lane['lane_id']}.lock.json"
    if workspace.path(destination).exists(): raise LaneLockError(f"refusing to overwrite lane lock: {workspace.path(destination)}")
    return workspace.write_json(destination, lock)
def validate_before_capture(document: Mapping[str, Any], lock: Mapping[str, Any], *, root: str | Path, manifest_path: str | Path | None = None, adapter_resolver: Callable[[str], Mapping[str, Any] | None] | None = None) -> None:
    lane = validate_lane(document)
    if lane.get("schema_version") not in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION): raise LaneLockError("legacy lanes are read-only and cannot be captured")
    if "capture" not in lane["execute"]: raise LaneLockError("capture is not declared in execute stages")
    if not isinstance(lock, Mapping) or set(lock) != {"schema_version", "lock_format", "lane_id", "manifest", "references", "lock_sha256"} or lock.get("schema_version") != LOCK_SCHEMA_VERSION or lock.get("lock_format") != LOCK_FORMAT: raise LaneLockError("capture requires an exact bb.e4.lane_lock.v2 lock")
    if lock.get("lane_id") != lane["lane_id"]: raise LaneLockError("lane lock lane_id does not match manifest")
    manifest_row = lock.get("manifest")
    if not isinstance(manifest_row, Mapping) or set(manifest_row) != {"path", "sha256", "bytes"} or not all(isinstance(manifest_row.get(key), str) for key in ("path", "sha256")) or type(manifest_row.get("bytes")) is not int or not isinstance((reference_rows := lock.get("references")), list) or any(not isinstance(row, Mapping) or set(row) != {"name", "path", "sha256", "bytes"} or not all(isinstance(row.get(key), str) for key in ("name", "path", "sha256")) or type(row.get("bytes")) is not int for row in reference_rows): raise LaneLockError("capture requires an exact bb.e4.lane_lock.v2 lock")
    if manifest_path is None and not (candidate := Path(root).expanduser().resolve() / manifest_row["path"]).exists(): raise MutableReferenceError("manifest missing after lane lock")
    if manifest_path is None: manifest_path = candidate
    current = build_lane_lock(lane, root=root, manifest_path=manifest_path, adapter_resolver=adapter_resolver)
    if lock.get("manifest") != current["manifest"]: raise MutableReferenceError("manifest changed after lane lock")
    if lock.get("references") != current["references"]: raise MutableReferenceError("lane reference changed after lane lock")
    expected_hash = lock.get("lock_sha256"); unsigned = dict(lock); unsigned.pop("lock_sha256", None)
    if expected_hash != _sha256_bytes(_canonical(unsigned)): raise LaneLockError("lane lock digest is invalid")
