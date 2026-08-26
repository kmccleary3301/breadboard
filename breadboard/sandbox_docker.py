from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ray
from breadboard_engine.security import (
    build_child_environment,
    contains_provider_credential_value,
    provider_credential_values,
    protected_credential_paths,
    ProcessIsolationUnavailable,
    purge_provider_credentials,
    WorkspaceFilesystem,
    WorkspacePathError,
    redaction,
    validate_workspace_credential_boundary,
)

from .adaptive_iter import ADAPTIVE_PREFIX_ITERABLE


@ray.remote
class DockerSandboxV2:
    """Sandbox that runs shell commands inside a Docker container.

    File operations are still performed on the host filesystem but are scoped to the
    provided workspace root. `run_shell` is executed via `docker run --rm` with the
    workspace bind-mounted at `/workspace`.

    This actor intentionally does not inherit from `DevSandboxV2`: Ray actor
    classes cannot subclass other Ray actor classes.
    """

    def __init__(
        self,
        image: str,
        session_id: str = "",
        workspace: str = "",
        lsp_actor: Any = None,
        *,
        network: str = "none",
        runtime: str | None = None,
        docker_bin: str | None = None,
        protected_paths: Optional[Sequence[str | os.PathLike[str]]] = None,
        purge_process_environment: bool = True,
    ) -> None:
        normalized_network = str(network or "none").strip().lower()
        if normalized_network != "none":
            raise ProcessIsolationUnavailable(
                "Docker model sandbox network must be disabled"
            )
        self._protected_credential_paths = tuple(
            str(path)
            for path in dict.fromkeys(
                (
                    *protected_credential_paths(),
                    *(protected_paths or ()),
                )
            )
        )
        self._workspace_files = WorkspaceFilesystem(workspace)
        workspace_root = self._workspace_files.root
        self._credential_workspace_overlap = any(
            path == workspace_root
            or path.is_relative_to(workspace_root)
            or workspace_root.is_relative_to(path)
            for path in (
                Path(item).expanduser().resolve(strict=False)
                for item in self._protected_credential_paths
            )
        )
        if purge_process_environment:
            purge_provider_credentials()
        self.image = image
        self.session_id = session_id
        self.workspace = str(workspace_root)
        self.lsp_actor = lsp_actor
        self.network = "none"
        self.runtime = runtime or os.environ.get("RAY_DOCKER_RUNTIME")
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

    def get_session_id(self) -> str:
        return self.session_id

    def get_workspace(self) -> str:
        return self.workspace

    def provider_environment_is_clean(self) -> bool:
        """Report the provider-credential invariant without exposing names or values."""
        return not provider_credential_values()

    def _resolve_checked(self, path: str) -> Tuple[str, bool]:
        if self._credential_workspace_overlap:
            return self.workspace, False
        try:
            return self._workspace_files.display_path(path), True
        except (WorkspacePathError, OSError, ValueError):
            return self.workspace, False

    def _resolve(self, path: str) -> str:
        abs_path, _ok = self._resolve_checked(path)
        return abs_path

    def _touch_lsp(self, abs_path: str) -> None:
        actor = self.lsp_actor
        if actor is None:
            return
        try:
            touch = getattr(actor, "touch_file", None)
            if touch is None:
                return
            remote = getattr(touch, "remote", None)
            if callable(remote):
                remote(abs_path, False)
            elif callable(touch):
                touch(abs_path, False)
        except Exception:
            pass

    def exists(self, path: str) -> bool:
        if self._credential_workspace_overlap:
            return False
        try:
            return self._workspace_files.exists(path)
        except (WorkspacePathError, OSError, ValueError):
            return False

    def stat(self, path: str) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"path": abs_path, "exists": False, "error": "path_outside_workspace"}
        try:
            info = self._workspace_files.stat(path)
        except FileNotFoundError:
            return {"path": abs_path, "exists": False}
        except (WorkspacePathError, OSError, ValueError):
            return {"path": abs_path, "exists": False, "error": "unsafe_workspace_path"}
        return {
            "path": info.path,
            "exists": True,
            "type": info.kind,
            "size": info.size,
            "mtime": info.mtime,
        }

    def put(self, path: str, content: bytes) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"ok": False, "path": abs_path, "error": "path_outside_workspace"}
        payload = content or b""
        try:
            self._workspace_files.write_bytes(path, payload)
        except (WorkspacePathError, OSError, ValueError) as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        return {"ok": True, "path": abs_path, "bytes": len(payload)}

    def get(self, path: str) -> bytes:
        if self._credential_workspace_overlap:
            return b""
        try:
            return self._workspace_files.read_bytes(path)
        except (WorkspacePathError, OSError, ValueError):
            return b""

    def read_text(
        self,
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        start = max(0, int(offset or 0)) if offset is not None else 0
        if not ok:
            return {
                "path": abs_path,
                "content": "",
                "truncated": False,
                "offset": start,
                "limit": limit,
                "error": "path_outside_workspace",
            }
        try:
            raw = self._workspace_files.read_text(
                path,
                encoding=encoding,
                errors="replace",
            )
        except (WorkspacePathError, OSError, ValueError):
            raw = ""
        content = raw[start:] if start else raw
        truncated = False
        if limit is not None:
            try:
                limit_value = int(limit)
            except (TypeError, ValueError):
                limit_value = None
            if (
                limit_value is not None
                and limit_value >= 0
                and len(content) > limit_value
            ):
                content = content[:limit_value]
                truncated = True
        return {
            "path": abs_path,
            "content": content,
            "truncated": truncated,
            "offset": start,
            "limit": limit,
        }

    def write_text(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"ok": False, "path": abs_path, "error": "path_outside_workspace"}
        value = content or ""
        try:
            self._workspace_files.write_text(path, value, encoding=encoding)
        except (WorkspacePathError, OSError, ValueError) as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        return {"ok": True, "path": abs_path, "bytes": len(value)}

    def ls(self, path: str, depth: int = 1) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {
                "path": abs_path,
                "entries": [],
                "items": [],
                "tree_format": False,
                "error": "path_outside_workspace",
            }
        try:
            entries = [
                {"path": item.path, "type": item.kind}
                for item in self._workspace_files.list_entries(
                    path,
                    depth=max(1, int(depth or 1)),
                )
            ]
        except (WorkspacePathError, OSError, ValueError):
            entries = []
        return {
            "path": abs_path,
            "items": entries,
            "entries": entries,
            "tree_format": False,
        }

    def glob(
        self,
        pattern: str,
        root: str = ".",
        limit: Optional[int] = None,
    ) -> List[str]:
        if self._credential_workspace_overlap:
            return []
        try:
            return self._workspace_files.glob(pattern, root=root, limit=limit)
        except (WorkspacePathError, OSError, ValueError):
            return []

    def grep(
        self,
        pattern: str,
        path: str = ".",
        include: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        if self._credential_workspace_overlap:
            return {"matches": []}
        try:
            matches = self._workspace_files.grep(
                pattern,
                root=path,
                include=include,
                limit=limit,
            )
        except (WorkspacePathError, OSError, ValueError, re.error):
            matches = []
        return {"matches": matches}

    def _docker_prefix(self, *, env_names: Iterable[str] = ()) -> List[str]:
        if shutil.which(self.docker_bin) is None and self.docker_bin != "docker":
            raise RuntimeError(f"docker binary not found: {self.docker_bin}")
        if shutil.which("docker") is None and self.docker_bin == "docker":
            raise RuntimeError("docker not available on this host")

        workspace = str(Path(self.workspace).resolve())
        args: List[str] = [
            self.docker_bin,
            "run",
            "--rm",
            "--volume",
            f"{workspace}:/workspace",
            "--workdir",
            "/workspace",
        ]
        if self.network:
            args.extend(["--network", self.network])
        if self.runtime:
            args.extend(["--runtime", self.runtime])
        for key in env_names:
            args.extend(["--env", str(key)])
        args.append(self.image)
        return args

    def run_shell(
        self,
        command: str,
        timeout: int = 30,
        env: Optional[Dict[str, str]] = None,
        stream: bool = False,
        stdin_data: Optional[str] = None,
        shell: bool = True,
    ):
        del shell
        cmd = command or ""
        if self._credential_workspace_overlap:
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "Docker sandbox command rejected: workspace overlaps "
                    "credential storage"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]
        secret_values = (
            *provider_credential_values(),
            *provider_credential_values(env or {}),
        )
        if contains_provider_credential_value(
            (cmd, stdin_data, env),
            values=secret_values,
        ):
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "Docker sandbox command rejected: provider credential "
                    "in process input"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]
        try:
            host_env = build_child_environment(overrides=env)
        except ValueError:
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "Docker sandbox environment rejected: override key "
                    "is not allowlisted"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]
        env_names = tuple(
            str(key)
            for key in (env or {})
            if str(key) in host_env
        )
        try:
            validate_workspace_credential_boundary(
                self.workspace,
                protected_paths=self._protected_credential_paths,
            )
        except ProcessIsolationUnavailable:
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "Docker sandbox command rejected: workspace credential "
                    "boundary is unsafe"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]
        docker_cmd = [
            *self._docker_prefix(env_names=env_names),
            "sh",
            "-lc",
            cmd,
        ]
        with redaction.secret_value_scope(*secret_values):
            try:
                result = subprocess.run(
                    docker_cmd,
                    timeout=timeout,
                    input=stdin_data,
                    env=host_env,
                    capture_output=True,
                    text=True,
                )
                payload = {
                    "exit": result.returncode,
                    "stdout": redaction.scrub_text(result.stdout or ""),
                    "stderr": redaction.scrub_text(result.stderr or ""),
                }
            except subprocess.TimeoutExpired:
                payload = {
                    "exit": 124,
                    "stdout": "",
                    "stderr": "Command timed out",
                }
            except Exception as exc:  # noqa: BLE001
                payload = {
                    "exit": 1,
                    "stdout": "",
                    "stderr": redaction.safe_exception_message(
                        exc,
                        operation="Docker sandbox command",
                    ),
                }

        if not stream:
            return payload
        lines: List[Any] = [ADAPTIVE_PREFIX_ITERABLE]
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        for line in stdout.splitlines() or []:
            lines.append(line)
        if not stdout and stderr:
            for line in stderr.splitlines():
                lines.append(line)
        lines.append(payload)
        return lines

    def run(
        self,
        cmd: str,
        timeout: Optional[int] = None,
        stdin_data: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        stream: bool = True,
        shell: bool = True,
    ):
        return self.run_shell(
            cmd,
            timeout=timeout or 30,
            env=env,
            stream=stream,
            stdin_data=stdin_data,
            shell=shell,
        )
