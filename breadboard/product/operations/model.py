from __future__ import annotations

import sysconfig

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

EXIT_OK = 0
EXIT_VALIDATION_FAILURE = 2
EXIT_RESOLUTION_FAILURE = 3
EXIT_RUNTIME_FAILURE = 4
EXIT_LOCK_DRIFT = 5
EXIT_BLOCKED = 6

WorkspacePathPolicy = Literal["explicit-local", "contained-public"]


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _installed_data_root() -> Path:
    return Path(sysconfig.get_path("data"))


@dataclass(frozen=True, slots=True)
class OperationContext:
    workspace: Path
    path_policy: WorkspacePathPolicy = "explicit-local"
    reference_root: Path = field(default_factory=Path.cwd)
    protected_roots: tuple[Path, ...] = ()
    capabilities: frozenset[str] = frozenset()
    enabled_extensions: frozenset[str] = frozenset()
    source_root: Path = field(default_factory=_source_root)
    installed_data_root: Path = field(default_factory=_installed_data_root)

    @property
    def contained(self) -> bool:
        return self.path_policy == "contained-public"

    def resolve_path(self, reference: str | Path) -> Path:
        if self.path_policy == "explicit-local":
            candidate = Path(reference).expanduser()
            if not candidate.is_absolute():
                candidate = self.reference_root / candidate
            return candidate.resolve()

        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("resource identifier must be workspace-relative")
        candidate = self.workspace.joinpath(relative)
        if any(
            self.workspace.joinpath(*relative.parts[:index]).is_symlink()
            for index in range(1, len(relative.parts) + 1)
        ):
            raise ValueError("resource identifier cannot traverse a symlink")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.workspace) or any(
            resolved == root or resolved.is_relative_to(root)
            for root in self.protected_roots
        ):
            raise PermissionError(
                "public operations cannot address maintainer evidence trees"
            )
        return resolved

    def installed_resource(self, relative: str | Path) -> Path:
        source_path = self.source_root / relative
        if source_path.exists():
            return source_path
        return self.installed_data_root / relative


def portable_ref(
    path: str | Path,
    workspace: str | Path | None = None,
) -> str:
    candidate = Path(path).expanduser().resolve()
    root = Path(workspace or Path.cwd()).expanduser().resolve()
    try:
        return candidate.relative_to(root).as_posix() or "."
    except ValueError:
        return candidate.name


def _problem(
    code: str,
    message: str,
    stage: str | None = None,
    hint: str | None = None,
    refs: Sequence[str] = (),
    next_actions: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": "bb.problem.v1",
        "error_code": code,
        "message": message,
        "record_refs": list(refs),
        "failed_stage": stage,
        "hint": hint,
        "next_actions": list(next_actions),
    }


@dataclass(slots=True)
class OperationResult:
    command: Sequence[str]
    ok: bool = True
    exit_code: int = EXIT_OK
    record_refs: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    stage_outcomes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.cli.result.v1",
            "ok": self.ok,
            "status": "ok" if self.ok else "error",
            "command": list(self.command),
            "record_refs": self.record_refs,
            "hashes": dict(sorted(self.hashes.items())),
            "stage_outcomes": self.stage_outcomes,
            "warnings": self.warnings,
            "next_actions": self.next_actions,
            "error": self.error,
            "exit_code": self.exit_code,
            "data": self.data,
        }

    @classmethod
    def success(
        cls,
        command: Sequence[str],
        data: Mapping[str, Any] | None = None,
        refs: Sequence[str] = (),
        hashes: Mapping[str, str] | None = None,
        next_actions: Sequence[str] = (),
        stage: str = "command",
    ) -> OperationResult:
        return cls(
            command,
            record_refs=list(refs),
            hashes=dict(hashes or {}),
            stage_outcomes=[
                {
                    "stage": stage,
                    "status": "passed",
                    "report_ref": None,
                    "next_action": None,
                }
            ],
            next_actions=list(next_actions),
            data=dict(data or {}),
        )

    @classmethod
    def failure(
        cls,
        command: Sequence[str],
        code: int,
        error_code: str,
        message: str,
        failed_stage: str | None = None,
        hint: str | None = None,
        refs: Sequence[str] = (),
        next_actions: Sequence[str] = (),
        data: Mapping[str, Any] | None = None,
        status: str = "failed",
    ) -> OperationResult:
        return cls(
            command,
            False,
            code,
            list(refs),
            stage_outcomes=[
                {
                    "stage": failed_stage or "command",
                    "status": "blocked" if status == "blocked" else "failed",
                    "report_ref": None,
                    "next_action": next_actions[0] if next_actions else None,
                }
            ],
            next_actions=list(next_actions),
            error=_problem(
                error_code,
                message,
                failed_stage,
                hint,
                refs,
                next_actions,
            ),
            data=dict(data or {}),
        )


def from_exception(
    command: Sequence[str],
    error: BaseException,
    stage: str = "command",
) -> OperationResult:
    if isinstance(error, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return OperationResult.failure(
            command,
            EXIT_RESOLUTION_FAILURE,
            "path_unavailable",
            "path is unavailable",
            stage,
            "Check the workspace-relative path.",
            next_actions=["breadboard system health"],
        )
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return OperationResult.failure(
            command,
            EXIT_VALIDATION_FAILURE,
            "invalid_state",
            str(error),
            stage,
            "Validate the input before retrying.",
            next_actions=["breadboard system describe"],
        )
    message = (
        "internal runtime failure"
        if isinstance(error, OSError)
        else str(error) or error.__class__.__name__
    )
    return OperationResult.failure(
        command,
        EXIT_RUNTIME_FAILURE,
        "runtime_failure",
        message,
        stage,
    )
