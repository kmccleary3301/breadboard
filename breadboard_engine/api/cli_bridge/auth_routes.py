"""Canonical FastAPI boundary for broker credentials and model-role locks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

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


def _safe_credential(value: dict[str, Any]) -> AuthCredentialView:
    metadata, _problems = redaction.scrub_structure(value.get("metadata") or {}, path="$.metadata")
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
    )


def _ref_payload(credential_ref: str) -> dict[str, str]:
    ref = str(credential_ref).strip()
    if not ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="credential_ref is required")
    return {"account_id": ref} if ref.startswith("bbacct_") else {"credential_id": ref}


@router.get("/v1/auth/providers", response_model=list[AuthProviderView])
def list_auth_providers() -> list[AuthProviderView]:
    return [AuthProviderView(**item) for item in get_provider_broker().listProviders()]


@router.get("/v1/auth/credentials", response_model=list[AuthCredentialView])
def list_auth_credentials(provider_id: str | None = Query(default=None)) -> list[AuthCredentialView]:
    return [_safe_credential(item) for item in get_provider_broker().listCredentials(provider_id)]


@router.post("/v1/auth/login-sessions", response_model=AuthLoginSession)
def begin_auth_login(payload: BeginAuthLoginRequest) -> AuthLoginSession:
    result = get_provider_broker().beginLogin(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    return AuthLoginSession(**result)


@router.get("/v1/auth/login-sessions/{login_session_id}", response_model=AuthLoginSession)
def get_auth_login(login_session_id: str) -> AuthLoginSession:
    result = get_provider_broker().getLogin(login_session_id)
    return AuthLoginSession(**result)


@router.post("/v1/auth/login-sessions/{login_session_id}/complete", response_model=AuthLoginSession)
def complete_auth_login(login_session_id: str, payload: CompleteAuthLoginRequest) -> AuthLoginSession:
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    body["login_session_id"] = login_session_id
    result = get_provider_broker().completeLogin(body)
    return AuthLoginSession(**result)


@router.delete("/v1/auth/login-sessions/{login_session_id}", response_model=AuthActionResponse)
def cancel_auth_login(login_session_id: str) -> AuthActionResponse:
    return AuthActionResponse(**get_provider_broker().cancelLogin(login_session_id))


@router.put(
    "/v1/auth/credentials/{provider_id}/{account_label}/api-key",
    response_model=AuthCredentialView,
)
def put_auth_api_key(provider_id: str, account_label: str, payload: PutApiKeyRequest) -> AuthCredentialView:
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    body.update({"provider_id": provider_id, "account_label": account_label})
    try:
        result = get_provider_broker().putApiKey(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _safe_credential(result)


@router.delete("/v1/auth/credentials/{credential_ref}", response_model=AuthActionResponse)
def logout_auth_credential(credential_ref: str) -> AuthActionResponse:
    return AuthActionResponse(**get_provider_broker().logout(_ref_payload(credential_ref)))


@router.post("/v1/auth/credentials/{credential_ref}/revoke", response_model=AuthActionResponse)
def revoke_auth_credential(credential_ref: str) -> AuthActionResponse:
    return AuthActionResponse(**get_provider_broker().revoke(_ref_payload(credential_ref)))


@router.post("/v1/model-roles/resolve", response_model=ModelRolesResolveResponse)
def resolve_model_roles(payload: ModelRolesResolveRequest) -> ModelRolesResolveResponse:
    try:
        lock = compile_model_roles(
            payload.model_roles,
            role_overrides=payload.role_overrides,
            session_started=payload.session_started,
        )
    except ModelRoleResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.problem.to_dict()) from exc
    safe_lock, _problems = redaction.scrub_structure(lock.as_dict(), path="$.lock")
    return ModelRolesResolveResponse(
        lock=safe_lock if isinstance(safe_lock, dict) else {},
        lock_hash=lock.lock_hash,
    )
