from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
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
from breadboard.product.cli import harness as harness_operations
from breadboard.product.cli import session as session_operations
from breadboard.product.cli.result import CliResult

from .models import (
    PublicResult,
    SessionApprovalRequest,
    SessionCancelRequest,
    SessionInputRequest,
    SessionStartRequest,
    from_public_exception,
    invoke_async,
    invoke_idempotent_async,
    public_workspace,
    result_response,
    scrub_public,
    workspace_path,
)

router = APIRouter(tags=["public-session"])
_TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "canceled"})
class _ProductSessionUnavailable(RuntimeError):
    pass
def _service(request: Request):
    return request.app.state.session_service
async def _product_session(service, session_id: str):
    if not session_id or session_id in {".", ".."} or session_id != Path(session_id).name:
        raise ValueError("session_id must be a portable identifier")
    record = await service.ensure_session(session_id)
    session = getattr(record, "product_session", None)
    if session is None:
        raise _ProductSessionUnavailable("session product state is unavailable")
    return record, session
async def _read_product_session(service, session_id: str, workspace: Path):
    try:
        return await _product_session(service, session_id)
    except _ProductSessionUnavailable:
        pass
    except HTTPException as error:
        if error.status_code != 404:
            raise
    session, _ = await run_in_threadpool(session_operations.load_session, workspace, session_id)
    if session.read_model.status not in _TERMINAL_SESSION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="session runtime state is unavailable after service restart",
        )
    return None, session
async def _require_live_product_session(service, session_id: str, workspace: Path):
    try:
        return await _product_session(service, session_id)
    except _ProductSessionUnavailable:
        pass
    except HTTPException as error:
        if error.status_code != 404:
            raise
    await run_in_threadpool(session_operations.load_session, workspace, session_id)
    raise HTTPException(
        status_code=409,
        detail="session runtime state is unavailable after service restart",
    )
async def _session_result(service, session_id: str, command_name: str) -> CliResult:
    _, session = await _product_session(service, session_id)
    view = session.read_model
    return CliResult.success(
        ["session", command_name],
        {"session": view.as_dict()},
        hashes={"lock": view.effective_lock_hash, "task": view.task_hash},
        stage=f"session.{command_name}",
    )
async def _read_session_result(service, session_id: str, command_name: str, workspace: Path) -> CliResult:
    _, session = await _read_product_session(service, session_id, workspace)
    view = session.read_model
    return CliResult.success(
        ["session", command_name],
        {"session": view.as_dict()},
        hashes={"lock": view.effective_lock_hash, "task": view.task_hash},
        stage=f"session.{command_name}",
    )
def _resolve_start_lock(request: SessionStartRequest, workspace: Path):
    lock_path = workspace_path(request.lock_id, workspace)
    lock, metadata_path = harness_operations.load_lock(lock_path, workspace, explicit=True)
    metadata = json.loads(metadata_path.read_text())
    source_ref = metadata.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref:
        raise ValueError("lock metadata source_ref is missing")
    source_path = workspace_path(source_ref, workspace)
    checked = harness_operations.lock(SimpleNamespace(
        workspace=workspace,
        PATH=source_path,
        out=lock_path,
        check=True,
        contained=True,
    ))
    return lock, source_path, checked
async def _start_result(request: SessionStartRequest, workspace: Path, service) -> CliResult:
    if request.session_id and (request.session_id in {".", ".."} or request.session_id != Path(request.session_id).name):
        raise ValueError("session_id must be a portable identifier")
    effective_lock, source_path, checked = await run_in_threadpool(_resolve_start_lock, request, workspace)
    if not checked.ok:
        error = checked.error or {}
        return CliResult.failure(
            ["session", "start"],
            checked.exit_code,
            str(error.get("error_code") or "lock_drift"),
            str(error.get("message") or "harness lock validation failed"),
            "session.start",
            hint=error.get("hint"),
            refs=checked.record_refs,
            next_actions=checked.next_actions,
        )
    created = await service.create_session(
        BridgeSessionCreateRequest(
            config_path=str(source_path),
            task=request.task,
            workspace=str(workspace),
            metadata={"non_interactive_cli_session": True, "cli_session_kind": "oneshot"},
        ),
        session_id=request.session_id,
        event_root=workspace_path(".breadboard/sessions", workspace),
        runtime_root=workspace_path(".breadboard/service_records", workspace),
        effective_lock=effective_lock,
    )
    return await _session_result(service, created.session_id, "start")
async def _list_result(service, workspace: Path) -> CliResult:
    durable = await run_in_threadpool(session_operations.list_sessions, SimpleNamespace(workspace=workspace))
    if not durable.ok:
        return durable
    rows = {
        row["session_id"]: row
        for row in durable.data.get("sessions", [])
        if row.get("status") in _TERMINAL_SESSION_STATUSES
    }
    for summary in await service.list_sessions():
        try:
            _, session = await _product_session(service, summary.session_id)
            view = session.read_model
            rows[view.session_id] = {"session_id": view.session_id, "status": view.status, "event_count": view.event_count}
        except Exception:
            continue
    ordered = [rows[session_id] for session_id in sorted(rows)]
    return CliResult.success(["session", "list"], {"sessions": ordered, "count": len(ordered)}, refs=durable.record_refs, stage="session.list")
async def _send_result(service, session_id: str, content: str, workspace: Path) -> CliResult:
    await _require_live_product_session(service, session_id, workspace)
    await service.send_input(session_id, BridgeSessionInputRequest(content=content))
    return await _session_result(service, session_id, "send-input")
