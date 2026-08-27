from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from breadboard.product.cli import system as legacy_system_operations
from breadboard.product.operations.model import OperationContext
from breadboard.product.operations.system import (
    DescribeSystemRequest,
    describe_system,
)

from .models import PublicResult, invoke


router = APIRouter(tags=["public-system"])


def _operation_context(workspace: Path) -> OperationContext:
    enabled_extensions = (
        frozenset({"e4"})
        if os.environ.get("BREADBOARD_ENABLE_E4_API", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else frozenset()
    )
    return OperationContext(
        workspace=workspace,
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
        lambda workspace: legacy_system_operations.health(
            ["system", "health"],
            workspace,
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
        lambda workspace: legacy_system_operations.schemas(
            ["system", "schemas"],
            workspace,
        ),
    )
