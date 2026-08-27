from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

EXIT_OK = 0
EXIT_VALIDATION_FAILURE = 2
EXIT_RESOLUTION_FAILURE = 3
EXIT_RUNTIME_FAILURE = 4
EXIT_LOCK_DRIFT = 5
EXIT_BLOCKED = 6


@dataclass(frozen=True, slots=True)
class OperationContext:
    workspace: Path
    enabled_extensions: frozenset[str] = frozenset()


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
            str(error),
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
    return OperationResult.failure(
        command,
        EXIT_RUNTIME_FAILURE,
        "runtime_failure",
        str(error) or error.__class__.__name__,
        stage,
    )
