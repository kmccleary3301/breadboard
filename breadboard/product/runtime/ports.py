"""Small host-owned ports used by the product Session kernel."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class Clock(Protocol):
    def now(self) -> str: ...


class IdSource(Protocol):
    def new_id(self) -> str: ...


class EventSink(Protocol):
    def append(self, event: object) -> None: ...


class SystemClock:
    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class UUIDSource:
    def new_id(self) -> str:
        return str(uuid4())


class JsonlEventSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: object) -> None:
        record = event.as_dict()  # type: ignore[attr-defined]
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class NullEventSink:
    def append(self, event: object) -> None:
        return None
