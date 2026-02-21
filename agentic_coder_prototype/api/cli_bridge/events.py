"""Event primitives for the CLI bridge FastAPI service."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "1.0"

class EventType(str, enum.Enum):
    """Canonical event types exposed over the streaming endpoint."""

    TURN_START = "turn_start"
    STREAM_GAP = "stream.gap"
    CONVERSATION_COMPACTION_START = "conversation.compaction.start"
    CONVERSATION_COMPACTION_END = "conversation.compaction.end"
    ASSISTANT_MESSAGE_START = "assistant.message.start"
    ASSISTANT_MESSAGE_DELTA = "assistant.message.delta"
    ASSISTANT_MESSAGE_END = "assistant.message.end"
    ASSISTANT_REASONING_DELTA = "assistant.reasoning.delta"
    ASSISTANT_THOUGHT_SUMMARY_DELTA = "assistant.thought_summary.delta"
    ASSISTANT_MESSAGE = "assistant_message"
    ASSISTANT_DELTA = "assistant_delta"
    TOOL_RESULT_DOT = "tool.result"
    USER_MESSAGE = "user_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    CHECKPOINT_LIST = "checkpoint_list"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    SKILLS_CATALOG = "skills_catalog"
    SKILLS_SELECTION = "skills_selection"
    CTREE_NODE = "ctree_node"
    CTREE_SNAPSHOT = "ctree_snapshot"
    TASK_EVENT = "task_event"
    WARNING = "warning"
    REWARD_UPDATE = "reward_update"
    LIMITS_UPDATE = "limits_update"
    COMPLETION = "completion"
    LOG_LINK = "log_link"
    ERROR = "error"
    RUN_FINISHED = "run_finished"


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SessionEvent:
    """Normalized session event emitted to streaming clients."""

    type: EventType
    session_id: str
    payload: Dict[str, Any]
    turn: Optional[int] = None
    created_at: int = field(default_factory=_now_ms)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    seq: Optional[int] = None

    def asdict(self) -> Dict[str, Any]:
        timestamp_ms = int(self.created_at)
        return {
            "id": self.event_id,
            "seq": self.seq,
            "type": self.type.value,
            "session_id": self.session_id,
            "turn": self.turn,
            "timestamp": timestamp_ms,
            "timestamp_ms": timestamp_ms,
            "protocol_version": PROTOCOL_VERSION,
            "payload": self.payload,
        }
