from __future__ import annotations
import json, math, re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
try:
    import yaml
except ModuleNotFoundError:  # JSON manifests remain usable in minimal installs.
    yaml = None  # type: ignore[assignment]
from .workspace import BreadBoardWorkspace
MANIFEST_SCHEMA_VERSION = "bb.e4.lane_manifest.v2"
LANE_SCHEMA_VERSION = "bb.e4.lane_def.v3"
LEGACY_SCHEMA_VERSIONS = frozenset(("bb.e4.lane_manifest.v1", "bb.e4.lane_def.v1", "bb.e4.lane_def.v2"))
STAGES = frozenset(("capture", "normalize", "replay", "compare", "claim"))
REF_NAMES = ("harness", "target", "adapter", "source", "comparator", "policy")
_MUTABLE_MARKERS = frozenset(("head", "latest", "main", "master", "trunk", "develop", "dev", "working", "workspace"))
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_REFERENCE = re.compile(r"^[a-z0-9._/-]+$")
class LaneValidationError(ValueError): pass
class MutableReferenceError(LaneValidationError): pass
def _json_domain(value: Any, *, pointer: str = "$", _seen: frozenset[int] = frozenset()) -> None:
    if value is None or type(value) in (bool, str, int): return
    if type(value) is float:
        if not math.isfinite(value): raise LaneValidationError(f"{pointer}: non-finite number")
    elif isinstance(value, Mapping):
        if id(value) in _seen or any(type(key) is not str for key in value): raise LaneValidationError(f"{pointer}: cyclic value or non-string object key")
        for key, item in value.items(): _json_domain(item, pointer=f"{pointer}.{key}", _seen=_seen | {id(value)})
    elif type(value) is list:
        if id(value) in _seen: raise LaneValidationError(f"{pointer}: cyclic value")
        for index, item in enumerate(value): _json_domain(item, pointer=f"{pointer}[{index}]", _seen=_seen | {id(value)})
    else: raise LaneValidationError(f"{pointer}: value is not JSON-compatible")
def _canonical(value: Any) -> bytes:
    _json_domain(value); return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
def _mutable_reference(value: str) -> bool:
    text = value.strip(); lower = text.lower()
    if not text or "\n" in text or "\r" in text: return True
    if lower.startswith(("http://", "https://", "git://", "git+", "ssh://")): return True
    if lower in _MUTABLE_MARKERS or any(part in _MUTABLE_MARKERS for part in Path(lower).parts) or lower.endswith((":latest", "@main", "@master", "@head")): return True
    if any(token in lower for token in ("branch=", "branch:", "ref=main", "ref=head", "ref=latest")): return True
    return False
