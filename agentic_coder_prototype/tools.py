from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def build_tool_catalog_specs(tools: List[Any]) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for tool in tools or []:
        tool_id = None
        if isinstance(tool, dict):
            tool_id = tool.get("name") or tool.get("tool_id") or tool.get("id")
        else:
            tool_id = getattr(tool, "name", None) or getattr(tool, "tool_id", None) or getattr(tool, "id", None)
        if tool_id is None:
            tool_id = str(tool)
        specs.append({"tool_id": str(tool_id)})
    return specs


def tool_catalog_hash(specs: List[Dict[str, Any]]) -> str:
    payload = json.dumps(specs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

