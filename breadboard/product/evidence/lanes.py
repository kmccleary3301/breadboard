"""Product-owned lane authoring and validation.

The v2 manifest and v3 lane definition are deliberately candidate-only.  The
reader still accepts the historical E4 records so existing evidence remains
inspectable, but those records never enter the authoring inventory.
"""

from __future__ import annotations

import json
import re
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


class LaneValidationError(ValueError):
    """Raised for malformed or unsafe lane authoring records."""


class MutableReferenceError(LaneValidationError):
    """Raised when a candidate points at a mutable target/source identity."""


def _json_domain(value: Any, *, pointer: str = "$") -> None:
    if value is None or type(value) in (bool, str, int):
        return
    if type(value) is float:
        if not __import__("math").isfinite(value):
            raise LaneValidationError(f"{pointer}: non-finite number")
        return
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise LaneValidationError(f"{pointer}: object keys must be strings")
        for key, item in value.items():
            _json_domain(item, pointer=f"{pointer}.{key}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _json_domain(item, pointer=f"{pointer}[{index}]")
        return
    raise LaneValidationError(f"{pointer}: value is not JSON-compatible")


def _canonical(value: Any) -> bytes:
    _json_domain(value)
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _mutable_reference(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    if not text or "\n" in text or "\r" in text:
        return True
    if lower.startswith(("http://", "https://", "git://", "git+", "ssh://")):
        return True
    if lower in _MUTABLE_MARKERS or Path(lower).name in _MUTABLE_MARKERS or lower.endswith((":latest", "@main", "@master", "@head")):
        return True
    if any(token in lower for token in ("branch=", "branch:", "ref=main", "ref=head", "ref=latest")):
        return True
    return False


def _validate_ref(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LaneValidationError(f"references.{name} must be a non-empty repo-relative path")
    text = value.strip()
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text:
        raise LaneValidationError(f"references.{name} must be a repo-relative POSIX path")
    if _mutable_reference(text):
        raise MutableReferenceError(f"references.{name} points at a mutable reference: {value!r}")
    return text


def _validate_stages(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise LaneValidationError(f"{name} must be an array of stage names")
    stages = list(value)
    unknown = sorted(set(stages) - STAGES)
    if unknown:
        raise LaneValidationError(f"{name} contains unknown stage(s): {', '.join(unknown)}")
    if len(stages) != len(set(stages)):
        raise LaneValidationError(f"{name} contains duplicate stages")
    return stages


def _validate_candidate(document: Mapping[str, Any], *, schema_version: str) -> dict[str, Any]:
    allowed = {"schema_version", "lane_id", "status", "execute", "reuse", "references", "metadata"}
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise LaneValidationError(f"candidate lane contains unsupported field(s): {', '.join(unknown)}")
    if document.get("schema_version") != schema_version:
        raise LaneValidationError(f"expected schema_version {schema_version!r}")
    lane_id = document.get("lane_id")
    if not isinstance(lane_id, str) or not _IDENTIFIER.fullmatch(lane_id):
        raise LaneValidationError("lane_id must be a lower snake-case identifier")
    status = document.get("status")
    if status not in ("draft", "candidate"):
        raise LaneValidationError("candidate lane status must be draft or candidate")
    execute = _validate_stages("execute", document.get("execute"))
    reuse = _validate_stages("reuse", document.get("reuse"))
    if not execute and not reuse:
        raise LaneValidationError("lane must declare at least one execute or reuse stage")
    overlap = sorted(set(execute) & set(reuse))
    if overlap:
        raise LaneValidationError(f"stage cannot be both execute and reuse: {', '.join(overlap)}")
    refs = document.get("references")
    if not isinstance(refs, Mapping) or set(refs) != set(REF_NAMES):
        raise LaneValidationError("references must contain exactly harness, target, adapter, source, comparator, policy")
    normalized_refs = {name: _validate_ref(name, refs[name]) for name in REF_NAMES}
    metadata = document.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise LaneValidationError("metadata must be an object")
    result = {
        "schema_version": schema_version,
        "lane_id": lane_id,
        "status": status,
        "execute": execute,
        "reuse": reuse,
        "references": normalized_refs,
    }
    if metadata:
        result["metadata"] = dict(metadata)
    return result


def validate_lane(document: Mapping[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    """Validate a candidate manifest/definition without consulting registries."""
    if not isinstance(document, Mapping):
        raise LaneValidationError("lane record must be an object")
    version = document.get("schema_version")
    if version == MANIFEST_SCHEMA_VERSION:
        return _validate_candidate(document, schema_version=MANIFEST_SCHEMA_VERSION)
    if version == LANE_SCHEMA_VERSION:
        return _validate_candidate(document, schema_version=LANE_SCHEMA_VERSION)
    if version in LEGACY_SCHEMA_VERSIONS:
        # Legacy records are intentionally read-only.  Preserve their bytes/shape
        # and mark them so callers cannot accidentally select them for authoring.
        legacy = dict(document)
        legacy["_authoring_default"] = False
        legacy["_legacy_source"] = str(source) if source is not None else None
        return legacy
    prefix = f"{source}: " if source is not None else ""
    raise LaneValidationError(prefix + "unsupported lane schema_version")


def _read_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text) if path.suffix == ".json" or yaml is None else yaml.safe_load(text)
    except (OSError, ValueError) as exc:
        raise LaneValidationError(f"cannot read lane document {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise LaneValidationError(f"lane document must contain an object: {path}")
    return dict(payload)


def load_lane(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    return validate_lane(_read_document(source), source=source)


def author_lane(document: Mapping[str, Any], workspace: BreadBoardWorkspace) -> Path:
    """Persist one candidate manifest under ``workspace/.breadboard``."""
    candidate = validate_lane({**dict(document), "schema_version": MANIFEST_SCHEMA_VERSION})
    return workspace.write_json(
        Path(".breadboard") / "lanes" / f"{candidate['lane_id']}.manifest.json", candidate
    )


def init_lane(
    workspace: BreadBoardWorkspace,
    lane_id: str,
    *,
    references: Mapping[str, str],
    execute: list[str] | None = None,
    reuse: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane_id": lane_id,
        "status": "draft",
        "execute": list(execute or []),
        "reuse": list(reuse or []),
        "references": dict(references),
    }
    if metadata:
        document["metadata"] = dict(metadata)
    return author_lane(document, workspace)


def iter_authoring_lanes(workspace: BreadBoardWorkspace) -> tuple[dict[str, Any], ...]:
    """List only product-owned candidate manifests; legacy lanes stay readable."""
    if not workspace.lanes_root.is_dir():
        return ()
    rows = []
    for path in sorted(workspace.lanes_root.glob("*.manifest.json")):
        value = load_lane(path)
        if value.get("schema_version") == MANIFEST_SCHEMA_VERSION:
            rows.append(value)
    return tuple(rows)


def resolve_reference(path: str, *, root: Path) -> Path:
    """Resolve a manifest reference for locking without ancestor-checkout probing."""
    relative = _validate_ref("reference", path)
    resolved = (root.resolve() / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise LaneValidationError(f"reference escapes checkout: {path}")
    if not resolved.exists():
        raise LaneValidationError(f"reference does not exist: {path}")
    return resolved


def canonical_lane_bytes(document: Mapping[str, Any]) -> bytes:
    return _canonical(validate_lane(document))
