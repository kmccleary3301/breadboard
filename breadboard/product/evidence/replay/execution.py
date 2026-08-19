from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any

from breadboard.product.runtime.artifacts import ArtifactRef
from breadboard.product.runtime.events import EventSink

_TERMINAL_STATES = frozenset(
    {"completed", "failed", "canceled", "timed_out", "integrity_failed"}
)
_ALLOWED = {
    "planned": frozenset({"admitted", "failed", "canceled"}),
    "admitted": frozenset({"running", "failed", "canceled"}),
    "running": _TERMINAL_STATES,
}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ReplayExecutionEvent:
    sequence: int
    state: str
    kind: str
    span_id: str
    parent_span_id: str | None
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("replay execution sequence must be positive")
        if not all(
            isinstance(value, str) and value
            for value in (self.state, self.kind, self.span_id)
        ):
            raise ValueError("replay execution identity fields must be populated")
        if self.parent_span_id is not None and (
            not isinstance(self.parent_span_id, str) or not self.parent_span_id
        ):
            raise ValueError("parent_span_id must be populated when present")
        object.__setattr__(self, "details", _freeze(self.details))

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "state": self.state,
            "kind": self.kind,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "details": _plain(self.details),
        }


class ReplayExecution:
    """Append-only execution state for one immutable replay plan."""

    def __init__(self, plan_id: str, *, sink: EventSink | None = None) -> None:
        if not isinstance(plan_id, str) or not plan_id.startswith("sha256:"):
            raise ValueError("replay execution requires a canonical plan_id")
        self.plan_id = plan_id
        self.execution_id = "replay_execution:" + plan_id.removeprefix("sha256:")
        self._lock = RLock()
        self._sink = sink
        self._events: list[ReplayExecutionEvent] = []
        self._artifacts: Mapping[str, ArtifactRef] = MappingProxyType({})
        self._integrity_verified = False
        self._append("planned", "replay.planned", None, {"plan_id": plan_id})

    @classmethod
    def from_events(cls, events: Iterable[ReplayExecutionEvent]) -> ReplayExecution:
        rows = tuple(events)
        if not rows:
            raise ValueError("replay execution event stream is empty")
        first = rows[0]
        plan_id = first.details.get("plan_id")
        if (
            first.state != "planned"
            or first.kind != "replay.planned"
            or not isinstance(plan_id, str)
        ):
            raise ValueError(
                "replay execution event stream must begin with replay.planned"
            )
        execution = cls(plan_id)
        if execution.events[0].as_dict() != first.as_dict():
            raise ValueError("replay execution planned event is not canonical")
        for event in rows[1:]:
            if event.state == "admitted" and event.kind == "replay.admitted":
                execution.admit()
            elif event.state == "running" and event.kind == "replay.running":
                execution.run()
            elif event.state == "completed" and event.kind == "replay.completed":
                raw_artifacts = event.details.get("artifacts")
                if not isinstance(raw_artifacts, Mapping):
                    raise ValueError("completed replay event requires artifacts")
                artifacts = {
                    name: ArtifactRef(
                        str(value["digest"]),
                        value["size_bytes"],
                        str(value["media_type"]),
                    )
                    for name, value in raw_artifacts.items()
                    if isinstance(name, str) and isinstance(value, Mapping)
                }
                if len(artifacts) != len(raw_artifacts):
                    raise ValueError(
                        "completed replay event contains malformed artifacts"
                    )
                execution.complete(artifacts, integrity_verified=True)
            elif event.state == "failed" and event.kind == "replay.failed":
                execution.fail(str(event.details.get("reason", "")))
            elif event.state == "canceled" and event.kind == "replay.canceled":
                execution.cancel(str(event.details.get("reason", "")))
            elif event.state == "timed_out" and event.kind == "replay.timed_out":
                execution.time_out(str(event.details.get("reason", "")))
            elif (
                event.state == "integrity_failed"
                and event.kind == "replay.integrity_failed"
            ):
                execution.integrity_fail(str(event.details.get("reason", "")))
            else:
                raise ValueError(
                    "replay execution event stream contains an unknown transition"
                )
            if execution.events[-1].as_dict() != event.as_dict():
                raise ValueError("replay execution event stream is not canonical")
        return execution

    @property
    def events(self) -> tuple[ReplayExecutionEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def state(self) -> str:
        with self._lock:
            return self._events[-1].state

    @property
    def artifacts(self) -> Mapping[str, ArtifactRef]:
        with self._lock:
            return self._artifacts

    @property
    def integrity_verified(self) -> bool:
        with self._lock:
            return self._integrity_verified

    @property
    def claimable(self) -> bool:
        with self._lock:
            return (
                self.state == "completed"
                and self._integrity_verified
                and bool(self._artifacts)
            )

    def admit(self) -> None:
        self._transition("admitted", "replay.admitted", {})

    def run(self) -> None:
        self._transition("running", "replay.running", {})

    def complete(
        self, artifacts: Mapping[str, ArtifactRef], *, integrity_verified: bool
    ) -> None:
        if not integrity_verified:
            raise ValueError("completed replay must be integrity verified")
        if not artifacts or any(
            not isinstance(name, str) or not isinstance(ref, ArtifactRef)
            for name, ref in artifacts.items()
        ):
            raise ValueError("completed replay requires named immutable artifacts")
        artifact_refs = MappingProxyType(dict(sorted(artifacts.items())))
        details = {
            "artifacts": {name: ref.as_dict() for name, ref in artifact_refs.items()}
        }
        with self._lock:
            current = self._events[-1].state
            if "completed" not in _ALLOWED.get(current, frozenset()):
                raise RuntimeError(f"invalid replay transition {current} -> completed")
            self._append(
                "completed", "replay.completed", self._events[-1].span_id, details
            )
            self._artifacts = artifact_refs
            self._integrity_verified = True

    def fail(self, error: str) -> None:
        self._terminal("failed", "replay.failed", error)

    def cancel(self, reason: str) -> None:
        self._terminal("canceled", "replay.canceled", reason)

    def time_out(self, reason: str) -> None:
        self._terminal("timed_out", "replay.timed_out", reason)

    def integrity_fail(self, reason: str) -> None:
        self._terminal("integrity_failed", "replay.integrity_failed", reason)

    def require_claimable(self) -> None:
        if not self.claimable:
            raise RuntimeError(
                f"replay execution is not claimable from state {self.state}"
            )

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "bb.replay_execution.v1",
                "execution_id": self.execution_id,
                "plan_id": self.plan_id,
                "state": self.state,
                "integrity_verified": self._integrity_verified,
                "artifacts": {
                    name: ref.as_dict() for name, ref in self._artifacts.items()
                },
                "events": [event.as_dict() for event in self._events],
            }

    def _terminal(self, state: str, kind: str, reason: str) -> None:
        if not isinstance(reason, str) or not reason:
            raise ValueError("replay terminal reason must be populated")
        self._transition(state, kind, {"reason": reason})

    def _transition(self, state: str, kind: str, details: Mapping[str, Any]) -> None:
        with self._lock:
            current = self._events[-1].state
            if state not in _ALLOWED.get(current, frozenset()):
                raise RuntimeError(f"invalid replay transition {current} -> {state}")
            self._append(state, kind, self._events[-1].span_id, details)

    def _append(
        self, state: str, kind: str, parent: str | None, details: Mapping[str, Any]
    ) -> None:
        sequence = len(self._events) + 1
        span_id = f"{self.execution_id}:{sequence}"
        event = ReplayExecutionEvent(sequence, state, kind, span_id, parent, details)
        if self._sink is not None:
            self._sink.append(event)
        self._events.append(event)