async def _command_result(service, session_id: str, command: str, payload: dict, command_name: str, workspace: Path) -> CliResult:
    await _require_live_product_session(service, session_id, workspace)
    await service.execute_command(session_id, BridgeSessionCommandRequest(command=command, payload=payload))
    return await _session_result(service, session_id, command_name)
async def _cancel_result(service, session_id: str, reason: str, workspace: Path) -> CliResult:
    await _require_live_product_session(service, session_id, workspace)
    await service.stop_session(session_id, reason=reason)
    return await _session_result(service, session_id, "cancel")
async def _artifacts_result(service, session_id: str) -> CliResult:
    record, _ = await _product_session(service, session_id)
    artifacts = getattr(record, "product_artifacts", {})
    rows = [{"name": name, **ref.as_dict()} for name, ref in sorted(artifacts.items())]
    return CliResult.success(["session", "artifacts"], {"session_id": session_id, "artifacts": rows}, stage="session.artifacts")
async def _read_artifacts_result(service, session_id: str, workspace: Path) -> CliResult:
    try:
        return await _artifacts_result(service, session_id)
    except _ProductSessionUnavailable:
        pass
    except HTTPException as error:
        if error.status_code != 404:
            raise
    await _read_product_session(service, session_id, workspace)
    return await run_in_threadpool(session_operations.artifacts, SimpleNamespace(workspace=workspace, SESSION_ID=session_id))
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
        "payload_schema_version": event["schema_version"],
    }
@router.post("/v1/sessions", operation_id="session.start", response_model=PublicResult, status_code=202)
async def start(request: SessionStartRequest, context: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    values = request.model_dump(mode="json")
    return await invoke_idempotent_async(
        "session.start",
        idempotency_key,
        values,
        lambda workspace: _start_result(request, workspace, _service(context)),
    )
@router.get("/v1/sessions", operation_id="session.list", response_model=PublicResult)
async def list_sessions(request: Request):
    return await invoke_async("session.list", lambda workspace: _list_result(_service(request), workspace))
@router.post("/v1/sessions/{session_id}/input", operation_id="session.send_input", response_model=PublicResult, status_code=202)
async def send_input(session_id: str, request: SessionInputRequest, context: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    return await invoke_idempotent_async(
        "session.send_input",
        idempotency_key,
        values,
        lambda workspace: _send_result(_service(context), session_id, request.content, workspace),
    )
@router.post("/v1/sessions/{session_id}/approve", operation_id="session.approve", response_model=PublicResult, status_code=202)
async def approve(session_id: str, request: SessionApprovalRequest, context: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    return await invoke_idempotent_async(
        "session.approve",
        idempotency_key,
        values,
        lambda workspace: _command_result(
            _service(context), session_id, "permission_response",
            {"request_id": request.request_id, "response": request.decision}, "approve", workspace,
        ),
    )
@router.post("/v1/sessions/{session_id}/resume", operation_id="session.resume", response_model=PublicResult, status_code=202)
async def resume(session_id: str, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return await invoke_idempotent_async(
        "session.resume",
        idempotency_key,
        {"session_id": session_id},
        lambda workspace: _command_result(_service(request), session_id, "resume", {}, "resume", workspace),
    )
@router.post("/v1/sessions/{session_id}/cancel", operation_id="session.cancel", response_model=PublicResult, status_code=202)
async def cancel(session_id: str, request: SessionCancelRequest, context: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    values = {"session_id": session_id, **request.model_dump(mode="json")}
    return await invoke_idempotent_async(
        "session.cancel",
        idempotency_key,
        values,
        lambda workspace: _cancel_result(_service(context), session_id, request.reason, workspace),
    )
@router.get("/v1/sessions/{session_id}/events", operation_id="session.events")
async def events(session_id: str, request: Request, resume_token: int | None = Query(default=None, ge=0), last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0), limit: int = Query(default=256, ge=1, le=1000)):
    workspace = None
    try:
        workspace = public_workspace()
        record, session = await _read_product_session(_service(request), session_id, workspace)
    except Exception as error:
        return result_response(from_public_exception("session.events", error), workspace=workspace)
    start_after = resume_token if resume_token is not None else last_event_id or 0
    async def bounded_stream():
        emitted = 0
        cursor = start_after
        while emitted < limit:
            ready = await run_in_threadpool(lambda cursor=cursor: [event for event in session.events if event.sequence > cursor])
            if not ready:
                if record is None or session.read_model.status in _TERMINAL_SESSION_STATUSES or await request.is_disconnected():
                    return
                await asyncio.sleep(0.05)
                continue
            for item in ready:
                event = item.as_dict()
                terminal = event["kind"] in {"session.completed", "session.failed", "session.canceled"}
                public_event = scrub_public(_kernel_event(event), workspace)
                cursor = int(event["sequence"])
                yield f"id: {cursor}\nevent: {event['kind']}\ndata: {json.dumps(public_event, sort_keys=True, separators=(',', ':'))}\n\n"
                emitted += 1
                if terminal or emitted >= limit:
                    return
    return StreamingResponse(bounded_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
@router.get("/v1/sessions/{session_id}/artifacts", operation_id="session.artifacts", response_model=PublicResult)
async def artifacts(session_id: str, request: Request):
    return await invoke_async("session.artifacts", lambda workspace: _read_artifacts_result(_service(request), session_id, workspace))
@router.get("/v1/sessions/{session_id}", operation_id="session.get", response_model=PublicResult)
async def get(session_id: str, request: Request):
    return await invoke_async("session.get", lambda workspace: _read_session_result(_service(request), session_id, "get", workspace))
