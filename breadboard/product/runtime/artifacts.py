"""Content-addressed artifact ownership without machine-path references."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
_RESERVED_NAME_CHARACTERS = frozenset('<>:"/\\|?*')
_UNSAFE_EDGE_CHARACTERS = frozenset(". ")
_WINDOWS_DEVICE_BASENAMES = frozenset(
    {"aux", "clock$", "con", "conin$", "conout$", "nul", "prn", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
)
def _validate_artifact_name(name: object) -> None:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 255
        or name[0] in _UNSAFE_EDGE_CHARACTERS
        or name[-1] in _UNSAFE_EDGE_CHARACTERS
        or any(
            character in _RESERVED_NAME_CHARACTERS or not 32 <= ord(character) <= 126
            for character in name
        )
        or name.partition(".")[0].lower() in _WINDOWS_DEVICE_BASENAMES
    ):
        raise ValueError("artifact name must be a portable basename")
def _hexdigest(value: object, message: str) -> str:
    prefix, separator, digest = value.partition(":") if isinstance(value, str) else ("", "", "")
    if prefix != "sha256" or not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): raise ValueError(message)
    return digest
@dataclass(frozen=True, slots=True)
class ArtifactRef:
    digest: str; size_bytes: int; media_type: str
    def __post_init__(self) -> None:
        _hexdigest(self.digest, "artifact digest must be a lowercase sha256 digest")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0: raise ValueError("artifact size_bytes must be a nonnegative integer")
        if not isinstance(self.media_type, str) or not self.media_type: raise ValueError("artifact media_type must be populated")
    def as_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "size_bytes": self.size_bytes, "media_type": self.media_type}
def _sync_directory(path: Path) -> None:
    if os.name == "nt": return
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
    def put(self, content: bytes, *, media_type: str = "application/octet-stream") -> ArtifactRef:
        if not isinstance(content, bytes) or not media_type: raise TypeError("artifact content must be bytes and media_type must be populated")
        digest = hashlib.sha256(content).hexdigest()
        ref = ArtifactRef(f"sha256:{digest}", len(content), media_type)
        target = self._root / "sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True); directories = target.parent, target.parent.parent, self._root, self._root.parent
        if target.exists():
            with target.open("rb") as stream:
                if stream.read() != content: raise RuntimeError("content-address collision")
                os.fsync(stream.fileno())
            for directory in directories: _sync_directory(directory)
            return ref
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{digest}.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name); stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            for directory in directories: _sync_directory(directory)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return ref
    def put_json(self, value: Any) -> ArtifactRef:
        content = (json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        return self.put(content, media_type="application/json")
    def read(self, ref: ArtifactRef) -> bytes:
        digest = _hexdigest(ref.digest, "unsupported artifact digest")
        content = (self._root / "sha256" / digest[:2] / digest).read_bytes()
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != digest: raise RuntimeError("artifact verification failed")
        return content
    def materialize(self, ref: ArtifactRef, destination: str | Path) -> Path:
        content, target = self.read(ref), Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target
    def manifest(self, session_id: str, artifacts: dict[str, ArtifactRef]) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id: raise ValueError("manifest session_id must be a nonempty string")
        for name in artifacts: _validate_artifact_name(name)
        rows = [{"name": name, **ref.as_dict()} for name, ref in sorted(artifacts.items())]
        body = {"schema_version": "bb.artifact_manifest.v1", "session_id": session_id, "artifacts": rows}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        body["manifest_id"] = "artifact_manifest:" + hashlib.sha256(canonical).hexdigest()
        return body
