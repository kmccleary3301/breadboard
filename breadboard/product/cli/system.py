from __future__ import annotations

import os
from pathlib import Path

from breadboard.product.operations.model import OperationContext, OperationResult
from breadboard.product.operations.system import (
    DescribeSystemRequest,
    HealthSystemRequest,
    SchemasSystemRequest,
    describe_system,
    health_system,
    schemas_system,
)


def _operation_context(workspace: Path) -> OperationContext:
    enabled_extensions = (
        frozenset({"e4"})
        if os.environ.get("BREADBOARD_ENABLE_E4_API", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else frozenset()
    )
    return OperationContext(
        workspace=workspace.expanduser().resolve(),
        path_policy="explicit-local",
        reference_root=Path.cwd().resolve(),
        enabled_extensions=enabled_extensions,
    )


def describe(workspace: Path) -> OperationResult:
    return describe_system(
        DescribeSystemRequest(),
        _operation_context(workspace),
    )


def health(
    command: list[str],
    workspace: Path,
) -> OperationResult:
    return health_system(
        HealthSystemRequest(),
        _operation_context(workspace),
    )


def schemas(
    command: list[str],
    workspace: Path,
) -> OperationResult:
    return schemas_system(
        SchemasSystemRequest(),
        _operation_context(workspace),
    )
