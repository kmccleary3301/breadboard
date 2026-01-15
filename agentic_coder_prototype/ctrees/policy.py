from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .store import CTreeStore


_ID_RE = re.compile(r"^ctn_(\d+)$")


def _sort_node_ids(ids: List[str]) -> List[str]:
    def _key(value: str) -> tuple[int, int | str]:
        match = _ID_RE.match(value)
        if match:
            try:
                return (0, int(match.group(1)))
            except Exception:
                return (0, value)
        return (1, value)

    return sorted(ids, key=_key)


def collapse_policy(store: CTreeStore, *, target: Optional[int] = None) -> Dict[str, Any]:
    """Placeholder collapse policy for future deterministic pruning logic."""

    nodes = getattr(store, "nodes", []) or []
    node_ids = [str(node.get("id")) for node in nodes if node.get("id") is not None]
    ordered_ids = _sort_node_ids(node_ids)
    node_count = len(ordered_ids)
    normalized_target = target if target is not None and target >= 0 else None

    drop: List[str] = []
    if normalized_target is not None and node_count > normalized_target:
        drop = ordered_ids[: node_count - normalized_target]

    remaining = [node_id for node_id in ordered_ids if node_id not in set(drop)]
    collapse: List[str] = []
    if len(remaining) > 1:
        collapse = remaining[:-1]

    stages = [
        {"stage": "RAW", "target": normalized_target, "drop": [], "collapse": [], "node_count": node_count},
        {"stage": "SPEC", "target": normalized_target, "drop": drop, "collapse": [], "node_count": node_count},
        {"stage": "HEADER", "target": normalized_target, "drop": drop, "collapse": collapse, "node_count": node_count},
        {
            "stage": "FROZEN",
            "target": normalized_target,
            "drop": drop,
            "collapse": collapse,
            "node_count": node_count,
        },
    ]

    return {
        "kind": "stub",
        "target": normalized_target,
        "drop": drop,
        "collapse": collapse,
        "node_count": node_count,
        "stages": stages,
    }
