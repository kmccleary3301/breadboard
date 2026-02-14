"""FastAPI application exposing the CLI bridge surface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, AsyncIterator, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from .events import SessionEvent, PROTOCOL_VERSION
from .models import (
    AttachmentUploadResponse,
    ErrorResponse,
    ModelCatalogResponse,
    ProviderAuthAttachRequest,
    ProviderAuthAttachResponse,
    ProviderAuthDetachRequest,
    ProviderAuthDetachResponse,
    ProviderAuthStatusResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SkillCatalogResponse,
    CTreeSnapshotResponse,
    SessionFileContent,
    SessionFileInfo,
    SessionInputRequest,
    SessionInputResponse,
    SessionSummary,
)
from .service import SessionService

logger = logging.getLogger(__name__)
ENGINE_STARTED_AT = time.time()


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = str(host).strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


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


def create_app(service: SessionService | None = None) -> FastAPI:
    engine_version = (os.environ.get("BREADBOARD_ENGINE_VERSION") or "0.1.0").strip() or "0.1.0"
    app = FastAPI(title="BreadBoard CLI Bridge", version=engine_version)
    _service = service or SessionService()
    chaos_config = _load_chaos_config()
    required_token = (os.environ.get("BREADBOARD_API_TOKEN") or "").strip()

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

    @app.on_event("startup")
    async def _ensure_ray_initialized() -> None:
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

                # Important: initialize Ray in the main thread. Session execution happens in worker
                # threads, and Ray can degrade or refuse to install signal handlers if initialized
                # off the main thread.
                start = time.monotonic()
                _init_ray_sync()
                elapsed = time.monotonic() - start
                if timeout_s > 0 and elapsed > timeout_s:
                    logger.warning("Ray init exceeded configured timeout (%.1fs > %.1fs)", elapsed, timeout_s)
                logger.info("Ray initialized during engine startup")
        except BaseException as exc:  # noqa: BLE001
            # Do not crash the engine; sessions may fall back to local execution if Ray is unavailable.
            if strict_required:
                raise
            logger.warning("Ray init failed during engine startup: %s", exc)

    def get_service() -> SessionService:
        return _service

    async def event_payloads(events: AsyncIterator[SessionEvent]) -> AsyncIterator[bytes]:
        async for event in events:
            if chaos_config:
                drop_rate = chaos_config.get("dropRate", 0.0)
                if drop_rate and random.random() < drop_rate:
                    continue
                latency_ms = chaos_config.get("latencyMs", 0.0)
                jitter_ms = chaos_config.get("jitterMs", 0.0)
                extra_delay = latency_ms + (random.random() * jitter_ms)
                if extra_delay > 0:
                    await asyncio.sleep(extra_delay / 1000.0)
            payload = json.dumps(event.asdict(), separators=(",", ":"))
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
        }

    @app.post(
        "/v1/provider-auth/attach",
        response_model=ProviderAuthAttachResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def attach_provider_auth(payload: ProviderAuthAttachRequest, request: Request):
        """Attach short-lived provider auth material to the in-memory engine store."""

        from ...auth.enforcer import apply_dotted_overrides, check_conformance
        from ...auth.material import EngineAuthMaterial, EmulationProfileRequirement
        from ...auth.store import DEFAULT_PROVIDER_AUTH_STORE
        from ...compilation.v2_loader import load_agent_config

        client_host = getattr(getattr(request, "client", None), "host", None)
        if payload.material.is_subscription_plan and not _is_loopback_host(client_host):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "subscription-plan auth is local-only by default"},
            )

        required_profile = None
        if payload.required_profile is not None:
            locked = list(payload.required_profile.locked_json_pointers or [])
            if not locked:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "required_profile.locked_json_pointers must be provided"},
                )
            if not payload.config_path:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "config_path is required when required_profile is provided"},
                )
            cfg = load_agent_config(payload.config_path)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg = apply_dotted_overrides(cfg, payload.overrides)
            expected = payload.required_profile.conformance_hash
            result = check_conformance(config=cfg, locked_json_pointers=locked, expected_hash=expected)
            if not result.ok:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "sealed profile conformance mismatch",
                        "expected_hash": result.expected_hash,
                        "actual_hash": result.actual_hash,
                        "details": result.details,
                    },
                )
            required_profile = EmulationProfileRequirement(
                profile_id=payload.required_profile.profile_id,
                conformance_hash=payload.required_profile.conformance_hash,
                locked_json_pointers=tuple(locked),
            )

        api_key = payload.material.api_key
        if not api_key:
            # Allow callers to provide the bearer token in headers (common in plan adapters).
            auth = (payload.material.headers or {}).get("Authorization") or (payload.material.headers or {}).get("authorization")
            if isinstance(auth, str) and auth.strip():
                value = auth.strip()
                if value.lower().startswith("bearer "):
                    api_key = value[7:].strip()
                else:
                    api_key = value

        material = EngineAuthMaterial(
            provider_id=payload.material.provider_id,
            alias=(payload.material.alias or "").strip(),
            api_key=api_key,
            headers=dict(payload.material.headers or {}),
            base_url=payload.material.base_url,
            routing=dict(payload.material.routing or {}) if isinstance(payload.material.routing, dict) else None,
            issued_at_ms=payload.material.issued_at_ms,
            expires_at_ms=payload.material.expires_at_ms,
            is_subscription_plan=bool(payload.material.is_subscription_plan),
            required_profile=required_profile,
        )

        DEFAULT_PROVIDER_AUTH_STORE.attach(
            material,
            ttl_seconds=payload.material.ttl_seconds,
            required_profile=required_profile,
        )
        return ProviderAuthAttachResponse(ok=True, detail={"attached": True})

    @app.post(
        "/v1/provider-auth/detach",
        response_model=ProviderAuthDetachResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def detach_provider_auth(payload: ProviderAuthDetachRequest):
        from ...auth.store import DEFAULT_PROVIDER_AUTH_STORE

        ok = DEFAULT_PROVIDER_AUTH_STORE.detach(payload.provider_id, alias=(payload.alias or "").strip())
        return ProviderAuthDetachResponse(ok=ok)

    @app.get(
        "/v1/provider-auth/status",
        response_model=ProviderAuthStatusResponse,
    )
    async def provider_auth_status():
        from ...auth.store import DEFAULT_PROVIDER_AUTH_STORE

        items = DEFAULT_PROVIDER_AUTH_STORE.status()
        return ProviderAuthStatusResponse(attached=items)

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
            # Preserve explicit "0" values (e.g. head_lines=0 means "no head"),
            # while still applying sane defaults for snippet mode.
            if mode == "snippet":
                resolved_head_lines = 200 if head_lines is None else head_lines
                resolved_tail_lines = 80 if tail_lines is None else tail_lines
                resolved_max_bytes = 80_000 if max_bytes is None else max_bytes
            else:
                resolved_head_lines = head_lines
                resolved_tail_lines = tail_lines
                resolved_max_bytes = max_bytes
            return await svc.read_file(
                session_id,
                path or ".",
                mode=mode,
                head_lines=resolved_head_lines,
                tail_lines=resolved_tail_lines,
                max_bytes=resolved_max_bytes,
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
        svc: SessionService = Depends(get_service),
    ):
        try:
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

        return StreamingResponse(
            event_payloads(generator),
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
