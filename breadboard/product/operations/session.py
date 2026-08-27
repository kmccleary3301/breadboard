from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from breadboard.product.operations.model import (
    EXIT_BLOCKED,
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard.product.runtime.events import KernelEvent, Session
from breadboard.product.runtime.session_store import (
    load_session,
    session_artifact_rows,
    session_names,
)


TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "canceled"})
_RUNTIME_UNAVAILABLE_MESSAGE = (
    "session runtime state is unavailable after service restart"
)


class LiveSessionReadPort(Protocol):
    async def get_live_session(self, session_id: str) -> Session | None: ...

    async def list_live_sessions(self) -> Sequence[Session]: ...

    async def get_live_artifacts(
        self,
        session_id: str,
    ) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class ListSessionsRequest:
    pass


@dataclass(frozen=True, slots=True)
class GetSessionRequest:
    session_id: str
    command_name: str = "get"


@dataclass(frozen=True, slots=True)
class ListSessionArtifactsRequest:
    session_id: str


@dataclass(frozen=True, slots=True)
class ListSessionEventsRequest:
    session_id: str
    after_sequence: int = 0
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class SessionEventBatch:
    events: tuple[KernelEvent, ...]
    cursor: int
    terminal: bool
    source: Literal["live", "durable"] | None
    record_ref: str | None = None
    error: OperationResult | None = None


def _runtime_unavailable(command_name: str) -> OperationResult:
    return OperationResult.failure(
        ["session", command_name],
        EXIT_BLOCKED,
        "invalid_state",
        _RUNTIME_UNAVAILABLE_MESSAGE,
        f"session.{command_name}",
    )


def _session_result(
    session: Session,
    command_name: str,
    *,
    refs: Sequence[str] = (),
) -> OperationResult:
    view = session.read_model
    return OperationResult.success(
        ["session", command_name],
        {"session": view.as_dict()},
        refs,
        {"lock": view.effective_lock_hash, "task": view.task_hash},
        stage=f"session.{command_name}",
    )


def _durable_sessions(
    context: OperationContext,
) -> tuple[list[tuple[Session, str]], list[str]]:
    suffix = ".events.jsonl"
    names = session_names(context.workspace)
    session_ids = {
        name[: -len(suffix)] if name.endswith(suffix) else name for name in names
    }
    sessions: list[tuple[Session, str]] = []
    refs: list[str] = []
    for session_id in sorted(session_ids):
        try:
            session, event_path = load_session(context.workspace, session_id)
            reference = portable_ref(event_path, context.workspace)
            sessions.append((session, reference))
            refs.append(reference)
        except Exception:
            pass
    return sessions, refs


async def list_sessions(
    _request: ListSessionsRequest,
    context: OperationContext,
    live_port: LiveSessionReadPort | None = None,
) -> OperationResult:
    try:
        durable, refs = await asyncio.to_thread(_durable_sessions, context)
        if live_port is None:
            rows = [
                {
                    "session_id": session.read_model.session_id,
                    "status": session.read_model.status,
                    "event_count": session.read_model.event_count,
                }
                for session, _ in durable
            ]
        else:
            rows_by_id = {
                session.read_model.session_id: {
                    "session_id": session.read_model.session_id,
                    "status": session.read_model.status,
                    "event_count": session.read_model.event_count,
                }
                for session, _ in durable
                if session.read_model.status in TERMINAL_SESSION_STATUSES
            }
            for session in await live_port.list_live_sessions():
                view = session.read_model
                rows_by_id[view.session_id] = {
                    "session_id": view.session_id,
                    "status": view.status,
                    "event_count": view.event_count,
                }
            rows = [rows_by_id[session_id] for session_id in sorted(rows_by_id)]
        return OperationResult.success(
            ["session", "list"],
            {"sessions": rows, "count": len(rows)},
            refs,
            stage="session.list",
        )
    except Exception as error:
        return from_exception(["session", "list"], error, "session.list")


