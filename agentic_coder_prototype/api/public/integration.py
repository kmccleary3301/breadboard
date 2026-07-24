from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter

from breadboard.product.cli import integration as operations
from breadboard.product.cli.result import CliResult
from .models import PublicResult, invoke

router = APIRouter(tags=["public-integration"])


@router.get("/v1/integrations", operation_id="integration.list", response_model=PublicResult)
def list_integrations():
    return invoke("integration.list", lambda workspace: operations.list_integrations(SimpleNamespace(workspace=workspace)))


def _get(integration_id: str) -> CliResult:
    adapter = operations._catalog().get(integration_id)
    return CliResult.success(
        ["integration", "get"],
        {"integration": operations._record(adapter.descriptor)},
        stage="integration.get",
    )


@router.get("/v1/integrations/{integration_id}", operation_id="integration.get", response_model=PublicResult)
def get(integration_id: str):
    return invoke("integration.get", lambda workspace: _get(integration_id))


@router.post("/v1/integrations/{integration_id}/probe", operation_id="integration.probe", response_model=PublicResult, status_code=202)
def probe(integration_id: str):
    return invoke("integration.probe", lambda workspace: operations.probe(SimpleNamespace(workspace=workspace, INTEGRATION_ID=integration_id)))
