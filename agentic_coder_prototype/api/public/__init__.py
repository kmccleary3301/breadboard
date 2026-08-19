from __future__ import annotations
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from breadboard.product.operation_catalog import product_operation_catalog
from .artifact import router as artifact_router
from .harness import router as harness_router
from .integration import router as integration_router
from .session import router as session_router
from .system import router as system_router
def mount_public_routes(app: FastAPI) -> None:
    families: tuple[APIRouter, ...] = (
        system_router,
        harness_router,
        integration_router,
        artifact_router,
        session_router,
    )
    expected = {operation["operation_id"] for operation in product_operation_catalog()["operations"]}
    observed = {
        route.operation_id
        for family in families
        for route in family.routes
        if isinstance(route, APIRoute)
    }
    if observed != expected:
        raise RuntimeError(
            f"product router/catalog mismatch: missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )
    routes = [route for family in families for route in family.routes]
    app.router.routes[0:0] = routes
__all__ = ["mount_public_routes"]
