"""Content-addressed artifact ownership without machine-path references."""
from __future__ import annotations
import hashlib, json, os, tempfile, threading
from contextlib import contextmanager; from dataclasses import dataclass; from pathlib import Path; from typing import Any, Iterator
_RESERVED_NAME_CHARACTERS, _UNSAFE_EDGE_CHARACTERS = frozenset('<>:"/\\|?*'), frozenset(". ")
_WINDOWS_DEVICE_BASENAMES = frozenset({"aux", "clock$", "con", "conin$", "conout$", "nul", "prn", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))})
def _validate_artifact_name(name: object) -> None:
    invalid_edge = not isinstance(name, str) or not name or len(name) > 255 or name[0] in _UNSAFE_EDGE_CHARACTERS or name[-1] in _UNSAFE_EDGE_CHARACTERS
    if invalid_edge or any(character in _RESERVED_NAME_CHARACTERS or not 32 <= ord(character) <= 126 for character in name) or name.partition(".")[0].lower() in _WINDOWS_DEVICE_BASENAMES: raise ValueError("artifact name must be a portable basename")
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
def _windows_handle(path: Path, *, directory: bool, create: bool = True) -> int:
    if os.name != "nt": raise OSError("Windows handle requested on non-Windows host")
    import ctypes; from ctypes import wintypes
    expected = os.path.normcase(os.path.abspath(path))
    if directory and create: path.mkdir(parents=True, exist_ok=True)
    kernel = ctypes.windll.kernel32; kernel.CreateFileW.argtypes, kernel.CreateFileW.restype = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes, kernel.GetFileInformationByHandleEx.restype = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL; kernel.GetFinalPathNameByHandleW.argtypes, kernel.GetFinalPathNameByHandleW.restype = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD], wintypes.DWORD; kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0 if directory else 0xC0000000, 3, None, 3 if directory or not create else 4, (0x02000000 if directory else 0) | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError()
    class Info(ctypes.Structure): _fields_ = [("attributes", ctypes.c_ulong), ("tag", ctypes.c_ulong)]
    info, resolved = Info(), ctypes.create_unicode_buffer(32768)
    if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or info.attributes & 0x400 or not kernel.GetFinalPathNameByHandleW(handle, resolved, len(resolved), 0) or os.path.normcase(os.path.abspath(resolved.value.removeprefix("\\\\?\\").replace("UNC\\", "\\\\", 1))) != expected:
        kernel.CloseHandle(handle); raise OSError("unsafe Windows workspace metadata path")
    return int(handle)
def _close_windows_handle(handle: int | None) -> None:
    if handle is not None: import ctypes; ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
