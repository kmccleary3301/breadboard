from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import ray


@ray.remote
class DevSandboxV2:
    """Local filesystem-backed sandbox implementation.

    This is a minimal stand-in for recovery/testing. It executes commands on the
    host filesystem scoped to the provided workspace.
    """

    def __init__(self, image: str, session_id: str = "", workspace: str = "", lsp_actor: Any = None) -> None:
        self.image = image
        self.session_id = session_id
        self.workspace = str(workspace)
        self.lsp_actor = lsp_actor

    def _resolve(self, path: str) -> str:
        ws = Path(self.workspace).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = ws / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(ws)
        except Exception:
            return str(ws)
        return str(candidate)

    def read_text(self, path: str) -> Dict[str, Any]:
        abs_path = self._resolve(path)
        try:
            content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = ""
        return {"path": abs_path, "content": content}

    def write_text(self, path: str, content: str) -> Dict[str, Any]:
        abs_path = self._resolve(path)
        p = Path(abs_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return {"ok": True, "path": abs_path, "bytes": len(content or "")}

    def ls(self, path: str, depth: int = 1) -> Dict[str, Any]:
        abs_path = self._resolve(path)
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
        return {"path": abs_path, "entries": entries}

    def glob(self, pattern: str, root: str = ".", limit: Optional[int] = None) -> List[str]:
        root_path = Path(self._resolve(root))
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

    def run(self, command: str, timeout: int = 30, stream: bool = False, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return self.run_shell(command, timeout=timeout, env=env, stream=stream)

    def run_shell(
        self,
        command: str,
        timeout: int = 30,
        env: Optional[Dict[str, str]] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        cmd = command or ""
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                shell=True,
                timeout=timeout,
                env={**os.environ, **(env or {})},
                capture_output=True,
                text=True,
            )
            return {
                "exit": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
        except subprocess.TimeoutExpired:
            return {"exit": 124, "stdout": "", "stderr": "Command timed out"}
        except Exception as exc:
            return {"exit": 1, "stdout": "", "stderr": str(exc)}

    def edit_replace(self, path: str, old_string: str, new_string: str, count: int = 0) -> Dict[str, Any]:
        abs_path = self._resolve(path)
        p = Path(abs_path)
        content = ""
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
        if count and count > 0:
            updated = content.replace(old_string, new_string, count)
        else:
            updated = content.replace(old_string, new_string)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(updated, encoding="utf-8")
        return {"ok": True, "path": abs_path}

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        op = (request or {}).get("operation") or "status"
        if op == "status":
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return {"ok": True, "output": result.stdout}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"Unsupported vcs op: {op}"}

    def lsp_diagnostics(self, path: str) -> Dict[str, Any]:
        return {}
