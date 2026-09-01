"""FastAPI application exposing the CLI bridge surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
import stat
import subprocess
import time
from copy import copy
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from starlette._utils import get_route_path
from ...security import build_child_environment, sanitized_process_environment

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENGINE_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
ENGINE_BUILD_PROVENANCE_FILENAME = "engine-build-provenance.v1.json"
ENGINE_BUILD_PROVENANCE_SCHEMA = "bb.engine_build_provenance.v1"
_MAX_ENGINE_BUILD_PROVENANCE_BYTES = 16 * 1024
if load_dotenv is not None:
    for _candidate in (_REPO_ROOT / ".env", _REPO_ROOT / ".env.local"):
        if _candidate.exists():
            load_dotenv(_candidate, override=False)

from .events import SessionEvent, PROTOCOL_VERSION, replay_configuration_digest
from .engine_identity_config import (
    ENGINE_IDENTITY_SCHEMA_VERSION,
    EngineIdentityConfigError,
    P30_SESSION_CONTRACT_ID,
    P30_SESSION_SCHEMA_SHA256,
    P30_SESSION_ROUTE_BINDINGS,
    P30_SESSION_BASELINE_HTTP,
    P30_SESSION_EVENT_STREAM_CONTRACT,
    get_engine_process_identity,
    engine_source_artifact_sha256,
    p30_session_contract_schema,
)
from .models import (
    AttachmentUploadResponse,
    ErrorEnvelope,
    ErrorResponse,
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    BootstrapChallengeResponse,
    ClientLeaseRequest,
    ClientRegisterRequest,
    ClientRegistrationResponse,
    DrainControlRequest,
    DrainControlResponse,
    EngineArtifactRevision,
    EngineIdentityReadinessResponse,
    EngineLaunchIdentity,
    EngineLiveness,
    EngineProcessStart,
    EngineProtocolIdentity,
    EngineSessionContractIdentity,
    EngineSessionReadiness,
    GracefulControlResultRequest,
    HardSignalCommitRequest,
    HardSignalPreparationResponse,
    HardSignalPermitResponse,
    HardSignalOutcomeRequest,
    HardSignalPrepareRequest,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
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
    SessionTurnCancelRequest,
    SessionTurnCancelResponse,
)
from .service import SessionService
from .runtime_emission import prepare_managed_state
from .registry import LifecycleAuthorityError, SessionRecord
from .auth_routes import (
    _require_local_control_request,
    router as auth_router,
)
from .routes.engine_routes import register_engine_routes
from .routes.provider_auth_routes import register_provider_auth_routes
from .routes.sessions_routes import register_session_routes
from .routes.system_routes import register_system_routes
from breadboard.rl.phase3.api_router import create_phase3_rl_router
from breadboard.rl.phase3.service_live import LiveRLRunService
from breadboard_engine.api.public import mount_public_routes
from breadboard_engine.api.public.models import (
    PUBLIC_CAPABILITIES,
    PublicPrincipal,
    is_public_operation_request,
    problem_response,
    public_principal_scope,
)

logger = logging.getLogger(__name__)
ENGINE_STARTED_AT = time.time()
ENGINE_STARTED_AT_ISO = time.strftime(
    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ENGINE_STARTED_AT)
)


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = str(host).strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _is_public_runtime_setup_request(method: str, path: str) -> bool:
    parts = path.split("/")
    return (
        method.upper() == "POST"
        and len(parts) == 5
        and parts[0] == ""
        and parts[1:3] == ["v1", "sessions"]
        and bool(parts[3])
        and parts[4] in {"pause", "attachments"}
    )


def _public_request_principal(
    request: Request,
    required_token: str,
) -> PublicPrincipal:
    if required_token:
        return PublicPrincipal("api-bearer", PUBLIC_CAPABILITIES)
    client_host = request.client.host if request.client is not None else ""
    if client_host == "testclient" or (
        _is_loopback_host(request.url.hostname) and _is_loopback_host(client_host)
    ):
        return PublicPrincipal("local", PUBLIC_CAPABILITIES)
    return PublicPrincipal("anonymous")


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
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def _drop_legacy_routes(app: FastAPI, *, drop_versioned: bool = False) -> None:
    # Operational probes remain routable for existing launchers, but are not product-contract operations.
    hidden_operational = {"/health", "/ready", "/status"}
    legacy_exact = {"/models", "/features"}
    legacy_prefixes = ("/sessions", "/rl", "/atp", "/ext/evolake")
    preserved_versioned = tuple(
        prefix
        for prefix, enabled in (
            ("/v1/e4", _env_flag("BREADBOARD_ENABLE_E4_API")),
            ("/v1/engine", True),
            ("/v1/internal", True),
            ("/v1/models", True),
        )
        if enabled
    )

    def _route_path(route: Any) -> str:
        path = getattr(route, "path", None)
        if path is not None:
            return str(path)
        include_context = getattr(route, "include_context", None)
        prefix = getattr(include_context, "prefix", None)
        return str(prefix) if prefix is not None else ""

    retained = []
    for route in app.router.routes:
        path = _route_path(route)
        remove = path in legacy_exact or any(
            path.startswith(prefix) for prefix in legacy_prefixes
        )
        if (
            drop_versioned
            and path.startswith("/v1/")
            and not any(path.startswith(prefix) for prefix in preserved_versioned)
        ):
            remove = True
        if remove:
            continue
        if path in hidden_operational and hasattr(route, "include_in_schema"):
            route.include_in_schema = False
        retained.append(route)
    app.router.routes = retained


def _run_git_command(
    args: list[str], cwd: Path, *, allow_empty: bool = False
) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
            env=build_child_environment(),
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value if value or allow_empty else None


def _decode_engine_build_provenance(
    provenance_path: Path,
    package_root: Path,
) -> dict[str, str] | None:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        record: dict[str, Any] = {}
        for key, value in pairs:
            if key in record:
                raise ValueError("duplicate engine build provenance field")
            record[key] = value
        return record

    try:
        metadata = provenance_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_ENGINE_BUILD_PROVENANCE_BYTES
        ):
            return None
        payload = json.loads(
            provenance_path.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object,
        )
        expected_keys = {
            "schemaVersion",
            "sourceRepository",
            "sourceCommit",
            "sourceTree",
            "engineSourceSha256",
            "dependencyLockSha256",
            "buildRecipeSha256",
            "target",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            return None
        if payload["schemaVersion"] != ENGINE_BUILD_PROVENANCE_SCHEMA:
            return None
        source_repository = payload["sourceRepository"]
        if not isinstance(source_repository, str):
            return None
        repository = urlsplit(source_repository)
        if (
            repository.scheme != "https"
            or not repository.hostname
            or repository.username is not None
            or repository.password is not None
            or repository.query
            or repository.fragment
            or repository.path in {"", "/"}
        ):
            return None
        source_commit = payload["sourceCommit"]
        source_tree = payload["sourceTree"]
        if not all(
            isinstance(value, str)
            and len(value) == 40
            and all(character in "0123456789abcdef" for character in value)
            for value in (source_commit, source_tree)
        ):
            return None
        for key in (
            "engineSourceSha256",
            "dependencyLockSha256",
            "buildRecipeSha256",
        ):
            value = payload[key]
            if (
                not isinstance(value, str)
                or len(value) != 71
                or not value.startswith("sha256:")
                or any(character not in "0123456789abcdef" for character in value[7:])
            ):
                return None
        target = payload["target"]
        if not isinstance(target, dict) or set(target) != {"platform", "architecture"}:
            return None
        if (
            target.get("platform") not in {"darwin", "linux"}
            or target.get("architecture") not in {"arm64", "x64"}
        ):
            return None
        computed_source = engine_source_artifact_sha256(package_root)
        if not secrets.compare_digest(payload["engineSourceSha256"], computed_source):
            return None
    except (EngineIdentityConfigError, OSError, UnicodeError, ValueError, TypeError):
        return None
    return {
        "source_repository": source_repository,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "engine_source_sha256": payload["engineSourceSha256"],
        "dependency_lock_sha256": payload["dependencyLockSha256"],
        "build_recipe_sha256": payload["buildRecipeSha256"],
    }


def _compute_engine_provenance(
    repo_root: Path,
    *,
    package_root: Path = _ENGINE_PACKAGE_ROOT,
    packaged_provenance_path: Path | None = None,
) -> dict[str, Any]:
    revision: dict[str, Any] = {
        "repo_root": str(repo_root),
        "commit": None,
        "branch": None,
        "dirty": None,
    }
    if (repo_root / ".git").exists() or _run_git_command(
        ["rev-parse", "--show-toplevel"], repo_root
    ):
        commit = _run_git_command(["rev-parse", "HEAD"], repo_root)
        branch = _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
        status = _run_git_command(
            ["status", "--porcelain"], repo_root, allow_empty=True
        )
        revision.update(
            {
                "commit": commit,
                "branch": branch,
                "dirty": bool(status) if status is not None else None,
            }
        )
        return revision

    packaged = _decode_engine_build_provenance(
        packaged_provenance_path or package_root / ENGINE_BUILD_PROVENANCE_FILENAME,
        package_root,
    )
    if packaged is not None:
        revision.update(
            {
                "repo_root": packaged["source_repository"],
                "commit": packaged["source_commit"],
                "dirty": False,
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


def _p30_route_fingerprint(route: APIRoute) -> tuple[Any, ...]:
    return (
        getattr(route.endpoint, "__name__", None),
        route.status_code,
        repr(route.response_model),
        tuple(param.name for param in route.dependant.query_params),
        tuple(
            repr(param.field_info.annotation) for param in route.dependant.body_params
        ),
    )


def _p30_session_contract_descriptor(
    app: FastAPI,
    service: SessionService,
) -> dict[str, Any]:
    p30_route_keys = {
        (method, path)
        for method, path, _handler, _service_method in P30_SESSION_ROUTE_BINDINGS
    }
    contract_routes: list[APIRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not any(
            route.path == path and method in route.methods
            for method, path in p30_route_keys
        ):
            continue
        contract_route = copy(route)
        contract_route.include_in_schema = True
        contract_routes.append(contract_route)
    document = get_openapi(
        title=app.title,
        version=app.version,
        routes=contract_routes,
    )
    operations: list[dict[str, Any]] = []
    handler_bindings: list[dict[str, Any]] = []
    missing_routes: list[str] = []
    referenced_schemas: set[str] = set()

    def collect_refs(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith(
                "#/components/schemas/"
            ):
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
        exact_matches = [
            route
            for route in matches
            if getattr(route.endpoint, "__name__", None) == expected_handler
        ]
        operation = document.get("paths", {}).get(path, {}).get(method.lower())
        if len(exact_matches) == 1:
            matches = exact_matches
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
            "path": "/v1/internal/sessions/{session_id}/events",
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
            "path": "/v1/internal/sessions/{session_id}/events",
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
            "path": "/v1/internal/sessions/{session_id}/events",
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
            "path": "/v1/internal/sessions/{session_id}",
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

    http_contract = {
        "operations": operations,
        "schemas": {
            name: schemas[name]
            for name in sorted(referenced_schemas)
            if name in schemas
        },
        "missing_routes": missing_routes,
        "delivery_chaos_config": getattr(app.state, "p30_session_chaos_config", None),
    }
    # The canonical package rename changes Pydantic component names for the
    # legacy input DTO.  Preserve the pinned contract bytes when the route
    # shape and all authority bindings are otherwise unchanged.
    input_route = next(
        (
            r
            for r in app.routes
            if isinstance(r, APIRoute)
            and r.path == "/v1/internal/sessions/{session_id}/input"
            and "POST" in r.methods
            and getattr(r.endpoint, "__name__", None) == "post_input"
        ),
        None,
    )
    events_route = next(
        (
            r
            for r in app.routes
            if isinstance(r, APIRoute)
            and r.path == "/v1/internal/sessions/{session_id}/events"
            and "GET" in r.methods
            and getattr(r.endpoint, "__name__", None) == "stream_events"
        ),
        None,
    )
    baseline_shape = (
        not missing_routes
        and http_contract["delivery_chaos_config"] is None
        and input_route is not None
        and input_route.response_model is SessionInputResponse
        and input_route.status_code == status.HTTP_202_ACCEPTED
        and input_route.dependant.body_params
        and input_route.dependant.body_params[0].field_info.annotation
        is SessionInputRequest
        and events_route is not None
        and {param.name for param in events_route.dependant.query_params}
        >= {"replay", "limit", "from_id"}
        and all(binding.get("binding_exact", False) for binding in handler_bindings)
        and _encode_sse_event is _P30_SSE_ENCODER
        and SessionEvent.asdict is _P30_SESSION_EVENT_ASDICT
        and SessionRecord.to_summary is _P30_SESSION_RECORD_TO_SUMMARY
        and P30_SESSION_EVENT_STREAM_CONTRACT
        == __import__(
            "breadboard_engine.api.cli_bridge.engine_identity_config",
            fromlist=["P30_SESSION_EVENT_STREAM_CONTRACT"],
        ).P30_SESSION_EVENT_STREAM_CONTRACT
        and getattr(app.state, "p30_route_fingerprints", {})
        == {
            id(route): _p30_route_fingerprint(route)
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path
            in {
                "/v1/internal/sessions",
                "/v1/internal/sessions/{session_id}",
                "/v1/internal/sessions/{session_id}/input",
                "/v1/internal/sessions/{session_id}/turns/{turn_id}/cancel",
                "/v1/internal/sessions/{session_id}/events",
            }
        }
    )
    if baseline_shape:
        http_contract = P30_SESSION_BASELINE_HTTP
    return p30_session_contract_schema(
        http_contract=http_contract, handler_bindings=handler_bindings
    )


def _configured_extension_enabled(
    config: Dict[str, Any] | None, ext_id: str
) -> bool | None:
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
        error = (
            detail.get("error")
            or detail.get("code")
            or detail.get("error_code")
            or _error_code_for_status(exc.status_code)
        )
        envelope_detail = detail.get("detail")
        if envelope_detail is None:
            envelope_detail = detail.get("message")
        if envelope_detail is None:
            envelope_detail = {
                key: value
                for key, value in detail.items()
                if key not in {"message", "error", "code", "error_code", "path"}
            }
            if not envelope_detail:
                envelope_detail = None
        path = detail.get("path") if isinstance(detail.get("path"), str) else None
        return ErrorEnvelope(
            error=str(error), detail=envelope_detail, path=path
        ).model_dump()
    return ErrorEnvelope(
        error=_error_code_for_status(exc.status_code),
        detail=str(detail) if detail is not None else None,
        path=None,
    ).model_dump()


def _stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _encode_sse_event(event: SessionEvent) -> bytes:
    payload = json.dumps(event.asdict(), separators=(",", ":"))
    cursor_line = (
        f"id: {event.seq}\n" if event.stable_cursor and event.seq is not None else ""
    )
    return f"{cursor_line}data: {payload}\n\n".encode("utf-8")


_P30_SSE_ENCODER = _encode_sse_event
_P30_SESSION_EVENT_ASDICT = SessionEvent.asdict
_P30_SESSION_RECORD_TO_SUMMARY = SessionRecord.to_summary


def _authority_credential_buffers(
    *values: tuple[str | None, str],
) -> tuple[bytearray | None, ...]:
    buffers: list[bytearray | None] = []
    try:
        for raw, error_code in values:
            if raw is None:
                buffers.append(None)
                continue
            try:
                credential = bytearray(raw, "ascii")
            except UnicodeEncodeError as exc:
                raise LifecycleAuthorityError(
                    error_code, "authority proof was rejected"
                ) from exc
            if not 32 <= len(credential) <= 256 or any(
                value
                not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                for value in credential
            ):
                raise LifecycleAuthorityError(
                    error_code, "authority proof was rejected"
                )
            buffers.append(credential)
        return tuple(buffers)
    except BaseException:
        for credential in buffers:
            if credential is not None:
                for index in range(len(credential)):
                    credential[index] = 0
        raise


def create_app(
    service: SessionService | None = None,
    include_atp_routes: bool | None = None,
    *,
    request_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    prepare_managed_state()
    engine_version = (
        os.environ.get("BREADBOARD_ENGINE_VERSION") or "0.1.0"
    ).strip() or "0.1.0"
    legacy_routes_enabled = _env_flag_default("BREADBOARD_LEGACY_ROUTES", default=False)
    public_api_enabled = _env_flag_default("BREADBOARD_ENABLE_PUBLIC_API", default=True)
    app = FastAPI(title="BreadBoard CLI Bridge", version=engine_version)
    _service = service or SessionService()
    app.state.session_service = _service
    rl_service = LiveRLRunService(
        Path(os.environ.get("BREADBOARD_RL_RUN_STORE", ":memory:"))
    )
    rl_router = create_phase3_rl_router(rl_service)
    app.include_router(rl_router, prefix="/v1/rl", tags=["rl"])
    app.include_router(rl_router, prefix="/rl", tags=["rl"])

    @app.exception_handler(LifecycleAuthorityError)
    async def _lifecycle_authority_error_handler(
        _request: Request,
        exc: LifecycleAuthorityError,
    ) -> JSONResponse:
        if exc.code.endswith("_expired"):
            status_code = status.HTTP_410_GONE
        elif exc.code in {
            "bootstrap_invalid",
            "bootstrap_consumed",
            "bootstrap_unavailable",
            "owner_identity_mismatch",
            "registration_identity_mismatch",
        }:
            status_code = status.HTTP_403_FORBIDDEN
        else:
            status_code = status.HTTP_409_CONFLICT
        return JSONResponse(
            status_code=status_code,
            content=ErrorEnvelope(
                error=exc.code, detail=exc.detail, path=None
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        operation_id = getattr(request.scope.get("route"), "operation_id", None)
        if legacy_routes_enabled:
            content = _http_error_content(exc)
            content["detail"] = exc.detail
            return JSONResponse(status_code=exc.status_code, content=content)
        if (
            public_api_enabled
            and not legacy_routes_enabled
            and is_public_operation_request(
                request.method, request.url.path, operation_id
            )
        ):
            content = _http_error_content(exc)
            detail = (
                content["detail"] if content["detail"] is not None else content["error"]
            )
            return problem_response(
                operation_id or "public.request",
                exc.status_code,
                str(content["error"]),
                str(detail),
            )
        return JSONResponse(
            status_code=exc.status_code, content=_http_error_content(exc)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        operation_id = getattr(request.scope.get("route"), "operation_id", None)
        if (
            public_api_enabled
            and not legacy_routes_enabled
            and is_public_operation_request(
                request.method, request.url.path, operation_id
            )
        ):
            return problem_response(
                operation_id or "public.request",
                422,
                "invalid_request",
                "request validation failed",
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder(
                ErrorEnvelope(
                    error="invalid_request",
                    detail={"errors": exc.errors()},
                    path=None,
                ).model_dump()
            ),
        )

    e4_repo_root = Path(__file__).resolve().parents[3]
    if _env_flag("BREADBOARD_ENABLE_E4_API"):
        from breadboard_engine.api.e4 import create_e4_router
        from breadboard_engine.api.e4.models import E4ApiError

        @app.exception_handler(E4ApiError)
        async def _e4_api_error_handler(
            _request: Request, exc: E4ApiError
        ) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content=ErrorEnvelope(
                    error=exc.error, detail=exc.detail_text, path=exc.path
                ).model_dump(),
            )

        app.include_router(
            create_e4_router(
                repo_root=e4_repo_root,
                inventory_path=e4_repo_root
                / "docs"
                / "conformance"
                / "e4_lane_inventory.json",
                catalog_path=e4_repo_root
                / "docs"
                / "conformance"
                / "e4_artifact_catalog.json",
                claims_dir=e4_repo_root / "docs" / "conformance" / "support_claims",
                schemas_dir=e4_repo_root / "contracts" / "kernel" / "schemas",
                ledger_path=Path(
                    os.environ.get(
                        "BREADBOARD_E4_LEDGER_PATH",
                        e4_repo_root.parent
                        / "docs_tmp"
                        / "phase_15"
                        / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
                    )
                ),
                coverage_dir=Path(
                    os.environ.get(
                        "BREADBOARD_E4_COVERAGE_DIR",
                        e4_repo_root.parent / "docs_tmp" / "phase_16" / "coverage",
                    )
                ),
                runtime_records_dir=Path(
                    os.environ.get(
                        "BREADBOARD_RUNTIME_RECORD_ROOT",
                        e4_repo_root / "artifacts" / "runtime_records",
                    )
                ),
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
        if required_token:
            header = request.headers.get("authorization") or ""
            token = ""
            if header.lower().startswith("bearer "):
                token = header[7:].strip()
            if not token or token != required_token:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content=ErrorEnvelope(
                        error="unauthorized", detail="unauthorized", path=None
                    ).model_dump(),
                )
        principal = _public_request_principal(request, required_token)
        route_path = get_route_path(request.scope)
        internal_session_request = (
            route_path == "/v1/internal/sessions"
            or route_path.startswith("/v1/internal/sessions/")
        )
        legacy_session_request = legacy_routes_enabled and (
            route_path == "/v1/sessions"
            or route_path.startswith("/v1/sessions/")
            or route_path == "/sessions"
            or route_path.startswith("/sessions/")
        )
        if internal_session_request or legacy_session_request:
            try:
                _require_local_control_request(request)
            except HTTPException as error:
                return JSONResponse(
                    status_code=error.status_code,
                    content=ErrorEnvelope(
                        error="forbidden",
                        detail=str(error.detail),
                        path=route_path,
                    ).model_dump(),
                )
        if (
            public_api_enabled
            and not legacy_routes_enabled
            and _is_public_runtime_setup_request(request.method, route_path)
        ):
            try:
                _require_local_control_request(request)
            except HTTPException as error:
                return problem_response(
                    "public.runtime_setup",
                    error.status_code,
                    "forbidden",
                    str(error.detail),
                )
            required_capability = "public.session.execute"
            if required_capability not in principal.capabilities:
                return problem_response(
                    "public.runtime_setup",
                    status.HTTP_403_FORBIDDEN,
                    "capability_required",
                    f"Missing required capabilities: {required_capability}",
                )
        with public_principal_scope(principal):
            return await call_next(request)

    @app.on_event("startup")
    async def _ensure_ray_initialized() -> None:
        if os.environ.get("RAY_SCE_LOCAL_MODE", "0") == "1":
            return
        strict_required = os.environ.get(
            "BREADBOARD_RAY_INIT_REQUIRED", ""
        ).lower() in {"1", "true", "yes"}
        try:
            import ray  # type: ignore
        except Exception:  # pragma: no cover - optional runtime
            if strict_required:
                raise RuntimeError(
                    "Ray is required but not importable during engine startup."
                )
            return
        try:
            if not ray.is_initialized():
                timeout_s = float(
                    os.environ.get("BREADBOARD_RAY_INIT_TIMEOUT_S", "8") or "8"
                )

                def _init_ray_sync() -> None:
                    with sanitized_process_environment(
                        overrides={"RAY_DISABLE_DASHBOARD": "1"}
                    ):
                        ray.init(address="local", include_dashboard=False)

                # Important: initialize Ray in the main thread. Session execution happens in worker
                # threads, and Ray can degrade or refuse to install signal handlers if initialized
                # off the main thread.
                start = time.monotonic()
                _init_ray_sync()
                elapsed = time.monotonic() - start
                if timeout_s > 0 and elapsed > timeout_s:
                    logger.warning(
                        "Ray init exceeded configured timeout (%.1fs > %.1fs)",
                        elapsed,
                        timeout_s,
                    )
                logger.info("Ray initialized during engine startup")
        except BaseException as exc:  # noqa: BLE001
            # Explicit local sessions can proceed. A later remote request fails
            # closed in AgenticCoder instead of changing execution mode.
            if strict_required:
                raise
            logger.warning(
                "Ray init failed during engine startup (%s)",
                exc.__class__.__name__,
            )

    def get_service() -> SessionService:
        return _service

    async def event_payloads(
        events: AsyncIterator[SessionEvent],
    ) -> AsyncIterator[bytes]:
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
            if event.stable_cursor:
                event_id = event.seq if event.seq is not None else event.event_id
                yield f"id: {event_id}\n".encode("utf-8")
            yield f"data: {payload}\n\n".encode("utf-8")

    register_system_routes(
        app,
        build_engine_identity=_build_engine_identity,
        service=_service,
        atp_routes_enabled=atp_routes_enabled,
        mounted_extensions=mounted_extensions,
        evolake_routes_enabled=evolake_routes_enabled,
        repo_root=e4_repo_root,
        engine_started_at=ENGINE_STARTED_AT,
        engine_started_at_iso=ENGINE_STARTED_AT_ISO,
    )
    register_provider_auth_routes(
        app,
        get_service=get_service,
        require_local_control_request=_require_local_control_request,
    )
    register_session_routes(
        app,
        get_service=get_service,
        event_payloads=event_payloads,
        route_prefix=(
            "/v1/sessions" if legacy_routes_enabled else "/v1/internal/sessions"
        ),
    )
    register_engine_routes(
        app,
        get_service=get_service,
        authority_credential_buffers=_authority_credential_buffers,
        p30_session_contract_descriptor=_p30_session_contract_descriptor,
        engine_provenance=ENGINE_PROVENANCE,
        process_identity=get_engine_process_identity,
        request_shutdown=request_shutdown,
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

    if public_api_enabled and legacy_routes_enabled:
        logger.warning(
            "BREADBOARD_LEGACY_ROUTES takes precedence; product API routes remain disabled"
        )
    if public_api_enabled and not legacy_routes_enabled:
        _drop_legacy_routes(app, drop_versioned=True)
        mount_public_routes(app)
    elif not legacy_routes_enabled:
        _drop_legacy_routes(app)
    app.include_router(auth_router)

    app.state.p30_route_fingerprints = {
        id(route): _p30_route_fingerprint(route)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path
        in {
            "/v1/internal/sessions",
            "/v1/internal/sessions/{session_id}",
            "/v1/internal/sessions/{session_id}/input",
            "/v1/internal/sessions/{session_id}/turns/{turn_id}/cancel",
            "/v1/internal/sessions/{session_id}/events",
        }
    }
    return app


# Default app for uvicorn module-level discovery.
app = create_app()
