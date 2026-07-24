from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from breadboard.product.cli import harness as harness_operations
from breadboard.product.cli import session as operations
from breadboard.product.cli.result import CliResult, from_exception, portable_ref
from breadboard.product.runtime.events import JsonlEventSink, Session
from .models import (
    PublicResult,
    SessionApprovalRequest,
    SessionCancelRequest,
    SessionInputRequest,
    SessionStartRequest,
    invoke,
    public_workspace,
    result_response,
    workspace_path,
)

router = APIRouter(tags=["public-session"])


def _args(workspace, session_id: str, **values):
    return SimpleNamespace(workspace=workspace, SESSION_ID=session_id, **values)


def start_session_result(request: SessionStartRequest, workspace):
    session_id = request.session_id or str(uuid4())
    if not session_id or session_id != Path(session_id).name:
        raise ValueError("session_id must be a portable identifier")
    lock_path = workspace_path(request.lock_id, workspace)
    lock, _ = harness_operations.load_lock(lock_path, workspace, explicit=True)
    event_path = operations._ep(workspace, session_id)
    if event_path.exists():
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
    return invoke("session.list", lambda workspace: operations.list_sessions(SimpleNamespace(workspace=workspace)))


@router.post("/v1/sessions/{session_id}/input", operation_id="session.send_input", response_model=PublicResult, status_code=202)
def send_input(session_id: str, request: SessionInputRequest):
    return invoke("session.send_input", lambda workspace: operations.send_input(_args(workspace, session_id, content=request.content, TEXT=None)))


@router.post("/v1/sessions/{session_id}/approve", operation_id="session.approve", response_model=PublicResult, status_code=202)
def approve(session_id: str, request: SessionApprovalRequest):
    return invoke("session.approve", lambda workspace: operations.approve(_args(workspace, session_id, request_id=request.request_id, decision=request.decision)))


@router.post("/v1/sessions/{session_id}/resume", operation_id="session.resume", response_model=PublicResult, status_code=202)
def resume(session_id: str):
    return invoke("session.resume", lambda workspace: operations.resume(_args(workspace, session_id)))


@router.post("/v1/sessions/{session_id}/cancel", operation_id="session.cancel", response_model=PublicResult, status_code=202)
def cancel(session_id: str, request: SessionCancelRequest):
    return invoke("session.cancel", lambda workspace: operations.cancel(_args(workspace, session_id, reason=request.reason)))


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
    resume_token: int | None = Query(default=None, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    limit: int = Query(default=256, ge=1, le=1000),
):
    workspace = None
    try:
        workspace = public_workspace()
        result = operations.events(_args(workspace, session_id))
    except Exception as error:
        result = from_exception(["session", "events"], error, "session.events")
    if not result.ok:
        return result_response(result, workspace=workspace)
    start_after = resume_token if resume_token is not None else last_event_id or 0
    rows = [event for event in result.data["events"] if int(event["sequence"]) > start_after][:limit]

    def stream():
        for event in rows:
            sequence = int(event["sequence"])
            item = _kernel_event(event)
            yield f"id: {sequence}\nevent: {event['kind']}\ndata: {json.dumps(item, sort_keys=True, separators=(',', ':'))}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/v1/sessions/{session_id}/artifacts", operation_id="session.artifacts", response_model=PublicResult)
def artifacts(session_id: str):
    return invoke("session.artifacts", lambda workspace: operations.artifacts(_args(workspace, session_id)))


@router.get("/v1/sessions/{session_id}", operation_id="session.get", response_model=PublicResult)
def get(session_id: str):
    return invoke("session.get", lambda workspace: operations.get(_args(workspace, session_id)))
