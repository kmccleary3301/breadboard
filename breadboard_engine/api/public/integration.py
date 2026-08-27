from __future__ import annotations


from fastapi import APIRouter, Header

from breadboard.product.operations import integration as integration_operations
from breadboard.product.operations.integration import (
    GetIntegrationRequest,
    ListIntegrationsRequest,
)

from .models import PublicResult, invoke, invoke_idempotent, public_operation_context


router = APIRouter(tags=["public-integration"])


@router.get(
    "/v1/integrations",
    operation_id="integration.list",
    response_model=PublicResult,
)
def list_integrations():
    return invoke(
        "integration.list",
        lambda workspace: integration_operations.list_integrations(
            ListIntegrationsRequest(),
            public_operation_context(workspace),
        ),
    )


@router.get(
    "/v1/integrations/{integration_id}",
    operation_id="integration.get",
    response_model=PublicResult,
)
def get(integration_id: str):
    return invoke(
        "integration.get",
        lambda workspace: integration_operations.get_integration(
            GetIntegrationRequest(integration_id),
            public_operation_context(workspace),
        ),
    )


@router.post(
    "/v1/integrations/{integration_id}/probe",
    operation_id="integration.probe",
    response_model=PublicResult,
    status_code=202,
)
def probe(
    integration_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from types import SimpleNamespace

    from breadboard.product.cli import (
        integration as legacy_integration_operations,
    )

    return invoke_idempotent(
        "integration.probe",
        idempotency_key,
        {"integration_id": integration_id},
        lambda workspace: legacy_integration_operations.probe(
            SimpleNamespace(workspace=workspace, INTEGRATION_ID=integration_id)
        ),
    )
