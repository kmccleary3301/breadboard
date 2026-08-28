from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from breadboard_engine.api.cli_bridge.models import (
    SessionCommandRequest as BridgeSessionCommandRequest,
)
from breadboard_engine.api.cli_bridge.models import (
    SessionCreateRequest as BridgeSessionCreateRequest,
)
from breadboard_engine.api.cli_bridge.models import (
    SessionInputRequest as BridgeSessionInputRequest,
)
from breadboard.product.operations import session as session_operations
from breadboard.product.runtime import session_store

from .models import (
    PublicResult,
    SessionApprovalRequest,
    SessionCancelRequest,
    SessionInputRequest,
    SessionStartRequest,
    authorize_public_operation,
    from_public_exception,
    invoke_async,
    invoke_idempotent_async,
    public_workspace,
    public_operation_context,
    result_response,
    scrub_public,
    workspace_path,
)

router = APIRouter(tags=["public-session"])

_OBSERVATION_PAYLOAD_SCHEMAS = {
    "assistant_message": "bb.payload.message.assistant.v1",
    "tool_call": "bb.payload.tool.called.v1",
    "tool_result": "bb.payload.tool.completed.v1",
}


class _ProductSessionUnavailable(RuntimeError):
    pass


def _service(request: Request):
    return request.app.state.session_service


async def _product_session(service, session_id: str):
    if (
        not session_id
        or session_id in {".", ".."}
        or session_id != Path(session_id).name
    ):
        raise ValueError("session_id must be a portable identifier")
    record = await service.ensure_session(session_id)
    session = getattr(record, "product_session", None)
    if session is None:
        raise _ProductSessionUnavailable("session product state is unavailable")
    return record, session


class _LiveSessionAdapter:
    def __init__(self, service) -> None:
        self._service = service
        self._sessions = {}

    async def get_live_session(self, session_id: str):
        if session_id in self._sessions:
            return self._sessions[session_id]
        try:
            _, session = await _product_session(self._service, session_id)
            self._sessions[session_id] = session
            return session
        except _ProductSessionUnavailable:
            return None
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    async def list_live_sessions(self):
        sessions = []
        for summary in await self._service.list_sessions():
            try:
                session = await self.get_live_session(summary.session_id)
                if session is not None:
                    sessions.append(session)
            except Exception:
                continue
        return sessions

    async def get_live_artifacts(self, session_id: str):
        try:
            record, _ = await _product_session(self._service, session_id)
        except _ProductSessionUnavailable:
            return None
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise
        artifacts = getattr(record, "product_artifacts", {})
        return [
            {"name": name, **reference.as_dict()}
            for name, reference in sorted(artifacts.items())
        ]


async def _require_live_product_session(
    service,
    session_id: str,
    workspace: Path,
):
    try:
        return await _product_session(service, session_id)
    except _ProductSessionUnavailable:
        pass
    except HTTPException as error:
        if error.status_code != 404:
            raise
    await run_in_threadpool(
        session_store.load_session,
        workspace,
        session_id,
    )
    raise HTTPException(
        status_code=409,
        detail="session runtime state is unavailable after service restart",
    )


def _mutation_error(error: HTTPException) -> session_operations.SessionMutationError:
    status_code = int(error.status_code)
    exit_code = {404: 3, 409: 6, 422: 2}.get(
        status_code, 4 if status_code >= 500 else 2
    )
    error_code = {
        404: "path_unavailable",
        409: "invalid_state",
        422: "invalid_state",
    }.get(status_code, "runtime_failure" if status_code >= 500 else "invalid_state")
    return session_operations.SessionMutationError(
        exit_code,
        error_code,
        str(error.detail),
    )


