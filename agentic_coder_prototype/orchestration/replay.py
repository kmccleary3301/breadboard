"""Event log replay helpers for multi-agent orchestration."""

from __future__ import annotations

from typing import Optional

from .event_log import Event, EventLog


def load_event_log(path: str) -> EventLog:
    return EventLog.from_jsonl(path)


def write_event_log(log: EventLog, path: str) -> None:
    log.to_jsonl(path)


class EventLogReplay:
    """Simple iterator for replaying events in deterministic order."""

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._index = 0

    def peek(self) -> Optional[Event]:
        if self._index >= len(self._log.events):
            return None
        return self._log.events[self._index]

    def next(self) -> Optional[Event]:
        event = self.peek()
        if event is None:
            return None
        self._index += 1
        return event

    def reset(self) -> None:
        self._index = 0

    def remaining(self) -> int:
        return max(len(self._log.events) - self._index, 0)
