from __future__ import annotations
from fastapi import APIRouter
from .artifact import router as artifact_router
from .harness import router as harness_router
from .integration import router as integration_router
from .session import router as session_router
from .system import router as system_router
def create_public_router() -> APIRouter:
    router = APIRouter()
    for family in (system_router, harness_router, integration_router, artifact_router, session_router):
        router.routes.extend(family.routes)
    return router
__all__ = ["create_public_router"]
