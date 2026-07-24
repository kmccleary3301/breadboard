from __future__ import annotations
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from breadboard.product.cli import harness as harness_operations
from breadboard.product.cli import session as operations
from breadboard.product.cli.result import CliResult, from_exception, portable_ref
from breadboard.product.runtime.events import JsonlEventSink, Session, _ProcessLock
from .models import (
    PublicResult,
    SessionApprovalRequest,
    SessionCancelRequest,
    SessionInputRequest,
    SessionStartRequest,
    invoke,
    invoke_idempotent,
    public_workspace,
    result_response,
    scrub_public,
    workspace_path,
)
router = APIRouter(tags=["public-session"])
def _args(workspace, session_id: str, **values):
    return SimpleNamespace(workspace=workspace, SESSION_ID=session_id, **values)
def _session_paths(workspace, session_id: str):
    if not session_id or session_id != Path(session_id).name:
        raise ValueError("session_id must be a portable identifier")
    directory = workspace_path(".breadboard/sessions", workspace)
    directory.mkdir(parents=True, exist_ok=True)
    event_path = workspace_path(str(operations._ep(workspace, session_id).relative_to(workspace)), workspace)
    metadata_path = workspace_path(str(operations._meta(workspace, session_id).relative_to(workspace)), workspace)
    return event_path, metadata_path, directory / f"{session_id}.mutation"
def _mutate(workspace, session_id: str, function):
    _, _, guard = _session_paths(workspace, session_id)
    with _ProcessLock(guard):
        return function()
def start_session_result(request: SessionStartRequest, workspace):
    session_id = request.session_id or str(uuid4())
    event_path, metadata_path, guard = _session_paths(workspace, session_id)
    lock_path = workspace_path(request.lock_id, workspace)
    lock, _ = harness_operations.load_lock(lock_path, workspace, explicit=True)
    with _ProcessLock(guard):
        if event_path.exists() or metadata_path.exists():
            raise ValueError(f"session already exists: {session_id}")
        session = Session.start(lock, request.task, session_id=session_id, sink=JsonlEventSink(event_path))
        operations._persist(workspace, session)
    view = session.read_model
    return CliResult.success(
        ["session", "start"],
        {"session": view.as_dict()},
        refs=[portable_ref(event_path, workspace)],
        hashes={"lock": view.effective_lock_hash, "task": view.task_hash},
        next_actions=[f"breadboard session get {session_id}"],
        stage="session.start",
    )
@router.get("/v1/sessions", operation_id="session.list", response_model=PublicResult)
def list_sessions():
    return invoke("session.list", lambda workspace: (workspace_path(".breadboard/sessions", workspace), operations.list_sessions(SimpleNamespace(workspace=workspace)))[1])
@router.post("/v1/sessions/{session_id}/input", operation_id="session.send_input", response_model=PublicResult, status_code=202)
def send_input(
    session_id: str,
    request: SessionInputRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, "content": request.content}
    return invoke_idempotent(
        "session.send_input",
        idempotency_key,
        values,
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.send_input(_args(workspace, session_id, content=request.content, TEXT=None)),
        ),
    )
@router.post("/v1/sessions/{session_id}/approve", operation_id="session.approve", response_model=PublicResult, status_code=202)
def approve(
    session_id: str,
    request: SessionApprovalRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, "request_id": request.request_id, "decision": request.decision}
    return invoke_idempotent(
        "session.approve",
        idempotency_key,
        values,
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.approve(
                _args(workspace, session_id, request_id=request.request_id, decision=request.decision)
            ),
        ),
    )
@router.post("/v1/sessions/{session_id}/resume", operation_id="session.resume", response_model=PublicResult, status_code=202)
def resume(
    session_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return invoke_idempotent(
        "session.resume",
        idempotency_key,
        {"session_id": session_id},
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.resume(_args(workspace, session_id)),
        ),
    )
@router.post("/v1/sessions/{session_id}/cancel", operation_id="session.cancel", response_model=PublicResult, status_code=202)
def cancel(
    session_id: str,
    request: SessionCancelRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    values = {"session_id": session_id, "reason": request.reason}
    return invoke_idempotent(
        "session.cancel",
        idempotency_key,
        values,
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.cancel(_args(workspace, session_id, reason=request.reason)),
        ),
    )
def _kernel_event(event):
    session_id, sequence = str(event["session_id"]), int(event["sequence"])
    return {
        "schema_version": "bb.kernel_event.v2",
        "event_id": f"{session_id}:{sequence}",
        "run_id": session_id,
        "session_id": session_id,
        "seq": sequence,
        "occurred_at_utc": event["occurred_at"],
        "actor": {"actor_kind": "system", "actor_id": "product.session"},
        "visibility": {
            "model_visible": True,
            "provider_visible": True,
            "host_visible": True,
            "redaction_state": "none",
        },
        "kind": event["kind"],
        "payload": event["payload"],
        "payload_schema_version": event["schema_version"],
    }
@router.get("/v1/sessions/{session_id}/events", operation_id="session.events")
def events(
    session_id: str,
    request: Request,
    resume_token: int | None = Query(default=None, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    limit: int = Query(default=256, ge=1, le=1000),
):
    workspace = None
    try:
        workspace = public_workspace()
        event_path, _, _ = _session_paths(workspace, session_id)
        if not event_path.is_file():
            raise FileNotFoundError(f"session not found: {session_id}")
    except Exception as error:
        return result_response(
            from_exception(["session", "events"], error, "session.events"),
            workspace=workspace,
        )
    start_after = resume_token if resume_token is not None else last_event_id or 0
    async def stream():
        emitted = 0
        with event_path.open() as source:
            while emitted < limit:
                position, line = source.tell(), source.readline()
                if not line:
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.05)
                    continue
                if not line.endswith("\n"):
                    source.seek(position)
                    await asyncio.sleep(0.05)
                    continue
                event = operations._event(json.loads(line)).as_dict()
                terminal = event["kind"] in {"session.completed", "session.failed", "session.canceled"}
                if int(event["sequence"]) <= start_after:
                    if terminal:
                        return
                    continue
                item = scrub_public(_kernel_event(event), workspace)
                sequence = int(event["sequence"])
                yield f"id: {sequence}\nevent: {event['kind']}\ndata: {json.dumps(item, sort_keys=True, separators=(',', ':'))}\n\n"
                emitted += 1
                if terminal:
                    return
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
@router.get("/v1/sessions/{session_id}/artifacts", operation_id="session.artifacts", response_model=PublicResult)
def artifacts(session_id: str):
    return invoke(
        "session.artifacts",
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.artifacts(_args(workspace, session_id)),
        ),
    )
@router.get("/v1/sessions/{session_id}", operation_id="session.get", response_model=PublicResult)
def get(session_id: str):
    return invoke(
        "session.get",
        lambda workspace: _mutate(
            workspace,
            session_id,
            lambda: operations.get(_args(workspace, session_id)),
        ),
    )
