from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence
from ..security import (
    WorkspaceFilesystem,
    WorkspacePathError,
    build_child_environment,
    build_restricted_process_command,
    contains_provider_credential_value,
    provider_credential_values,
    protected_credential_paths,
    redaction,
)

from .contracts import safe_relative_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")



def _lexical_absolute(path: str | os.PathLike[str]) -> Path:
    try:
        return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspacePathError("output_path_invalid") from exc


def _path_forms(path: str | os.PathLike[str]) -> tuple[Path, Path]:
    lexical = _lexical_absolute(path)
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspacePathError("output_path_unavailable") from exc
    return lexical, resolved


def _paths_overlap(left: Path, right: Path) -> bool:
    for candidate, root in ((left, right), (right, left)):
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            pass
    return False


def validate_output_destination(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None,
    protected_paths: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Reject output paths that overlap workspace or protected locations."""
    lexical, resolved = _path_forms(path)
    roots: list[tuple[Path, Path]] = []
    if workspace_root is not None:
        roots.append(_path_forms(workspace_root))
    roots.extend(_path_forms(item) for item in protected_paths)
    for root_lexical, root_resolved in roots:
        if _paths_overlap(lexical, root_lexical) or _paths_overlap(resolved, root_resolved):
            raise WorkspacePathError("output_path_overlaps_trusted_boundary")
    return lexical


def _secure_output_filesystem(
    path: str | os.PathLike[str],
    *,
    workspace_root: str | os.PathLike[str] | None,
    protected_paths: Sequence[str | os.PathLike[str]] = (),
) -> WorkspaceFilesystem:
    lexical = validate_output_destination(
        path,
        workspace_root=workspace_root,
        protected_paths=protected_paths,
    )
    filesystem = WorkspaceFilesystem.open_anchored_root(
        lexical,
        create=True,
    )
    try:
        validate_output_destination(
            filesystem.root,
            workspace_root=workspace_root,
            protected_paths=protected_paths,
        )
    except BaseException:
        filesystem.close()
        raise
    return filesystem


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


@dataclass(frozen=True)
class EvaluatorSpec:
    name: str
    command: Sequence[str] | str
    cwd: str | None = None
    timeout_seconds: float = 30.0
    env: Mapping[str, str] = field(default_factory=dict)
    shell: bool = False
    required: bool = True

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        name = str(
            safe_relative_path(
                str(self.name or "").strip(),
                field_name="name",
            )
        )
        if not self.command:
            raise ValueError("command must be non-empty")
        if float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        object.__setattr__(
            self, "env", {str(k): str(v) for k, v in dict(self.env or {}).items()}
        )
        if self.cwd:
            object.__setattr__(
                self, "cwd", str(safe_relative_path(self.cwd, field_name="cwd"))
            )

    def command_for_subprocess(self) -> Sequence[str] | str:
        if self.shell:
            return (
                self.command
                if isinstance(self.command, str)
                else " ".join(shlex.quote(str(item)) for item in self.command)
            )
        if isinstance(self.command, str):
            return shlex.split(self.command)
        return [str(item) for item in self.command]

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "command": (
                self.command if isinstance(self.command, str) else list(self.command)
            ),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "env": dict(self.env),
            "shell": self.shell,
            "required": self.required,
        }
        with redaction.secret_value_scope(
            *provider_credential_values(),
            *provider_credential_values(self.env),
        ):
            scrubbed, _ = redaction.scrub_structure(payload)
        return scrubbed if isinstance(scrubbed, dict) else {}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EvaluatorSpec":
        return EvaluatorSpec(
            name=str(data.get("name") or ""),
            command=data.get("command") or [],
            cwd=data.get("cwd"),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            env=dict(data.get("env") or {}),
            shell=bool(data.get("shell", False)),
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class EvaluatorResult:
    name: str
    status: str
    exit_code: int | None
    duration_seconds: float
    command: Sequence[str] | str
    cwd: str
    required: bool
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    started_at: str = ""
    finished_at: str = ""
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "command": self.command
            if isinstance(self.command, str)
            else list(self.command),
            "cwd": self.cwd,
            "required": self.required,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reasons": list(self.failure_reasons),
        }


def _safe_cwd(root: Path, cwd: str | None) -> Path:
    root_path = root.resolve()
    if not cwd:
        return root_path
    rel = safe_relative_path(cwd, field_name="cwd")
    full = (root_path / rel).resolve()
    try:
        full.relative_to(root_path)
    except ValueError:
        raise ValueError(f"evaluator cwd escapes root: {cwd}") from None
    return full


def run_evaluator(
    spec: EvaluatorSpec,
    *,
    root: Path,
    output_dir: Path,
) -> EvaluatorResult:
    protected_paths = protected_credential_paths()
    cwd = _safe_cwd(root, spec.cwd)
    with _secure_output_filesystem(
        output_dir,
        workspace_root=None,
        protected_paths=protected_paths,
    ) as output_filesystem:
        return _run_evaluator(
            spec,
            root=root,
            cwd=cwd,
            output_filesystem=output_filesystem,
            protected_paths=protected_paths,
        )


def _run_evaluator(
    spec: EvaluatorSpec,
    *,
    root: Path,
    cwd: Path,
    output_filesystem: WorkspaceFilesystem,
    protected_paths: Sequence[str | os.PathLike[str]],
) -> EvaluatorResult:
    started = _utc_now()
    start_time = time.monotonic()
    stdout_path = Path(output_filesystem.display_path("stdout.txt"))
    stderr_path = Path(output_filesystem.display_path("stderr.txt"))
    command = spec.command_for_subprocess()
    secret_values = (
        *provider_credential_values(),
        *provider_credential_values(spec.env),
    )
    with redaction.secret_value_scope(*secret_values):
        scrubbed_command, _ = redaction.scrub_structure(
            command,
            path="$.evaluator.command",
        )
        public_command = (
            scrubbed_command if isinstance(scrubbed_command, (str, list, tuple)) else []
        )
        if contains_provider_credential_value(
            spec.env,
            values=secret_values,
        ):
            error_text = (
                "evaluator environment rejected: provider credential "
                "in child environment"
            )
            output_filesystem.write_text("stdout.txt", "", encoding="utf-8")
            output_filesystem.write_text(
                "stderr.txt", f"{error_text}\n", encoding="utf-8"
            )
            return EvaluatorResult(
                name=spec.name,
                status="error",
                exit_code=None,
                duration_seconds=time.monotonic() - start_time,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stderr_tail=error_text,
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=("credential_in_environment",),
            )
        try:
            env = build_child_environment(overrides=spec.env)
        except ValueError:
            error_text = (
                "evaluator environment rejected: override key is not allowlisted"
            )
            output_filesystem.write_text("stdout.txt", "", encoding="utf-8")
            output_filesystem.write_text(
                "stderr.txt", f"{error_text}\n", encoding="utf-8"
            )
            return EvaluatorResult(
                name=spec.name,
                status="error",
                exit_code=None,
                duration_seconds=time.monotonic() - start_time,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stderr_tail=error_text,
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=("environment_override_not_allowed",),
            )
        if contains_provider_credential_value(
            command,
            values=secret_values,
        ):
            error_text = "evaluator command rejected: provider credential in argv"
            output_filesystem.write_text("stdout.txt", "", encoding="utf-8")
            output_filesystem.write_text(
                "stderr.txt", f"{error_text}\n", encoding="utf-8"
            )
            return EvaluatorResult(
                name=spec.name,
                status="error",
                exit_code=None,
                duration_seconds=time.monotonic() - start_time,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stderr_tail=error_text,
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=("credential_in_argv",),
            )
        try:
            isolated_command, env = build_restricted_process_command(
                command,
                workspace=root,
                working_directory=cwd,
                shell=spec.shell,
                environment=env,
                protected_paths=protected_paths,
            )
            proc = subprocess.run(
                isolated_command,
                cwd=str(cwd),
                env=env,
                shell=False,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
            duration = time.monotonic() - start_time
            stdout = redaction.scrub_text(proc.stdout or "")
            stderr = redaction.scrub_text(proc.stderr or "")
            output_filesystem.write_text("stdout.txt", stdout, encoding="utf-8")
            output_filesystem.write_text("stderr.txt", stderr, encoding="utf-8")
            status = "passed" if proc.returncode == 0 else "failed"
            failures = () if proc.returncode == 0 else ("nonzero_exit",)
            return EvaluatorResult(
                name=spec.name,
                status=status,
                exit_code=proc.returncode,
                duration_seconds=duration,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=failures,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            stdout = (
                exc.stdout
                if isinstance(exc.stdout, str)
                else (exc.stdout or b"").decode("utf-8", errors="replace")
            )
            stderr = (
                exc.stderr
                if isinstance(exc.stderr, str)
                else (exc.stderr or b"").decode("utf-8", errors="replace")
            )
            stdout = redaction.scrub_text(stdout or "")
            stderr = redaction.scrub_text(stderr or "")
            output_filesystem.write_text("stdout.txt", stdout, encoding="utf-8")
            output_filesystem.write_text("stderr.txt", stderr, encoding="utf-8")
            return EvaluatorResult(
                name=spec.name,
                status="timeout",
                exit_code=None,
                duration_seconds=duration,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=("timeout",),
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            error_text = redaction.safe_exception_message(
                exc,
                operation="evaluator",
            )
            output_filesystem.write_text(
                "stderr.txt", f"{error_text}\n", encoding="utf-8"
            )
            output_filesystem.write_text("stdout.txt", "", encoding="utf-8")
            return EvaluatorResult(
                name=spec.name,
                status="error",
                exit_code=None,
                duration_seconds=duration,
                command=public_command,
                cwd=str(cwd),
                required=spec.required,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                stderr_tail=error_text,
                started_at=started,
                finished_at=_utc_now(),
                failure_reasons=("infrastructure_error",),
            )


def run_evaluators(
    specs: Sequence[EvaluatorSpec], *, root: Path, output_dir: Path
) -> tuple[EvaluatorResult, ...]:
    results: list[EvaluatorResult] = []
    for spec in specs:
        results.append(
            run_evaluator(spec, root=root, output_dir=output_dir / spec.name)
        )
    return tuple(results)
