from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

from breadboard.product.operations import session as session_operations
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    portable_ref,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import session_store
from breadboard.product.runtime.events import Session, SessionView

_MutationOutcome = TypeVar(
    "_MutationOutcome",
    session_operations.StartSessionOutcome,
    session_operations.SendSessionInputOutcome,
    session_operations.ApproveSessionOutcome,
    session_operations.ResumeSessionOutcome,
    session_operations.CancelSessionOutcome,
)


def _workspace(arguments: object | None = None, workspace: Path | None = None) -> Path:
    selected = workspace or Path(getattr(arguments, "workspace", None) or Path.cwd())
    return selected.expanduser().resolve()


def _context(arguments: object) -> OperationContext:
    return OperationContext(
        workspace=_workspace(arguments),
        reference_root=Path.cwd().resolve(),
    )


def _run(operation: Coroutine[Any, Any, OperationResult]) -> OperationResult:
    return asyncio.run(operation)


def list_sessions(arguments: object) -> OperationResult:
    return _run(
        session_operations.list_sessions(
            session_operations.ListSessionsRequest(),
            _context(arguments),
        )
    )


def get(arguments: object, command_name: str = "get") -> OperationResult:
    return _run(
        session_operations.get_session(
            session_operations.GetSessionRequest(
                session_id=arguments.SESSION_ID,
                command_name=command_name,
            ),
            _context(arguments),
        )
    )


class _DurableSessionMutationAdapter:
    @staticmethod
    def _outcome(
        view: SessionView,
        event_path: Path,
        workspace: Path,
        outcome_type: Callable[
            [SessionView, tuple[str, ...]],
            _MutationOutcome,
        ],
    ) -> _MutationOutcome:
        return outcome_type(view, (portable_ref(event_path, workspace),))

    @staticmethod
    def _mutate(
        workspace: Path,
        session_id: str,
        mutation: Callable[[Session], SessionView],
        outcome_type: Callable[
            [SessionView, tuple[str, ...]],
            _MutationOutcome,
        ],
    ) -> _MutationOutcome:
        session, event_path = session_store.load_session(workspace, session_id)
        view = mutation(session)
        session_store.persist_session(workspace, session, event_path)
        return _DurableSessionMutationAdapter._outcome(
            view,
            event_path,
            workspace,
            outcome_type,
        )

    async def start(
        self,
        request: session_operations.StartSessionRequest,
        context: OperationContext,
        effective_lock: EffectiveHarnessLock,
        _source_path: Path,
    ) -> session_operations.StartSessionOutcome:
        def create() -> session_operations.StartSessionOutcome:
            if request.session_id is not None:
                try:
                    session_store.load_session(context.workspace, request.session_id)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError(f"session already exists: {request.session_id}")
            session = Session.start(
                effective_lock,
                request.task,
                session_id=request.session_id,
            )
            event_path = session_store.session_event_path(
                context.workspace,
                session.read_model.session_id,
            )
            event_path.parent.mkdir(parents=True, exist_ok=True)
            session_store.persist_session(context.workspace, session, event_path)
            return self._outcome(
                session.read_model,
                event_path,
                context.workspace,
                session_operations.StartSessionOutcome,
            )

        return await asyncio.to_thread(create)

    async def send_input(
        self,
        request: session_operations.SendSessionInputRequest,
        context: OperationContext,
    ) -> session_operations.SendSessionInputOutcome:
        return await asyncio.to_thread(
            self._mutate,
            context.workspace,
            request.session_id,
            lambda session: session.input(request.content),
            session_operations.SendSessionInputOutcome,
        )

    async def approve(
        self,
        request: session_operations.ApproveSessionRequest,
        context: OperationContext,
    ) -> session_operations.ApproveSessionOutcome:
        return await asyncio.to_thread(
            self._mutate,
            context.workspace,
            request.session_id,
            lambda session: session.resolve_approval(
                request.request_id,
                request.decision,
            ),
            session_operations.ApproveSessionOutcome,
        )

    async def resume(
        self,
        request: session_operations.ResumeSessionRequest,
        context: OperationContext,
    ) -> session_operations.ResumeSessionOutcome:
        return await asyncio.to_thread(
            self._mutate,
            context.workspace,
            request.session_id,
            lambda session: session.resume(),
            session_operations.ResumeSessionOutcome,
        )

    async def cancel(
        self,
        request: session_operations.CancelSessionRequest,
        context: OperationContext,
    ) -> session_operations.CancelSessionOutcome:
        return await asyncio.to_thread(
            self._mutate,
            context.workspace,
            request.session_id,
            lambda session: session.cancel(request.reason),
            session_operations.CancelSessionOutcome,
        )


def start(arguments: object) -> OperationResult:
    request = session_operations.StartSessionRequest(
        lock_id=str(
            getattr(arguments, "lock_id", None) or getattr(arguments, "LOCK_ID")
        ),
        task=str(getattr(arguments, "task", None) or getattr(arguments, "TASK")),
        session_id=getattr(arguments, "session_id", None),
    )
    return _run(
        session_operations.start(
            request,
            _context(arguments),
            _DurableSessionMutationAdapter(),
        )
    )


def send_input(arguments: object) -> OperationResult:
    content = (
        arguments.content
        if getattr(arguments, "content", None) is not None
        else arguments.TEXT
    )
    return _run(
        session_operations.send_input(
            session_operations.SendSessionInputRequest(
                session_id=arguments.SESSION_ID,
                content=content,
            ),
            _context(arguments),
            _DurableSessionMutationAdapter(),
        )
    )


def approve(arguments: object) -> OperationResult:
    return _run(
        session_operations.approve(
            session_operations.ApproveSessionRequest(
                session_id=arguments.SESSION_ID,
                request_id=arguments.request_id,
                decision=arguments.decision,
            ),
            _context(arguments),
            _DurableSessionMutationAdapter(),
        )
    )


def resume(arguments: object) -> OperationResult:
    return _run(
        session_operations.resume(
            session_operations.ResumeSessionRequest(arguments.SESSION_ID),
            _context(arguments),
            _DurableSessionMutationAdapter(),
        )
    )


def cancel(arguments: object) -> OperationResult:
    reason = getattr(arguments, "reason", None) or "operator request"
    return _run(
        session_operations.cancel(
            session_operations.CancelSessionRequest(arguments.SESSION_ID, reason),
            _context(arguments),
            _DurableSessionMutationAdapter(),
        )
    )


def events(arguments: object) -> OperationResult:
    return _run(
        session_operations.list_session_events(
            session_operations.ListSessionEventsRequest(arguments.SESSION_ID),
            _context(arguments),
        )
    )


def artifacts(arguments: object) -> OperationResult:
    return _run(
        session_operations.list_session_artifacts(
            session_operations.ListSessionArtifactsRequest(arguments.SESSION_ID),
            _context(arguments),
        )
    )
