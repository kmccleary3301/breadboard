from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .context_engine import select_ctree_context
from .schema import canonical_ctree_json, sanitize_ctree_payload
from .store import CTreeStore

CTREE_TREE_ROOT_ID = "ctrees:root"


def _sha256(value: Any) -> str:
    blob = canonical_ctree_json(value)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _payload_sha1(payload: Any) -> str:
    blob = canonical_ctree_json(payload)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _node_label(node: Dict[str, Any]) -> str:
    kind = str(node.get("kind") or "")
    payload = node.get("payload")
    if kind == "message" and isinstance(payload, dict):
        role = payload.get("role")
        if role is not None and str(role).strip():
            return f"message:{str(role).strip()}"
        return "message"
    if kind in {"lifecycle", "guardrail"} and isinstance(payload, dict):
        type_value = payload.get("type")
        if type_value is not None and str(type_value).strip():
            return f"{kind}:{str(type_value).strip()}"
    if kind == "task_event" and isinstance(payload, dict):
        event_kind = payload.get("kind")
        if event_kind is not None and str(event_kind).strip():
            return f"task:{str(event_kind).strip()}"
    if kind == "subagent" and isinstance(payload, dict):
        subagent_type = payload.get("subagent_type")
        if subagent_type is not None and str(subagent_type).strip():
            return f"subagent:{str(subagent_type).strip()}"
    return kind or "node"


def _turn_key(turn: Optional[int]) -> Tuple[int, int]:
    if isinstance(turn, int):
        return (0, turn)
    return (1, 0)


