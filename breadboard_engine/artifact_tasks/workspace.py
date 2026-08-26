from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping
from breadboard_engine.security import protected_credential_paths

from breadboard_engine.security.workspace_files import (
    WorkspaceFilesystem,
    WorkspacePathError,
)


PROTECTED_NAMES = {
    str(Path(path).resolve(strict=False))
    for path in (
        "/",
        Path.home(),
        "/tmp",
        "/var",
        "/usr",
        "/bin",
        "/etc",
    )
}

_DISPOSABLE_MARKER = ".breadboard-disposable-workspace"
_DISPOSABLE_MARKER_CONTENT = "breadboard.artifact-workspace.v1\n"


@dataclass(frozen=True)
class WorkspaceBridgeSpec:
    workspace_template: str | None
    disposable_workspace: str
    export_dir: str | None = None
    overwrite_disposable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.disposable_workspace or "").strip():
            raise ValueError("disposable_workspace must be non-empty")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class WorkspaceBridgeResult:
    workspace_root: str
    template_root: str | None
    export_dir: str | None
    copied: bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "template_root": self.template_root,
            "export_dir": self.export_dir,
            "copied": self.copied,
            "metadata": dict(self.metadata),
        }


def _canonical_target(path: Path, *, field_name: str) -> Path:
    try:
        lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    parent = lexical.parent
    missing: list[str] = []
    while not os.path.lexists(parent):
        if parent == parent.parent:
            raise ValueError(f"{field_name} parent is unavailable")
        missing.append(parent.name)
        parent = parent.parent
    try:
        canonical_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{field_name} parent is unavailable") from exc
    for component in reversed(missing):
        canonical_parent /= component
    return canonical_parent / lexical.name


def _reject_protected(path: Path, *, field_name: str) -> Path:
    target = _canonical_target(path, field_name=field_name)
    if str(target) in PROTECTED_NAMES:
        raise ValueError(f"{field_name} points at a protected root: {target}")
    if len(target.parts) < 3:
        raise ValueError(f"{field_name} is too broad to use safely: {target}")
    return target


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def prepare_workspace_bridge(spec: WorkspaceBridgeSpec) -> WorkspaceBridgeResult:
    workspace = _reject_protected(
        Path(spec.disposable_workspace),
        field_name="disposable_workspace",
    )
    template = (
        _reject_protected(
            Path(spec.workspace_template),
            field_name="workspace_template",
        )
        if spec.workspace_template
        else None
    )
    export_dir = (
        _reject_protected(
            Path(spec.export_dir),
            field_name="export_dir",
        )
        if spec.export_dir
        else None
    )
    named_paths = [
        (name, path)
        for name, path in (
            ("disposable_workspace", workspace),
            ("workspace_template", template),
            ("export_dir", export_dir),
        )
        if path is not None
    ]
    protected_paths = tuple(
        Path(path).resolve(strict=False) for path in protected_credential_paths()
    )
    for name, path in named_paths:
        if any(_paths_overlap(path, protected) for protected in protected_paths):
            raise ValueError(f"{name} overlaps a protected credential path")
    for index, (left_name, left) in enumerate(named_paths):
        for right_name, right in named_paths[index + 1 :]:
            if _paths_overlap(left, right):
                raise ValueError(f"{left_name} and {right_name} must not overlap")

    try:
        with WorkspaceFilesystem.open_anchored_root(
            workspace.parent,
            create=True,
        ) as workspace_parent:
            if workspace_parent.exists(workspace.name):
                if not spec.overwrite_disposable:
                    raise ValueError(
                        f"disposable workspace already exists: {workspace}"
                    )
                with WorkspaceFilesystem.open_anchored_root(workspace) as existing:
                    try:
                        marker = existing.read_text(_DISPOSABLE_MARKER)
                    except (FileNotFoundError, WorkspacePathError) as exc:
                        raise ValueError(
                            "refusing to overwrite an unowned workspace"
                        ) from exc
                    if marker != _DISPOSABLE_MARKER_CONTENT:
                        raise ValueError("refusing to overwrite an unowned workspace")
                workspace_parent.remove_tree(workspace.name)
            workspace_parent.create_directory(workspace.name)

            if template is not None:
                try:
                    with WorkspaceFilesystem.open_anchored_root(template) as source:
                        with WorkspaceFilesystem.open_anchored_root(
                            workspace
                        ) as destination:
                            source.copy_tree_to(destination)
                            destination.write_text(
                                _DISPOSABLE_MARKER,
                                _DISPOSABLE_MARKER_CONTENT,
                                overwrite=False,
                            )
                except Exception:
                    workspace_parent.remove_tree(workspace.name)
                    raise
                copied = True
            else:
                with WorkspaceFilesystem.open_anchored_root(workspace) as destination:
                    destination.write_text(
                        _DISPOSABLE_MARKER,
                        _DISPOSABLE_MARKER_CONTENT,
                        overwrite=False,
                    )
                copied = False

        if export_dir is not None:
            with WorkspaceFilesystem.open_anchored_root(
                export_dir,
                create=True,
            ):
                pass
    except WorkspacePathError as exc:
        raise ValueError(f"workspace boundary rejected path: {exc.code}") from exc

    return WorkspaceBridgeResult(
        workspace_root=str(workspace),
        template_root=str(template) if template else None,
        export_dir=str(export_dir) if export_dir else None,
        copied=copied,
        metadata=dict(spec.metadata),
    )
