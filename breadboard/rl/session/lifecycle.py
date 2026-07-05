from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SessionStatus:
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    EVALUATED = "evaluated"
    FAILED = "failed"
    TERMINATED = "terminated"


ALLOWED_TRANSITIONS = {
    SessionStatus.CREATED: {SessionStatus.READY, SessionStatus.FAILED, SessionStatus.TERMINATED},
    SessionStatus.READY: {SessionStatus.RUNNING, SessionStatus.EVALUATED, SessionStatus.FAILED, SessionStatus.TERMINATED},
    SessionStatus.RUNNING: {SessionStatus.RUNNING, SessionStatus.EVALUATED, SessionStatus.FAILED, SessionStatus.TERMINATED},
    SessionStatus.EVALUATED: {SessionStatus.TERMINATED},
    SessionStatus.FAILED: {SessionStatus.TERMINATED},
    SessionStatus.TERMINATED: set(),
}


@dataclass(frozen=True)
class SessionLifecycleState:
    status: str = SessionStatus.CREATED
    history: list[str] = field(default_factory=lambda: [SessionStatus.CREATED])

    def transition(self, next_status: str) -> "SessionLifecycleState":
        if next_status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            raise ValueError(f"invalid session transition: {self.status} -> {next_status}")
        return SessionLifecycleState(status=next_status, history=[*self.history, next_status])

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "history": list(self.history)}
