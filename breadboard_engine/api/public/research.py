from __future__ import annotations

from fastapi import APIRouter, Request

from breadboard.product.operations.research import (
    CompareResearchRequest,
    compare_research,
)

from .models import (
    PublicResult,
    ResearchCompareBody,
    invoke_async,
    public_operation_context,
)

router = APIRouter(tags=["public-research"])


@router.post(
    "/v1/research/compare", operation_id="research.compare", response_model=PublicResult
)
async def compare(body: ResearchCompareBody, request: Request):
    operation = CompareResearchRequest(
        body.definition, body.world, body.generation, body.projection, body.compare
    )
    return await invoke_async(
        "research.compare",
        lambda workspace: compare_research(
            operation,
            public_operation_context(workspace),
            registry=request.app.state.session_service.registry,
        ),
    )
