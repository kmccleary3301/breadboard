from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

from breadboard.product.harness.lock import EffectiveHarnessLock, load_lock
from breadboard.product.operations.harness import LockHarnessRequest, lock_harness
from breadboard.product.operations.model import (
    EXIT_BLOCKED,
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)

from breadboard.product.runtime.events import KernelEvent, Session, SessionView
from breadboard.product.runtime.session_store import (
    load_session,
    session_artifact_rows,
    session_names,
    validate_session_id,
)


TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "canceled"})
_RUNTIME_UNAVAILABLE_MESSAGE = (
    "session runtime state is unavailable after service restart"
)


class SessionMutationError(RuntimeError):
    """Presentation-neutral failure raised by a mutation port."""

    def __init__(
        self,
        exit_code: int,
        error_code: str,
        message: str,
        *,
        hint: str | None = None,
        refs: Sequence[str] = (),
        next_actions: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.error_code = error_code
        self.message = message
        self.hint = hint
        self.refs = tuple(refs)
        self.next_actions = tuple(next_actions)


@dataclass(frozen=True, slots=True)
class StartSessionRequest:
    lock_id: str
    task: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class SendSessionInputRequest:
    session_id: str
    content: str


@dataclass(frozen=True, slots=True)
class ApproveSessionRequest:
    session_id: str
    request_id: str
    decision: str


@dataclass(frozen=True, slots=True)
class ResumeSessionRequest:
    session_id: str


@dataclass(frozen=True, slots=True)
class CancelSessionRequest:
    session_id: str
    reason: str = "operator request"


@dataclass(frozen=True, slots=True)
class StartSessionOutcome:
    view: SessionView
    refs: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendSessionInputOutcome:
    view: SessionView
    refs: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApproveSessionOutcome:
    view: SessionView
    refs: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResumeSessionOutcome:
    view: SessionView
    refs: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelSessionOutcome:
    view: SessionView
    refs: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)


class SessionMutationPort(Protocol):
    async def start(
        self,
        request: StartSessionRequest,
        context: OperationContext,
        effective_lock: EffectiveHarnessLock,
        source_path: Path,
    ) -> StartSessionOutcome: ...

    async def send_input(
        self,
        request: SendSessionInputRequest,
        context: OperationContext,
    ) -> SendSessionInputOutcome: ...

    async def approve(
        self,
        request: ApproveSessionRequest,
        context: OperationContext,
    ) -> ApproveSessionOutcome: ...

    async def resume(
        self,
        request: ResumeSessionRequest,
        context: OperationContext,
    ) -> ResumeSessionOutcome: ...

    async def cancel(
        self,
        request: CancelSessionRequest,
        context: OperationContext,
    ) -> CancelSessionOutcome: ...


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
    session_ids = set(session_names(context.workspace))
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
            allow_untrusted_running=live_port is not None,
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
            allow_untrusted_running=live_port is not None,
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
                allow_untrusted_running=live_port is not None,
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


def _mutation_result(
    command: Sequence[str],
    stage: str,
    outcome: (
        StartSessionOutcome
        | SendSessionInputOutcome
        | ApproveSessionOutcome
        | ResumeSessionOutcome
        | CancelSessionOutcome
    ),
) -> OperationResult:
    view = outcome.view
    hashes = dict(outcome.hashes) or {
        "lock": view.effective_lock_hash,
        "task": view.task_hash,
    }
    return OperationResult.success(
        command,
        {"session": view.as_dict()},
        outcome.refs,
        hashes,
        stage=stage,
    )


def _mutation_failure(
    command: Sequence[str],
    stage: str,
    error: SessionMutationError,
) -> OperationResult:
    return OperationResult.failure(
        command,
        error.exit_code,
        error.error_code,
        error.message,
        stage,
        hint=error.hint,
        refs=error.refs,
        next_actions=error.next_actions,
    )


