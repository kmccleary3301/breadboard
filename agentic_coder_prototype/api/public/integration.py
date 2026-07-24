from __future__ import annotations
from types import SimpleNamespace
from fastapi import APIRouter, Header
from breadboard.product.cli import integration as operations
from breadboard.product.cli.result import CliResult, EXIT_BLOCKED
from .models import PublicResult, invoke, invoke_idempotent
router = APIRouter(tags=["public-integration"])
@router.get("/v1/integrations", operation_id="integration.list", response_model=PublicResult)
def list_integrations():
    return invoke("integration.list", lambda workspace: operations.list_integrations(SimpleNamespace(workspace=workspace)))
def _get(integration_id: str) -> CliResult:
    try:
        adapter = operations._catalog().get(integration_id)
    except KeyError:
        adapter = None
    if adapter is None:
        return CliResult.failure(
            ["integration", "get"],
            EXIT_BLOCKED,
            "integration_not_found",
            f"integration not found: {integration_id}",
            "integration.get",
            next_actions=["breadboard integration list"],
            status="blocked",
        )
    return CliResult.success(
        ["integration", "get"],
        {"integration": operations._record(adapter.descriptor)},
        stage="integration.get",
    )
@router.get("/v1/integrations/{integration_id}", operation_id="integration.get", response_model=PublicResult)
def get(integration_id: str):
    return invoke("integration.get", lambda workspace: _get(integration_id))
@router.post("/v1/integrations/{integration_id}/probe", operation_id="integration.probe", response_model=PublicResult, status_code=202)
def probe(
    integration_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return invoke_idempotent(
        "integration.probe",
        idempotency_key,
        {"integration_id": integration_id},
        lambda workspace: operations.probe(
            SimpleNamespace(workspace=workspace, INTEGRATION_ID=integration_id)
        ),
    )
