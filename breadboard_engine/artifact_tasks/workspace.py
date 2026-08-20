from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping


PROTECTED_NAMES = {"/", str(Path.home()), "/tmp", "/var", "/usr", "/bin", "/etc"}


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


def _reject_protected(path: Path, *, field_name: str) -> Path:
    resolved = path.resolve()
    if str(resolved) in PROTECTED_NAMES:
        raise ValueError(f"{field_name} points at a protected root: {resolved}")
    if len(resolved.parts) < 3:
        raise ValueError(f"{field_name} is too broad to use safely: {resolved}")
    return resolved


def prepare_workspace_bridge(spec: WorkspaceBridgeSpec) -> WorkspaceBridgeResult:
    workspace = _reject_protected(Path(spec.disposable_workspace), field_name="disposable_workspace")
    template = _reject_protected(Path(spec.workspace_template), field_name="workspace_template") if spec.workspace_template else None
    export_dir = _reject_protected(Path(spec.export_dir), field_name="export_dir") if spec.export_dir else None

    if workspace.exists():
        if not spec.overwrite_disposable:
            raise ValueError(f"disposable workspace already exists: {workspace}")
        shutil.rmtree(workspace)
    if template:
        if not template.exists() or not template.is_dir():
            raise ValueError(f"workspace template must be an existing directory: {template}")
        shutil.copytree(template, workspace, symlinks=False)
        copied = True
    else:
        workspace.mkdir(parents=True, exist_ok=False)
        copied = False
    if export_dir:
        export_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceBridgeResult(
        workspace_root=str(workspace),
        template_root=str(template) if template else None,
        export_dir=str(export_dir) if export_dir else None,
        copied=copied,
        metadata=dict(spec.metadata),
    )
