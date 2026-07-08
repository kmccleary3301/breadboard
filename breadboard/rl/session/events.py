from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionEvent:
    event_id: str
    session_id: str
    event_kind: str
    status_before: str
    status_after: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_kind": self.event_kind,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "payload": dict(self.payload),
        }
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload
