from __future__ import annotations
import os

from pathlib import Path
from typing import Literal


ReferenceNamespace = Literal["repo", "workspace_evidence"]


class ReferenceResolutionError(ValueError):
    pass


def _validated_workspace_root(value: str | Path, *, source: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    if not candidate.is_dir() or not (candidate / "docs_tmp").is_dir():
        raise ReferenceResolutionError(
            f"{source} must name an existing workspace containing docs_tmp: "
            f"{candidate}"
        )
    return candidate


def workspace_root_for_checkout(checkout_root: Path) -> Path:
    """Return the required explicitly provisioned workspace root."""
    override = os.environ.get("BB_WORKSPACE_ROOT")
    if not override:
        raise ReferenceResolutionError(
            "BB_WORKSPACE_ROOT is required for workspace evidence references; "
            f"checkout={checkout_root.resolve()}"
        )
    return _validated_workspace_root(override, source="BB_WORKSPACE_ROOT")


def resolve_declared_reference(
    reference: str | Path,
    *,
    checkout_root: Path,
    namespace: ReferenceNamespace,
    label: str = "declared reference",
    workspace_root: Path | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve a declared input without probing ancestor checkouts.

    Repo inputs are always checkout-relative. Workspace evidence uses the
    explicit checkout/workspace split: ``docs_tmp`` and checkout-qualified
    references are workspace-relative; other relative references remain
    checkout-relative.
    """
    raw = str(reference).split("#", 1)[0]
    path = Path(raw)
    checkout = checkout_root.resolve()
    workspace: Path | None = None

    if namespace == "repo":
        if path.is_absolute() or ".." in path.parts:
            raise ReferenceResolutionError(
                f"{label} must be relative to checkout {checkout}: {reference}"
            )
        resolved = (checkout / path).resolve()
        boundary = checkout
    elif namespace == "workspace_evidence":
        workspace = (
            _validated_workspace_root(
                workspace_root,
                source="explicit workspace root",
            )
            if workspace_root is not None
            else workspace_root_for_checkout(checkout)
        )
        if path.is_absolute():
            resolved = path.resolve()
        elif path.parts and path.parts[0] in {"docs_tmp", checkout.name}:
            resolved = (workspace / path).resolve()
        else:
            resolved = (checkout / path).resolve()
        if resolved.is_relative_to(checkout):
            boundary = checkout
        elif resolved.is_relative_to(workspace):
            boundary = workspace
        else:
            raise ReferenceResolutionError(
                f"{label} escapes checkout/workspace boundary: {reference}"
            )
    else:
        raise ReferenceResolutionError(f"unsupported reference namespace: {namespace}")

    if not resolved.is_relative_to(boundary):
        raise ReferenceResolutionError(f"{label} escapes {boundary}: {reference}")
    if must_exist and not resolved.exists():
        raise ReferenceResolutionError(
            f"{label} is missing from checkout/workspace {boundary}: {reference}"
        )
    return resolved
