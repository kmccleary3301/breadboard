"""Provider authentication and model catalog routes."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status

from ..models import (
    ErrorResponse, ModelCatalogResponse, ProviderAuthAttachRequest,
    ProviderAuthAttachResponse, ProviderAuthDetachRequest,
    ProviderAuthDetachResponse, ProviderAuthStatusResponse,
)
from ..service import SessionService


def register_provider_auth_routes(
    app: FastAPI,
    *,
    get_service,
    is_loopback_host,
) -> None:
    _is_loopback_host = is_loopback_host

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
        """Attach provider auth material through the process-local broker."""

        from ....auth.enforcer import apply_dotted_overrides, check_conformance
        from ....compilation.v2_loader import load_agent_config
        from ....provider_broker import get_provider_broker

        broker = get_provider_broker()

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
            required_profile = {
                "profile_id": payload.required_profile.profile_id,
                "conformance_hash": payload.required_profile.conformance_hash,
                "locked_json_pointers": locked,
            }

        api_key = payload.material.api_key
        if not api_key:
            # Allow callers to provide the bearer token in headers (common in plan adapters).
            auth = (payload.material.headers or {}).get("Authorization") or (payload.material.headers or {}).get("authorization")
            if isinstance(auth, str) and auth.strip():
                value = auth.strip()
                api_key = value[7:].strip() if value.lower().startswith("bearer ") else value

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "provider auth material must include api_key or Authorization"},
            )

        detail = broker.putApiKey(
            {
                "provider_id": payload.material.provider_id,
                "alias": (payload.material.alias or "").strip(),
                "api_key": api_key,
                "headers": dict(payload.material.headers or {}),
                "base_url": payload.material.base_url,
                "routing": dict(payload.material.routing or {}) if isinstance(payload.material.routing, dict) else {},
                "expires_at_ms": payload.material.expires_at_ms,
                "ttl_seconds": payload.material.ttl_seconds,
                "account_label": (payload.material.alias or payload.material.provider_id).strip(),
                "metadata": {
                    "header_keys": sorted(str(key) for key in (payload.material.headers or {}) if key),
                    "issued_at_ms": payload.material.issued_at_ms,
                    "is_subscription_plan": bool(payload.material.is_subscription_plan),
                    "required_profile": required_profile,
                },
            }
        )
        return ProviderAuthAttachResponse(ok=True, detail={"attached": True, "credential": detail})

    @app.post(
        "/v1/provider-auth/detach",
        response_model=ProviderAuthDetachResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def detach_provider_auth(payload: ProviderAuthDetachRequest):
        from ....provider_broker import get_provider_broker

        result = get_provider_broker().logout(
            {"provider_id": payload.provider_id, "label": (payload.alias or "").strip() or None}
        )
        return ProviderAuthDetachResponse(ok=bool(result.get("ok")))

    @app.get(
        "/v1/provider-auth/status",
        response_model=ProviderAuthStatusResponse,
    )
    async def provider_auth_status():
        from ....provider_broker import get_provider_broker

        items = get_provider_broker().listCredentials()
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

