from __future__ import annotations

from fastapi import APIRouter

from .models import EvoLakeHealthResponse, EvoLakeHelloResponse


def build_evolake_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/ext/evolake/health",
        response_model=EvoLakeHealthResponse,
    )
    async def evolake_health() -> EvoLakeHealthResponse:
        return EvoLakeHealthResponse()

    @router.get(
        "/ext/evolake/hello",
        response_model=EvoLakeHelloResponse,
    )
    async def evolake_hello() -> EvoLakeHelloResponse:
        return EvoLakeHelloResponse()

    return router
