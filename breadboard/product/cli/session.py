from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.operations import session as session_operations
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
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


_PUBLIC_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "ok",
        "status",
        "command",
        "record_refs",
        "hashes",
        "stage_outcomes",
        "warnings",
        "next_actions",
        "error",
        "exit_code",
        "data",
    }
)
_PROBLEM_FIELDS = frozenset(
    {
        "schema_version",
        "error_code",
        "message",
        "record_refs",
        "failed_stage",
        "hint",
        "next_actions",
    }
)
_STAGE_OUTCOME_FIELDS = frozenset(
    {"stage", "status", "report_ref", "next_action"}
)
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXIT_CODES = frozenset({0, 2, 3, 4, 5, 6})
_FAILURE_EXIT_CODES = _EXIT_CODES - {0}
_STAGE_STATUSES = frozenset({"passed", "failed", "blocked", "stale"})


def _is_string_list(value: object, *, nonempty: bool) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and (bool(item) or not nonempty)
        for item in value
    )


def _is_optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_problem(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == _PROBLEM_FIELDS
        and value["schema_version"] == "bb.problem.v1"
        and isinstance(value["error_code"], str)
        and _ERROR_CODE_PATTERN.fullmatch(value["error_code"]) is not None
        and isinstance(value["message"], str)
        and bool(value["message"])
        and _is_string_list(value["record_refs"], nonempty=True)
        and _is_optional_string(value["failed_stage"])
        and _is_optional_string(value["hint"])
        and _is_string_list(value["next_actions"], nonempty=True)
    )


def _is_stage_outcome(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == _STAGE_OUTCOME_FIELDS
        and isinstance(value["stage"], str)
        and bool(value["stage"])
        and isinstance(value["status"], str)
        and value["status"] in _STAGE_STATUSES
        and _is_optional_string(value["report_ref"])
        and _is_optional_string(value["next_action"])
    )


def _is_public_result(value: dict[str, Any]) -> bool:
    ok = value["ok"]
    status = value["status"]
    exit_code = value["exit_code"]
    stages = value["stage_outcomes"]
    error = value["error"]
    common_fields_valid = (
        type(ok) is bool
        and isinstance(status, str)
        and status in {"ok", "error"}
        and _is_string_list(value["command"], nonempty=False)
        and bool(value["command"])
        and _is_string_list(value["record_refs"], nonempty=True)
        and isinstance(value["hashes"], dict)
        and all(
            isinstance(name, str)
            and isinstance(digest, str)
            and _SHA256_PATTERN.fullmatch(digest) is not None
            for name, digest in value["hashes"].items()
        )
        and isinstance(stages, list)
        and all(_is_stage_outcome(stage) for stage in stages)
        and _is_string_list(value["warnings"], nonempty=True)
        and _is_string_list(value["next_actions"], nonempty=True)
        and type(exit_code) is int
        and exit_code in _EXIT_CODES
        and isinstance(value["data"], dict)
    )
    if not common_fields_valid:
        return False
    if ok:
        return (
            status == "ok"
            and exit_code == 0
            and error is None
            and all(stage["status"] == "passed" for stage in stages)
        )
    return (
        status == "error"
        and exit_code in _FAILURE_EXIT_CODES
        and _is_problem(error)
        and any(stage["status"] != "passed" for stage in stages)
    )


def _remote_client(arguments: object) -> Any | None:
    server = getattr(arguments, "server", None)
    if not server:
        return None
    from breadboard_sdk import BreadBoardClient

    auth_token = os.environ.get("BREADBOARD_API_TOKEN")
    if auth_token:
        return BreadBoardClient(str(server), auth_token=auth_token, timeout_s=120)
    return BreadBoardClient(str(server), timeout_s=120)


def _idempotency_key(arguments: object) -> str:
    return str(getattr(arguments, "idempotency_key", None) or uuid.uuid4().hex)


def _remote_result(value: object) -> OperationResult:
    if not isinstance(value, dict) or value.keys() != _PUBLIC_RESULT_FIELDS:
        raise ValueError("server returned an invalid public result")
    if value["schema_version"] != "bb.cli.result.v1":
        raise ValueError("server returned an unsupported public result")
    if not _is_public_result(value):
        raise ValueError("server returned an invalid public result")
    return OperationResult(
        command=value["command"],
        ok=value["ok"],
        exit_code=value["exit_code"],
        record_refs=value["record_refs"],
        hashes=value["hashes"],
        stage_outcomes=value["stage_outcomes"],
        warnings=value["warnings"],
        next_actions=value["next_actions"],
        error=value["error"],
        data=value["data"],
    )


