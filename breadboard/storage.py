import json
import os
from typing import Any, Dict, List


class JSONStorage:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _p(self, rel: str) -> str:
        full = os.path.abspath(os.path.join(self.root, rel))
        if not full.startswith(self.root + os.sep) and full != self.root:
            raise ValueError("Path escapes storage root")
        return full

    def write_json(self, rel: str, content: Any) -> None:
        path = self._p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
        os.replace(tmp, path)

    def read_json(self, rel: str) -> Any:
        path = self._p(rel)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list(self, rel_dir: str) -> List[str]:
        base = self._p(rel_dir)
        out: List[str] = []
        if not os.path.isdir(base):
            return out
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                out.append(os.path.relpath(full, self.root))
        out.sort()
        return out