async def get_session(
    request: GetSessionRequest,
    context: OperationContext,
    live_port: LiveSessionReadPort | None = None,
) -> OperationResult:
    try:
        if live_port is not None:
            live_session = await live_port.get_live_session(request.session_id)
            if live_session is not None:
                return _session_result(live_session, request.command_name)
        session, event_path = await asyncio.to_thread(
            load_session,
            context.workspace,
            request.session_id,
        )
        if (
            live_port is not None
            and session.read_model.status not in TERMINAL_SESSION_STATUSES
        ):
            return _runtime_unavailable(request.command_name)
        refs = (
            ()
            if live_port is not None
            else (portable_ref(event_path, context.workspace),)
        )
        return _session_result(
            session,
            request.command_name,
            refs=refs,
        )
    except Exception as error:
        return from_exception(
            ["session", request.command_name],
            error,
            f"session.{request.command_name}",
        )


async def list_session_artifacts(
    request: ListSessionArtifactsRequest,
    context: OperationContext,
    live_port: LiveSessionReadPort | None = None,
) -> OperationResult:
    try:
        if live_port is not None:
            live_rows = await live_port.get_live_artifacts(request.session_id)
            if live_rows is not None:
                return OperationResult.success(
                    ["session", "artifacts"],
                    {
                        "session_id": request.session_id,
                        "artifacts": live_rows,
                    },
                    stage="session.artifacts",
                )
        session, event_path = await asyncio.to_thread(
            load_session,
            context.workspace,
            request.session_id,
        )
        if (
            live_port is not None
            and session.read_model.status not in TERMINAL_SESSION_STATUSES
        ):
            return _runtime_unavailable("artifacts")
        rows = await asyncio.to_thread(
            session_artifact_rows,
            context.workspace,
            request.session_id,
        )
        return OperationResult.success(
            ["session", "artifacts"],
            {"session_id": request.session_id, "artifacts": rows},
            [portable_ref(event_path, context.workspace)],
            stage="session.artifacts",
        )
    except Exception as error:
        return from_exception(
            ["session", "artifacts"],
            error,
            "session.artifacts",
        )


async def read_session_event_batch(
    request: ListSessionEventsRequest,
    context: OperationContext,
    live_port: LiveSessionReadPort | None = None,
) -> SessionEventBatch:
    command = ["session", "events"]
    try:
        source: Literal["live", "durable"] = "durable"
        session = None
        if live_port is not None:
            session = await live_port.get_live_session(request.session_id)
            if session is not None:
                source = "live"
        record_ref = None
        if session is None:
            session, event_path = await asyncio.to_thread(
                load_session,
                context.workspace,
                request.session_id,
            )
            record_ref = portable_ref(event_path, context.workspace)
            if (
                live_port is not None
                and session.read_model.status not in TERMINAL_SESSION_STATUSES
            ):
                return SessionEventBatch(
                    events=(),
                    cursor=request.after_sequence,
                    terminal=False,
                    source=None,
                    error=_runtime_unavailable("events"),
                )
        events = tuple(
            event for event in session.events if event.sequence > request.after_sequence
        )
        if request.limit is not None:
            events = events[: request.limit]
        cursor = events[-1].sequence if events else request.after_sequence
        return SessionEventBatch(
            events=events,
            cursor=cursor,
            terminal=session.read_model.status in TERMINAL_SESSION_STATUSES,
            source=source,
            record_ref=record_ref,
        )
    except Exception as error:
        return SessionEventBatch(
            events=(),
            cursor=request.after_sequence,
            terminal=False,
            source=None,
            error=from_exception(command, error, "session.events"),
        )


async def list_session_events(
    request: ListSessionEventsRequest,
    context: OperationContext,
    live_port: LiveSessionReadPort | None = None,
) -> OperationResult:
    batch = await read_session_event_batch(request, context, live_port)
    if batch.error is not None:
        return batch.error
    refs = [batch.record_ref] if batch.record_ref is not None else []
    return OperationResult.success(
        ["session", "events"],
        {
            "session_id": request.session_id,
            "events": [event.as_dict() for event in batch.events],
        },
        refs,
        stage="session.events",
    )