def _retarget_remote_result(
    result: OperationResult,
    command: list[str],
    stage: str,
) -> OperationResult:
    source_stage = ".".join(result.command)
    if list(result.command) == command:
        return result
    result.command = command
    result.stage_outcomes = [
        {**outcome, "stage": stage}
        if outcome.get("stage") == source_stage
        else outcome
        for outcome in result.stage_outcomes
    ]
    if (
        result.error is not None
        and result.error.get("failed_stage") == source_stage
    ):
        result.error = {**result.error, "failed_stage": stage}
    return result


def _remote_error(
    command: list[str],
    stage: str,
    status: int,
    body: object,
) -> OperationResult:
    expected_exit_codes = {
        401: frozenset({2}),
        404: frozenset({3}),
        409: frozenset({5, 6}),
        422: frozenset({2}),
    }.get(
        status,
        frozenset({4 if status >= 500 else 2}),
    )
    if isinstance(body, dict) and body.keys() == _PUBLIC_RESULT_FIELDS:
        canonical_error = _remote_result(body)
        if (
            not canonical_error.ok
            and canonical_error.exit_code in expected_exit_codes
        ):
            return canonical_error
    error_code = "runtime_failure" if status >= 500 else "invalid_state"
    message = f"remote server returned HTTP {status}"
    if isinstance(body, dict):
        if isinstance(body.get("error"), str):
            error_code = body["error"]
        elif isinstance(body.get("error_code"), str):
            error_code = body["error_code"]
        if isinstance(body.get("detail"), str):
            message = body["detail"]
        elif isinstance(body.get("message"), str):
            message = body["message"]
    exit_code = {401: 2, 404: 3, 409: 6, 422: 2}.get(
        status,
        4 if status >= 500 else 2,
    )
    return OperationResult.failure(
        command,
        exit_code,
        error_code,
        message,
        stage,
    )


def _remote_operation(
    command: list[str],
    stage: str,
    operation: Callable[[], OperationResult],
) -> OperationResult:
    from breadboard_sdk import ApiError

    try:
        result = operation()
    except ApiError as error:
        result = _remote_error(command, stage, error.status, error.body)
    return _retarget_remote_result(result, command, stage)



_REMOTE_EVENT_PAGE_SIZE = 256
_TERMINAL_EVENT_KINDS = frozenset(
    {"session.completed", "session.failed", "session.canceled"}
)


def _remote_event_snapshot(
    client: Any,
    session_id: str,
    upper_sequence: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    resume_token = 0
    while resume_token < upper_sequence:
        page_limit = min(
            _REMOTE_EVENT_PAGE_SIZE,
            upper_sequence - resume_token,
        )
        page = list(
            client.events_session(
                session_id,
                resume_token=resume_token or None,
                limit=page_limit,
                follow=False,
            )
        )
        if len(page) > page_limit:
            raise ValueError("server returned an oversized session event page")
        if not page:
            if events:
                return events
            raise ValueError(
                "server event snapshot ended before its initial bound"
            )
        terminal_sequence = None
        previous_sequence = resume_token
        for event in page:
            sequence = event["seq"]
            if type(sequence) is not int or sequence <= previous_sequence:
                raise ValueError(
                    "server returned a non-increasing session event page"
                )
            if sequence > upper_sequence:
                raise ValueError(
                    "server event snapshot exceeded its initial bound"
                )
            if terminal_sequence is not None:
                raise ValueError("server returned an event after termination")
            if event["kind"] in _TERMINAL_EVENT_KINDS:
                if sequence != upper_sequence:
                    raise ValueError(
                        "server event snapshot terminated before its initial bound"
                    )
                terminal_sequence = sequence
            previous_sequence = sequence
        next_resume_token = page[-1]["seq"]
        events.extend(page)
        if next_resume_token == upper_sequence:
            return events
        resume_token = next_resume_token
    return events


def _remote_events_result(client: Any, session_id: str) -> OperationResult:
    session_result = _remote_result(client.get_session(session_id))
    if not session_result.ok:
        return session_result
    if not isinstance(session_result.data, dict):
        raise ValueError("server returned invalid session snapshot metadata")
    session = session_result.data.get("session")
    if not isinstance(session, dict):
        raise ValueError("server returned invalid session snapshot metadata")
    upper_sequence = session.get("event_count")
    if type(upper_sequence) is not int or upper_sequence < 1:
        raise ValueError("server returned invalid session event count")
    return OperationResult.success(
        ["session", "events"],
        {
            "session_id": session_id,
            "events": _remote_event_snapshot(
                client,
                session_id,
                upper_sequence,
            ),
        },
        stage="session.events",
    )

def list_sessions(arguments: object) -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "list"],
            "session.list",
            lambda: _remote_result(client.list_session()),
        )
    runtime = session_operations.SessionRuntime(_context(arguments))
    return _run(
        runtime.list_sessions(session_operations.ListSessionsRequest())
    )


