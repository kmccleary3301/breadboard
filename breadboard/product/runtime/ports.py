"""Small host-owned ports used by the product Session kernel."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4
def _sync(stream: Any) -> None:
    stream.flush()
    os.fsync(stream.fileno())
class Clock(Protocol):
    def now(self) -> str: ...
class IdSource(Protocol):
    def new_id(self) -> str: ...
class EventSink(Protocol):
    def append(self, event: object) -> None: ...
class SystemClock:
    def now(self) -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
class UUIDSource:
    def new_id(self) -> str: return str(uuid4())
class _SinkState:
    def __init__(self) -> None: self.lock, self.poisoned = RLock(), None
_STATES = tuple(_SinkState() for _ in range(256))
class JsonlEventSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve(); path, state = self.path, _STATES[hash(self.path) % len(_STATES)]
        with state.lock: self._mkdir_parent(path.parent); self._recover(path, state)
    def _mkdir_parent(self, path: Path) -> None:
        if path.exists(): return
        self._mkdir_parent(path.parent)
        try: path.mkdir()
        except FileExistsError: return
        try: self._sync_parent(path)
        except BaseException: path.rmdir(); raise
    def _transaction_paths(self, path: Path) -> tuple[Path, Path]: wal = path.with_name(f".{path.name}.txn"); return wal, wal.with_name(f"{wal.name}.tmp")
    def _recover(self, path: Path, state: _SinkState) -> None:
        wal, temporary = self._transaction_paths(path); temporary.unlink(missing_ok=True)
        try:
            if not wal.exists(): return
            offset = int(wal.read_text(encoding="ascii"))
            if path.exists():
                with path.open("r+b", buffering=0) as stream:
                    stream.seek(0, os.SEEK_END); size = stream.tell()
                    if offset < 0 or offset > size: raise ValueError("invalid event transaction offset")
                    stream.truncate(offset); _sync(stream)
            elif offset: raise ValueError("event transaction references a missing log")
            wal.unlink(); self._sync_parent(path)
        except BaseException: state.poisoned = path if state.poisoned is None else True; raise RuntimeError("event sink recovery failed")
    def _begin(self, path: Path, offset: int) -> None:
        wal, temporary = self._transaction_paths(path); data = str(offset).encode("ascii"); descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.write(descriptor, data) != len(data): raise OSError("short event transaction write")
            os.fsync(descriptor)
        finally: os.close(descriptor)
        os.replace(temporary, wal); self._sync_parent(path)
    def _finish(self, path: Path) -> None:
        wal, temporary = self._transaction_paths(path); temporary.unlink(missing_ok=True); wal.unlink(missing_ok=True); self._sync_parent(path)
    def append(self, event: object) -> None:
        payload = (json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(); path = Path(self.path).resolve(); state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock:
            if state.poisoned is True or state.poisoned == path: raise RuntimeError("event sink is poisoned after an unconfirmed rollback")
            self._recover(path, state)
            try: stream = path.open("x+b", buffering=0); created = True
            except FileExistsError: stream = path.open("a+b", buffering=0); created = False
            try:
                stream.seek(0, os.SEEK_END); offset = stream.tell()
                try:
                    self._begin(path, offset)
                    if stream.write(payload) != len(payload): raise OSError("short event sink write")
                    _sync(stream); self._finish(path)
                except BaseException:
                    try:
                        stream.seek(offset); stream.truncate(); _sync(stream); self._finish(path)
                        if created: stream.close(); path.unlink(); self._sync_parent(path)
                    except BaseException: state.poisoned = path if state.poisoned is None else True
                    raise
            finally:
                try: stream.close()
                except OSError: pass
    def _sync_parent(self, path: Path) -> None:
        if os.name == "nt": return
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
class NullEventSink:
    def append(self, event: object) -> None: return None
