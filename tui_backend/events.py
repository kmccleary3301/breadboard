"""Event primitives for the BreadBoard CLI backend service."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class EventType(str, enum.Enum):
    """Canonical event types exposed over the streaming endpoint."""

    TURN_START = "turn_start"
    ASSISTANT_MESSAGE = "assistant_message"
    USER_MESSAGE = "user_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REWARD_UPDATE = "reward_update"
    COMPLETION = "completion"
    LOG_LINK = "log_link"
    ERROR = "error"


def _now_ts() -> float:
    return time.time()


@dataclass
class SessionEvent:
    """Normalized session event emitted to streaming clients."""

    type: EventType
    session_id: str
    payload: Dict[str, Any]
    turn: Optional[int] = None
    created_at: float = field(default_factory=_now_ts)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def asdict(self) -> Dict[str, Any]:
        return {
            "id": self.event_id,
            "type": self.type.value,
            "session_id": self.session_id,
            "turn": self.turn,
            "timestamp": self.created_at,
            "payload": self.payload,
        }
