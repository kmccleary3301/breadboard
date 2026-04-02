import ast
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set

import ray


def _mk_diagnostic(path: str, message: str, line: int, col: int, severity: int = 1) -> Dict[str, Any]:
    return {
        "file": path,
        "severity": severity,  # 1=ERROR, 2=WARN, 3=INFO, 4=HINT
        "message": message,
        "range": {
            "start": {"line": max(line - 1, 0), "character": max(col - 1, 0)},
            "end": {"line": max(line - 1, 0), "character": max(col, 0)},
        },
    }


@ray.remote
class LSPManager:
    def __init__(self):
        self.roots: Set[str] = set()
        self.touched: Set[str] = set()
        # capability flags
        self.pyright_path: Optional[str] = shutil.which("pyright") or shutil.which("pyright-langserver")

    def register_root(self, root: str) -> None:
        self.roots.add(os.path.abspath(root))

    def touch_file(self, path: str, wait: bool = True) -> None:
        self.touched.add(os.path.abspath(path))

    def diagnostics(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Minimal Python-aware diagnostics: parse .py files for syntax errors using ast.
        Returns mapping file->list[diagnostic]. Non-Python files return no diagnostics.
        """
        results: Dict[str, List[Dict[str, Any]]] = {}
        targets: Set[str] = set(self.touched) if self.touched else set()
        if not targets:
            # if nothing touched, scan all .py under roots (limited)
            for root in self.roots:
                for dirpath, _, filenames in os.walk(root):
                    for name in filenames:
                        if name.endswith(".py"):
                            targets.add(os.path.join(dirpath, name))

        for file_path in list(targets):
            if not os.path.isfile(file_path):
                continue
            if not file_path.endswith(".py"):
                continue
            # Prefer pyright CLI if available for richer diagnostics
            if self.pyright_path and os.path.isdir(os.path.dirname(file_path)):
                try:
                    proc = subprocess.Popen(
                        ["pyright", file_path, "--outputjson"],
                        cwd=os.path.dirname(file_path),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    out, _ = proc.communicate(timeout=10)
                    if proc.returncode is not None and out:
                        import json

                        data = json.loads(out)
                        for d in data.get("generalDiagnostics", []):
                            if not d.get("file"):
                                continue
                            fp = d["file"]
                            r = d.get("range", {})
                            start = r.get("start", {"line": 1, "character": 1})
                            diags = results.setdefault(fp, [])
                            diags.append(
                                _mk_diagnostic(
                                    fp,
                                    d.get("message", "diagnostic"),
                                    (start.get("line", 1) + 1),
                                    (start.get("character", 1) + 1),
                                    1 if d.get("severity") == "error" else 2,
                                )
                            )
                        continue
                except Exception:
                    # fall back to ast
                    pass
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
                ast.parse(src, filename=file_path)
            except SyntaxError as e:
                diags = results.setdefault(file_path, [])
                diags.append(_mk_diagnostic(file_path, e.msg, e.lineno or 1, (e.offset or 1)))
            except Exception:
                continue

        # reset touched set after reporting
        self.touched.clear()
        return results

    # --- Symbol APIs (Python-only prototype)
    def workspace_symbol(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        symbols: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for root in list(self.roots):
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, name)
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            src = f.read()
                        tree = ast.parse(src, filename=path)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                name_ = getattr(node, "name", "")
                                if query.lower() in name_.lower():
                                    key = f"{path}:{name_}:{getattr(node, 'lineno', 1)}"
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    kind = 12 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 5
                                    symbols.append(
                                        {
                                            "name": name_,
                                            "kind": kind,
                                            "location": {
                                                "uri": f"file://{path}",
                                                "range": _mk_diagnostic(path, "", getattr(node, "lineno", 1), 1)[
                                                    "range"
                                                ],
                                            },
                                        }
                                    )
                                    if len(symbols) >= limit:
                                        return symbols
                    except Exception:
                        continue
        return symbols

    def document_symbol(self, path: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.isfile(path) or not path.endswith(".py"):
            return out
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name_ = getattr(node, "name", "")
                    kind = 12 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 5
                    out.append(
                        {
                            "name": name_,
                            "kind": kind,
                            "range": _mk_diagnostic(path, "", getattr(node, "lineno", 1), 1)["range"],
                            "selectionRange": _mk_diagnostic(path, "", getattr(node, "lineno", 1), 1)["range"],
                        }
                    )
        except Exception:
            return out
        return out

    def hover(self, file: str, line: int, character: int) -> Dict[str, Any]:
        """Return a trivial hover: the word under cursor with a generic label."""
        try:
            with open(file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if line - 1 < 0 or line - 1 >= len(lines):
                return {}
            text = lines[line - 1]
            # find word boundaries
            start = character - 1
            if start < 0:
                start = 0
            while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
                start -= 1
            end = character - 1
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            word = text[start:end].strip()
            if not word:
                return {}
            return {"contents": [{"language": "python", "value": f"symbol: {word}"}]}
        except Exception:
            return {}


