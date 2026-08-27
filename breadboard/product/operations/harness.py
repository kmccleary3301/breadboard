from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


from breadboard.product.harness.lock import lock_path
from breadboard.product.harness.resolution import (
    compile_harness_source,
    load_harness_document,
)
from breadboard.product.harness.templates import DAILY_DRIVER_TEMPLATE_NAME
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
    load_harness_definition,
)
from breadboard.product.operations.model import (
    EXIT_VALIDATION_FAILURE,
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)


@dataclass(frozen=True, slots=True)
class ListHarnessesRequest:
    directory: str | Path | None = None


@dataclass(frozen=True, slots=True)
class GetHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class ValidateHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class ExplainHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class GetHarnessLockRequest:
    path: str | Path


def _is_harness_path(path: Path, context: OperationContext) -> bool:
    try:
        if "harness" not in path.name and path.name != DAILY_DRIVER_TEMPLATE_NAME:
            return False
        if not path.is_file() or path.is_symlink():
            return False
        reference: str | Path = path
        if context.contained:
            reference = path.relative_to(context.workspace)
        resolved = context.resolve_path(reference)
        load_harness_definition(resolved)
        return True
    except (OSError, PermissionError, TypeError, ValueError, yaml.YAMLError):
        return False


def list_harnesses(
    request: ListHarnessesRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "list"]
    stage = "harness.list"
    try:
        root = (
            context.workspace
            if request.directory is None
            else context.resolve_path(request.directory)
        )
        paths = (
            path
            for path in sorted(root.rglob("*.yaml"))
            if _is_harness_path(path, context)
        )
        refs = [portable_ref(path, context.workspace) for path in paths]
        return OperationResult.success(
            command,
            {"harnesses": refs, "count": len(refs)},
            refs,
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def get_harness(
    request: GetHarnessRequest,
    context: OperationContext,
    *,
    command_name: str = "get",
) -> OperationResult:
    command = ["harness", command_name]
    stage = f"harness.{command_name}"
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        return OperationResult.success(
            command,
            {"path": reference, "definition": load_harness_document(path)},
            [reference],
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def validate_harness(
    request: ValidateHarnessRequest,
    context: OperationContext,
    *,
    command_name: str = "validate",
) -> OperationResult:
    command = ["harness", command_name]
    stage = f"harness.{command_name}"
    path: Path | None = None
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        definition = load_harness_definition(path)
        return OperationResult.success(
            command,
            {"path": reference, "schema_version": definition["schema_version"]},
            [reference],
            stage=stage,
        )
    except HarnessDefinitionValidationError as error:
        refs = () if path is None else (portable_ref(path, context.workspace),)
        return OperationResult.failure(
            command,
            EXIT_VALIDATION_FAILURE,
            "invalid_harness",
            str(error),
            stage,
            refs=refs,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def explain_harness(
    request: ExplainHarnessRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "explain"]
    stage = "harness.explain"
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        explanation = compile_harness_source(
            path,
            context.workspace,
            context.contained,
        ).explanation.as_dict()
        explanation["config_path"] = reference
        return OperationResult.success(
            command,
            explanation,
            [reference],
            {"config": str(explanation.get("config_sha256", ""))},
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def get_harness_lock(
    request: GetHarnessLockRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness-lock", "get"]
    stage = "harness-lock.get"
    try:
        path = context.resolve_path(request.path)
        target = path if path.name.endswith(".lock.json") else lock_path(path)
        lock = json.loads(target.read_text())
        reference = portable_ref(target, context.workspace)
        return OperationResult.success(
            command,
            {"path": reference, "lock": lock},
            [reference],
            {"graph": str(lock.get("graph_hash", ""))},
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)
