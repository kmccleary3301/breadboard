from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .compiler import compile_ctree
from .policy import collapse_policy
from .store import CTreeStore

CONTEXT_ENGINE_MODES = ("off", "prepend_system", "replace_messages")
CONTEXT_ENGINE_STAGES = ("RAW", "SPEC", "HEADER", "FROZEN")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_context_engine_config(ctrees_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(ctrees_cfg or {})
    engine_cfg = cfg.get("context_engine")
    if not isinstance(engine_cfg, dict):
        engine_cfg = {}

    enabled = bool(engine_cfg.get("enabled", False))

    mode = str(engine_cfg.get("mode", "off") or "off").strip().lower()
    if mode not in CONTEXT_ENGINE_MODES:
        mode = "off"

    stage = str(engine_cfg.get("stage", "SPEC") or "SPEC").strip().upper()
    if stage not in CONTEXT_ENGINE_STAGES:
        stage = "SPEC"

    selection_cfg = engine_cfg.get("selection")
    if not isinstance(selection_cfg, dict):
        selection_cfg = {}

    kind_allowlist = selection_cfg.get("kind_allowlist", selection_cfg.get("kinds"))
    if isinstance(kind_allowlist, str):
        kind_allowlist = [kind_allowlist]
    if not isinstance(kind_allowlist, list) or not kind_allowlist:
        kind_allowlist = ["message"]
    kinds = [str(kind).strip() for kind in kind_allowlist if str(kind).strip()]
    if not kinds:
        kinds = ["message"]

    max_nodes = selection_cfg.get("max_nodes")
    max_turns = selection_cfg.get("max_turns")
    try:
        max_nodes = int(max_nodes) if max_nodes is not None else None
    except Exception:
        max_nodes = None
    try:
        max_turns = int(max_turns) if max_turns is not None else None
    except Exception:
        max_turns = None
    if isinstance(max_nodes, int) and max_nodes <= 0:
        max_nodes = None
    if isinstance(max_turns, int) and max_turns <= 0:
        max_turns = None

    collapse_cfg = engine_cfg.get("collapse")
    if not isinstance(collapse_cfg, dict):
        collapse_cfg = {}
    collapse_target = collapse_cfg.get("target")
    try:
        collapse_target = int(collapse_target) if collapse_target is not None else None
    except Exception:
        collapse_target = None
    if isinstance(collapse_target, int) and collapse_target <= 0:
        collapse_target = None

    collapse_mode = str(collapse_cfg.get("mode", "all_but_last") or "all_but_last").strip().lower()
    if collapse_mode not in {"none", "all_but_last"}:
        collapse_mode = "all_but_last"

    header_cfg = engine_cfg.get("header")
    if not isinstance(header_cfg, dict):
        header_cfg = {}
    header_mode = str(header_cfg.get("mode", "hash_only") or "hash_only").strip().lower()
    if header_mode not in {"hash_only", "sanitized_content"}:
        header_mode = "hash_only"
    preview_chars = header_cfg.get("preview_chars", 160)
    try:
        preview_chars = int(preview_chars) if preview_chars is not None else 160
    except Exception:
        preview_chars = 160
    if preview_chars < 0:
        preview_chars = 0

    redaction_cfg = engine_cfg.get("redaction")
    if not isinstance(redaction_cfg, dict):
        redaction_cfg = {}
    secret_like_strings = redaction_cfg.get("secret_like_strings")
    if secret_like_strings is None:
        secret_like_strings = True
    secret_like_strings = bool(secret_like_strings)

    allow_in_replay = bool(engine_cfg.get("allow_in_replay", False))
    dangerously_allow_replace = bool(engine_cfg.get("dangerously_allow_replace", False))

    return {
        "enabled": enabled,
        "mode": mode,
        "stage": stage,
        "selection": {
            "kind_allowlist": kinds,
            "max_nodes": max_nodes,
            "max_turns": max_turns,
        },
        "collapse": {
            "target": collapse_target,
            "mode": collapse_mode,
        },
        "header": {
            "mode": header_mode,
            "preview_chars": preview_chars,
            "redact_secret_like": secret_like_strings,
        },
        "redaction": {
            "secret_like_strings": secret_like_strings,
        },
        "allow_in_replay": allow_in_replay,
        "dangerously_allow_replace": dangerously_allow_replace,
    }


def _extract_stage(policy: Dict[str, Any], stage: str) -> Dict[str, Any]:
    stages = policy.get("stages")
    if isinstance(stages, list):
        for entry in stages:
            if isinstance(entry, dict) and str(entry.get("stage") or "").upper() == stage:
                return entry
    return {}


def _message_role(node: Dict[str, Any]) -> Optional[str]:
    payload = node.get("payload")
    if not isinstance(payload, dict):
        return None
    role = payload.get("role")
    if role is None:
        return None
    role_str = str(role).strip()
    return role_str or None


def _pin_latest_message_ids(nodes: Sequence[Dict[str, Any]]) -> List[str]:
    last_user = None
    last_assistant = None
    for node in nodes:
        if str(node.get("kind") or "") != "message":
            continue
        role = _message_role(node)
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if role == "user":
            last_user = node_id
        elif role == "assistant":
            last_assistant = node_id
    pinned: List[str] = []
    if last_user:
        pinned.append(last_user)
    if last_assistant and last_assistant != last_user:
        pinned.append(last_assistant)
    return pinned


def _ids_in_append_order(nodes: Sequence[Dict[str, Any]], ids: Sequence[str]) -> List[str]:
    index: Dict[str, int] = {}
    for idx, node in enumerate(nodes):
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id and node_id not in index:
            index[node_id] = idx
    present = []
    for node_id in ids:
        if node_id in index and node_id not in present:
            present.append(node_id)
    return sorted(present, key=lambda value: index.get(value, 10**12))


def select_ctree_context(
    store: CTreeStore,
    *,
    selection_config: Dict[str, Any],
    header_config: Dict[str, Any],
    collapse_target: Optional[int],
    collapse_mode: str = "all_but_last",
    stage: str,
    pin_latest: bool = True,
    extra_pin_ids: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (selection, compiled) where selection is stable and compact.

    The returned selection is deterministic and designed to be safe to surface in metadata and SSE.
    """

    nodes: List[Dict[str, Any]] = list(getattr(store, "nodes", []) or [])
    compiled = compile_ctree(store, config={"selection": dict(selection_config), "header": dict(header_config)})
    header = (compiled.get("stages") or {}).get("HEADER") if isinstance(compiled.get("stages"), dict) else None
    candidate_ids: List[str] = []
    if isinstance(header, dict):
        selected = header.get("selected_node_ids")
        if isinstance(selected, list):
            candidate_ids = [str(item) for item in selected if str(item)]

    # Filter candidates to actual message nodes (stable append-order).
    node_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict) and node.get("id")}
    candidate_ids = [node_id for node_id in candidate_ids if node_id in node_by_id and str(node_by_id[node_id].get("kind") or "") == "message"]

    selected_nodes = [node_by_id[node_id] for node_id in candidate_ids if node_id in node_by_id]
    policy = collapse_policy(SimpleNamespace(nodes=selected_nodes), target=collapse_target, mode=collapse_mode)
    stage_key = str(stage or "SPEC").strip().upper()
    if stage_key not in CONTEXT_ENGINE_STAGES:
        stage_key = "SPEC"

    stage_payload = _extract_stage(policy if isinstance(policy, dict) else {}, stage_key)
    dropped_ids = stage_payload.get("drop") if isinstance(stage_payload, dict) else None
    collapsed_ids = stage_payload.get("collapse") if isinstance(stage_payload, dict) else None
    if not isinstance(dropped_ids, list):
        dropped_ids = []
    if not isinstance(collapsed_ids, list):
        collapsed_ids = []
    dropped_ids = [str(item) for item in dropped_ids if str(item)]
    collapsed_ids = [str(item) for item in collapsed_ids if str(item)]

    pinned_ids: List[str] = _pin_latest_message_ids(nodes) if pin_latest else []
    if extra_pin_ids:
        for pinned in extra_pin_ids:
            if isinstance(pinned, str) and pinned and pinned not in pinned_ids:
                pinned_ids.append(pinned)
    if pinned_ids:
        drop_set = set(dropped_ids)
        collapse_set = set(collapsed_ids)
        for pinned in pinned_ids:
            drop_set.discard(pinned)
            collapse_set.discard(pinned)
        dropped_ids = [item for item in dropped_ids if item in drop_set]
        collapsed_ids = [item for item in collapsed_ids if item in collapse_set]

    dropped_set = set(dropped_ids)
    kept_ids = [node_id for node_id in candidate_ids if node_id not in dropped_set]
    if pinned_ids:
        keep_set = set(kept_ids)
        for pinned in pinned_ids:
            if pinned in node_by_id and pinned not in keep_set:
                kept_ids.append(pinned)
        kept_ids = _ids_in_append_order(nodes, kept_ids)

    collapse_set = set(collapsed_ids)
    collapsed_ids = [node_id for node_id in kept_ids if node_id in collapse_set]

    summary = {
        "stage": stage_key,
        "candidate_count": len(candidate_ids),
        "kept_count": len(kept_ids),
        "dropped_count": len(dropped_ids),
        "collapsed_count": len(collapsed_ids),
        "pinned_count": len(pinned_ids),
        "candidate_ids": list(candidate_ids),
        "kept_ids": list(kept_ids),
        "dropped_ids": list(dropped_ids),
        "collapsed_ids": list(collapsed_ids),
        "pinned_ids": list(_ids_in_append_order(nodes, pinned_ids)) if pinned_ids else [],
    }
    summary["selection_sha256"] = _sha256(summary)
    return summary, compiled


def render_context_system_message(
    *,
    selection: Dict[str, Any],
    compiled: Dict[str, Any],
    mode: str,
) -> Optional[Dict[str, Any]]:
    header = (compiled.get("stages") or {}).get("HEADER") if isinstance(compiled.get("stages"), dict) else None
    if not isinstance(header, dict):
        return None

    kept = set(selection.get("kept_ids") or [])
    collapsed = set(selection.get("collapsed_ids") or [])
    dropped = set(selection.get("dropped_ids") or [])
    pinned = selection.get("pinned_ids") or []
    selection_sha256 = selection.get("selection_sha256")

    items = header.get("messages")
    if not isinstance(items, list):
        items = []

    kept_items = []
    collapsed_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        node_id = item.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if node_id in dropped:
            continue
        if node_id in collapsed:
            collapsed_items.append(item)
        elif node_id in kept:
            kept_items.append(item)

    lines: List[str] = []
    lines.append("C-TREES CONTEXT (deterministic)")
    lines.append(f"mode={mode} stage={selection.get('stage')}")
    if selection_sha256:
        lines.append(f"selection_sha256={selection_sha256}")
    lines.append(
        f"selected={selection.get('candidate_count')} kept={selection.get('kept_count')} "
        f"dropped={selection.get('dropped_count')} collapsed={selection.get('collapsed_count')}"
    )
    if pinned:
        lines.append(f"pinned={','.join(str(x) for x in pinned)}")

    def format_item(item: Dict[str, Any], *, include_preview: bool) -> str:
        role = item.get("role")
        node_id = item.get("node_id")
        turn = item.get("turn")
        content_hash = item.get("content_hash")
        content_len = item.get("content_len")
        tool_call_count = item.get("tool_call_count")
        parts = []
        if turn is not None:
            parts.append(f"turn={turn}")
        if role is not None:
            parts.append(f"role={role}")
        parts.append(f"id={node_id}")
        if include_preview and item.get("content_preview") is not None:
            parts.append(f"preview={json.dumps(item.get('content_preview'), ensure_ascii=True)}")
        if content_hash:
            parts.append(f"content_hash={content_hash}")
        if content_len is not None:
            parts.append(f"len={content_len}")
        if tool_call_count is not None:
            parts.append(f"tool_calls={tool_call_count}")
        return " ".join(parts)

    if kept_items:
        lines.append("kept_messages:")
        for item in kept_items:
            include_preview = bool(header.get("header_mode") == "sanitized_content")
            lines.append(f"- {format_item(item, include_preview=include_preview)}")
    if collapsed_items:
        lines.append("collapsed_messages:")
        for item in collapsed_items:
            lines.append(f"- {format_item(item, include_preview=False)}")

    content = "\n".join(lines).strip() + "\n"
    return {"role": "system", "content": content}
