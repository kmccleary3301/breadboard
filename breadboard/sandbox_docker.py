from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ray

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
    ) -> None:
        self.image = image
        self.session_id = session_id
        self.workspace = str(workspace)
        self.lsp_actor = lsp_actor
        self.network = str(network or "none")
        self.runtime = runtime or os.environ.get("RAY_DOCKER_RUNTIME")
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

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
        return ok and Path(abs_path).exists()

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
            start = max(0, int(offset or 0)) if offset is not None else 0
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
        start = max(0, int(offset or 0)) if offset is not None else 0
        content = raw[start:] if start else raw
        truncated = False
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
        return {"ok": True, "path": abs_path, "bytes": len(content or "")}

    def ls(self, path: str, depth: int = 1) -> Dict[str, Any]:
        abs_path, ok = self._resolve_checked(path)
        if not ok:
            return {"path": abs_path, "entries": [], "items": [], "tree_format": False, "error": "path_outside_workspace"}
        depth = max(1, int(depth or 1))
        root = Path(abs_path)
        entries: List[Dict[str, str]] = []
        if not root.exists():
            return {"path": abs_path, "entries": []}

        def _walk(current: Path, rel: Path, remaining: int) -> None:
            try:
                for child in sorted(current.iterdir()):
                    rel_child = rel / child.name
                    entries.append({"path": str(rel_child), "type": "dir" if child.is_dir() else "file"})
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
                    matches.append(str(match.relative_to(root_path)))
                except Exception:
                    matches.append(str(match))
        except Exception:
            return []
        try:
            matches.sort(
                key=lambda p: (root_path / p).stat().st_mtime if (root_path / p).exists() else 0.0,
                reverse=True,
            )
        except Exception:
            pass
        if limit is not None:
            try:
                lim = int(limit)
                if lim >= 0:
                    matches = matches[:lim]
            except Exception:
                pass
        return matches

    def grep(self, pattern: str, path: str = ".", include: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        root = Path(self._resolve(path))
        if not root.exists():
            return {"matches": []}
        try:
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
                        if int(limit or 0) > 0 and len(matches) >= int(limit or 0):
                            return {"matches": matches}
            return {"matches": matches}
        except Exception:
            return {"matches": matches}

    def _docker_prefix(self, *, env: Optional[Dict[str, str]] = None) -> List[str]:
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
        for key, value in (env or {}).items():
            if not key:
                continue
            args.extend(["-e", f"{key}={value}"])
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
        # Execute inside the container via sh -lc.
        cmd = command or ""
        docker_cmd = [*self._docker_prefix(env=env), "sh", "-lc", cmd]
        try:
            result = subprocess.run(
                docker_cmd,
                timeout=timeout,
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
            return lines
        except subprocess.TimeoutExpired:
            payload = {"exit": 124, "stdout": "", "stderr": "Command timed out"}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]
        except Exception as exc:  # noqa: BLE001
            payload = {"exit": 1, "stdout": "", "stderr": str(exc)}
            if not stream:
                return payload
            return [ADAPTIVE_PREFIX_ITERABLE, payload]

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
