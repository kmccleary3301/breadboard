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
def _descriptor_path(descriptor: int) -> Path:
    proc = Path("/proc/self/fd")
    if proc.is_dir(): return proc / str(descriptor)
    import fcntl  # POSIX fallback for Darwin, where /dev/fd exposes only standard descriptors.
    return Path(fcntl.fcntl(descriptor, 50, b"\0" * 1024).split(b"\0", 1)[0].decode())
def _open_directory(parent: int, name: str, *, create: bool = True) -> int:
    if create:
        try: os.mkdir(name, dir_fd=parent)
        except FileExistsError: pass
    return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
def _read_at(parent: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
    with os.fdopen(descriptor, "rb") as stream: return stream.read()
def _write_at(parent: int, name: str, content: bytes) -> None:
    temporary, descriptor = f".{name}.{os.urandom(8).hex()}.tmp", None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream: descriptor = None; stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent); os.fsync(parent)
    finally:
        if descriptor is not None: os.close(descriptor)
        try: os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError: pass
def _sync_directory(path: Path) -> None:
    if os.name == "nt": return
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
class ArtifactStore:
    @property
    def _root(self) -> Path: return _descriptor_path(self._descriptor) if self._descriptor is not None else self._root_path
    def __init__(self, root: str | Path, *, descriptor: int | None = None) -> None:
        self._root_path, self._descriptor = Path(root), descriptor
        if descriptor is None: self._root.mkdir(parents=True, exist_ok=True)
    def _digest_directories(self, digest: str, *, create: bool) -> tuple[int, int]:
        if self._descriptor is None: raise RuntimeError("artifact descriptor unavailable")
        sha = _open_directory(self._descriptor, "sha256", create=create)
        try: return sha, _open_directory(sha, digest[:2], create=create)
        except BaseException: os.close(sha); raise
    def put(self, content: bytes, *, media_type: str = "application/octet-stream") -> ArtifactRef:
        if not isinstance(content, bytes) or not media_type: raise TypeError("artifact content must be bytes and media_type must be populated")
        digest = hashlib.sha256(content).hexdigest()
        ref = ArtifactRef(f"sha256:{digest}", len(content), media_type)
        if self._descriptor is not None:
            sha, prefix = self._digest_directories(digest, create=True)
            try:
                try: existing = _read_at(prefix, digest)
                except FileNotFoundError: _write_at(prefix, digest, content)
                else:
                    if existing != content: raise RuntimeError("content-address collision")
                os.fsync(prefix); os.fsync(sha); os.fsync(self._descriptor)
            finally: os.close(prefix); os.close(sha)
            return ref
        root = self._root; target = root / "sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True); directories = target.parent, target.parent.parent, root, root.parent, root.parent.parent
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
        if self._descriptor is not None:
            sha, prefix = self._digest_directories(digest, create=False)
            try: content = _read_at(prefix, digest)
            finally: os.close(prefix); os.close(sha)
        else:
            content = (self._root / "sha256" / digest[:2] / digest).read_bytes()
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != digest: raise RuntimeError("artifact verification failed")
        return content
    def materialize(self, ref: ArtifactRef, destination: str | Path) -> Path:
        if self._descriptor is not None: raise RuntimeError("descriptor-backed stores require materialize_at")
        content, target, temporary = self.read(ref), Path(destination), None
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name); stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target); _sync_directory(target.parent); _sync_directory(target.parent.parent)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return target
    def materialize_at(self, ref: ArtifactRef, directory: int, filename: str) -> None: _write_at(directory, filename, self.read(ref))
    def manifest(self, session_id: str, artifacts: dict[str, ArtifactRef]) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id: raise ValueError("manifest session_id must be a nonempty string")
        for name in artifacts: _validate_artifact_name(name)
        rows = [{"name": name, **ref.as_dict()} for name, ref in sorted(artifacts.items())]
        body = {"schema_version": "bb.artifact_manifest.v1", "session_id": session_id, "artifacts": rows}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        body["manifest_id"] = "artifact_manifest:" + hashlib.sha256(canonical).hexdigest()
        return body