def _resolve_start_lock(
    request: StartSessionRequest,
    context: OperationContext,
) -> tuple[EffectiveHarnessLock, Path, OperationResult]:
    lock_path = context.resolve_path(request.lock_id)
    lock, metadata_path = load_lock(lock_path, context.workspace, explicit=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_ref = metadata.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref:
        raise ValueError("lock metadata source_ref is missing")
    source_reference: str | Path = source_ref
    if not context.contained and not Path(source_ref).is_absolute():
        source_reference = context.workspace / source_ref
    source_path = context.resolve_path(source_reference)
    lock_request_path: str | Path = source_ref if context.contained else source_path
    lock_request_out: str | Path = (
        lock_path.relative_to(context.workspace) if context.contained else lock_path
    )
    checked = lock_harness(
        LockHarnessRequest(
            path=lock_request_path,
            out=lock_request_out,
            check=True,
        ),
        context,
    )
    return lock, source_path, checked


async def start(
    request: StartSessionRequest,
    context: OperationContext,
    mutation_port: SessionMutationPort,
) -> OperationResult:
    command = ["session", "start"]
    stage = "session.start"
    try:
        if request.session_id is not None:
            validate_session_id(request.session_id)
        effective_lock, source_path, checked = await asyncio.to_thread(
            _resolve_start_lock,
            request,
            context,
        )
        if not checked.ok:
            error = checked.error or {}
            return OperationResult.failure(
                command,
                checked.exit_code,
                str(error.get("error_code") or "lock_drift"),
                str(error.get("message") or "harness lock validation failed"),
                stage,
                hint=error.get("hint"),
                refs=checked.record_refs,
                next_actions=checked.next_actions,
            )
        outcome = await mutation_port.start(
            request,
            context,
            effective_lock,
            source_path,
        )
        return _mutation_result(command, stage, outcome)
    except SessionMutationError as error:
        return _mutation_failure(command, stage, error)
    except Exception as error:
        return from_exception(command, error, stage)


async def send_input(
    request: SendSessionInputRequest,
    context: OperationContext,
    mutation_port: SessionMutationPort,
) -> OperationResult:
    command = ["session", "send-input"]
    stage = "session.send-input"
    try:
        validate_session_id(request.session_id)
        return _mutation_result(
            command,
            stage,
            await mutation_port.send_input(request, context),
        )
    except SessionMutationError as error:
        return _mutation_failure(command, stage, error)
    except Exception as error:
        return from_exception(command, error, stage)


async def approve(
    request: ApproveSessionRequest,
    context: OperationContext,
    mutation_port: SessionMutationPort,
) -> OperationResult:
    command = ["session", "approve"]
    stage = "session.approve"
    try:
        validate_session_id(request.session_id)
        return _mutation_result(
            command,
            stage,
            await mutation_port.approve(request, context),
        )
    except SessionMutationError as error:
        return _mutation_failure(command, stage, error)
    except Exception as error:
        return from_exception(command, error, stage)


async def resume(
    request: ResumeSessionRequest,
    context: OperationContext,
    mutation_port: SessionMutationPort,
) -> OperationResult:
    command = ["session", "resume"]
    stage = "session.resume"
    try:
        validate_session_id(request.session_id)
        return _mutation_result(
            command,
            stage,
            await mutation_port.resume(request, context),
        )
    except SessionMutationError as error:
        return _mutation_failure(command, stage, error)
    except Exception as error:
        return from_exception(command, error, stage)


async def cancel(
    request: CancelSessionRequest,
    context: OperationContext,
    mutation_port: SessionMutationPort,
) -> OperationResult:
    command = ["session", "cancel"]
    stage = "session.cancel"
    try:
        validate_session_id(request.session_id)
        return _mutation_result(
            command,
            stage,
            await mutation_port.cancel(request, context),
        )
    except SessionMutationError as error:
        return _mutation_failure(command, stage, error)
    except Exception as error:
        return from_exception(command, error, stage)