class _LiveSessionMutationAdapter:
    def __init__(self, service) -> None:
        self._service = service

    async def start(
        self,
        request: session_operations.StartSessionRequest,
        context,
        effective_lock,
        source_path: Path,
    ) -> session_operations.StartSessionOutcome:
        try:
            created = await self._service.create_session(
                BridgeSessionCreateRequest(
                    config_path=str(source_path),
                    task=request.task,
                    workspace=str(context.workspace),
                    metadata={
                        "non_interactive_cli_session": True,
                        "cli_session_kind": "oneshot",
                    },
                ),
                session_id=request.session_id,
                event_root=workspace_path(
                    ".breadboard/sessions",
                    context.workspace,
                ),
                runtime_root=workspace_path(
                    ".breadboard/service_records",
                    context.workspace,
                ),
                effective_lock=effective_lock,
            )
            _, session = await _product_session(
                self._service,
                created.session_id,
            )
            return session_operations.StartSessionOutcome(session.read_model)
        except HTTPException as error:
            raise _mutation_error(error) from error

    async def send_input(
        self,
        request: session_operations.SendSessionInputRequest,
        context,
    ) -> session_operations.SendSessionInputOutcome:
        try:
            await _require_live_product_session(
                self._service,
                request.session_id,
                context.workspace,
            )
            await self._service.send_input(
                request.session_id,
                BridgeSessionInputRequest(content=request.content),
            )
            _, session = await _product_session(
                self._service,
                request.session_id,
            )
            return session_operations.SendSessionInputOutcome(session.read_model)
        except HTTPException as error:
            raise _mutation_error(error) from error

    async def approve(
        self,
        request: session_operations.ApproveSessionRequest,
        context,
    ) -> session_operations.ApproveSessionOutcome:
        try:
            await _require_live_product_session(
                self._service,
                request.session_id,
                context.workspace,
            )
            await self._service.execute_command(
                request.session_id,
                BridgeSessionCommandRequest(
                    command="permission_response",
                    payload={
                        "request_id": request.request_id,
                        "response": request.decision,
                    },
                ),
            )
            _, session = await _product_session(
                self._service,
                request.session_id,
            )
            return session_operations.ApproveSessionOutcome(session.read_model)
        except HTTPException as error:
            raise _mutation_error(error) from error

    async def resume(
        self,
        request: session_operations.ResumeSessionRequest,
        context,
    ) -> session_operations.ResumeSessionOutcome:
        try:
            await _require_live_product_session(
                self._service,
                request.session_id,
                context.workspace,
            )
            await self._service.execute_command(
                request.session_id,
                BridgeSessionCommandRequest(command="resume", payload={}),
            )
            _, session = await _product_session(
                self._service,
                request.session_id,
            )
            return session_operations.ResumeSessionOutcome(session.read_model)
        except HTTPException as error:
            raise _mutation_error(error) from error

    async def cancel(
        self,
        request: session_operations.CancelSessionRequest,
        context,
    ) -> session_operations.CancelSessionOutcome:
        try:
            await _require_live_product_session(
                self._service,
                request.session_id,
                context.workspace,
            )
            await self._service.stop_session(
                request.session_id,
                reason=request.reason,
            )
            _, session = await _product_session(
                self._service,
                request.session_id,
            )
            return session_operations.CancelSessionOutcome(session.read_model)
        except HTTPException as error:
            raise _mutation_error(error) from error


def _kernel_event(event):
    session_id, sequence = str(event["session_id"]), int(event["sequence"])
    return {
        "schema_version": "bb.kernel_event.v2",
        "event_id": f"session:{session_id}:{sequence}",
        "seq": sequence,
        "timestamp": event["occurred_at"],
        "work_item_id": None,
        "parent_work_item_id": None,
        "attempt_id": None,
        "session_id": session_id,
        "span_id": None,
        "visibility": {
            "model_visible": True,
            "provider_visible": True,
            "host_visible": True,
            "redaction_state": "none",
        },
        "kind": event["kind"],
        "payload": event["payload"],
        "payload_schema_version": _OBSERVATION_PAYLOAD_SCHEMAS.get(
            str(event["kind"]),
            event["schema_version"],
        ),
    }


