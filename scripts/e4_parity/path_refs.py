from __future__ import annotations

from pathlib import Path


class ReferenceResolutionError(ValueError):
    pass


def resolve_declared_reference(
    reference: str | Path,
    *,
    checkout_root: Path,
    namespace: str = "repo",
    label: str = "declared reference",
    must_exist: bool = True,
) -> Path:
    """Resolve a repo-owned input beneath the active checkout only."""
    if namespace != "repo":
        raise ReferenceResolutionError(
            f"unsupported reference namespace in workspace-free resolver: {namespace}"
        )
    path = Path(str(reference).split("#", 1)[0])
    checkout = checkout_root.resolve()
    if path.is_absolute() or ".." in path.parts:
        raise ReferenceResolutionError(
            f"{label} must be relative to checkout {checkout}: {reference}"
        )
    resolved = (checkout / path).resolve()
    if not resolved.is_relative_to(checkout):
        raise ReferenceResolutionError(
            f"{label} escapes checkout {checkout}: {reference}"
        )
    if must_exist and not resolved.exists():
        raise ReferenceResolutionError(
            f"{label} is missing from checkout {checkout}: {reference}"
        )
    return resolved
