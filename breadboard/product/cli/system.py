from __future__ import annotations

import os
import sysconfig
from pathlib import Path

from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard.product.operations.system import (
    DescribeSystemRequest,
    describe_system,
)


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


def describe(workspace: Path) -> OperationResult:
    return describe_system(
        DescribeSystemRequest(),
        _operation_context(workspace),
    )


def resource_path(relative: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    source_path = root / relative
    if source_path.exists():
        return source_path
    return Path(sysconfig.get_path("data")) / relative


def health(
    command: list[str],
    workspace: Path,
) -> OperationResult:
    try:
        root = workspace.expanduser().resolve()
        if not root.exists():
            return OperationResult.failure(
                command,
                3,
                "workspace_unavailable",
                f"workspace does not exist: {portable_ref(root, root)}",
                "system.health",
            )
        metadata = root / ".breadboard"
        return OperationResult.success(
            command,
            {
                "workspace": ".",
                "workspace_exists": True,
                "metadata_dir": portable_ref(metadata, root),
                "metadata_exists": metadata.is_dir(),
                "python": sysconfig.get_platform(),
            },
            stage="system.health",
        )
    except Exception as error:
        return from_exception(command, error, "system.health")


def schemas(
    command: list[str],
    workspace: Path,
) -> OperationResult:
    try:
        names = sorted(
            path.name
            for path in resource_path("contracts/public/schemas").glob("*.schema.json")
        )
        return OperationResult.success(
            command,
            {"schema_count": len(names), "schemas": names},
            stage="system.schemas",
        )
    except Exception as error:
        return from_exception(command, error, "system.schemas")
