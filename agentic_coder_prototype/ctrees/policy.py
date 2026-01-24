from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import CTreeStore


def collapse_policy(
    store: CTreeStore,
    *,
    target: Optional[int] = None,
    mode: str = "all_but_last",
    kind_allowlist: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Deterministic pruning policy for staged collapse.

    This policy is intentionally simple and stable:
    - ordering is append-order (the order nodes were recorded)
    - if `target` is set and exceeded, drop the oldest nodes first
    - for HEADER/FROZEN, optionally collapse remaining nodes (configurable)
    """

    nodes = getattr(store, "nodes", []) or []
    allow: Optional[set[str]] = None
    if kind_allowlist:
        allow = {str(item) for item in kind_allowlist if str(item).strip()}
    if allow:
        nodes = [node for node in nodes if str(node.get("kind") or "") in allow]

    ordered_ids = [str(node.get("id")) for node in nodes if node.get("id") is not None]
    node_count = len(ordered_ids)
    normalized_target = target if target is not None and target >= 0 else None

    drop: List[str] = []
    if normalized_target is not None and node_count > normalized_target:
        drop_count = node_count - normalized_target
        drop = ordered_ids[:drop_count]

    drop_set = set(drop)
    remaining = [node_id for node_id in ordered_ids if node_id not in drop_set]
    collapse: List[str] = []
    mode_key = str(mode or "all_but_last").strip().lower()
    if mode_key not in {"none", "all_but_last"}:
        mode_key = "all_but_last"
    if mode_key == "all_but_last" and len(remaining) > 1:
        collapse = remaining[:-1]

    stages = [
        {"stage": "RAW", "target": normalized_target, "drop": [], "collapse": [], "node_count": node_count, "ordering": "append"},
        {"stage": "SPEC", "target": normalized_target, "drop": drop, "collapse": [], "node_count": node_count, "ordering": "append"},
        {"stage": "HEADER", "target": normalized_target, "drop": drop, "collapse": collapse, "node_count": node_count, "ordering": "append"},
        {
            "stage": "FROZEN",
            "target": normalized_target,
            "drop": drop,
            "collapse": collapse,
            "node_count": node_count,
            "ordering": "append",
        },
    ]

    return {
        "kind": "deterministic",
        "target": normalized_target,
        "mode": mode_key,
        "kinds": sorted(allow) if allow else None,
        "ordering": "append",
        "drop": drop,
        "collapse": collapse,
        "node_count": node_count,
        "dropped": len(drop),
        "collapsed": len(collapse),
        "stages": stages,
    }
