from __future__ import annotations

from fastapi import APIRouter

from breadboard.product.cli import system as operations
from .models import PublicResult, invoke

router = APIRouter(tags=["public-system"])


@router.get("/v1/system", operation_id="system.describe", response_model=PublicResult)
def describe():
    return invoke("system.describe", lambda workspace: operations.describe(["system", "describe"], workspace))


@router.get("/v1/health", operation_id="system.health", response_model=PublicResult)
def health():
    return invoke("system.health", lambda workspace: operations.health(["system", "health"], workspace))


@router.get("/v1/schemas", operation_id="system.schemas", response_model=PublicResult)
def schemas():
    return invoke("system.schemas", lambda workspace: operations.schemas(["system", "schemas"], workspace))
