from __future__ import annotations

from fastapi import APIRouter, Depends

from .models import ATPReplBatchRequest, ATPReplBatchResponse, ATPReplRequest, ATPReplResponse, ErrorResponse
from .service import SessionService


def build_atp_router(get_service) -> APIRouter:
    router = APIRouter()

    route_errors = {
        400: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    }

    @router.post("/atp/repl", response_model=ATPReplResponse, responses=route_errors)
    @router.post("/atp/v1/repl", response_model=ATPReplResponse, responses=route_errors)
    async def atp_repl(payload: ATPReplRequest, svc: SessionService = Depends(get_service)):
        return await svc.atp_repl(payload)

    @router.post("/atp/repl/batch", response_model=ATPReplBatchResponse, responses=route_errors)
    @router.post("/atp/v1/repl/batch", response_model=ATPReplBatchResponse, responses=route_errors)
    async def atp_repl_batch(payload: ATPReplBatchRequest, svc: SessionService = Depends(get_service)):
        return await svc.atp_repl_batch(payload)

    return router
