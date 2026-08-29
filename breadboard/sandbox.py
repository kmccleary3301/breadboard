"""Canonical BreadBoard sandbox module.

This now points at the current light/process-backed sandbox implementation.
Legacy callers may still import `breadboard.sandbox_v2` during migration.
"""

from __future__ import annotations

import os
import signal
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, List

import ray
from breadboard_engine.security import (
    build_child_environment,
    ProcessIsolationUnavailable,
    build_restricted_process_command,
    contains_provider_credential_value,
    provider_credential_values,
    protected_credential_paths,
    purge_provider_credentials,
    WorkspaceFilesystem,
    WorkspacePathError,
    redaction,
)

from .adaptive_iter import ADAPTIVE_PREFIX_ITERABLE


@ray.remote
class DevSandboxV2:
    """Local filesystem-backed sandbox implementation.

    This is a minimal stand-in for recovery/testing. It executes commands on the
    host filesystem scoped to the provided workspace.
    """

    def __init__(
        self,
        image: str,
        session_id: str = "",
        workspace: str = "",
        lsp_actor: Any = None,
        *,
        protected_paths: Optional[Sequence[str | os.PathLike[str]]] = None,
        purge_process_environment: bool = True,
    ) -> None:
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
            return {
                "path": abs_path,
                "exists": False,
                "error": "path_outside_workspace",
            }
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

    def run_shell(
        self,
        command: str,
        timeout: int = 30,
        env: Optional[Dict[str, str]] = None,
        stream: bool = False,
        stdin_data: Optional[str] = None,
        shell: bool = True,
    ) -> Dict[str, Any]:
        cmd = command or ""
        proc: Optional[subprocess.Popen[str]] = None
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
                    "sandbox command rejected: provider credential in process input"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]  # type: ignore[return-value]
        try:
            child_env = build_child_environment(
                overrides=env,
                allowed_override_keys=env or (),
            )
        except ValueError:
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "sandbox environment rejected: override key is not allowlisted"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]  # type: ignore[return-value]
        try:
            isolated_argv, child_env = build_restricted_process_command(
                cmd,
                workspace=self.workspace,
                shell=bool(shell),
                environment=child_env,
                protected_paths=self._protected_credential_paths,
            )
        except ProcessIsolationUnavailable:
            payload = {
                "exit": 126,
                "stdout": "",
                "stderr": (
                    "sandbox command rejected: filesystem isolation is unavailable"
                ),
            }
            if not stream:
                return payload
            return [
                ADAPTIVE_PREFIX_ITERABLE,
                payload["stderr"],
                payload,
            ]  # type: ignore[return-value]
        with redaction.secret_value_scope(*secret_values):
            try:
                proc = subprocess.Popen(
                    isolated_argv,
                    cwd=self.workspace,
                    shell=False,
                    env=child_env,
                    stdin=(
                        subprocess.PIPE
                        if stdin_data is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
                payload = {
                    "exit": proc.returncode,
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                }
            except subprocess.TimeoutExpired:
                if proc is not None and proc.pid is not None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        stdout, stderr = proc.communicate(timeout=2)
                    except Exception:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        stdout, stderr = proc.communicate()
                else:
                    stdout, stderr = "", ""
                payload = {
                    "exit": 124,
                    "stdout": stdout or "",
                    "stderr": stderr or "Command timed out",
                }
            except Exception as exc:
                payload = {
                    "exit": 1,
                    "stdout": "",
                    "stderr": redaction.safe_exception_message(
                        exc,
                        operation="sandbox command",
                    ),
                }
            scrubbed, _problems = redaction.scrub_structure(
                payload,
                path="$.sandbox_result",
            )
            payload = (
                scrubbed
                if isinstance(scrubbed, dict)
                else {
                    "exit": 1,
                    "stdout": "",
                    "stderr": "sandbox result unavailable",
                }
            )

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
        return lines  # type: ignore[return-value]

    def edit_replace(
        self,
        path: str,
        old_string: str,
        new_string: str,
        count: int = 0,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"ok": False, "path": abs_path, "error": "path_outside_workspace"}
        try:
            content = (
                self._workspace_files.read_text(
                    path,
                    encoding=encoding,
                    errors="replace",
                )
                if self._workspace_files.exists(path)
                else ""
            )
            updated = (
                content.replace(old_string, new_string, count)
                if count and count > 0
                else content.replace(old_string, new_string)
            )
            self._workspace_files.write_text(path, updated, encoding=encoding)
        except (WorkspacePathError, OSError, ValueError) as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        return {"ok": True, "path": abs_path}

    def multiedit(
        self, edits: List[Dict[str, Any]], encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """Apply multiple edits in sequence.

        Each edit may contain:
          - path + content (write)
          - path + old + new (+ count) (replace)
        """

        results: List[Dict[str, Any]] = []
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            path = str(edit.get("path") or "")
            if not path:
                continue
            if "content" in edit:
                results.append(
                    self.write_text(
                        path, str(edit.get("content") or ""), encoding=encoding
                    )
                )
                continue
            old = str(edit.get("old") or edit.get("old_string") or "")
            new = str(edit.get("new") or edit.get("new_string") or "")
            count = int(edit.get("count") or 0)
            results.append(self.edit_replace(path, old, new, count, encoding=encoding))
        return {"ok": True, "results": results}

    def _run_git(
        self,
        args: List[str],
        *,
        timeout: int = 10,
    ) -> subprocess.CompletedProcess[str]:
        child_env = build_child_environment()
        isolated_argv, child_env = build_restricted_process_command(
            ("git", *args),
            workspace=self.workspace,
            shell=False,
            environment=child_env,
            protected_paths=self._protected_credential_paths,
        )
        with redaction.secret_value_scope(*provider_credential_values()):
            result = subprocess.run(
                isolated_argv,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
            )
            result.stdout = redaction.scrub_text(result.stdout or "")
            result.stderr = redaction.scrub_text(result.stderr or "")
            return result

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        action = (
            (request or {}).get("action")
            or (request or {}).get("operation")
            or "status"
        )
        action = str(action).strip().lower()
        params = (request or {}).get("params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            if action == "init":
                user = (request or {}).get("user") or {}
                self._run_git(["init"])
                name = (
                    user.get("name") if isinstance(user, dict) else None
                ) or "BreadBoard"
                email = (
                    user.get("email") if isinstance(user, dict) else None
                ) or "breadboard@local"
                self._run_git(["config", "user.name", str(name)])
                self._run_git(["config", "user.email", str(email)])
                return {"ok": True}

            if action == "add":
                res = self._run_git(["add", "-A"])
                return {
                    "ok": res.returncode == 0,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                }

            if action == "commit":
                message = str((params or {}).get("message") or "update")
                res = self._run_git(["commit", "-m", message])
                ok = res.returncode == 0
                return {"ok": ok, "stdout": res.stdout, "stderr": res.stderr}

            if action == "status":
                res = self._run_git(["status", "--porcelain"])
                return {
                    "ok": res.returncode == 0,
                    "data": {"output": res.stdout},
                    "stderr": res.stderr,
                }

            if action == "diff":
                staged = bool((params or {}).get("staged"))
                unified = (params or {}).get("unified")
                args = ["diff"]
                if staged:
                    args.append("--cached")
                if unified is not None:
                    try:
                        args.append(f"-U{int(unified)}")
                    except Exception:
                        pass
                res = self._run_git(args, timeout=20)
                return {"ok": res.returncode == 0, "data": {"diff": res.stdout}}

            if action == "apply_patch":
                patch_text = str((params or {}).get("patch") or "")
                if not patch_text.strip():
                    return {"ok": False, "error": "empty patch"}
                args = ["apply"]
                three_way = bool(
                    (params or {}).get("three_way") or (params or {}).get("threeWay")
                )
                if three_way:
                    refresh = self._run_git(["update-index", "--refresh"], timeout=20)
                    if refresh.returncode != 0:
                        return {
                            "ok": False,
                            "stdout": refresh.stdout,
                            "stderr": refresh.stderr,
                        }
                    args.append("--3way")
                patch_name = f".breadboard_patch_{uuid.uuid4().hex}.diff"
                patch_path = self._workspace_files.display_path(patch_name)
                self._workspace_files.write_text(
                    patch_name,
                    patch_text,
                    encoding="utf-8",
                )
                if bool((params or {}).get("index")):
                    args.append("--index")
                whitespace = (params or {}).get("whitespace")
                if isinstance(whitespace, str) and whitespace.strip():
                    args.append(f"--whitespace={whitespace.strip()}")
                if bool((params or {}).get("reverse")):
                    args.append("-R")
                if bool((params or {}).get("keep_rejects")):
                    args.append("--reject")
                args.append(patch_path)
                res = self._run_git(args, timeout=30)
                try:
                    self._workspace_files.unlink(patch_name)
                except (WorkspacePathError, OSError):
                    pass
                except Exception:
                    pass
                return {
                    "ok": res.returncode == 0,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                }

            return {"ok": False, "error": f"Unsupported vcs action: {action}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def lsp_diagnostics(self, path: str) -> Dict[str, Any]:
        actor = self.lsp_actor
        if actor is None:
            return {}
        try:
            diagnostics = getattr(actor, "diagnostics", None)
            remote = (
                getattr(diagnostics, "remote", None)
                if diagnostics is not None
                else None
            )
            if callable(remote):
                return ray.get(remote())
            if callable(diagnostics):
                return diagnostics()
        except Exception:
            return {}
        return {}


def new_dev_sandbox_v2(
    image: str,
    workspace: str,
    *,
    name: str | None = None,
    session_id: str | None = None,
    lsp_actor: Any = None,
    driver: str | None = None,
    driver_options: Dict[str, Any] | None = None,
    protected_paths: Optional[Sequence[str | os.PathLike[str]]] = None,
):
    """Create a sandbox actor for the requested driver.

    This is the primary constructor used by tests and higher-level engine code.
    """

    from .sandbox_driver import (
        SandboxLaunchSpec,
        create_sandbox,
        resolve_driver_from_env,
    )

    resolved_driver = (driver or resolve_driver_from_env()).strip().lower()
    spec = SandboxLaunchSpec(
        driver=resolved_driver,
        image=str(image),
        workspace=str(workspace),
        session_id=session_id or f"sb-{uuid.uuid4()}",
        name=name,
        lsp_actor=lsp_actor,
        driver_options=dict(driver_options or {}),
        protected_paths=tuple(
            str(path)
            for path in (
                protected_paths
                if protected_paths is not None
                else protected_credential_paths()
            )
        ),
    )
    return create_sandbox(spec)


# Canonical aliases for the stabilized module path. We keep the V2 names
# available during migration, but new imports should prefer `breadboard.sandbox`.
DevSandbox = DevSandboxV2


def new_dev_sandbox(*args: Any, **kwargs: Any):
    return new_dev_sandbox_v2(*args, **kwargs)


__all__ = [
    "DevSandbox",
    "DevSandboxV2",
    "new_dev_sandbox",
    "new_dev_sandbox_v2",
]
