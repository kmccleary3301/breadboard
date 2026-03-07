#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable, List


class SnapshotPoolLockSet:
    def __init__(self, snapshot_dirs: Iterable[Path], *, lock_name: str = ".bb_snapshot_pool.lock") -> None:
        self._snapshot_dirs = [Path(path).resolve() for path in snapshot_dirs]
        self._lock_name = lock_name
        self._acquired: List[Path] = []

    def acquire(self) -> List[Path]:
        pid = os.getpid()
        now = time.time()
        for snapshot_dir in self._snapshot_dirs:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            lock_path = snapshot_dir / self._lock_name
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError as exc:
                self.release()
                raise RuntimeError(f"Snapshot dir is already locked: {snapshot_dir}") from exc
            payload = {
                "pid": pid,
                "created_at": now,
                "snapshot_dir": str(snapshot_dir),
            }
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.write("\n")
            self._acquired.append(lock_path)
        return list(self._acquired)

    def release(self) -> None:
        for lock_path in reversed(self._acquired):
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._acquired = []

    def __enter__(self) -> "SnapshotPoolLockSet":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
