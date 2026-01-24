"""FastAPI application exposing the CLI bridge surface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

def _maybe_load_dotenv() -> None:
    if (os.environ.get("BREADBOARD_LOAD_DOTENV") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return
    try:
        load_dotenv()
    except Exception:
        return

from .events import SessionEvent, PROTOCOL_VERSION, is_legacy_event_type
from .persistence import build_session_index, list_event_log_sessions, eventlog_last_activity_ms, eventlog_capped
from .models import (
    AttachmentUploadResponse,
    ErrorResponse,
    ModelCatalogResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SkillCatalogResponse,
    CTreeDiskArtifactsResponse,
    CTreeEventsResponse,
    CTreeSnapshotResponse,
    CTreeTreeResponse,
    SessionFileContent,
    SessionFileInfo,
    SessionInputRequest,
    SessionInputResponse,
    SessionSummary,
)
from .service import SessionService

logger = logging.getLogger(__name__)
ENGINE_STARTED_AT = time.time()


def _load_chaos_config() -> Dict[str, float] | None:
    latency = max(0, int(os.environ.get("BREADBOARD_CLI_LATENCY_MS", "0")))
    jitter = max(0, int(os.environ.get("BREADBOARD_CLI_JITTER_MS", "0")))
    try:
        drop = float(os.environ.get("BREADBOARD_CLI_DROP_RATE", "0"))
    except ValueError:
        drop = 0.0
    drop = max(0.0, min(1.0, drop))
    if latency == 0 and jitter == 0 and drop == 0:
        return None
    return {
        "latencyMs": latency,
        "jitterMs": jitter,
        "dropRate": drop,
    }


def _env_flag(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _iso_from_ms(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
    except Exception:
        return None


def create_app(service: SessionService | None = None) -> FastAPI:
    _maybe_load_dotenv()
    engine_version = (os.environ.get("BREADBOARD_ENGINE_VERSION") or "0.1.0").strip() or "0.1.0"
    _service = service or SessionService()
    chaos_config = _load_chaos_config()
    required_token = (os.environ.get("BREADBOARD_API_TOKEN") or "").strip()

    async def _ensure_ray_initialized(app: FastAPI) -> None:
        if os.environ.get("RAY_SCE_LOCAL_MODE", "0") == "1":
            return
        strict_required = os.environ.get("BREADBOARD_RAY_INIT_REQUIRED", "").lower() in {"1", "true", "yes"}
        try:
            import ray  # type: ignore
        except Exception:  # pragma: no cover - optional runtime
            if strict_required:
                raise RuntimeError("Ray is required but not importable during engine startup.")
            return
        try:
            if not ray.is_initialized():
                timeout_s = float(os.environ.get("BREADBOARD_RAY_INIT_TIMEOUT_S", "8") or "8")

                def _init_ray_sync() -> None:
                    os.environ.setdefault("RAY_DISABLE_DASHBOARD", "1")
                    ray.init(address="local", include_dashboard=False)

                start = time.monotonic()
                _init_ray_sync()
                elapsed = time.monotonic() - start
                if timeout_s > 0 and elapsed > timeout_s:
                    logger.warning("Ray init exceeded configured timeout (%.1fs > %.1fs)", elapsed, timeout_s)
                logger.info("Ray initialized during engine startup")
                app.state.ray_initialized_by_bridge = True
        except BaseException as exc:  # noqa: BLE001
            if strict_required:
                raise
            logger.warning("Ray init failed during engine startup: %s", exc)

    async def _maybe_shutdown_ray(app: FastAPI) -> None:
        if not getattr(app.state, "ray_initialized_by_bridge", False):
            return
        try:
            import ray  # type: ignore
        except Exception:
            return
        try:
            if ray.is_initialized():
                ray.shutdown()
        except Exception:
            pass

    async def _bootstrap_event_logs() -> None:
        try:
            index_count = await _service.bootstrap_index()
            if index_count:
                logger.info("Bootstrapped %d sessions from session index", index_count)
            count = await _service.bootstrap_event_logs()
            if count:
                logger.info("Bootstrapped %d sessions from event logs", count)
        except Exception as exc:
            logger.warning("Event log bootstrap failed: %s", exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ray_initialized_by_bridge = False
        await _ensure_ray_initialized(app)
        await _bootstrap_event_logs()
        yield
        await _maybe_shutdown_ray(app)

    app = FastAPI(title="BreadBoard CLI Bridge", version=engine_version, lifespan=lifespan)

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not required_token:
            return await call_next(request)
        header = request.headers.get("authorization") or ""
        token = ""
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
        if not token or token != required_token:
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"message": "unauthorized"})
        return await call_next(request)

    def get_service() -> SessionService:
        return _service

    async def event_payloads(
        events: AsyncIterator[SessionEvent],
        *,
        schema_version: int = 1,
        include_legacy: bool = True,
    ) -> AsyncIterator[bytes]:
        keepalive_raw = (os.environ.get("BREADBOARD_SSE_KEEPALIVE_S") or "").strip()
        try:
            keepalive_s = float(keepalive_raw) if keepalive_raw else 15.0
        except Exception:
            keepalive_s = 15.0
        keepalive_s = max(0.0, keepalive_s)
        iterator = events.__aiter__()
        while True:
            try:
                if keepalive_s > 0:
                    event = await asyncio.wait_for(iterator.__anext__(), timeout=keepalive_s)
                else:
                    event = await iterator.__anext__()
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            except StopAsyncIteration:
                break
            if schema_version == 1 and not is_legacy_event_type(event.type):
                continue
            if schema_version != 1 and (not include_legacy) and is_legacy_event_type(event.type):
                continue
            if chaos_config:
                drop_rate = chaos_config.get("dropRate", 0.0)
                if drop_rate and random.random() < drop_rate:
                    continue
                latency_ms = chaos_config.get("latencyMs", 0.0)
                jitter_ms = chaos_config.get("jitterMs", 0.0)
                extra_delay = latency_ms + (random.random() * jitter_ms)
                if extra_delay > 0:
                    await asyncio.sleep(extra_delay / 1000.0)
            if schema_version == 1:
                payload_dict = event.asdict_legacy()
            else:
                payload_dict = event.asdict()
                if include_legacy and is_legacy_event_type(event.type):
                    if not payload_dict.get("visibility"):
                        payload_dict["visibility"] = "debug"
                    tags = payload_dict.get("tags") or []
                    if isinstance(tags, list) and "legacy" not in tags:
                        tags.append("legacy")
                    payload_dict["tags"] = tags
            payload = json.dumps(payload_dict, separators=(",", ":"))
            event_id = event.seq if event.seq is not None else event.event_id
            yield f"id: {event_id}\n".encode("utf-8")
            yield f"data: {payload}\n\n".encode("utf-8")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "version": app.version,
            "engine_version": app.version,
        }

    @app.get("/status")
    async def engine_status() -> dict[str, Any]:
        ray_available = False
        ray_initialized = False
        try:
            import ray  # type: ignore

            ray_available = True
            ray_initialized = bool(ray.is_initialized())
        except Exception:
            ray_available = False
            ray_initialized = False
        eventlog_dir = (os.environ.get("BREADBOARD_EVENTLOG_DIR") or "").strip()
        session_index_dir = (os.environ.get("BREADBOARD_SESSION_INDEX_DIR") or eventlog_dir).strip()
        session_index_engine = (os.environ.get("BREADBOARD_SESSION_INDEX_ENGINE") or "json").strip()
        eventlog_sessions = None
        eventlog_last_activity = None
        if eventlog_dir:
            try:
                eventlog_sessions = len(list_event_log_sessions(eventlog_dir))
                eventlog_last_activity = _iso_from_ms(eventlog_last_activity_ms(eventlog_dir))
            except Exception:
                eventlog_sessions = None
                eventlog_last_activity = None
        session_index_count = None
        session_index_last_activity = None
        if _env_flag("BREADBOARD_SESSION_INDEX") and session_index_dir:
            try:
                index = build_session_index(session_index_dir, engine=session_index_engine)
                if index is not None:
                    summaries = await index.list_summaries()
                    session_index_count = len(summaries)
                    if summaries:
                        latest = max((s.last_activity_at for s in summaries), default=None)
                        if latest is not None:
                            session_index_last_activity = latest.isoformat()
            except Exception:
                session_index_count = None
                session_index_last_activity = None
        return {
            "status": "ok",
            "pid": os.getpid(),
            "uptime_s": max(0.0, time.time() - ENGINE_STARTED_AT),
            "protocol_version": PROTOCOL_VERSION,
            "version": app.version,
            "engine_version": app.version,
            "ray": {
                "available": ray_available,
                "initialized": ray_initialized,
            },
            "eventlog": {
                "enabled": bool(eventlog_dir),
                "dir": eventlog_dir or None,
                "bootstrap": _env_flag("BREADBOARD_EVENTLOG_BOOTSTRAP"),
                "replay": _env_flag("BREADBOARD_EVENTLOG_REPLAY"),
                "max_mb": os.environ.get("BREADBOARD_EVENTLOG_MAX_MB"),
                "sessions": eventlog_sessions,
                "last_activity": eventlog_last_activity,
                "capped": eventlog_capped(),
            },
            "session_index": {
                "enabled": _env_flag("BREADBOARD_SESSION_INDEX"),
                "dir": session_index_dir or None,
                "engine": session_index_engine or None,
                "sessions": session_index_count,
                "last_activity": session_index_last_activity,
            },
        }

    @app.get(
        "/models",
        response_model=ModelCatalogResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def list_models(
        config_path: str,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_models(config_path)

    @app.post(
        "/sessions",
        response_model=SessionCreateResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def create_session(payload: SessionCreateRequest, svc: SessionService = Depends(get_service)):
        return await svc.create_session(payload)

    @app.get(
        "/sessions",
        response_model=list[SessionSummary],
    )
    async def list_sessions(svc: SessionService = Depends(get_service)):
        summaries = await svc.list_sessions()
        return list(summaries)

    @app.get(
        "/sessions/{session_id}",
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session(session_id: str, svc: SessionService = Depends(get_service)):
        record = await svc.ensure_session(session_id)
        return record.to_summary()

    @app.post(
        "/sessions/{session_id}/input",
        response_model=SessionInputResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
    )
    async def post_input(session_id: str, payload: SessionInputRequest, svc: SessionService = Depends(get_service)):
        return await svc.send_input(session_id, payload)

    @app.post(
        "/sessions/{session_id}/command",
        response_model=SessionCommandResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
        },
    )
    async def post_command(session_id: str, payload: SessionCommandRequest, svc: SessionService = Depends(get_service)):
        return await svc.execute_command(session_id, payload)

    @app.post(
        "/sessions/{session_id}/attachments",
        response_model=AttachmentUploadResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def upload_attachments(
        session_id: str,
        metadata: str | None = Form(default=None),
        files: list[UploadFile] = File(...),
        svc: SessionService = Depends(get_service),
    ):
        metadata_payload = None
        if metadata:
            try:
                metadata_payload = json.loads(metadata)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"metadata must be valid JSON: {exc}",
                ) from exc
        return await svc.upload_attachments(session_id, files, metadata_payload)

    @app.get(
        "/sessions/{session_id}/files",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def session_files(
        session_id: str,
        path: str | None = None,
        mode: str | None = None,
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
        svc: SessionService = Depends(get_service),
    ):
        if mode:
            return await svc.read_file(
                session_id,
                path or ".",
                mode=mode,
                head_lines=head_lines or 200,
                tail_lines=tail_lines or 80,
                max_bytes=max_bytes or 80_000,
            )
        return await svc.list_files(session_id, root=path or ".")

    @app.get(
        "/sessions/{session_id}/skills",
        response_model=SkillCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_skills(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.list_skills(session_id)

    @app.get(
        "/sessions/{session_id}/ctrees",
        response_model=CTreeSnapshotResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.get_ctree_snapshot(session_id)

    @app.get(
        "/sessions/{session_id}/ctrees/disk",
        response_model=CTreeDiskArtifactsResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees_disk(
        session_id: str,
        with_sha256: bool = False,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.get_ctree_disk_artifacts(session_id, with_sha256=with_sha256)

    @app.get(
        "/sessions/{session_id}/ctrees/events",
        response_model=CTreeEventsResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees_events(
        session_id: str,
        source: str = "auto",
        offset: int = 0,
        limit: int | None = None,
        with_sha256: bool = False,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.get_ctree_events(
            session_id,
            source=source,
            offset=offset,
            limit=limit,
            with_sha256=with_sha256,
        )

    @app.get(
        "/sessions/{session_id}/ctrees/tree",
        response_model=CTreeTreeResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees_tree(
        session_id: str,
        source: str = "auto",
        stage: str = "FROZEN",
        include_previews: bool = False,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.get_ctree_tree_view(
            session_id,
            source=source,
            stage=stage,
            include_previews=include_previews,
        )

    @app.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
    )
    async def delete_session(session_id: str, svc: SessionService = Depends(get_service)):
        await svc.stop_session(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/sessions/{session_id}/events",
        responses={404: {"model": ErrorResponse}},
    )
    async def stream_events(
        session_id: str,
        request: Request,
        replay: bool = False,
        limit: int | None = None,
        from_id: str | None = None,
        from_seq: int | None = None,
        schema: int | None = None,
        v: int | None = None,
        include_legacy: bool = True,
        svc: SessionService = Depends(get_service),
    ):
        try:
            if from_seq is not None and not from_id:
                from_id = str(from_seq)
            if not from_id:
                from_id = request.headers.get("last-event-id") or request.headers.get("Last-Event-ID")
            if from_id:
                await svc.validate_event_stream(session_id, from_id=from_id, replay=replay)
            generator = svc.event_stream(
                session_id,
                replay=replay,
                limit=limit,
                from_id=from_id,
                validated=True,
            )
        except HTTPException as exc:
            raise exc
        schema_version = schema or v or 1
        try:
            schema_version = int(schema_version)
        except Exception:
            schema_version = 1
        if schema_version not in {1, 2}:
            schema_version = 1
        return StreamingResponse(
            event_payloads(generator, schema_version=schema_version, include_legacy=include_legacy),
            media_type="text/event-stream",
        )

    @app.get(
        "/sessions/{session_id}/download",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    async def download_artifact(session_id: str, artifact: str, svc: SessionService = Depends(get_service)):
        path = await svc.resolve_artifact_path(session_id, artifact)
        return FileResponse(path)

    return app


# Default app for uvicorn module-level discovery.
app = create_app()
