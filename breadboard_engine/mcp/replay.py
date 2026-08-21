from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class MCPReplayTape:
    entries: List[Dict[str, Any]]
    cursor: int = 0

    def next(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if self.cursor >= len(self.entries):
            return {"error": "mcp replay exhausted"}
        entry = self.entries[self.cursor]
        self.cursor += 1
        result = entry.get("result")
        if isinstance(result, dict):
            return result
        return {"result": result}


def load_mcp_replay_tape(path: Path) -> MCPReplayTape:
    if not path.exists():
        return MCPReplayTape(entries=[])
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return MCPReplayTape(entries=entries)


def append_mcp_replay_entry(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

