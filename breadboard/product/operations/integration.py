from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from breadboard.product.operations.model import (
    EXIT_BLOCKED,
    OperationContext,
    OperationResult,
    from_exception,
)


@dataclass(frozen=True, slots=True)
class ListIntegrationsRequest:
    pass


@dataclass(frozen=True, slots=True)
class GetIntegrationRequest:
    integration_id: str


@dataclass(frozen=True, slots=True)
class ProbeIntegrationRequest:
    integration_id: str | None = None


_PROBE_COMMAND = ("integration", "probe")


_LIST_COMMAND = ("integration", "list")
_GET_COMMAND = ("integration", "get")


def _catalog() -> Any:
    from breadboard.product.integrations import IntegrationCatalog

    return IntegrationCatalog()


def _record(value: Any) -> Any:
    if hasattr(value, "to_record"):
        return value.to_record()
    if hasattr(value, "descriptor"):
        return _record(value.descriptor)
    if isinstance(value, (list, tuple)):
        return [_record(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _record(item) for key, item in value.items()}
    return value


def list_integrations(
    _request: ListIntegrationsRequest,
    _context: OperationContext,
) -> OperationResult:
    try:
        rows = _record(_catalog().list())
        rows = rows if isinstance(rows, list) else [rows]
        return OperationResult.success(
            _LIST_COMMAND,
            {"integrations": rows, "count": len(rows)},
            stage="integration.list",
        )
    except ModuleNotFoundError:
        return OperationResult.failure(
            _LIST_COMMAND,
            EXIT_BLOCKED,
            "integration_catalog_unavailable",
            "integration catalog is unavailable in this installation",
            "integration.list",
            status="blocked",
        )
    except Exception as error:
        return from_exception(_LIST_COMMAND, error, "integration.list")


def get_integration(
    request: GetIntegrationRequest,
    _context: OperationContext,
) -> OperationResult:
    try:
        integration = _record(_catalog().get(request.integration_id))
        if integration is None:
            return OperationResult.failure(
                _GET_COMMAND,
                EXIT_BLOCKED,
                "integration_not_found",
                f"integration not found: {request.integration_id}",
                "integration.get",
                next_actions=["breadboard integration list"],
                status="blocked",
            )
        return OperationResult.success(
            _GET_COMMAND,
            {"integration": integration},
            stage="integration.get",
        )
    except KeyError:
        return OperationResult.failure(
            _GET_COMMAND,
            EXIT_BLOCKED,
            "integration_not_found",
            f"integration not found: {request.integration_id}",
            "integration.get",
            next_actions=["breadboard integration list"],
            status="blocked",
        )
    except ModuleNotFoundError:
        return OperationResult.failure(
            _GET_COMMAND,
            EXIT_BLOCKED,
            "integration_catalog_unavailable",
            "integration catalog is unavailable in this installation",
            "integration.get",
            status="blocked",
        )
    except Exception as error:
        return from_exception(_GET_COMMAND, error, "integration.get")


def probe_integration(
    request: ProbeIntegrationRequest,
    _context: OperationContext,
) -> OperationResult:
    try:
        return OperationResult.success(
            _PROBE_COMMAND,
            {"probe": _record(_catalog().probe(request.integration_id))},
            stage="integration.probe",
        )
    except ModuleNotFoundError:
        return OperationResult.failure(
            _PROBE_COMMAND,
            EXIT_BLOCKED,
            "integration_catalog_unavailable",
            "integration catalog is unavailable in this installation",
            "integration.probe",
            status="blocked",
        )
    except Exception as error:
        return from_exception(_PROBE_COMMAND, error, "integration.probe")