def _windows_file_descriptor(path: Path, *, create: bool = True) -> int:
    import msvcrt; return msvcrt.open_osfhandle(_windows_handle(path, directory=False, create=create), os.O_RDWR | os.O_APPEND)
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
_CAS_LOCK = threading.RLock()
class ArtifactStore:
    @property
    def _root(self) -> Path: return _descriptor_path(self._descriptor) if self._descriptor is not None else self._root_path
    def __init__(self, root: str | Path, *, descriptor: int | None = None) -> None:
        self._root_path, self._descriptor, self._transaction_depth, self._transaction_stream, self._transaction_owner = Path(root), descriptor, 0, None, None
        if descriptor is None: self._root.mkdir(parents=True, exist_ok=True)
    @contextmanager
    def transaction(self) -> Iterator[None]:
        with _CAS_LOCK:
            outer, stream = self._transaction_owner != threading.get_ident(), self._transaction_stream
            if outer:
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                descriptor = _windows_file_descriptor(self._root / ".transaction.lock") if os.name == "nt" else os.open(".transaction.lock", flags, 0o600, dir_fd=self._descriptor) if self._descriptor is not None else os.open(self._root / ".transaction.lock", flags, 0o600)
                stream = self._transaction_stream = os.fdopen(descriptor, "a+b", buffering=0)
                try:
                    if os.name == "nt":
                        import msvcrt; stream.seek(0, os.SEEK_END)
                        if not stream.tell(): stream.write(b"\0")
                        stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl; fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                except BaseException: stream.close(); self._transaction_stream = None; raise
                self._transaction_owner = threading.get_ident()
            self._transaction_depth += 1
            try: yield
            finally:
                self._transaction_depth -= 1
                if outer:
                    if os.name == "nt":
                        import msvcrt; stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl; fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    stream.close(); self._transaction_stream, self._transaction_owner = None, None
    def _digest_directories(self, digest: str, *, create: bool) -> tuple[int, int]:
        if self._descriptor is None: raise RuntimeError("artifact descriptor unavailable")
        sha = _open_directory(self._descriptor, "sha256", create=create)
        try: return sha, _open_directory(sha, digest[:2], create=create)
        except BaseException: os.close(sha); raise
    def put(self, content: bytes, *, media_type: str = "application/octet-stream", created: set[ArtifactRef] | None = None) -> ArtifactRef:
        if self._transaction_owner == threading.get_ident(): return self._put(content, media_type=media_type, created=created)
        if created is not None: raise RuntimeError("created artifact tracking requires a transaction")
        with self.transaction(): return self._put(content, media_type=media_type, created=None)
    def _put(self, content: bytes, *, media_type: str, created: set[ArtifactRef] | None) -> ArtifactRef:
        if not isinstance(content, bytes) or not media_type: raise TypeError("artifact content must be bytes and media_type must be populated")
        digest = hashlib.sha256(content).hexdigest(); ref = ArtifactRef(f"sha256:{digest}", len(content), media_type)
        if self._descriptor is not None:
            sha, prefix = self._digest_directories(digest, create=True)
            try:
                try: existing = _read_at(prefix, digest)
                except FileNotFoundError: created.add(ref) if created is not None else None; _write_at(prefix, digest, content)
                else:
                    if existing != content: raise RuntimeError("content-address collision")
                os.fsync(prefix); os.fsync(sha); os.fsync(self._descriptor)
            finally: os.close(prefix); os.close(sha)
            return ref
        root = self._root; target = root / "sha256" / digest[:2] / digest; directories = target.parent, target.parent.parent, root, root.parent, root.parent.parent
        locks: list[int] = []; temporary: Path | None = None
        try:
            if os.name == "nt":
                for path in (root, target.parent.parent, target.parent): locks.append(_windows_handle(path, directory=True))
            else: target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                with (os.fdopen(_windows_file_descriptor(target, create=False), "rb") if os.name == "nt" else target.open("rb")) as stream:
                    if stream.read() != content: raise RuntimeError("content-address collision")
                    os.fsync(stream.fileno())
                for directory in directories: _sync_directory(directory)
                return ref
            created.add(ref) if created is not None else None
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{digest}.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name); stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            for directory in directories: _sync_directory(directory)
            return ref
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            for handle in reversed(locks): _close_windows_handle(handle)
    def put_json(self, value: Any, *, created: set[ArtifactRef] | None = None) -> ArtifactRef:
        return self.put((json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(), media_type="application/json", created=created)
    def discard(self, ref: ArtifactRef) -> None:
        if self._transaction_owner == threading.get_ident(): return self._discard(ref)
        with self.transaction(): self._discard(ref)
    def _discard(self, ref: ArtifactRef) -> None:
        digest = _hexdigest(ref.digest, "unsupported artifact digest")
        if self._descriptor is not None:
            sha, prefix = self._digest_directories(digest, create=False)
            try: os.unlink(digest, dir_fd=prefix); os.fsync(prefix); os.fsync(sha); os.fsync(self._descriptor)
            except FileNotFoundError: pass
            finally: os.close(prefix); os.close(sha)
            return
        target, locks = self._root / "sha256" / digest[:2] / digest, []
        try:
            if os.name == "nt":
                for path in (self._root, target.parent.parent, target.parent): locks.append(_windows_handle(path, directory=True, create=False))
            target.unlink(missing_ok=True)
            for directory in (target.parent, target.parent.parent, self._root): _sync_directory(directory) if directory.is_dir() else None
        finally:
            for handle in reversed(locks): _close_windows_handle(handle)
    def read(self, ref: ArtifactRef) -> bytes:
        with self.transaction():
            digest = _hexdigest(ref.digest, "unsupported artifact digest")
            if self._descriptor is not None:
                sha, prefix = self._digest_directories(digest, create=False)
                try: content = _read_at(prefix, digest)
                finally: os.close(prefix); os.close(sha)
            else:
                target, locks = self._root / "sha256" / digest[:2] / digest, []
                try:
                    if os.name == "nt":
                        for path in (self._root, target.parent.parent, target.parent): locks.append(_windows_handle(path, directory=True, create=False))
                    with (os.fdopen(_windows_file_descriptor(target, create=False), "rb") if os.name == "nt" else target.open("rb")) as stream: content = stream.read()
                finally:
                    for handle in reversed(locks): _close_windows_handle(handle)
            if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != digest: raise RuntimeError("artifact verification failed")
            return content
    def materialize(self, ref: ArtifactRef, destination: str | Path) -> Path:
        if self._descriptor is not None: raise RuntimeError("descriptor-backed stores require materialize_at")
        content, target, temporary, locks = self.read(ref), Path(destination), None, []
        try:
            if os.name == "nt":
                for path in (target.parent.parent, target.parent): locks.append(_windows_handle(path, directory=True))
            else: target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream: temporary = Path(stream.name); stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target); _sync_directory(target.parent); _sync_directory(target.parent.parent)
            return target
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            for handle in reversed(locks): _close_windows_handle(handle)
    def materialize_at(self, ref: ArtifactRef, directory: int, filename: str) -> None: _write_at(directory, filename, self.read(ref))
    def manifest(self, session_id: str, artifacts: dict[str, ArtifactRef]) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id: raise ValueError("manifest session_id must be a nonempty string")
        for name in artifacts: _validate_artifact_name(name)
        rows = [{"name": name, **ref.as_dict()} for name, ref in sorted(artifacts.items())]; body = {"schema_version": "bb.artifact_manifest.v1", "session_id": session_id, "artifacts": rows}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode(); body["manifest_id"] = "artifact_manifest:" + hashlib.sha256(canonical).hexdigest()
        return body
def read_workspace_artifact(workspace: str | Path, ref: ArtifactRef) -> bytes:
    workspace = Path(workspace); metadata, artifacts = workspace / ".breadboard", workspace / ".breadboard" / "artifacts"
    if os.name == "nt":
        handles: list[int] = []
        try:
            for path in (workspace, metadata, artifacts): handles.append(_windows_handle(path, directory=True, create=False))
            return ArtifactStore(artifacts).read(ref)
        finally:
            for handle in reversed(handles): _close_windows_handle(handle)
    descriptors = [os.open(workspace, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))]
    try:
        descriptors.append(_open_directory(descriptors[-1], ".breadboard", create=False)); descriptors.append(_open_directory(descriptors[-1], "artifacts", create=False))
        return ArtifactStore(artifacts, descriptor=descriptors[-1]).read(ref)
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)