def build_ctree_tree_view(
    store: CTreeStore,
    *,
    stage: str,
    compiler_config: Dict[str, Any],
    collapse_target: Optional[int],
    collapse_mode: str = "all_but_last",
    include_previews: bool,
) -> Dict[str, Any]:
    """Build a deterministic, compact graph view suitable for a client reducer.

    This is engine-owned and intentionally does not require TUI-side “tree semantics” decisions.
    """

    stage_key = str(stage or "FROZEN").strip().upper()
    if stage_key not in {"RAW", "SPEC", "HEADER", "FROZEN"}:
        stage_key = "FROZEN"

    compiler_cfg = dict(compiler_config or {})
    selection_cfg = compiler_cfg.get("selection") if isinstance(compiler_cfg.get("selection"), dict) else {}
    header_cfg = compiler_cfg.get("header") if isinstance(compiler_cfg.get("header"), dict) else {}

    if include_previews:
        header_cfg = dict(header_cfg)
        header_cfg["mode"] = "sanitized_content"
    else:
        header_cfg = dict(header_cfg)
        header_cfg["mode"] = "hash_only"

    selection, compiled = select_ctree_context(
        store,
        selection_config=dict(selection_cfg),
        header_config=dict(header_cfg),
        collapse_target=collapse_target,
        collapse_mode=str(collapse_mode or "all_but_last"),
        stage=stage_key,
        pin_latest=True,
    )

    nodes: List[Dict[str, Any]] = list(getattr(store, "nodes", []) or [])
    node_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict) and node.get("id")}

    selected_ids = set(selection.get("candidate_ids") or [])
    kept_ids = set(selection.get("kept_ids") or [])
    dropped_ids = set(selection.get("dropped_ids") or [])
    collapsed_ids = set(selection.get("collapsed_ids") or [])

    header_items = {}
    header = None
    try:
        stages = compiled.get("stages") if isinstance(compiled, dict) else None
        header = stages.get("HEADER") if isinstance(stages, dict) else None
    except Exception:
        header = None
    if isinstance(header, dict):
        items = header.get("messages")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("node_id"), str):
                    header_items[item["node_id"]] = dict(item)

    root = {
        "id": CTREE_TREE_ROOT_ID,
        "parent_id": None,
        "kind": "root",
        "turn": None,
        "label": "C-Trees",
        "meta": {
            "stage": stage_key,
            "selection_sha256": selection.get("selection_sha256"),
            "node_hash": (compiled.get("hashes") or {}).get("node_hash") if isinstance(compiled, dict) else None,
            "z1": (compiled.get("hashes") or {}).get("z1") if isinstance(compiled, dict) else None,
            "z2": (compiled.get("hashes") or {}).get("z2") if isinstance(compiled, dict) else None,
            "z3": (compiled.get("hashes") or {}).get("z3") if isinstance(compiled, dict) else None,
        },
    }

    turns: List[Optional[int]] = []
    for node in nodes:
        turn = node.get("turn")
        if isinstance(turn, int) and turn not in turns:
            turns.append(turn)
    turns = sorted(turns, key=_turn_key)

    graph_nodes: List[Dict[str, Any]] = [root]
    turn_node_ids: Dict[Optional[int], str] = {}
    for turn in turns:
        turn_id = f"ctrees:turn:{turn}"
        turn_node_ids[turn] = turn_id
        graph_nodes.append(
            {
                "id": turn_id,
                "parent_id": CTREE_TREE_ROOT_ID,
                "kind": "turn",
                "turn": turn,
                "label": f"Turn {turn}",
                "meta": {},
            }
        )

    # Optional subagent grouping: task_event nodes are grouped under a stable task tree.
    tasks_info: Dict[str, Dict[str, Any]] = {}
    for idx, node in enumerate(nodes):
        kind = str(node.get("kind") or "")
        if kind not in {"task_event", "subagent"}:
            continue
        payload = node.get("payload")
        if not isinstance(payload, dict):
            continue
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        task_id = task_id.strip()
        info = tasks_info.get(task_id)
        if info is None:
            info = {"first_index": idx}
            tasks_info[task_id] = info
        parent_task_id = payload.get("parent_task_id")
        if isinstance(parent_task_id, str) and parent_task_id.strip():
            info.setdefault("parent_task_id", parent_task_id.strip())
        tree_path = payload.get("tree_path")
        if isinstance(tree_path, str) and tree_path.strip():
            info.setdefault("tree_path", tree_path.strip())
        subagent_type = payload.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type.strip():
            info.setdefault("subagent_type", subagent_type.strip())

    task_group_node_ids: Dict[str, str] = {}
    if tasks_info:
        tasks_root_id = "ctrees:tasks"
        graph_nodes.append(
            {
                "id": tasks_root_id,
                "parent_id": CTREE_TREE_ROOT_ID,
                "kind": "task_root",
                "turn": None,
                "label": "Tasks",
                "meta": {},
            }
        )
        ordered_task_ids = sorted(tasks_info.keys(), key=lambda task_id: int(tasks_info[task_id].get("first_index", 0)))
        for task_id in ordered_task_ids:
            task_node_id = f"ctrees:task:{task_id}"
            task_group_node_ids[task_id] = task_node_id
        for task_id in ordered_task_ids:
            info = tasks_info.get(task_id) or {}
            parent_task_id = info.get("parent_task_id")
            parent_node_id = tasks_root_id
            if (
                isinstance(parent_task_id, str)
                and parent_task_id
                and parent_task_id != task_id
                and parent_task_id in task_group_node_ids
            ):
                parent_node_id = task_group_node_ids[parent_task_id]
            graph_nodes.append(
                {
                    "id": task_group_node_ids[task_id],
                    "parent_id": parent_node_id,
                    "kind": "task",
                    "turn": None,
                    "label": f"Task {task_id}",
                    "meta": {k: v for k, v in info.items() if k != "first_index"},
                }
            )

    def include_leaf(node: Dict[str, Any]) -> bool:
        node_id = str(node.get("id") or "")
        if not node_id:
            return False
        if stage_key == "RAW":
            return True
        if str(node.get("kind") or "") != "message":
            return True
        if node_id in dropped_ids:
            return False
        if stage_key in {"HEADER", "FROZEN"} and node_id in collapsed_ids:
            return False
        if node_id in kept_ids:
            return True
        # If we didn't compute a selection, default to include.
        return True

    # Emit leaves in append order (stable).
    for node in nodes:
        if not include_leaf(node):
            continue
        node_id = str(node.get("id") or "")
        turn = node.get("turn") if isinstance(node.get("turn"), int) else None
        parent_id = turn_node_ids.get(turn) if turn in turn_node_ids else CTREE_TREE_ROOT_ID
        kind = str(node.get("kind") or "")
        if kind in {"task_event", "subagent"}:
            payload = node.get("payload")
            task_id = payload.get("task_id") if isinstance(payload, dict) else None
            if isinstance(task_id, str) and task_id in task_group_node_ids:
                parent_id = task_group_node_ids[task_id]
        meta: Dict[str, Any] = {
            "digest": node.get("digest"),
            "selected": node_id in selected_ids,
            "kept": node_id in kept_ids,
            "dropped": node_id in dropped_ids,
            "collapsed": node_id in collapsed_ids,
        }
        if kind == "message" and node_id in header_items:
            item = dict(header_items[node_id])
            if not include_previews:
                item.pop("content_preview", None)
                item.pop("content_preview_truncated", None)
                item.pop("content_preview_redacted", None)
            meta.update(
                {
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "payload_hash": item.get("payload_hash"),
                    "content_hash": item.get("content_hash"),
                    "content_len": item.get("content_len"),
                    "tool_call_count": item.get("tool_call_count"),
                }
            )
            if include_previews and item.get("content_preview") is not None:
                meta["content_preview"] = item.get("content_preview")
                meta["content_preview_truncated"] = item.get("content_preview_truncated")
                meta["content_preview_redacted"] = item.get("content_preview_redacted")
        else:
            meta["payload_sha1"] = _payload_sha1(sanitize_ctree_payload(node.get("payload")))

        graph_nodes.append(
            {
                "id": f"ctrees:node:{node_id}",
                "parent_id": parent_id,
                "kind": kind or "node",
                "turn": turn,
                "label": _node_label(node),
                "meta": meta,
            }
        )

    # Emit per-turn collapsed summaries for HEADER/FROZEN.
    if stage_key in {"HEADER", "FROZEN"} and collapsed_ids:
        collapsed_by_turn: Dict[Optional[int], List[str]] = {}
        for node_id in collapsed_ids:
            node = node_by_id.get(node_id)
            turn = node.get("turn") if isinstance(node, dict) and isinstance(node.get("turn"), int) else None
            collapsed_by_turn.setdefault(turn, []).append(node_id)
        for turn, ids in sorted(collapsed_by_turn.items(), key=lambda item: _turn_key(item[0])):
            ids_sorted = [str(v) for v in ids if str(v)]
            if not ids_sorted:
                continue
            parent_id = turn_node_ids.get(turn) if turn in turn_node_ids else CTREE_TREE_ROOT_ID
            graph_nodes.append(
                {
                    "id": f"ctrees:collapsed:{turn if turn is not None else 'unknown'}",
                    "parent_id": parent_id,
                    "kind": "collapsed",
                    "turn": turn,
                    "label": f"collapsed {len(ids_sorted)} messages",
                    "meta": {
                        "collapsed_ids": ids_sorted,
                        "collapsed_sha256": _sha256(ids_sorted),
                    },
                }
            )

    hashes = {}
    if isinstance(compiled, dict):
        hashes = dict(compiled.get("hashes") or {})
    hashes["tree_sha256"] = _sha256([n.get("id") for n in graph_nodes if isinstance(n, dict)])

    return {
        "root_id": CTREE_TREE_ROOT_ID,
        "nodes": graph_nodes,
        "stage": stage_key,
        "selection": selection,
        "hashes": hashes,
    }