@router.post(
    "/v1/sessions",
    operation_id="session.start",
    response_model=PublicResult,
    status_code=202,
)
async def start(
    request: SessionStartRequest,
    context: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = request.model_dump(mode="json")
    neutral_request = session_operations.StartSessionRequest(
        lock_id=request.lock_id,
        task=request.task,
        session_id=request.session_id,
    )
    return await invoke_idempotent_async(
        "session.start",
        idempotency_key,
        values,
        lambda workspace: session_operations.start(
            neutral_request,
            public_operation_context(workspace),
            _LiveSessionMutationAdapter(_service(context)),
        ),
    )


@router.get("/v1/sessions", operation_id="session.list", response_model=PublicResult)
async def list_sessions(request: Request):
    return await invoke_async(
        "session.list",
        lambda workspace: session_operations.list_sessions(
            session_operations.ListSessionsRequest(),
            public_operation_context(workspace),
            _LiveSessionAdapter(_service(request)),
        ),
    )


@router.post(
    "/v1/sessions/{session_id}/input",
    operation_id="session.send_input",
    response_model=PublicResult,
    status_code=202,
)
async def send_input(
    session_id: str,
    request: SessionInputRequest,
    context: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    neutral_request = session_operations.SendSessionInputRequest(
        session_id=session_id,
        content=request.content,
    )
    return await invoke_idempotent_async(
        "session.send_input",
        idempotency_key,
        values,
        lambda workspace: session_operations.send_input(
            neutral_request,
            public_operation_context(workspace),
            _LiveSessionMutationAdapter(_service(context)),
        ),
    )


@router.post(
    "/v1/sessions/{session_id}/approve",
    operation_id="session.approve",
    response_model=PublicResult,
    status_code=202,
)
async def approve(
    session_id: str,
    request: SessionApprovalRequest,
    context: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    neutral_request = session_operations.ApproveSessionRequest(
        session_id=session_id,
        request_id=request.request_id,
        decision=request.decision,
    )
    return await invoke_idempotent_async(
        "session.approve",
        idempotency_key,
        values,
        lambda workspace: session_operations.approve(
            neutral_request,
            public_operation_context(workspace),
            _LiveSessionMutationAdapter(_service(context)),
        ),
    )


@router.post(
    "/v1/sessions/{session_id}/resume",
    operation_id="session.resume",
    response_model=PublicResult,
    status_code=202,
)
async def resume(
    session_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await invoke_idempotent_async(
        "session.resume",
        idempotency_key,
        {"session_id": session_id},
        lambda workspace: session_operations.resume(
            session_operations.ResumeSessionRequest(session_id),
            public_operation_context(workspace),
            _LiveSessionMutationAdapter(_service(request)),
        ),
    )


@router.post(
    "/v1/sessions/{session_id}/cancel",
    operation_id="session.cancel",
    response_model=PublicResult,
    status_code=202,
)
async def cancel(
    session_id: str,
    request: SessionCancelRequest,
    context: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    neutral_request = session_operations.CancelSessionRequest(
        session_id=session_id,
        reason=request.reason,
    )
    return await invoke_idempotent_async(
        "session.cancel",
        idempotency_key,
        values,
        lambda workspace: session_operations.cancel(
            neutral_request,
            public_operation_context(workspace),
            _LiveSessionMutationAdapter(_service(context)),
        ),
    )


@router.get("/v1/sessions/{session_id}/events", operation_id="session.events")
async def events(
    session_id: str,
    request: Request,
    resume_token: int | None = Query(default=None, ge=0),
    last_event_id: int | None = Header(
        default=None,
        alias="Last-Event-ID",
        ge=0,
    ),
    limit: int = Query(default=256, ge=1, le=1000),
):
    granted = authorize_public_operation("session.events")
    if isinstance(granted, JSONResponse):
        return granted
    workspace = None
    start_after = resume_token if resume_token is not None else last_event_id or 0
    try:
        workspace = public_workspace()
        context = public_operation_context(workspace, capabilities=granted)
        live_port = _LiveSessionAdapter(_service(request))
        first_batch = await session_operations.read_session_event_batch(
            session_operations.ListSessionEventsRequest(
                session_id=session_id,
                after_sequence=start_after,
                limit=limit,
            ),
            context,
            live_port,
        )
    except Exception as error:
        return result_response(
            from_public_exception("session.events", error),
            workspace=workspace,
        )
    if first_batch.error is not None:
        return result_response(first_batch.error, workspace=workspace)

    async def bounded_stream():
        emitted = 0
        cursor = start_after
        batch = first_batch
        while emitted < limit:
            if not batch.events:
                if (
                    batch.source == "durable"
                    or batch.terminal
                    or await request.is_disconnected()
                ):
                    return
                await asyncio.sleep(0.05)
                batch = await session_operations.read_session_event_batch(
                    session_operations.ListSessionEventsRequest(
                        session_id=session_id,
                        after_sequence=cursor,
                        limit=limit - emitted,
                    ),
                    context,
                    live_port,
                )
                if batch.error is not None:
                    return
                continue
            for item in batch.events:
                event = item.as_dict()
                terminal = event["kind"] in {
                    "session.completed",
                    "session.failed",
                    "session.canceled",
                }
                public_event = scrub_public(_kernel_event(event), workspace)
                cursor = int(event["sequence"])
                yield (
                    f"id: {cursor}\n"
                    f"event: {event['kind']}\n"
                    "data: "
                    f"{json.dumps(public_event, sort_keys=True, separators=(',', ':'))}"
                    "\n\n"
                )
                emitted += 1
                if terminal or emitted >= limit:
                    return
            batch = await session_operations.read_session_event_batch(
                session_operations.ListSessionEventsRequest(
                    session_id=session_id,
                    after_sequence=cursor,
                    limit=limit - emitted,
                ),
                context,
                live_port,
            )
            if batch.error is not None:
                return

    return StreamingResponse(
        bounded_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get(
    "/v1/sessions/{session_id}/artifacts",
    operation_id="session.artifacts",
    response_model=PublicResult,
)
async def artifacts(session_id: str, request: Request):
    return await invoke_async(
        "session.artifacts",
        lambda workspace: session_operations.list_session_artifacts(
            session_operations.ListSessionArtifactsRequest(session_id),
            public_operation_context(workspace),
            _LiveSessionAdapter(_service(request)),
        ),
    )


@router.get(
    "/v1/sessions/{session_id}",
    operation_id="session.get",
    response_model=PublicResult,
)
async def get(session_id: str, request: Request):
    return await invoke_async(
        "session.get",
        lambda workspace: session_operations.get_session(
            session_operations.GetSessionRequest(session_id),
            public_operation_context(workspace),
            _LiveSessionAdapter(_service(request)),
        ),
    )
