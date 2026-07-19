"""Safe, workspace-local persistence for product-owned evidence inputs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspacePathError(ValueError):
    """Raised when a product write would escape the public workspace boundary."""


_FORBIDDEN_PUBLIC_PREFIX = Path("docs") / "conformance"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class BreadBoardWorkspace:
    """A checkout-independent ``.breadboard`` output boundary."""

    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise WorkspacePathError(f"workspace root must be an existing directory: {root}")
        object.__setattr__(self, "root", root)

    @property
    def metadata_root(self) -> Path:
        return self.root / ".breadboard"

    @property
    def lanes_root(self) -> Path:
        return self.metadata_root / "lanes"

    def init(self) -> Path:
        self.lanes_root.mkdir(parents=True, exist_ok=True)
        return self.metadata_root

    def path(self, relative: str | Path) -> Path:
        """Resolve a metadata-relative path without allowing public-doc writes."""
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspacePathError("workspace output must be a relative path")
        if candidate.parts[:2] == _FORBIDDEN_PUBLIC_PREFIX.parts:
            raise WorkspacePathError("public workspace writes to docs/conformance are prohibited")
        resolved = (self.root / candidate).resolve()
        if not resolved.is_relative_to(self.metadata_root.resolve()):
            raise WorkspacePathError("product evidence writes must stay under .breadboard")
        return resolved

    def lane_manifest_path(self, lane_id: str) -> Path:
        return self.path(Path(".breadboard") / "lanes" / f"{lane_id}.manifest.json")

    def lane_lock_path(self, lane_id: str) -> Path:
        return self.path(Path(".breadboard") / "lanes" / f"{lane_id}.lock.json")

    def write_json(self, relative: str | Path, value: Any) -> Path:
        destination = self.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_bytes(_json_bytes(value))
        temporary.replace(destination)
        return destination

    def read_json(self, relative: str | Path) -> dict[str, Any]:
        source = self.path(relative)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise WorkspacePathError(f"workspace record must be an object: {source}")
        return payload


def assert_workspace_output(path: str | Path, *, workspace: BreadBoardWorkspace) -> Path:
    """Validate a caller-provided output and return its safe absolute path."""
    return workspace.path(path)
