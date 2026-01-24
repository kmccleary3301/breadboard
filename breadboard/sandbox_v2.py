from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import ray

from .adaptive_iter import ADAPTIVE_PREFIX_ITERABLE


class DevSandboxV2Base:
    """Base class for sandbox implementations (non-Ray actor).

    This provides all the sandbox functionality without the @ray.remote decorator,
    allowing subclasses to inherit and add their own @ray.remote decorator.

    File operations are performed on the host filesystem scoped to the workspace.
    Command execution can be overridden by subclasses for different isolation backends.
    """

    def __init__(self, image: str, session_id: str = "", workspace: str = "", lsp_actor: Any = None) -> None:
        self.image = image
        self.session_id = session_id
        self.workspace = str(workspace)
        self.lsp_actor = lsp_actor

    def get_session_id(self) -> str:
        return self.session_id

    def get_workspace(self) -> str:
        return self.workspace

    def _resolve_checked(self, path: str) -> Tuple[str, bool]:
        ws = Path(self.workspace).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = ws / candidate
        try:
            candidate = candidate.resolve()
        except Exception:
            return str(ws), False
        try:
            candidate.relative_to(ws)
        except Exception:
            return str(ws), False
        return str(candidate), True

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
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return False
        return Path(abs_path).exists()

    def stat(self, path: str) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"path": abs_path, "exists": False, "error": "path_outside_workspace"}
        p = Path(abs_path)
        if not p.exists():
            return {"path": abs_path, "exists": False}
        try:
            st = p.stat()
            return {
                "path": abs_path,
                "exists": True,
                "type": "dir" if p.is_dir() else "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        except Exception:
            return {"path": abs_path, "exists": True}

    def put(self, path: str, content: bytes) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"ok": False, "path": abs_path, "error": "path_outside_workspace"}
        p = Path(abs_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_bytes(content or b"")
        except Exception as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        return {"ok": True, "path": abs_path, "bytes": len(content or b"")}

    def get(self, path: str) -> bytes:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return b""
        try:
            return Path(abs_path).read_bytes()
        except Exception:
            return b""

    def read_text(
        self,
        path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            start = int(offset or 0) if offset is not None else 0
            if start < 0:
                start = 0
            return {
                "path": abs_path,
                "content": "",
                "truncated": False,
                "offset": start,
                "limit": limit,
                "error": "path_outside_workspace",
            }
        try:
            raw = Path(abs_path).read_text(encoding=encoding, errors="replace")
        except Exception:
            raw = ""

        start = int(offset or 0) if offset is not None else 0
        if start < 0:
            start = 0
        truncated = False
        content = raw
        if start:
            content = content[start:]
        if limit is not None:
            try:
                lim = int(limit)
            except Exception:
                lim = None
            if lim is not None and lim >= 0 and len(content) > lim:
                content = content[:lim]
                truncated = True

        return {"path": abs_path, "content": content, "truncated": truncated, "offset": start, "limit": limit}

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"ok": False, "path": abs_path, "error": "path_outside_workspace"}
        p = Path(abs_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content or "", encoding=encoding)
        except Exception as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        size = len(content or "")
        return {"ok": True, "path": abs_path, "bytes": size, "size": size}

    def ls(self, path: str, depth: int = 1) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"path": abs_path, "entries": [], "items": [], "tree_format": False, "error": "path_outside_workspace"}
        depth = max(1, int(depth or 1))
        root = Path(abs_path)
        entries = []
        if not root.exists():
            return {"path": abs_path, "entries": []}

        def _walk(current: Path, rel: Path, remaining: int) -> None:
            try:
                for child in sorted(current.iterdir()):
                    rel_child = rel / child.name
                    entries.append({
                        "path": str(rel_child),
                        "type": "dir" if child.is_dir() else "file",
                    })
                    if child.is_dir() and remaining > 1:
                        _walk(child, rel_child, remaining - 1)
            except Exception:
                pass

        _walk(root, Path("."), depth)
        return {"path": abs_path, "items": entries, "entries": entries, "tree_format": False}

    def glob(self, pattern: str, root: str = ".", limit: Optional[int] = None) -> List[str]:
        resolved_root, ok = self._resolve_checked(root)
        if not ok:
            return []
        root_path = Path(resolved_root)
        if not root_path.exists():
            return []
        matches: List[str] = []
        try:
            for match in root_path.glob(pattern):
                try:
                    rel = str(match.relative_to(root_path))
                except Exception:
                    rel = str(match)
                matches.append(rel)
        except Exception:
            return []
        # Sort by mtime (desc) to align with OpenCode expectations
        try:
            matches.sort(
                key=lambda p: (root_path / p).stat().st_mtime if (root_path / p).exists() else 0.0,
                reverse=True,
            )
        except Exception:
            pass
        if limit is not None:
            try:
                limit_val = int(limit)
                if limit_val >= 0:
                    matches = matches[:limit_val]
            except Exception:
                pass
        return matches

    def grep(self, pattern: str, path: str = ".", include: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        root = Path(self._resolve(path))
        if not root.exists():
            return {"matches": []}
        try:
            import re
            regex = re.compile(pattern)
        except Exception:
            return {"matches": []}
        import fnmatch

        matches: List[Dict[str, Any]] = []
        try:
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                try:
                    rel = str(file_path.relative_to(root))
                except Exception:
                    rel = str(file_path)
                if include and not fnmatch.fnmatch(rel, include):
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for idx, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append({"path": rel, "line": idx, "text": line})
                        if len(matches) >= int(limit or 0 or 0) and int(limit or 0) > 0:
                            return {"matches": matches}
            return {"matches": matches}
        except Exception:
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
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                shell=bool(shell),
                timeout=timeout,
                env={**os.environ, **(env or {})},
                input=stdin_data,
                capture_output=True,
                text=True,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            payload = {"exit": result.returncode, "stdout": stdout, "stderr": stderr}
            if not stream:
                return payload

            lines: List[Any] = [ADAPTIVE_PREFIX_ITERABLE]
            for line in (stdout.splitlines() or []):
                lines.append(line)
            if not stdout and stderr:
                for line in stderr.splitlines():
                    lines.append(line)
            lines.append(payload)
            return lines  # type: ignore[return-value]
        except subprocess.TimeoutExpired:
            payload = {"exit": 124, "stdout": "", "stderr": "Command timed out"}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]  # type: ignore[return-value]
        except Exception as exc:
            payload = {"exit": 1, "stdout": "", "stderr": str(exc)}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]  # type: ignore[return-value]

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
        p = Path(abs_path)
        content = ""
        if p.exists():
            content = p.read_text(encoding=encoding, errors="replace")
        total_matches = content.count(old_string) if old_string else 0
        if count and count > 0:
            replacements = min(count, total_matches)
            updated = content.replace(old_string, new_string, count)
        else:
            replacements = total_matches
            updated = content.replace(old_string, new_string)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(updated, encoding=encoding)
        except Exception as exc:
            return {"ok": False, "path": abs_path, "error": str(exc)}
        self._touch_lsp(abs_path)
        return {"ok": True, "path": abs_path, "replacements": replacements}

    def multiedit(self, edits: List[Dict[str, Any]], encoding: str = "utf-8") -> Dict[str, Any]:
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
                results.append(self.write_text(path, str(edit.get("content") or ""), encoding=encoding))
                continue
            old = str(edit.get("old") or edit.get("old_string") or "")
            new = str(edit.get("new") or edit.get("new_string") or "")
            count = int(edit.get("count") or 0)
            results.append(self.edit_replace(path, old, new, count, encoding=encoding))
        return {"ok": True, "results": results}

    def _run_git(self, args: List[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        action = (request or {}).get("action") or (request or {}).get("operation") or "status"
        action = str(action).strip().lower()
        params = (request or {}).get("params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            if action == "init":
                user = (request or {}).get("user") or {}
                self._run_git(["init"])
                name = (user.get("name") if isinstance(user, dict) else None) or "BreadBoard"
                email = (user.get("email") if isinstance(user, dict) else None) or "breadboard@local"
                self._run_git(["config", "user.name", str(name)])
                self._run_git(["config", "user.email", str(email)])
                return {"ok": True}

            if action == "add":
                res = self._run_git(["add", "-A"])
                return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}

            if action == "commit":
                message = str((params or {}).get("message") or "update")
                res = self._run_git(["commit", "-m", message])
                ok = res.returncode == 0
                return {"ok": ok, "stdout": res.stdout, "stderr": res.stderr}

            if action == "status":
                res = self._run_git(["status", "--porcelain"])
                return {"ok": res.returncode == 0, "data": {"output": res.stdout}, "stderr": res.stderr}

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
                patch_path = Path(self.workspace) / f".breadboard_patch_{uuid.uuid4().hex}.diff"
                patch_path.write_text(patch_text, encoding="utf-8")
                args = ["apply"]
                if bool((params or {}).get("three_way") or (params or {}).get("threeWay")):
                    args.append("--3way")
                if bool((params or {}).get("index")):
                    args.append("--index")
                whitespace = (params or {}).get("whitespace")
                if isinstance(whitespace, str) and whitespace.strip():
                    args.append(f"--whitespace={whitespace.strip()}")
                if bool((params or {}).get("reverse")):
                    args.append("-R")
                if bool((params or {}).get("keep_rejects")):
                    args.append("--reject")
                args.append(str(patch_path))
                res = self._run_git(args, timeout=30)
                try:
                    patch_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}

            return {"ok": False, "error": f"Unsupported vcs action: {action}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def lsp_diagnostics(self, path: str) -> Dict[str, Any]:
        actor = self.lsp_actor
        if actor is None:
            return {}
        try:
            diagnostics = getattr(actor, "diagnostics", None)
            remote = getattr(diagnostics, "remote", None) if diagnostics is not None else None
            if callable(remote):
                return ray.get(remote())
            if callable(diagnostics):
                return diagnostics()
        except Exception:
            return {}
        return {}


@ray.remote
class DevSandboxV2(DevSandboxV2Base):
    """Local filesystem-backed sandbox implementation (Ray actor).

    This is a minimal stand-in for recovery/testing. It executes commands on the
    host filesystem scoped to the provided workspace.

    Inherits all functionality from DevSandboxV2Base.
    """

    pass  # All functionality inherited from DevSandboxV2Base


def new_dev_sandbox_v2(
    image: str,
    workspace: str,
    *,
    name: str | None = None,
    session_id: str | None = None,
    lsp_actor: Any = None,
    driver: str | None = None,
    driver_options: Dict[str, Any] | None = None,
):
    """Create a sandbox actor for the requested driver.

    This is the primary constructor used by tests and higher-level engine code.
    """

    from .sandbox_driver import SandboxLaunchSpec, create_sandbox, resolve_driver_from_env

    resolved_driver = (driver or resolve_driver_from_env()).strip().lower()
    spec = SandboxLaunchSpec(
        driver=resolved_driver,
        image=str(image),
        workspace=str(workspace),
        session_id=session_id or f"sb-{uuid.uuid4()}",
        name=name,
        lsp_actor=lsp_actor,
        driver_options=dict(driver_options or {}),
    )
    try:
        return create_sandbox(spec)
    except NotImplementedError:
        # Docker driver is optional; fall back to process sandbox until implemented.
        fallback = SandboxLaunchSpec(
            driver="process",
            image=str(image),
            workspace=str(workspace),
            session_id=spec.session_id,
            name=name,
            lsp_actor=lsp_actor,
            driver_options=spec.driver_options,
        )
        return create_sandbox(fallback)
