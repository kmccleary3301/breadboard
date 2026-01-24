"""Event primitives for the CLI bridge FastAPI service."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

PROTOCOL_VERSION = "1.0"
STREAM_SCHEMA_VERSION = 2
STREAM_SCHEMA_REV = "2026-01-16"

# Canonical v2 event types (string constants).
STREAM_HELLO = "stream.hello"
STREAM_GAP = "stream.gap"
SESSION_START = "session.start"
SESSION_META = "session.meta"
RUN_START = "run.start"
RUN_END = "run.end"
USER_MESSAGE = "user.message"
USER_COMMAND = "user.command"
ASSISTANT_MESSAGE_START = "assistant.message.start"
ASSISTANT_MESSAGE_DELTA = "assistant.message.delta"
ASSISTANT_MESSAGE_END = "assistant.message.end"
ASSISTANT_REASONING_DELTA = "assistant.reasoning.delta"
ASSISTANT_THOUGHT_SUMMARY_DELTA = "assistant.thought_summary.delta"
ASSISTANT_TOOL_CALL_START = "assistant.tool_call.start"
ASSISTANT_TOOL_CALL_DELTA = "assistant.tool_call.delta"
ASSISTANT_TOOL_CALL_END = "assistant.tool_call.end"
TOOL_EXEC_START = "tool.exec.start"
TOOL_EXEC_STDOUT_DELTA = "tool.exec.stdout.delta"
TOOL_EXEC_STDERR_DELTA = "tool.exec.stderr.delta"
TOOL_EXEC_END = "tool.exec.end"
TOOL_RESULT = "tool.result"
PERMISSION_REQUEST = "permission.request"
PERMISSION_DECISION = "permission.decision"
PERMISSION_TIMEOUT = "permission.timeout"
ERROR = "error"
WARNING = "warning"
INTERRUPT = "interrupt"
CANCEL_REQUESTED = "cancel.requested"
CANCEL_ACKNOWLEDGED = "cancel.acknowledged"
USAGE_UPDATE = "usage.update"
AGENT_SPAWN = "agent.spawn"
AGENT_STATUS = "agent.status"
AGENT_END = "agent.end"

class EventType(str, enum.Enum):
    """Canonical event types exposed over the streaming endpoint."""

    TURN_START = "turn_start"
    ASSISTANT_MESSAGE = "assistant_message"
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
    REWARD_UPDATE = "reward_update"
    COMPLETION = "completion"
    LOG_LINK = "log_link"
    ERROR = "error"
    RUN_FINISHED = "run_finished"
    ASSISTANT_DELTA = "assistant_delta"


LEGACY_EVENT_TYPES = {
    EventType.TURN_START.value,
    EventType.ASSISTANT_MESSAGE.value,
    EventType.ASSISTANT_DELTA.value,
    EventType.USER_MESSAGE.value,
    EventType.TOOL_CALL.value,
    EventType.TOOL_RESULT.value,
    EventType.PERMISSION_REQUEST.value,
    EventType.PERMISSION_RESPONSE.value,
    EventType.CHECKPOINT_LIST.value,
    EventType.CHECKPOINT_RESTORED.value,
    EventType.SKILLS_CATALOG.value,
    EventType.SKILLS_SELECTION.value,
    EventType.CTREE_NODE.value,
    EventType.CTREE_SNAPSHOT.value,
    EventType.TASK_EVENT.value,
    EventType.REWARD_UPDATE.value,
    EventType.COMPLETION.value,
    EventType.LOG_LINK.value,
    EventType.ERROR.value,
    EventType.RUN_FINISHED.value,
}

CANONICAL_EVENT_TYPES = {
    STREAM_HELLO,
    STREAM_GAP,
    SESSION_START,
    SESSION_META,
    RUN_START,
    RUN_END,
    USER_MESSAGE,
    USER_COMMAND,
    ASSISTANT_MESSAGE_START,
    ASSISTANT_MESSAGE_DELTA,
    ASSISTANT_MESSAGE_END,
    ASSISTANT_REASONING_DELTA,
    ASSISTANT_THOUGHT_SUMMARY_DELTA,
    ASSISTANT_TOOL_CALL_START,
    ASSISTANT_TOOL_CALL_DELTA,
    ASSISTANT_TOOL_CALL_END,
    TOOL_EXEC_START,
    TOOL_EXEC_STDOUT_DELTA,
    TOOL_EXEC_STDERR_DELTA,
    TOOL_EXEC_END,
    TOOL_RESULT,
    PERMISSION_REQUEST,
    PERMISSION_DECISION,
    PERMISSION_TIMEOUT,
    ERROR,
    WARNING,
    INTERRUPT,
    CANCEL_REQUESTED,
    CANCEL_ACKNOWLEDGED,
    USAGE_UPDATE,
    AGENT_SPAWN,
    AGENT_STATUS,
    AGENT_END,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_mono_ns() -> int:
    try:
        return time.monotonic_ns()
    except Exception:
        return 0


def _coerce_type(value: EventType | str) -> str:
    if isinstance(value, EventType):
        return value.value
    return str(value)


def is_legacy_event_type(value: str | EventType) -> bool:
    return _coerce_type(value) in LEGACY_EVENT_TYPES


@dataclass
class SessionEvent:
    """Normalized session event emitted to streaming clients."""

    type: EventType | str
    session_id: str
    payload: Dict[str, Any]
    run_id: Optional[str] = None
    turn: Optional[int] = None
    turn_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    actor: Optional[Dict[str, Any]] = None
    visibility: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    created_at: int = field(default_factory=_now_ms)
    mono_ns: int = field(default_factory=_now_mono_ns)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    seq: Optional[int] = None
    schema_rev: Optional[str] = None
    v: int = STREAM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            try:
                self.type = EventType(self.type)
            except Exception:
                self.type = str(self.type)

    def asdict(self) -> Dict[str, Any]:
        timestamp_ms = int(self.created_at)
        event_id = str(self.seq) if self.seq is not None else self.event_id
        payload: Dict[str, Any] = {
            "v": self.v,
            "type": _coerce_type(self.type),
            "schema_rev": self.schema_rev or STREAM_SCHEMA_REV,
            "session_id": self.session_id,
            "seq": self.seq,
            "id": event_id,
            "ts": timestamp_ms,
            "timestamp_ms": timestamp_ms,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "actor": self.actor,
            "visibility": self.visibility,
            "tags": list(self.tags) if self.tags else None,
            "payload": self.payload,
        }
        if self.turn is not None:
            payload["turn"] = self.turn
        if self.mono_ns:
            payload["mono_ns"] = self.mono_ns
        return payload

    def asdict_legacy(self) -> Dict[str, Any]:
        timestamp_ms = int(self.created_at)
        return {
            "id": self.event_id,
            "seq": self.seq,
            "type": _coerce_type(self.type),
            "session_id": self.session_id,
            "turn": self.turn,
            "timestamp": timestamp_ms,
            "timestamp_ms": timestamp_ms,
            "protocol_version": PROTOCOL_VERSION,
            "payload": self.payload,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
        }
