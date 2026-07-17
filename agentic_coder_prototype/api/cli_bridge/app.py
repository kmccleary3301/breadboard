"""FastAPI application exposing the CLI bridge surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.openapi.utils import get_openapi

from fastapi.routing import APIRoute

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

_REPO_ROOT = Path(__file__).resolve().parents[3]

if load_dotenv is not None:
    for _candidate in (_REPO_ROOT / ".env", _REPO_ROOT / ".env.local"):
        if _candidate.exists():
            load_dotenv(_candidate, override=False)

from .events import SessionEvent, PROTOCOL_VERSION
from .engine_identity_config import (
    ENGINE_IDENTITY_SCHEMA_VERSION,
    P30_SESSION_CONTRACT_ID,
    P30_SESSION_SCHEMA_SHA256,
    P30_SESSION_ROUTE_BINDINGS,
    p30_session_contract_schema,
    get_engine_process_identity,
)
from .models import (
    AttachmentUploadResponse,
    ErrorEnvelope,
    ErrorResponse,
    EngineArtifactRevision,
    EngineIdentityReadinessResponse,
    EngineLaunchIdentity,
    EngineLiveness,
    EngineProcessStart,
    EngineProtocolIdentity,
    EngineSessionContractIdentity,
    EngineSessionReadiness,
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
    SessionTurnCancelRequest,
    SessionTurnCancelResponse,
    SessionSummary,
)
from agentic_coder_prototype.api.e4 import create_e4_router
from agentic_coder_prototype.api.e4.models import E4ApiError
from .service import SessionService
from .registry import SessionRecord
from breadboard.rl.phase3.api_router import create_phase3_rl_router
from breadboard.rl.phase3.service_live import LiveRLRunService

logger = logging.getLogger(__name__)
ENGINE_STARTED_AT = time.time()
ENGINE_STARTED_AT_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ENGINE_STARTED_AT))
_OPENAI_AUTH_HEADERS_ENV = "BREADBOARD_OPENAI_AUTH_HEADERS_JSON"
_OPENAI_AUTH_BASE_URL_ENV = "BREADBOARD_OPENAI_AUTH_BASE_URL"


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = str(host).strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _project_provider_auth_material_to_env(
    provider_id: str,
    *,
    api_key: str | None,
    headers: dict[str, str] | None,
    base_url: str | None,
) -> None:
    if (provider_id or "").strip().lower() != "openai":
        return
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if headers:
        try:
            os.environ[_OPENAI_AUTH_HEADERS_ENV] = json.dumps(headers)
        except Exception:
            pass
    if base_url:
        os.environ[_OPENAI_AUTH_BASE_URL_ENV] = base_url


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
    return (os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"})


def _env_flag_default(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _drop_legacy_routes(app: FastAPI) -> None:
    # "/status" stays first-class: main's engine metadata contract (tests/test_cli_bridge_ready.py) and the TUI doctor consume it.
    legacy_exact = {"/models", "/features"}
    legacy_prefixes = ("/sessions", "/rl", "/atp", "/ext/evolake")

    def _route_path(route: Any) -> str:
        path = getattr(route, "path", None)
        if path is not None:
            return str(path)
        include_context = getattr(route, "include_context", None)
        prefix = getattr(include_context, "prefix", None)
        return str(prefix) if prefix is not None else ""

    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            _route_path(route) in legacy_exact
            or any(_route_path(route).startswith(prefix) for prefix in legacy_prefixes)
        )
    ]


def _run_git_command(args: list[str], cwd: Path, *, allow_empty: bool = False) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value if value or allow_empty else None


def _compute_engine_provenance(repo_root: Path) -> dict[str, Any]:
    revision: dict[str, Any] = {
        "repo_root": str(repo_root),
        "commit": None,
        "branch": None,
        "dirty": None,
    }
    if (repo_root / ".git").exists() or _run_git_command(["rev-parse", "--show-toplevel"], repo_root):
        commit = _run_git_command(["rev-parse", "HEAD"], repo_root)
        branch = _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
        status = _run_git_command(["status", "--porcelain"], repo_root, allow_empty=True)
        revision.update(
            {
                "commit": commit,
                "branch": branch,
                "dirty": bool(status) if status is not None else None,
            }
        )
    return revision


ENGINE_PROVENANCE = _compute_engine_provenance(_REPO_ROOT)


def _build_engine_identity(app: FastAPI) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "version": app.version,
        "engine_version": app.version,
        "started_at": ENGINE_STARTED_AT_ISO,
        "started_at_unix": ENGINE_STARTED_AT,
        "pid": os.getpid(),
        "served_revision": dict(ENGINE_PROVENANCE),
    }

def _p30_session_contract_descriptor(
    app: FastAPI,
    service: SessionService,
) -> dict[str, Any]:
    document = get_openapi(title=app.title, version=app.version, routes=app.routes)
    operations: list[dict[str, Any]] = []
    handler_bindings: list[dict[str, Any]] = []
    missing_routes: list[str] = []
    referenced_schemas: set[str] = set()

    def collect_refs(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                referenced_schemas.add(reference.rsplit("/", 1)[-1])
            for item in value.values():
                collect_refs(item)
        elif isinstance(value, list):
            for item in value:
                collect_refs(item)

    for method, path, expected_handler, service_method in P30_SESSION_ROUTE_BINDINGS:
        matches = [
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == path
            and method in route.methods
        ]
        operation = document.get("paths", {}).get(path, {}).get(method.lower())
        if len(matches) != 1 or not isinstance(operation, dict):
            missing_routes.append(f"{method} {path}")
            continue
        route = matches[0]
        http_operation = {
            "method": method,
            "path": path,
            "parameters": operation.get("parameters", []),
            "requestBody": operation.get("requestBody"),
            "responses": operation.get("responses", {}),
        }
        collect_refs(http_operation)
        operations.append(http_operation)
        bound_method = getattr(service, service_method, None)
        implementation = getattr(bound_method, "__func__", bound_method)
        expected_implementation = getattr(SessionService, service_method, None)
        handler_bindings.append(
            {
                "method": method,
                "path": path,
                "handler": getattr(route.endpoint, "__name__", None),
                "expected_handler": expected_handler,
                "service_method": service_method,
                "binding_exact": implementation is expected_implementation,
            }
        )

    prepared_stream = getattr(service, "prepared_event_stream", None)
    prepared_implementation = getattr(prepared_stream, "__func__", prepared_stream)
    handler_bindings.append(
        {
            "method": "GET",
            "path": "/v1/sessions/{session_id}/events",
            "handler": "prepared_event_stream",
            "expected_handler": "prepared_event_stream",
            "service_method": "prepared_event_stream",
            "binding_exact": (
                prepared_implementation is SessionService.prepared_event_stream
            ),
        }
    )
    handler_bindings.append(
        {
            "method": "GET",
            "path": "/v1/sessions/{session_id}/events",
            "handler": getattr(_encode_sse_event, "__name__", None),
            "expected_handler": "_encode_sse_event",
            "service_method": None,
            "serialization": "compact_session_event_asdict_v1",
            "binding_exact": _encode_sse_event is _P30_SSE_ENCODER,
        }
    )

    handler_bindings.append(
        {
            "method": "GET",
            "path": "/v1/sessions/{session_id}/events",
            "handler": "SessionEvent.asdict",
            "expected_handler": "SessionEvent.asdict",
            "service_method": None,
            "serialization": "session_event_envelope_v1",
            "binding_exact": SessionEvent.asdict is _P30_SESSION_EVENT_ASDICT,
        }
    )

    handler_bindings.append(
        {
            "method": "GET",
            "path": "/v1/sessions/{session_id}",
            "handler": "SessionRecord.to_summary",
            "expected_handler": "SessionRecord.to_summary",
            "service_method": None,
            "serialization": "retained_session_summary_v1",
            "binding_exact": SessionRecord.to_summary is _P30_SESSION_RECORD_TO_SUMMARY,
        }
    )

    schemas = document.get("components", {}).get("schemas", {})
    pending = list(referenced_schemas)
    while pending:
        schema_name = pending.pop()
        schema = schemas.get(schema_name)
        if not isinstance(schema, dict):
            continue
        before = set(referenced_schemas)
        collect_refs(schema)
        pending.extend(sorted(referenced_schemas - before))

    return p30_session_contract_schema(
        http_contract={
            "operations": operations,
            "schemas": {
                name: schemas[name]
                for name in sorted(referenced_schemas)
                if name in schemas
            },
            "missing_routes": missing_routes,
            "delivery_chaos_config": getattr(
                app.state,
                "p30_session_chaos_config",
                None,
            ),
        },
        handler_bindings=handler_bindings,
    )


def _configured_extension_enabled(config: Dict[str, Any] | None, ext_id: str) -> bool | None:
    if not isinstance(config, dict):
        return None
    ext_cfg = config.get("extensions")
    if not isinstance(ext_cfg, dict) or ext_id not in ext_cfg:
        return None
    entry = ext_cfg.get(ext_id)
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
        return bool(entry.get("enabled"))
    return None


def _error_code_for_status(status_code: int) -> str:
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "unauthorized"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_409_CONFLICT:
        return "conflict"
    if 400 <= status_code < 500:
        return "invalid_request"
    return "internal"


def _http_error_content(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail.get("error") or detail.get("code") or detail.get("error_code") or _error_code_for_status(exc.status_code)
        if "detail" in detail:
            envelope_detail = detail.get("detail")
        else:
            envelope_detail = {
                key: value
                for key, value in detail.items()
                if key not in {"error", "code", "error_code", "path"}
            }
            if not envelope_detail:
                envelope_detail = None
        path = detail.get("path") if isinstance(detail.get("path"), str) else None
        return ErrorEnvelope(error=str(error), detail=envelope_detail, path=path).model_dump()
    return ErrorEnvelope(error=_error_code_for_status(exc.status_code), detail=str(detail) if detail is not None else None, path=None).model_dump()


def _stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _encode_sse_event(event: SessionEvent) -> bytes:
    payload = json.dumps(event.asdict(), separators=(",", ":"))
    cursor_line = f"id: {event.seq}\n" if event.stable_cursor and event.seq is not None else ""
    return f"{cursor_line}data: {payload}\n\n".encode("utf-8")
_P30_SSE_ENCODER = _encode_sse_event
_P30_SESSION_EVENT_ASDICT = SessionEvent.asdict
_P30_SESSION_RECORD_TO_SUMMARY = SessionRecord.to_summary


def create_app(service: SessionService | None = None, include_atp_routes: bool | None = None) -> FastAPI:
    engine_version = (os.environ.get("BREADBOARD_ENGINE_VERSION") or "0.1.0").strip() or "0.1.0"
    app = FastAPI(title="BreadBoard CLI Bridge", version=engine_version)
    _service = service or SessionService()
    rl_service = LiveRLRunService(Path(os.environ.get("BREADBOARD_RL_RUN_STORE", ":memory:")))
    rl_router = create_phase3_rl_router(rl_service)
    app.include_router(rl_router, prefix="/v1/rl", tags=["rl"])
    app.include_router(rl_router, prefix="/rl", tags=["rl"])
    e4_repo_root = Path(__file__).resolve().parents[3]

    @app.exception_handler(E4ApiError)
    async def _e4_api_error_handler(_request: Request, exc: E4ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorEnvelope(error=exc.error, detail=exc.detail_text, path=exc.path).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_http_error_content(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorEnvelope(error="invalid_request", detail={"errors": exc.errors()}, path=None).model_dump(),
        )

    legacy_routes_enabled = _env_flag_default("BREADBOARD_LEGACY_ROUTES", default=False)
    e4_api_flag = os.environ.get("BREADBOARD_ENABLE_E4_API", "").strip().lower()
    if e4_api_flag not in {"0", "false", "no"}:
        app.include_router(
            create_e4_router(
                repo_root=e4_repo_root,
                inventory_path=e4_repo_root / "docs" / "conformance" / "e4_lane_inventory.json",
                catalog_path=e4_repo_root / "docs" / "conformance" / "e4_artifact_catalog.json",
                claims_dir=e4_repo_root / "docs" / "conformance" / "support_claims",
                schemas_dir=e4_repo_root / "contracts" / "kernel" / "schemas",
                ledger_path=Path(os.environ.get("BREADBOARD_E4_LEDGER_PATH", e4_repo_root.parent / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json")),
                coverage_dir=Path(os.environ.get("BREADBOARD_E4_COVERAGE_DIR", e4_repo_root.parent / "docs_tmp" / "phase_16" / "coverage")),
                runtime_records_dir=Path(os.environ.get("BREADBOARD_RUNTIME_RECORD_ROOT", e4_repo_root / "artifacts" / "runtime_records")),
            ),
            prefix="/v1/e4",
            tags=["e4"],
        )
    chaos_config = _load_chaos_config()
    app.state.p30_session_chaos_config = chaos_config
    required_token = (os.environ.get("BREADBOARD_API_TOKEN") or "").strip()
    extension_config = None
    mounted_extensions: list[str] = []
    try:
        from .extension_loader import load_extension_config_from_env

        extension_config = load_extension_config_from_env()
    except FileNotFoundError:
        extension_config = None
    except Exception as exc:
        logger.warning("Failed to load extension config: %s", exc)
        extension_config = None

    env_atp_enabled = _env_flag("ATP_REPL_ENABLE") or _env_flag("ATP_REPL_ROUTE")
    cfg_atp_enabled = _configured_extension_enabled(extension_config, "atp")
    cfg_evolake_enabled = _configured_extension_enabled(extension_config, "evolake")

    if include_atp_routes is True:
        atp_routes_enabled = True
    elif include_atp_routes is False:
        atp_routes_enabled = False
    elif cfg_atp_enabled is None:
        atp_routes_enabled = env_atp_enabled
    else:
        atp_routes_enabled = bool(cfg_atp_enabled)

    evolake_routes_enabled = bool(cfg_evolake_enabled)
    _service._atp_repl_enabled = bool(atp_routes_enabled)

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not required_token:
            return await call_next(request)
        header = request.headers.get("authorization") or ""
        token = ""
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
        if not token or token != required_token:
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=ErrorEnvelope(error="unauthorized", detail="unauthorized", path=None).model_dump())
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
            yield _encode_sse_event(event)

    def _registry_payloads() -> list[tuple[Path, dict[str, Any]]]:
        registries_dir = e4_repo_root / "contracts" / "kernel" / "registries"
        payloads: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(registries_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("registry_id"), str):
                payloads.append((path, payload))
        return payloads

    @app.get("/v1/registries")
    async def list_registries() -> dict[str, Any]:
        registries = [
            {
                "registry_id": str(payload["registry_id"]),
                "schema_version": payload.get("schema_version"),
                "path": path.relative_to(e4_repo_root).as_posix(),
                "entries": len(payload.get("entries")) if isinstance(payload.get("entries"), list) else 0,
            }
            for path, payload in _registry_payloads()
        ]
        return {"registries": registries, "total": len(registries)}

    @app.get("/v1/registries/{registry_id}")
    async def get_registry(registry_id: str) -> dict[str, Any]:
        for path, payload in _registry_payloads():
            if registry_id in {str(payload.get("registry_id")), path.name, path.stem}:
                return payload
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "registry_not_found", "detail": registry_id, "path": "contracts/kernel/registries"},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            **_build_engine_identity(app),
        }

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        try:
            from ...provider import runtime_codex as _runtime_codex_module  # noqa: F401
        except Exception:
            pass
        from ...provider.runtime import provider_registry

        try:
            runtime_classes = getattr(provider_registry, "_runtime_classes", {})
            runtime_ids = sorted(runtime_classes.keys()) if isinstance(runtime_classes, dict) else []
        except Exception:
            runtime_ids = []
        codex_ready = provider_registry.get_runtime_class("codex_app_server") is not None
        return {
            "status": "ok",
            "ready": codex_ready,
            **_build_engine_identity(app),
            "provider_runtimes": runtime_ids,
        }

    @app.get("/v1/status")
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
            "uptime_s": max(0.0, time.time() - ENGINE_STARTED_AT),
            **_build_engine_identity(app),
            "ray": {
                "available": ray_available,
                "initialized": ray_initialized,
            },
        }

    @app.get("/v1/features")
    @app.get("/features")
    async def feature_audit() -> dict[str, Any]:
        atp_status = _service.atp_feature_status(enabled=atp_routes_enabled)
        return {
            "status": "ok",
            "extensions": {
                "atp": {
                    "enabled": bool(atp_routes_enabled),
                    "mounted": bool("atp" in mounted_extensions),
                },
                "evolake": {
                    "enabled": bool(evolake_routes_enabled),
                    "mounted": bool("evolake" in mounted_extensions),
                },
            },
            "atp": atp_status,
            "metadata": {
                "mounted_extensions": list(mounted_extensions),
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
        _project_provider_auth_material_to_env(
            material.provider_id,
            api_key=material.api_key,
            headers=material.headers or {},
            base_url=material.base_url,
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
        "/v1/models",
        response_model=ModelCatalogResponse,
        responses={400: {"model": ErrorResponse}},
    )
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
        "/v1/sessions",
        response_model=SessionCreateResponse,
        responses={400: {"model": ErrorResponse}},
    )
    @app.post(
        "/sessions",
        response_model=SessionCreateResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def create_session(payload: SessionCreateRequest, svc: SessionService = Depends(get_service)):
        return await svc.create_session(payload)

    @app.get(
        "/v1/sessions",
        response_model=list[SessionSummary],
    )
    @app.get(
        "/sessions",
        response_model=list[SessionSummary],
    )
    async def list_sessions(svc: SessionService = Depends(get_service)):
        summaries = await svc.list_sessions()
        return list(summaries)

    @app.get(
        "/v1/sessions/{session_id}",
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}",
        response_model=SessionSummary,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.session_snapshot(session_id)

    @app.get(
        "/v1/sessions/{session_id}/records",
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session_records(
        session_id: str,
        schema_version: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_session_records(
            session_id,
            schema_version=schema_version,
            offset=offset,
            limit=limit,
        )

    @app.post(
        "/v1/sessions/{session_id}/input",
        response_model=SessionInputResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
    )
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
        "/v1/sessions/{session_id}/turns/{turn_id}/cancel",
        response_model=SessionTurnCancelResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
    )
    async def cancel_turn(
        session_id: str,
        turn_id: str,
        payload: SessionTurnCancelRequest,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.cancel_turn(session_id, turn_id, payload)

    @app.post(
        "/v1/sessions/{session_id}/command",
        response_model=SessionCommandResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
            501: {"model": ErrorResponse},
        },
    )
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
        "/v1/sessions/{session_id}/attachments",
        response_model=AttachmentUploadResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
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
        "/v1/sessions/{session_id}/files",
        response_model=list[SessionFileInfo],
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def list_session_files(
        session_id: str,
        path: str | None = None,
        svc: SessionService = Depends(get_service),
    ):
        return await svc.list_files(session_id, root=path or ".")

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
        "/v1/sessions/{session_id}/files/content",
        response_model=SessionFileContent,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    async def read_session_file(
        session_id: str,
        path: str,
        mode: str = "cat",
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
        svc: SessionService = Depends(get_service),
    ):
        if mode == "snippet":
            head_lines = 200 if head_lines is None else head_lines
            tail_lines = 80 if tail_lines is None else tail_lines
            max_bytes = 80_000 if max_bytes is None else max_bytes
        return await svc.read_file(
            session_id,
            path,
            mode=mode,
            head_lines=head_lines,
            tail_lines=tail_lines,
            max_bytes=max_bytes,
        )

    @app.get(
        "/v1/sessions/{session_id}/skills",
        response_model=SkillCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}/skills",
        response_model=SkillCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_skills(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.list_skills(session_id)

    @app.get(
        "/v1/sessions/{session_id}/ctrees",
        response_model=CTreeSnapshotResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @app.get(
        "/sessions/{session_id}/ctrees",
        response_model=CTreeSnapshotResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def session_ctrees(session_id: str, svc: SessionService = Depends(get_service)):
        return await svc.get_ctree_snapshot(session_id)

    @app.delete(
        "/v1/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
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
        "/v1/sessions/{session_id}/events",
        responses={404: {"model": ErrorResponse}},
    )
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
            prepared = await svc.prepare_event_stream(
                session_id,
                replay=replay,
                limit=limit,
                from_id=from_id,
                include_open_ack=True,
            )
            generator = svc.prepared_event_stream(prepared)
        except HTTPException as exc:
            raise exc

        return StreamingResponse(
            event_payloads(generator),
            media_type="text/event-stream",
        )

    @app.get(
        "/v1/sessions/{session_id}/download",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
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

    @app.get(
        "/v1/engine/identity",
        response_model=EngineIdentityReadinessResponse,
    )
    async def engine_identity_readiness(
        svc: SessionService = Depends(get_service),
    ) -> EngineIdentityReadinessResponse:
        contract_readiness = svc.p30_session_contract_readiness(
            _p30_session_contract_descriptor(app, svc)
        )
        process_identity = get_engine_process_identity()
        served_backend_commit = ENGINE_PROVENANCE.get("commit")
        if not isinstance(served_backend_commit, str) or len(served_backend_commit) != 40:
            served_backend_commit = None
        served_backend_dirty = ENGINE_PROVENANCE.get("dirty")
        if not isinstance(served_backend_dirty, bool):
            served_backend_dirty = None
        return EngineIdentityReadinessResponse(
            schema_version=ENGINE_IDENTITY_SCHEMA_VERSION,
            liveness=EngineLiveness(),
            process=EngineProcessStart(
                engine_instance_id=process_identity.engine_instance_id,
                engine_boot_id=process_identity.engine_boot_id,
                started_at=process_identity.started_at,
                started_at_unix=process_identity.started_at_unix,
                pid=process_identity.pid,
            ),
            launch=EngineLaunchIdentity(
                launch_id=process_identity.launch_id,
                source=process_identity.launch_source,
            ),
            artifact_revision=EngineArtifactRevision(
                engine_artifact_sha256=process_identity.engine_artifact_sha256,
                served_backend_commit=served_backend_commit,
                served_backend_dirty=served_backend_dirty,
            ),
            protocol=EngineProtocolIdentity(protocol_version=PROTOCOL_VERSION),
            session_contract=EngineSessionContractIdentity(
                contract_id=P30_SESSION_CONTRACT_ID,
                schema_sha256=P30_SESSION_SCHEMA_SHA256,
                compatibility=(
                    "compatible"
                    if contract_readiness.ready
                    else "incompatible"
                ),
            ),
            session_readiness=EngineSessionReadiness(
                ready=contract_readiness.ready,
                reason=contract_readiness.reason,
            ),
        )

    if atp_routes_enabled:
        from .atp_router import build_atp_router

        app.include_router(build_atp_router(get_service))
        mounted_extensions.append("atp")

    if evolake_routes_enabled:
        from breadboard.ext.interfaces import EndpointProvider
        from breadboard_ext.evolake import EvoLakeBridgeExtension

        extension = EvoLakeBridgeExtension()
        for provider in extension.providers():
            if isinstance(provider, EndpointProvider):
                provider.register_routes(app, get_service)
        mounted_extensions.append("evolake")

    if not legacy_routes_enabled:
        _drop_legacy_routes(app)

    return app


# Default app for uvicorn module-level discovery.
app = create_app()