def _validate_ref(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip(): raise LaneValidationError(f"references.{name} must be a non-empty repo-relative path")
    text = value.strip(); path = Path(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in text.split("/")) or "\\" in text or not _REFERENCE.fullmatch(text): raise LaneValidationError(f"references.{name} must be a lowercase repo-relative POSIX path")
    if _mutable_reference(text): raise MutableReferenceError(f"references.{name} points at a mutable reference: {value!r}")
    return text
def _validate_stages(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value): raise LaneValidationError(f"{name} must be an array of stage names")
    stages = list(value); unknown = sorted(set(stages) - STAGES)
    if unknown: raise LaneValidationError(f"{name} contains unknown stage(s): {', '.join(unknown)}")
    if len(stages) != len(set(stages)): raise LaneValidationError(f"{name} contains duplicate stages")
    return stages
def _validate_candidate(document: Mapping[str, Any], *, schema_version: str) -> dict[str, Any]:
    _json_domain(document); allowed = {"schema_version", "lane_id", "status", "execute", "reuse", "references", "metadata"}; unknown = sorted(set(document) - allowed)
    if unknown: raise LaneValidationError(f"candidate lane contains unsupported field(s): {', '.join(unknown)}")
    if document.get("schema_version") != schema_version: raise LaneValidationError(f"expected schema_version {schema_version!r}")
    lane_id = document.get("lane_id")
    if not isinstance(lane_id, str) or not _IDENTIFIER.fullmatch(lane_id): raise LaneValidationError("lane_id must be a lower snake-case identifier")
    status = document.get("status")
    if status not in ("draft", "candidate"): raise LaneValidationError("candidate lane status must be draft or candidate")
    execute = _validate_stages("execute", document.get("execute")); reuse = _validate_stages("reuse", document.get("reuse"))
    if not execute and not reuse: raise LaneValidationError("lane must declare at least one execute or reuse stage")
    overlap = sorted(set(execute) & set(reuse))
    if overlap: raise LaneValidationError(f"stage cannot be both execute and reuse: {', '.join(overlap)}")
    refs = document.get("references")
    if not isinstance(refs, Mapping) or set(refs) != set(REF_NAMES): raise LaneValidationError("references must contain exactly harness, target, adapter, source, comparator, policy")
    normalized_refs = {name: _validate_ref(name, refs[name]) for name in REF_NAMES}; metadata = document.get("metadata", {})
    if not isinstance(metadata, Mapping): raise LaneValidationError("metadata must be an object")
    result = {"schema_version": schema_version, "lane_id": lane_id, "status": status, "execute": execute, "reuse": reuse, "references": normalized_refs}
    if metadata: result["metadata"] = dict(metadata)
    return result
def validate_lane(document: Mapping[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    if not isinstance(document, Mapping): raise LaneValidationError("lane record must be an object")
    version = document.get("schema_version")
    if version in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION): return _validate_candidate(document, schema_version=version)
    if version in LEGACY_SCHEMA_VERSIONS:
        legacy = dict(document); legacy["_authoring_default"] = False; legacy["_legacy_source"] = str(source) if source is not None else None
        return legacy
    prefix = f"{source}: " if source is not None else ""; raise LaneValidationError(prefix + "unsupported lane schema_version")
def _read_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8"); payload = json.loads(text) if path.suffix == ".json" or yaml is None else yaml.safe_load(text)
    except (OSError, ValueError) + ((yaml.YAMLError,) if yaml is not None else ()) as exc: raise LaneValidationError(f"cannot read lane document {path}: {exc}") from exc
    if not isinstance(payload, Mapping): raise LaneValidationError(f"lane document must contain an object: {path}")
    return dict(payload)
def load_lane(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    return validate_lane(_read_document(source), source=source)
def author_lane(document: Mapping[str, Any], workspace: BreadBoardWorkspace) -> Path:
    if document.get("schema_version") not in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION): raise LaneValidationError("legacy lanes are read-only")
    candidate = validate_lane(document)
    destination = workspace.lane_manifest_path(candidate["lane_id"]); existing = tuple(sorted((*workspace.lanes_root.glob("*.manifest.json"), *workspace.lanes_root.glob("*.manifest.yaml"), *workspace.lanes_root.glob("*.manifest.yml"))))
    if destination.exists() or any(load_lane(path)["lane_id"] == candidate["lane_id"] for path in existing): raise LaneValidationError(f"refusing to overwrite author-owned lane: {destination}")
    return workspace.write_json(destination.relative_to(workspace.root), candidate)
def init_lane(workspace: BreadBoardWorkspace, lane_id: str, *, references: Mapping[str, str], execute: list[str] | None = None, reuse: list[str] | None = None, metadata: Mapping[str, Any] | None = None) -> Path:
    document = {"schema_version": MANIFEST_SCHEMA_VERSION, "lane_id": lane_id, "status": "draft", "execute": list(execute or []), "reuse": list(reuse or []), "references": dict(references)}
    if metadata: document["metadata"] = dict(metadata)
    return author_lane(document, workspace)
def iter_authoring_lanes(workspace: BreadBoardWorkspace) -> tuple[dict[str, Any], ...]:
    root = workspace.path(".breadboard/lanes")
    if not root.is_dir(): return ()
    rows = []
    for path in sorted((*root.glob("*.manifest.json"), *root.glob("*.manifest.yaml"), *root.glob("*.manifest.yml"))):
        path = workspace.path(path.relative_to(workspace.root)); value = load_lane(path)
        if value.get("schema_version") in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION):
            if any(row["lane_id"] == value["lane_id"] for row in rows): raise LaneValidationError(f"duplicate lane_id in workspace: {value['lane_id']}")
            rows.append(value)
    return tuple(rows)
def resolve_reference(path: str, *, root: Path) -> Path:
    relative = _validate_ref("reference", path); root_path = root.resolve(); candidate = root_path / relative
    if any(root_path.joinpath(*Path(relative).parts[:index]).is_symlink() for index in range(1, len(Path(relative).parts) + 1)): raise LaneValidationError(f"reference contains a symlink: {path}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root_path): raise LaneValidationError(f"reference escapes checkout: {path}")
    if not resolved.exists(): raise LaneValidationError(f"reference does not exist: {path}")
    return resolved
def canonical_lane_bytes(document: Mapping[str, Any]) -> bytes: return _canonical(validate_lane(document))
