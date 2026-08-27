from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from breadboard.product.harness.compile import (
    HarnessCompilation,
    compile_harness_definition,
)
from breadboard.product.harness.templates import (
    DAILY_DRIVER_MODEL_ROLES_NAME,
    DAILY_DRIVER_TEMPLATE_NAME,
    load_daily_driver_model_roles,
)
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
    parse_harness_definition,
    validate_harness_document_domain,
)
from breadboard.product.operations.model import portable_ref


class HarnessContainmentError(PermissionError):
    """Raised when a harness source or resource escapes its allowed root."""


class HarnessResourceInvalidError(ValueError):
    """Raised when a declared prompt resource is not usable text."""


def resolve_contained_target(
    candidate: Path,
    root: Path,
    label: str,
    *,
    strict: bool,
) -> Path:
    canonical_root = root.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise HarnessContainmentError(
            f"{label} must remain within workspace"
        ) from error
    if any(
        root.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise HarnessContainmentError(f"{label} cannot traverse a symlink")
    try:
        target = candidate.resolve(strict=strict)
    except RuntimeError as error:
        raise HarnessContainmentError(f"{label} cannot traverse a symlink") from error
    try:
        target.relative_to(canonical_root)
    except ValueError as error:
        raise HarnessContainmentError(
            f"{label} must remain within workspace"
        ) from error
    return target


def load_harness_document(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict):
        raise ValueError("harness definition must be a mapping")
    if findings := validate_harness_document_domain(document):
        raise HarnessDefinitionValidationError(findings)
    return document


def _prompt_resources(
    compilation: HarnessCompilation,
    paths: dict[str, Path],
    workspace: Path,
    contained: bool,
) -> dict[str, bytes]:
    sources = {
        layer["layer_id"]: layer.get("source_ref")
        for layer in compilation.lock["source_layers"]
    }
    resources: dict[str, bytes] = {}
    for row in compilation.lock["effective_values"]:
        if not row["path"].startswith("prompts.packs."):
            continue
        declared = row["value"]
        if not isinstance(declared, str):
            continue
        source_ref = sources.get(row["source_layer_id"])
        source_path = paths.get(source_ref)
        if source_path is None:
            raise ValueError(f"prompt resource source is unavailable: {source_ref}")
        candidate = Path(declared).expanduser()
        if contained and candidate.is_absolute():
            raise HarnessContainmentError("harness resource reference must be relative")
        unresolved = (
            candidate if candidate.is_absolute() else source_path.parent / candidate
        )
        target = (
            resolve_contained_target(
                unresolved,
                workspace,
                "harness resource",
                strict=True,
            )
            if contained
            else unresolved.resolve(strict=True)
        )
        if not target.is_file():
            raise HarnessResourceInvalidError(
                f"harness resource is not a file: {declared}"
            )
        resource_ref = f"{source_ref}::{declared}"
        content = target.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HarnessResourceInvalidError(
                f"harness prompt resource is not UTF-8: {declared}"
            ) from error
        prior = resources.setdefault(resource_ref, content)
        if prior != content:
            raise ValueError(f"resource identity collision: {resource_ref}")
    return resources


def _daily_driver_role_resources(
    path: Path,
    workspace: Path,
    contained: bool,
) -> dict[str, bytes]:
    temporary_prefix = f".{DAILY_DRIVER_TEMPLATE_NAME}."
    if path.name != DAILY_DRIVER_TEMPLATE_NAME and not path.name.startswith(
        temporary_prefix
    ):
        return {}
    unresolved = path.parent / DAILY_DRIVER_MODEL_ROLES_NAME
    target = (
        resolve_contained_target(
            unresolved,
            workspace,
            "harness resource",
            strict=True,
        )
        if contained
        else unresolved.resolve(strict=True)
    )
    if not target.is_file():
        raise IsADirectoryError(
            f"harness resource is not a file: {DAILY_DRIVER_MODEL_ROLES_NAME}"
        )
    load_daily_driver_model_roles(target)
    source_ref = portable_ref(path, workspace)
    return {f"{source_ref}::{DAILY_DRIVER_MODEL_ROLES_NAME}": target.read_bytes()}


def compile_harness_source(
    path: str | Path,
    workspace: str | Path,
    contained: bool = False,
) -> HarnessCompilation:
    source_path = Path(path)
    workspace_path = Path(workspace).resolve()
    if contained:
        if source_path.is_symlink():
            raise HarnessContainmentError("harness source cannot be a symlink")
        try:
            resolved_root = source_path.resolve(strict=True)
        except RuntimeError as error:
            raise HarnessContainmentError(
                "harness source cannot traverse a symlink"
            ) from error
        try:
            resolved_root.relative_to(workspace_path)
        except ValueError as error:
            raise HarnessContainmentError(
                "harness source must remain within workspace"
            ) from error
        source_path = resolved_root
    source_ref = portable_ref(source_path, workspace_path)
    paths = {source_ref: source_path}

    def load_ref(parent: str, declared: str) -> tuple[str, dict[str, Any]]:
        declared_path = Path(declared)
        if contained and declared_path.is_absolute():
            raise HarnessContainmentError("harness reference must be relative")
        unresolved = (
            declared_path
            if declared_path.is_absolute()
            else paths[parent].parent / declared_path
        )
        target = (
            resolve_contained_target(
                unresolved,
                workspace_path,
                "harness reference",
                strict=True,
            )
            if contained
            else unresolved.resolve()
        )
        resolved = portable_ref(target, workspace_path)
        if resolved in paths and paths[resolved] != target:
            raise ValueError(f"reference identity collision: {resolved}")
        paths[resolved] = target
        return resolved, load_harness_document(target)

    document = load_harness_document(source_path)
    compilation = compile_harness_definition(
        document,
        source_ref=source_ref,
        load_ref=load_ref,
    )
    parse_harness_definition(compilation.resolved_author_dict())
    resources = _prompt_resources(
        compilation,
        paths,
        workspace_path,
        contained,
    )
    roles = _daily_driver_role_resources(
        source_path,
        workspace_path,
        contained,
    )
    if overlap := resources.keys() & roles.keys():
        raise ValueError(f"resource identity collision: {sorted(overlap)[0]}")
    resources.update(roles)
    return compilation.with_resource_inputs(resources)
