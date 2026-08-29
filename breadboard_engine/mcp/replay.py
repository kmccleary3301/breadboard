from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..security import WorkspaceFilesystem, WorkspacePathError


_TAPE_LOCK = threading.Lock()


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
    target = Path(path).expanduser().absolute()
    try:
        filesystem = WorkspaceFilesystem.open_anchored_root(
            target.parent,
            create=False,
        )
    except WorkspacePathError as exc:
        if exc.code == "workspace_root_unavailable":
            return MCPReplayTape(entries=[])
        raise
    try:
        try:
            raw = filesystem.read_text(target.name, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return MCPReplayTape(entries=[])
    finally:
        filesystem.close()
    entries: List[Dict[str, Any]] = []
    for line in raw.splitlines():
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
    target = Path(path).expanduser().absolute()
    filesystem = WorkspaceFilesystem.open_anchored_root(
        target.parent,
        create=True,
    )
    try:
        with _TAPE_LOCK:
            try:
                existing = filesystem.read_bytes(target.name)
            except FileNotFoundError:
                existing = b""
            content = existing + (
                json.dumps(payload, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            filesystem.write_bytes(target.name, content)
    finally:
        filesystem.close()
