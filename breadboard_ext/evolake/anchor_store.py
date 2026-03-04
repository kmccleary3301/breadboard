from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Protocol

def _normalize_tenant_id(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = "default"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return safe[:120] or "default"


class AnchorStore(Protocol):
    def append_rows(self, tenant_id: str, campaign_id: str, rows: Iterable[Dict[str, object]]) -> Path: ...

    def read_rows(self, tenant_id: str, campaign_id: str) -> List[Dict[str, object]]: ...


@dataclass
class FileSystemAnchorStore:
    root: Path

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, tenant_id: str, campaign_id: str) -> Path:
        tenant = _normalize_tenant_id(tenant_id)
        return self.root / tenant / str(campaign_id) / "anchors.jsonl"

    def append_rows(self, tenant_id: str, campaign_id: str, rows: Iterable[Dict[str, object]]) -> Path:
        path = self._path(tenant_id, campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")
        return path

    def read_rows(self, tenant_id: str, campaign_id: str) -> List[Dict[str, object]]:
        path = self._path(tenant_id, campaign_id)
        if not path.exists():
            return []
        rows: List[Dict[str, object]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows
