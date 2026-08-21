"""Operational and registry discovery routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status


def register_system_routes(
    app: FastAPI,
    *,
    build_engine_identity,
    service,
    atp_routes_enabled: bool,
    mounted_extensions: list[str],
    evolake_routes_enabled: bool,
    repo_root: Path,
) -> None:
    _build_engine_identity = build_engine_identity
    _service = service
    e4_repo_root = repo_root

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

