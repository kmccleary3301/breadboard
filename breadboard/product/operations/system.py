from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from breadboard.product.harness.default_profile import (
    DefaultProfileResolutionError,
    default_profile_identity,
)
from breadboard.product.operation_catalog import (
    internal_evidence_operation_catalog,
    product_operation_catalog,
)
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
)


@dataclass(frozen=True, slots=True)
class DescribeSystemRequest:
    pass


_DESCRIBE_COMMAND = ("system", "describe")


def _candidate_operations() -> list[dict[str, Any]]:
    return list(product_operation_catalog()["operations"])


def _operation_rows() -> list[dict[str, str]]:
    rows = []
    for operation in _candidate_operations():
        binding = operation.get("bindings", {}).get("bbh", {})
        operation_id = operation.get("operation_id")
        command = binding.get("command") if isinstance(binding, dict) else None
        if isinstance(operation_id, str) and isinstance(command, str):
            rows.append(
                {
                    "operation_id": operation_id,
                    "command": command,
                    "status": str(operation.get("status") or "candidate"),
                }
            )
    return sorted(rows, key=lambda row: row["operation_id"])


def _internal_extensions(
    context: OperationContext,
) -> list[dict[str, Any]]:
    if "e4" not in context.enabled_extensions:
        return []
    catalog = internal_evidence_operation_catalog()
    return [
        {
            "extension_id": "e4",
            "catalog_id": catalog["contract_id"],
            "operation_count": len(catalog["operations"]),
        }
    ]


def describe_system(
    _request: DescribeSystemRequest,
    context: OperationContext,
) -> OperationResult:
    try:
        profile = default_profile_identity()
        operations = _operation_rows()
        return OperationResult.success(
            _DESCRIBE_COMMAND,
            {
                "system": "breadboard",
                "operation_count": len(operations),
                "operations": operations,
                "default_profile": profile,
                "internal_extensions": _internal_extensions(context),
                "result_schema": "bb.cli.result.v1",
                "workspace": ".",
            },
            hashes={"profile": str(profile["effective_lock_hash"])},
            next_actions=["breadboard system health"],
            stage="system.describe",
        )
    except DefaultProfileResolutionError as error:
        return OperationResult.failure(
            _DESCRIBE_COMMAND,
            error.exit_code,
            error.error_code,
            str(error),
            "system.describe",
            hint=error.hint,
        )
    except Exception as error:
        return from_exception(
            _DESCRIBE_COMMAND,
            error,
            "system.describe",
        )
