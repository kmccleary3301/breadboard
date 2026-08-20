from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .contracts import safe_relative_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        if not name:
            raise ValueError("name must be non-empty")
        if not self.command:
            raise ValueError("command must be non-empty")
        if float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        object.__setattr__(self, "env", {str(k): str(v) for k, v in dict(self.env or {}).items()})
        if self.cwd:
            object.__setattr__(self, "cwd", str(safe_relative_path(self.cwd, field_name="cwd")))

    def command_for_subprocess(self) -> Sequence[str] | str:
        if self.shell:
            return self.command if isinstance(self.command, str) else " ".join(shlex.quote(str(item)) for item in self.command)
        if isinstance(self.command, str):
            return shlex.split(self.command)
        return [str(item) for item in self.command]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command if isinstance(self.command, str) else list(self.command),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "env": dict(self.env),
            "shell": self.shell,
            "required": self.required,
        }

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
            "command": self.command if isinstance(self.command, str) else list(self.command),
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


def run_evaluator(spec: EvaluatorSpec, *, root: Path, output_dir: Path) -> EvaluatorResult:
    started = _utc_now()
    start_time = time.monotonic()
    cwd = _safe_cwd(root, spec.cwd)
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.txt"
    stderr_path = output_dir / "stderr.txt"
    command = spec.command_for_subprocess()
    env = os.environ.copy()
    env.update(spec.env)
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            shell=spec.shell,
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
        duration = time.monotonic() - start_time
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        status = "passed" if proc.returncode == 0 else "failed"
        failures = () if proc.returncode == 0 else ("nonzero_exit",)
        return EvaluatorResult(
            name=spec.name,
            status=status,
            exit_code=proc.returncode,
            duration_seconds=duration,
            command=command,
            cwd=str(cwd),
            required=spec.required,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            stdout_tail=_tail(proc.stdout or ""),
            stderr_tail=_tail(proc.stderr or ""),
            started_at=started,
            finished_at=_utc_now(),
            failure_reasons=failures,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start_time
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        return EvaluatorResult(
            name=spec.name,
            status="timeout",
            exit_code=None,
            duration_seconds=duration,
            command=command,
            cwd=str(cwd),
            required=spec.required,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            stdout_tail=_tail(stdout or ""),
            stderr_tail=_tail(stderr or ""),
            started_at=started,
            finished_at=_utc_now(),
            failure_reasons=("timeout",),
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        stderr_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        stdout_path.write_text("", encoding="utf-8")
        return EvaluatorResult(
            name=spec.name,
            status="error",
            exit_code=None,
            duration_seconds=duration,
            command=command,
            cwd=str(cwd),
            required=spec.required,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            stderr_tail=f"{type(exc).__name__}: {exc}",
            started_at=started,
            finished_at=_utc_now(),
            failure_reasons=("infrastructure_error",),
        )


def run_evaluators(specs: Sequence[EvaluatorSpec], *, root: Path, output_dir: Path) -> tuple[EvaluatorResult, ...]:
    results: list[EvaluatorResult] = []
    for spec in specs:
        results.append(run_evaluator(spec, root=root, output_dir=output_dir / spec.name))
    return tuple(results)
