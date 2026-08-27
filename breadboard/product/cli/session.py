from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Coroutine

from breadboard.product.operations import session as session_operations
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard.product.runtime import session_store
from breadboard.product.runtime.events import SessionView


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


def _view(view: SessionView) -> dict[str, object]:
    return view.as_dict()


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


def _mutate(arguments: object, name: str, mutation) -> OperationResult:
    workspace = _workspace(arguments)
    try:
        session, event_path = session_store.load_session(
            workspace,
            arguments.SESSION_ID,
        )
        view = mutation(session)
        session_store.persist_session(workspace, session, event_path)
        return OperationResult.success(
            ["session", name],
            {"session": _view(view)},
            [portable_ref(event_path, workspace)],
            stage=f"session.{name}",
        )
    except Exception as error:
        return from_exception(["session", name], error, f"session.{name}")


def send_input(arguments: object) -> OperationResult:
    content = (
        arguments.content
        if getattr(arguments, "content", None) is not None
        else arguments.TEXT
    )
    return _mutate(arguments, "send-input", lambda session: session.input(content))


def approve(arguments: object) -> OperationResult:
    return _mutate(
        arguments,
        "approve",
        lambda session: session.resolve_approval(
            arguments.request_id,
            arguments.decision,
        ),
    )


def resume(arguments: object) -> OperationResult:
    return _mutate(arguments, "resume", lambda session: session.resume())


def cancel(arguments: object) -> OperationResult:
    reason = getattr(arguments, "reason", None) or "operator request"
    return _mutate(arguments, "cancel", lambda session: session.cancel(reason))


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
