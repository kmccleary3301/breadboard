from __future__ import annotations

from pathlib import Path
from typing import Any

from breadboard.product.operations import integration as integration_operations
from breadboard.product.operations.integration import (
    GetIntegrationRequest,
    ListIntegrationsRequest,
    ProbeIntegrationRequest,
)
from breadboard.product.operations.model import OperationContext, OperationResult


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
    return integration_operations.probe_integration(
        ProbeIntegrationRequest(getattr(args, "INTEGRATION_ID", None)),
        _operation_context(_workspace(args)),
    )
