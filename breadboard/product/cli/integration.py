from __future__ import annotations

from pathlib import Path
from typing import Any

from breadboard.product.operations import integration as integration_operations
from breadboard.product.operations.integration import (
    GetIntegrationRequest,
    ListIntegrationsRequest,
)
from breadboard.product.operations.model import (
    EXIT_BLOCKED,
    OperationContext,
    OperationResult,
    from_exception,
)


def _operation_context(workspace: Path) -> OperationContext:
    return OperationContext(
        workspace=workspace,
        path_policy="explicit-local",
        reference_root=Path.cwd().resolve(),
    )


def _workspace(args: Any) -> Path:
    return Path(getattr(args, "workspace", None) or Path.cwd()).expanduser().resolve()


def list_integrations(args: Any) -> OperationResult:
    return integration_operations.list_integrations(
        ListIntegrationsRequest(),
        _operation_context(_workspace(args)),
    )


def get(args: Any) -> OperationResult:
    return integration_operations.get_integration(
        GetIntegrationRequest(args.INTEGRATION_ID),
        _operation_context(_workspace(args)),
    )


def probe(args: Any) -> OperationResult:
    try:
        return OperationResult.success(
            ["integration", "probe"],
            {
                "probe": integration_operations._record(
                    integration_operations._catalog().probe(
                        getattr(args, "INTEGRATION_ID", None)
                    )
                )
            },
            stage="integration.probe",
        )
    except ModuleNotFoundError:
        return OperationResult.failure(
            ["integration", "probe"],
            EXIT_BLOCKED,
            "integration_catalog_unavailable",
            "integration catalog is unavailable in this installation",
            "integration.probe",
            status="blocked",
        )
    except Exception as error:
        return from_exception(["integration", "probe"], error, "integration.probe")
