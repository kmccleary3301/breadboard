from __future__ import annotations

import os
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class _TailIndexIdentity:
    dev: int
    ino: int
    mtime_ns: int


@dataclass
class _TailIndexEntry:
    identity: _TailIndexIdentity
    size: int
    scanned_from: int
    # Absolute byte offsets for b"\n", in ascending order within the scanned suffix.
    newlines: "deque[int]" = field(default_factory=deque)
    lock: "threading.Lock" = field(default_factory=threading.Lock, repr=False)
    # Diagnostics counters (used by EX2 capture + tests).
    index_read_ops: int = 0
    index_read_bytes: int = 0
    tail_read_ops: int = 0
    tail_read_bytes: int = 0


class _TailLineIndexCache:
    """Chunked tail newline offset cache for large line-oriented artifacts (e.g. JSONL).

    This is intentionally dependency-free so it can be exercised from the TUI bundle gates
    without requiring the full FastAPI stack.
    """

    def __init__(self, *, max_entries: int = 48, chunk_size: int = 64 * 1024) -> None:
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _TailIndexEntry]" = OrderedDict()
        self._max_entries = max(1, int(max_entries))
        self._chunk_size = max(4 * 1024, int(chunk_size))

    def _stat_identity(self, stat: os.stat_result) -> _TailIndexIdentity:
        return _TailIndexIdentity(
            dev=int(getattr(stat, "st_dev", 0)),
            ino=int(getattr(stat, "st_ino", 0)),
            mtime_ns=int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        )

    def _get_entry(self, path: Path, stat: os.stat_result) -> _TailIndexEntry:
        key = str(path)
        identity = self._stat_identity(stat)
        size = int(stat.st_size)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                # Invalidate on rotation/replace (dev+ino), or timestamp changes, or truncation.
                if (
                    entry.identity.dev != identity.dev
                    or entry.identity.ino != identity.ino
                    or identity.mtime_ns < entry.identity.mtime_ns
                    or size < entry.size
                ):
                    entry = None
                else:
                    self._entries.move_to_end(key)
            if entry is None:
                entry = _TailIndexEntry(identity=identity, size=size, scanned_from=size)
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            else:
                entry.identity = identity
            return entry

    def _read_range(self, handle, start: int, length: int) -> bytes:
        handle.seek(start)
        data = handle.read(length)
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)

    def read_tail_text(self, path: Path, *, tail_lines: int, max_bytes: int) -> tuple[str, dict[str, int]]:
        tail_lines = max(0, int(tail_lines))
        max_bytes = max(1, int(max_bytes))
        stat = path.stat()
        entry = self._get_entry(path, stat)
        size = int(stat.st_size)

        if tail_lines == 0:
            return "", {"total_bytes": size, "returned_bytes": 0, "start_offset": size}

        with entry.lock:
            # Extend index for appended bytes.
            if size > entry.size:
                start = entry.size
                remaining = size - entry.size
                with path.open("rb") as handle:
                    offset = start
                    while remaining > 0:
                        chunk = self._read_range(handle, offset, min(self._chunk_size, remaining))
                        entry.index_read_ops += 1
                        entry.index_read_bytes += len(chunk)
                        for idx in (i for i, b in enumerate(chunk) if b == 0x0A):
                            entry.newlines.append(offset + idx)
                        offset += len(chunk)
                        remaining -= len(chunk)
                entry.size = size
                entry.scanned_from = min(entry.scanned_from, size)

            # Scan backwards until we have enough newline offsets (or reach BOF).
            with path.open("rb") as handle:
                while len(entry.newlines) < tail_lines and entry.scanned_from > 0:
                    chunk_end = entry.scanned_from
                    chunk_start = max(0, chunk_end - self._chunk_size)
                    chunk = self._read_range(handle, chunk_start, chunk_end - chunk_start)
                    entry.index_read_ops += 1
                    entry.index_read_bytes += len(chunk)
                    offsets: list[int] = []
                    for idx in (i for i, b in enumerate(chunk) if b == 0x0A):
                        offsets.append(chunk_start + idx)
                    for off in reversed(offsets):
                        entry.newlines.appendleft(off)
                    entry.scanned_from = chunk_start

                if len(entry.newlines) >= tail_lines:
                    start_offset = int(entry.newlines[-tail_lines]) + 1
                else:
                    start_offset = 0

                start_offset = max(start_offset, max(0, size - max_bytes))
                returned_len = max(0, size - start_offset)
                tail_raw = self._read_range(handle, start_offset, returned_len)
                entry.tail_read_ops += 1
                entry.tail_read_bytes += len(tail_raw)

        tail_text = tail_raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        lines = tail_text.split("\n")
        if len(lines) > tail_lines:
            lines = lines[-tail_lines:]
            tail_text = "\n".join(lines)
        return tail_text, {"total_bytes": size, "returned_bytes": len(tail_raw), "start_offset": start_offset}


_TAIL_LINE_INDEX_CACHE = _TailLineIndexCache()

