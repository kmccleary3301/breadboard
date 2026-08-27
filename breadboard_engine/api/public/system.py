from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from breadboard.product.operations.model import OperationContext
from breadboard.product.operations.system import (
    DescribeSystemRequest,
    HealthSystemRequest,
    SchemasSystemRequest,
    describe_system,
    health_system,
    schemas_system,
)

from .models import PublicResult, invoke, public_operation_context


router = APIRouter(tags=["public-system"])


def _operation_context(workspace: Path) -> OperationContext:
    enabled_extensions = (
        frozenset({"e4"})
        if os.environ.get("BREADBOARD_ENABLE_E4_API", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else frozenset()
    )
    return public_operation_context(
        workspace,
        enabled_extensions=enabled_extensions,
    )


@router.get(
    "/v1/system",
    operation_id="system.describe",
    response_model=PublicResult,
)
def describe() -> JSONResponse:
    return invoke(
        "system.describe",
        lambda workspace: describe_system(
            DescribeSystemRequest(),
            _operation_context(workspace),
        ),
    )


@router.get(
    "/v1/health",
    operation_id="system.health",
    response_model=PublicResult,
)
def health() -> JSONResponse:
    return invoke(
        "system.health",
        lambda workspace: health_system(
            HealthSystemRequest(),
            public_operation_context(workspace),
        ),
    )


@router.get(
    "/v1/schemas",
    operation_id="system.schemas",
    response_model=PublicResult,
)
def schemas() -> JSONResponse:
    return invoke(
        "system.schemas",
        lambda workspace: schemas_system(
            SchemasSystemRequest(),
            public_operation_context(workspace),
        ),
    )
