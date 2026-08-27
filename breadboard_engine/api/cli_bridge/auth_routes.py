"""Canonical FastAPI boundary for broker credentials and model-role locks."""

from __future__ import annotations
import ipaddress
import os
from urllib.parse import urlsplit

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from breadboard_engine.model_roles import ModelRoleResolutionError, compile_model_roles
from breadboard_engine.provider_broker import get_provider_broker
from breadboard_engine.security import redaction

from .models import (
    AuthActionResponse,
    AuthCredentialView,
    AuthLoginSession,
    AuthProviderView,
    BeginAuthLoginRequest,
    CompleteAuthLoginRequest,
    ModelRolesResolveRequest,
    ModelRolesResolveResponse,
    PutApiKeyRequest,
)

router = APIRouter(tags=["auth"])


def _is_local_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized in {"localhost", "testserver"}:
        return True
    try:
        address = ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback


def _require_local_control_request(request: Request) -> None:
    """Require authenticated or same-site loopback access to credential state."""
    if (os.environ.get("BREADBOARD_API_TOKEN") or "").strip():
        return
    hostname = request.url.hostname or ""
    client_host = request.client.host if request.client is not None else ""
    if not _is_local_hostname(hostname):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="credential control requires a loopback host or API bearer token",
        )
    if client_host != "testclient" and not _is_local_hostname(client_host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="credential control requires a loopback host or API bearer token",
        )
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-site credential control is forbidden",
        )
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        try:
            origin_hostname = urlsplit(origin).hostname or ""
        except ValueError:
            origin_hostname = ""
        if not _is_local_hostname(origin_hostname):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-site credential control is forbidden",
            )


def _safe_credential(value: dict[str, Any]) -> AuthCredentialView:
    metadata, _problems = redaction.scrub_structure(
        value.get("metadata") or {}, path="$.metadata"
    )
    refresh_state, _refresh_problems = redaction.scrub_structure(
        value.get("refresh_state") or {},
        path="$.refresh_state",
    )
    safe_refresh_state = dict(refresh_state) if isinstance(refresh_state, dict) else {}
    safe_refresh_state.setdefault("status", "idle")
    return AuthCredentialView(
        account_id=str(value.get("account_id") or ""),
        credential_id=str(value.get("credential_id") or ""),
        provider_id=str(value.get("provider_id") or ""),
        auth_scheme_id=str(value.get("auth_scheme_id") or "api_key"),
        label=str(value.get("label") or ""),
        alias=value.get("alias"),
        credential_kind=str(value.get("credential_kind") or "api_key"),
        status=str(value.get("status") or ""),
        source=str(value.get("source") or "broker"),
        secret_version=int(value.get("secret_version") or 1),
        created_at_ms=int(value.get("created_at_ms") or 0),
        updated_at_ms=int(value.get("updated_at_ms") or 0),
        expires_at_ms=value.get("expires_at_ms"),
        has_api_key=bool(value.get("has_api_key")),
        metadata=metadata if isinstance(metadata, dict) else {},
        refresh_state=safe_refresh_state,
    )


def _ref_payload(credential_ref: str) -> dict[str, str]:
    ref = str(credential_ref).strip()
    if not ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="credential_ref is required"
        )
    return {"account_id": ref} if ref.startswith("bbacct_") else {"credential_id": ref}


@router.get("/v1/auth/providers", response_model=list[AuthProviderView])
def list_auth_providers() -> list[AuthProviderView]:
    return [AuthProviderView(**item) for item in get_provider_broker().listProviders()]


@router.get("/v1/auth/credentials", response_model=list[AuthCredentialView])
def list_auth_credentials(
    request: Request,
    provider_id: str | None = Query(default=None),
) -> list[AuthCredentialView]:
    _require_local_control_request(request)
    return [
        _safe_credential(item)
        for item in get_provider_broker().listCredentials(provider_id)
    ]


@router.post("/v1/auth/login-sessions", response_model=AuthLoginSession)
def begin_auth_login(
    payload: BeginAuthLoginRequest,
    request: Request,
) -> AuthLoginSession:
    _require_local_control_request(request)
    result = get_provider_broker().beginLogin(
        payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    )
    return AuthLoginSession(**result)


@router.get(
    "/v1/auth/login-sessions/{login_session_id}", response_model=AuthLoginSession
)
def get_auth_login(
    login_session_id: str,
    request: Request,
) -> AuthLoginSession:
    _require_local_control_request(request)
    result = get_provider_broker().getLogin(login_session_id)
    return AuthLoginSession(**result)


@router.post(
    "/v1/auth/login-sessions/{login_session_id}/complete",
    response_model=AuthLoginSession,
)
def complete_auth_login(
    login_session_id: str,
    payload: CompleteAuthLoginRequest,
    request: Request,
) -> AuthLoginSession:
    _require_local_control_request(request)
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    body["login_session_id"] = login_session_id
    result = get_provider_broker().completeLogin(body)
    return AuthLoginSession(**result)


@router.delete(
    "/v1/auth/login-sessions/{login_session_id}", response_model=AuthActionResponse
)
def cancel_auth_login(
    login_session_id: str,
    request: Request,
) -> AuthActionResponse:
    _require_local_control_request(request)
    return AuthActionResponse(**get_provider_broker().cancelLogin(login_session_id))


@router.put(
    "/v1/auth/credentials/{provider_id}/{account_label}/api-key",
    response_model=AuthCredentialView,
)
def put_auth_api_key(
    provider_id: str,
    account_label: str,
    payload: PutApiKeyRequest,
    request: Request,
) -> AuthCredentialView:
    _require_local_control_request(request)
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    body.update({"provider_id": provider_id, "account_label": account_label})
    try:
        result = get_provider_broker().putApiKey(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _safe_credential(result)


@router.delete(
    "/v1/auth/credentials/{credential_ref}", response_model=AuthActionResponse
)
def logout_auth_credential(
    credential_ref: str,
    request: Request,
) -> AuthActionResponse:
    _require_local_control_request(request)
    return AuthActionResponse(
        **get_provider_broker().logout(_ref_payload(credential_ref))
    )


@router.post(
    "/v1/auth/credentials/{credential_ref}/revoke", response_model=AuthActionResponse
)
def revoke_auth_credential(
    credential_ref: str,
    request: Request,
) -> AuthActionResponse:
    _require_local_control_request(request)
    return AuthActionResponse(
        **get_provider_broker().revoke(_ref_payload(credential_ref))
    )


@router.post("/v1/model-roles/resolve", response_model=ModelRolesResolveResponse)
def resolve_model_roles(
    payload: ModelRolesResolveRequest,
    request: Request,
) -> ModelRolesResolveResponse:
    _require_local_control_request(request)
    try:
        broker = get_provider_broker()
        lock = compile_model_roles(
            payload.model_roles,
            broker=broker,
            role_overrides=payload.role_overrides,
            session_started=payload.session_started,
            catalog=payload.model_catalog,
        )
    except ModelRoleResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.problem.to_dict()
        ) from exc
    safe_lock, _problems = redaction.scrub_structure(lock.as_dict(), path="$.lock")
    return ModelRolesResolveResponse(
        lock=safe_lock if isinstance(safe_lock, dict) else {},
        lock_hash=lock.lock_hash,
    )
