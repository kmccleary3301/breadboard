from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.compile import (
    HarnessCompilation,
    HarnessCompileError,
    _merge,
    compile_harness_definition,
)
from breadboard.product.harness.lock import configuration_artifact_id, sha256_bytes
from breadboard.product.harness.templates import (
    DAILY_DRIVER_MODEL_ROLES_NAME,
    DAILY_DRIVER_TEMPLATE_NAME,
    load_daily_driver_model_roles,
    load_daily_driver_model_roles_bytes,
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


def load_harness_document_bytes(content: bytes) -> dict[str, Any]:
    try:
        document = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError("harness definition is not valid UTF-8/YAML") from error
    if not isinstance(document, dict):
        raise ValueError("harness definition must be a mapping")
    if findings := validate_harness_document_domain(document):
        raise HarnessDefinitionValidationError(findings)
    return document


def load_harness_document(path: Path) -> dict[str, Any]:
    return load_harness_document_bytes(path.read_bytes())


def _prompt_resources(
    compilation: HarnessCompilation,
    paths: dict[str, Path],
    workspace: Path,
    contained: bool,
) -> dict[str, bytes]:
    graph = compilation.lock.configuration_graph
    sources = {
        layer["layer_id"]: layer.get("source_ref")
        for layer in graph["source_layers"]
    }
    resources: dict[str, bytes] = {}
    for row in graph["effective_values"]:
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


def _daily_driver_role_path(
    path: Path,
    workspace: Path,
    contained: bool,
) -> Path | None:
    temporary_prefix = f".{DAILY_DRIVER_TEMPLATE_NAME}."
    if path.name != DAILY_DRIVER_TEMPLATE_NAME and not path.name.startswith(
        temporary_prefix
    ):
        return None
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
    return target


def daily_driver_model_roles_for_harness(
    path: str | Path,
    workspace: str | Path,
    contained: bool = False,
) -> dict[str, Any] | None:
    """Load the model-role document paired with a daily-driver harness."""

    target = _daily_driver_role_path(
        Path(path),
        Path(workspace).resolve(),
        contained,
    )
    return None if target is None else load_daily_driver_model_roles(target)


def _daily_driver_role_resources(
    path: Path,
    workspace: Path,
    contained: bool,
) -> dict[str, bytes]:
    target = _daily_driver_role_path(path, workspace, contained)
    if target is None:
        return {}
    content = target.read_bytes()
    load_daily_driver_model_roles_bytes(content)
    source_ref = portable_ref(path, workspace)
    return {f"{source_ref}::{DAILY_DRIVER_MODEL_ROLES_NAME}": content}


def _publish_configuration_artifacts(
    compilation: HarnessCompilation,
    source_bytes: Mapping[str, bytes],
    resource_bytes: Mapping[str, bytes],
    cas: FilesystemCAS,
) -> dict[str, dict[str, Any]]:
    """Persist the exact source/resource bytes named by the final graph."""

    graph = compilation.lock.configuration_graph
    artifacts: dict[str, dict[str, Any]] = {}
    for layer in graph["source_layers"]:
        source_ref = layer.get("source_ref")
        layer_hash = layer.get("layer_hash")
        if not isinstance(source_ref, str) or not isinstance(layer_hash, str):
            continue
        if layer.get("scope") == "resource":
            content = resource_bytes.get(source_ref)
        else:
            content = source_bytes.get(source_ref)
        if content is None:
            raise HarnessResourceInvalidError(
                f"locked configuration source is unavailable: {source_ref}"
            )
        content_hash = sha256_bytes(content)
        artifact = cas.put_bytes(
            content,
            artifact_id=configuration_artifact_id(source_ref, content_hash),
            media_type="application/octet-stream",
            metadata={
                "layer_hash": layer_hash,
                "source_ref": source_ref,
                "content_sha256": content_hash,
            },
        )
        artifacts[source_ref] = artifact.to_dict()
    return artifacts


def compile_harness_source(
    path: str | Path,
    workspace: str | Path,
    contained: bool = False,
    *,
    cas: FilesystemCAS | None = None,
) -> HarnessCompilation:
    """Load declared source/package bytes, then invoke the pure compiler."""

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
    root_bytes = source_path.read_bytes()
    source_bytes = {source_ref: root_bytes}
    documents: dict[str, dict[str, Any]] = {}

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
        if resolved in documents:
            return resolved, documents[resolved]
        paths[resolved] = target
        content = target.read_bytes()
        source_bytes[resolved] = content
        document = load_harness_document_bytes(content)
        documents[resolved] = document
        return resolved, document
    document = load_harness_document_bytes(root_bytes)
    documents[source_ref] = document
    merged_modules: Any = {}
    merged_module_sources: Any = {}

    def collect_modules(
        current: Mapping[str, Any],
        reference: str,
        stack: tuple[str, ...],
    ) -> None:
        nonlocal merged_modules, merged_module_sources
        for declared in _declared_references(current):
            resolved, loaded = load_ref(reference, declared)
            if resolved in stack:
                raise HarnessCompileError(
                    "cyclic reference: " + " -> ".join((*stack, resolved))
                )
            collect_modules(loaded, resolved, (*stack, resolved))
        if "modules" in current:
            merged_modules, merged_module_sources = _merge(
                merged_modules,
                merged_module_sources,
                current["modules"],
                reference,
                metadata=False,
            )
    try:
        collect_modules(document, source_ref, (source_ref,))
    except HarnessContainmentError as error:
        raise HarnessCompileError(str(error)) from error
    module_declarations: dict[str, tuple[Mapping[str, Any], Path]] = {}
    if isinstance(merged_modules, Mapping):
        bindings = merged_modules.get("bindings")
        binding_sources = (
            merged_module_sources.get("bindings")
            if isinstance(merged_module_sources, Mapping)
            else None
        )
        if isinstance(bindings, Mapping):
            for name, binding in bindings.items():
                if not isinstance(name, str) or not isinstance(binding, Mapping):
                    continue
                sources = (
                    binding_sources.get(name)
                    if isinstance(binding_sources, Mapping)
                    else None
                )
                package_sources = (
                    sources.get("package") if isinstance(sources, Mapping) else None
                )
                source_provenance = (
                    package_sources.get("source")
                    if isinstance(package_sources, Mapping)
                    else None
                )
                if not (
                    isinstance(source_provenance, tuple)
                    and len(source_provenance) == 2
                    and isinstance(source_provenance[0], str)
                ):
                    raise HarnessCompileError(
                        f"module binding {name!r} package source is invalid"
                    )
                package_source = paths.get(source_provenance[0])
                if package_source is None:
                    raise HarnessCompileError(
                        f"module binding {name!r} package source is unavailable"
                    )
                module_declarations[name] = (binding, package_source.parent)

    owns_cas = False
    active_cas = cas
    if active_cas is None:
        active_cas = FilesystemCAS(
            workspace_path / ".breadboard" / "module-artifacts"
        )
        owns_cas = True
    try:
        packages: dict[str, Any] | None = None
        if module_declarations:
            if active_cas is None:
                raise HarnessCompileError("module package CAS is unavailable")
            from breadboard.product.harness.packages import load_module_package
            packages = {}
            for name in sorted(module_declarations):
                binding, package_base = module_declarations[name]
                package_ref = binding.get("package")
                if not isinstance(package_ref, Mapping):
                    raise HarnessCompileError(
                        f"module binding {name!r} package is invalid"
                    )
                declared_source = package_ref.get("source")
                expected_digest = package_ref.get("digest")
                if not isinstance(declared_source, str) or not declared_source.strip():
                    raise HarnessCompileError(
                        f"module binding {name!r} package source is invalid"
                    )
                if not isinstance(expected_digest, str):
                    raise HarnessCompileError(
                        f"module binding {name!r} package digest is invalid"
                    )
                source = Path(declared_source)
                if contained and source.is_absolute():
                    raise HarnessContainmentError(
                        "module package reference must be relative"
                    )
                unresolved = (
                    source
                    if source.is_absolute()
                    else package_base / source
                )
                package_path = (
                    resolve_contained_target(
                        unresolved,
                        workspace_path,
                        "module package",
                        strict=True,
                    )
                    if contained
                    else unresolved.resolve(strict=True)
                )
                packages[name] = load_module_package(
                    package_path,
                    expected_digest,
                    cas=active_cas,
                )
        compilation = compile_harness_definition(
            document,
            source_ref=source_ref,
            load_ref=load_ref,
            packages=packages,
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
        resources.update(roles)
        compilation = compilation.with_resource_inputs(resources)
        artifact_refs = _publish_configuration_artifacts(
            compilation,
            source_bytes,
            resources,
            active_cas,
        )
        return compilation.with_configuration_artifacts(artifact_refs)
    finally:
        if owns_cas:
            active_cas.close()


def _declared_references(document: Mapping[str, Any]) -> tuple[str, ...]:
    value = document.get("extends")
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple)) else (value,)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise HarnessCompileError("invalid reference")
    return tuple(values)