def get(arguments: object, command_name: str = "get") -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", command_name],
            f"session.{command_name}",
            lambda: _remote_result(client.get_session(arguments.SESSION_ID)),
        )
    runtime = session_operations.SessionRuntime(_context(arguments))
    return _run(
        runtime.get_session(
            session_operations.GetSessionRequest(
                session_id=arguments.SESSION_ID,
                command_name=command_name,
            )
        )
    )


def bootstrap_local(arguments: object) -> OperationResult:
    """Create private authority for one explicitly selected local legacy session."""
    context = _context(arguments)
    command = ["session", "bootstrap-local"]
    try:
        durable, event_path = session_store.bootstrap_local_session_authority(
            context.workspace,
            arguments.SESSION_ID,
        )
        return OperationResult.success(
            command,
            {
                "session": durable.read_model.as_dict(),
                "projection_authority": "committed",
            },
            (portable_ref(event_path, context.workspace),),
            stage="session.bootstrap-local",
        )
    except Exception as error:
        return from_exception(command, error, "session.bootstrap-local")


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
        view, event_path = session_store.mutate_session(
            workspace,
            session_id,
            mutation,
        )
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
            session = Session.start(
                effective_lock,
                request.task,
                session_id=request.session_id,
            )
            session, event_path = session_store.create_session(
                context.workspace,
                session,
            )
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
    runtime = session_operations.SessionRuntime(
        _context(arguments),
        mutation_port=_DurableSessionMutationAdapter(),
    )
    return _run(runtime.start(request))


def send_input(arguments: object) -> OperationResult:
    content = (
        arguments.content
        if getattr(arguments, "content", None) is not None
        else arguments.TEXT
    )
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "send-input"],
            "session.send-input",
            lambda: _remote_result(
                client.send_input_session(
                    arguments.SESSION_ID,
                    content,
                    idempotency_key=_idempotency_key(arguments),
                )
            ),
        )
    runtime = session_operations.SessionRuntime(
        _context(arguments),
        mutation_port=_DurableSessionMutationAdapter(),
    )
    return _run(
        runtime.send_input(
            session_operations.SendSessionInputRequest(
                session_id=arguments.SESSION_ID,
                content=content,
            )
        )
    )


def approve(arguments: object) -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "approve"],
            "session.approve",
            lambda: _remote_result(
                client.approve_session(
                    arguments.SESSION_ID,
                    arguments.request_id,
                    arguments.decision,
                    idempotency_key=_idempotency_key(arguments),
                )
            ),
        )
    runtime = session_operations.SessionRuntime(
        _context(arguments),
        mutation_port=_DurableSessionMutationAdapter(),
    )
    return _run(
        runtime.approve(
            session_operations.ApproveSessionRequest(
                session_id=arguments.SESSION_ID,
                request_id=arguments.request_id,
                decision=arguments.decision,
            )
        )
    )


def resume(arguments: object) -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "resume"],
            "session.resume",
            lambda: _remote_result(
                client.resume_session(
                    arguments.SESSION_ID,
                    idempotency_key=_idempotency_key(arguments),
                )
            ),
        )
    runtime = session_operations.SessionRuntime(
        _context(arguments),
        mutation_port=_DurableSessionMutationAdapter(),
    )
    return _run(
        runtime.resume(
            session_operations.ResumeSessionRequest(arguments.SESSION_ID)
        )
    )


def cancel(arguments: object) -> OperationResult:
    reason = getattr(arguments, "reason", None) or "operator request"
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "cancel"],
            "session.cancel",
            lambda: _remote_result(
                client.cancel_session(
                    arguments.SESSION_ID,
                    reason,
                    idempotency_key=_idempotency_key(arguments),
                )
            ),
        )
    runtime = session_operations.SessionRuntime(
        _context(arguments),
        mutation_port=_DurableSessionMutationAdapter(),
    )
    return _run(
        runtime.cancel(
            session_operations.CancelSessionRequest(arguments.SESSION_ID, reason)
        )
    )


def events(arguments: object) -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "events"],
            "session.events",
            lambda: _remote_events_result(client, arguments.SESSION_ID),
        )
    runtime = session_operations.SessionRuntime(_context(arguments))
    return _run(
        runtime.list_session_events(
            session_operations.ListSessionEventsRequest(arguments.SESSION_ID)
        )
    )


def artifacts(arguments: object) -> OperationResult:
    client = _remote_client(arguments)
    if client is not None:
        return _remote_operation(
            ["session", "artifacts"],
            "session.artifacts",
            lambda: _remote_result(client.artifacts_session(arguments.SESSION_ID)),
        )
    runtime = session_operations.SessionRuntime(_context(arguments))
    return _run(
        runtime.list_session_artifacts(
            session_operations.ListSessionArtifactsRequest(arguments.SESSION_ID)
        )
    )
