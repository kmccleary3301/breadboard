from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
class WorkspacePathError(ValueError): pass
_FORBIDDEN_PUBLIC_PREFIX = Path("docs") / "conformance"
def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
@dataclass(frozen=True)
class BreadBoardWorkspace:
    root: Path
    def __post_init__(self) -> None:
        raw = Path(self.root).expanduser().absolute(); root = raw.resolve() if not any(path.is_symlink() for path in (raw, *raw.parents)) else (_ for _ in ()).throw(WorkspacePathError(f"workspace root contains a symlink: {raw}"))
        if any(root.parts[index:index + 2] == ("docs", "conformance") for index in range(len(root.parts) - 1)):
            raise WorkspacePathError("product workspace must not be rooted under docs/conformance")
        if root.exists() and not root.is_dir():
            raise WorkspacePathError(f"workspace root must be a directory: {root}")
        object.__setattr__(self, "root", root)
    @property
    def metadata_root(self) -> Path:
        return self.root / ".breadboard"
    @property
    def lanes_root(self) -> Path:
        return self.metadata_root / "lanes"
    def path(self, relative: str | Path) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspacePathError("workspace output must be a relative path")
        if candidate.parts[:2] == _FORBIDDEN_PUBLIC_PREFIX.parts:
            raise WorkspacePathError("public workspace writes to docs/conformance are prohibited")
        base = self.metadata_root; resolved = (self.root / candidate).resolve()
        if base.is_symlink() or (base.exists() and not base.is_dir()) or any((self.root / Path(*candidate.parts[:index])).is_symlink() for index in range(1, len(candidate.parts) + 1)) or not base.resolve().is_relative_to(self.root) or not resolved.is_relative_to(base.resolve()): raise WorkspacePathError("product evidence writes must stay under a real workspace-local .breadboard")
        return resolved
    def lane_manifest_path(self, lane_id: str) -> Path:
        return self.path(Path(".breadboard") / "lanes" / f"{lane_id}.manifest.json")
    def lane_lock_path(self, lane_id: str) -> Path:
        return self.path(Path(".breadboard") / "lanes" / f"{lane_id}.lock.json")
    def write_json(self, relative: str | Path, value: Any) -> Path:
        destination = self.path(relative); destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp"); fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream: stream.write(_json_bytes(value))
        try: os.link(temporary, destination)
        except Exception: temporary.unlink(missing_ok=True); raise
        temporary.unlink()
        return destination
    def read_json(self, relative: str | Path) -> dict[str, Any]:
        source = self.path(relative)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise WorkspacePathError(f"workspace record must be an object: {source}")
        return payload
