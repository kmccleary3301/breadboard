from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from .store import CTreeStore
from .schema import CTREE_SCHEMA_VERSION
from .schema import canonical_ctree_json, sanitize_ctree_payload


def _canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def _hash_payload(value: Any) -> str:
    blob = _canonical_dumps(value)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _hash_ctree_value(value: Any) -> str:
    blob = canonical_ctree_json(value)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _node_ref(node: Dict[str, Any]) -> Dict[str, Any]:
    payload = node.get("payload")
    payload_hash = _hash_ctree_value(payload)
    payload_shape: Any = None
    if isinstance(payload, dict):
        payload_shape = sorted(str(key) for key in payload.keys())
    elif isinstance(payload, list):
        payload_shape = [len(payload)]
    else:
        payload_shape = type(payload).__name__

    return {
        "id": str(node.get("id") or ""),
        "digest": str(node.get("digest") or ""),
        "kind": str(node.get("kind") or ""),
        "turn": node.get("turn"),
        "payload_hash": payload_hash,
        "payload_shape": payload_shape,
    }


def _kind_counts(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in nodes:
        kind = str(node.get("kind") or "")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


_SECRET_LIKE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)api[_-]?key\\s*[:=]\\s*\\S+"),
    re.compile(r"(?i)authorization\\s*[:=]\\s*\\S+"),
    re.compile(r"(?i)bearer\\s+[A-Za-z0-9._-]{12,}"),
)


def _looks_secret_like(text: str) -> bool:
    lowered = text.lower()
    if "-----begin" in lowered and "private key" in lowered:
        return True
    for pattern in _SECRET_LIKE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _message_role(node: Dict[str, Any]) -> Optional[str]:
    payload = node.get("payload")
    if not isinstance(payload, dict):
        return None
    role = payload.get("role")
    if role is None:
        return None
    role_str = str(role).strip()
    return role_str or None


def _pin_latest_user_and_assistant_ids(nodes: List[Dict[str, Any]]) -> List[str]:
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


def _pin_first_system_id(nodes: List[Dict[str, Any]]) -> Optional[str]:
    for node in nodes:
        if str(node.get("kind") or "") != "message":
            continue
        if _message_role(node) != "system":
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id:
            return node_id
    return None


def _ids_in_append_order(nodes: List[Dict[str, Any]], ids: List[str]) -> List[str]:
    index: Dict[str, int] = {}
    for idx, node in enumerate(nodes):
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id and node_id not in index:
            index[node_id] = idx
    present: List[str] = []
    for node_id in ids:
        if node_id in index and node_id not in present:
            present.append(node_id)
    return sorted(present, key=lambda value: index.get(value, 10**12))


def _preview_text(content: Any, *, preview_chars: int, redact_secret_like: bool) -> Optional[Dict[str, Any]]:
    if not isinstance(content, str):
        return None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if redact_secret_like and _looks_secret_like(normalized):
        return {"text": "***REDACTED***", "truncated": False, "redacted": True}
    truncated = False
    if preview_chars > 0 and len(normalized) > preview_chars:
        normalized = normalized[:preview_chars]
        truncated = True
    return {"text": normalized, "truncated": truncated, "redacted": False}


def _message_header_item(
    node: Dict[str, Any],
    *,
    header_mode: str,
    preview_chars: int,
    redact_secret_like: bool,
) -> Dict[str, Any]:
    payload = node.get("payload")
    if not isinstance(payload, dict):
        sanitized = sanitize_ctree_payload(payload)
        return {
            "node_id": str(node.get("id") or ""),
            "kind": str(node.get("kind") or ""),
            "payload_hash": _hash_ctree_value(sanitized),
        }
    role = payload.get("role")
    content = payload.get("content")
    if content is None and "text" in payload:
        content = payload.get("text")
    tool_calls = payload.get("tool_calls")
    name = payload.get("name")
    payload_hash = _hash_ctree_value(payload)
    content_hash = _hash_ctree_value(content if content is not None else payload)
    tool_calls_hash = _hash_ctree_value(tool_calls) if tool_calls is not None else None
    content_len = len(content) if isinstance(content, str) else None
    tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else None
    item: Dict[str, Any] = {
        "node_id": str(node.get("id") or ""),
        "role": str(role) if role is not None else None,
        "name": str(name) if name is not None else None,
        "payload_hash": payload_hash,
        "content_hash": content_hash,
        "content_len": content_len,
        "tool_call_count": tool_call_count,
    }
    if tool_calls_hash is not None:
        item["tool_calls_hash"] = tool_calls_hash
    if header_mode == "sanitized_content":
        preview = _preview_text(content, preview_chars=preview_chars, redact_secret_like=redact_secret_like)
        if preview:
            item["content_preview"] = preview.get("text")
            item["content_preview_truncated"] = bool(preview.get("truncated"))
            item["content_preview_redacted"] = bool(preview.get("redacted"))
    return item


def _compiler_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(config or {})
    selection_cfg = cfg.get("selection")
    header_cfg = cfg.get("header")
    if not isinstance(selection_cfg, dict):
        selection_cfg = {}
    if not isinstance(header_cfg, dict):
        header_cfg = {}

    kind_allowlist = selection_cfg.get("kind_allowlist", cfg.get("kind_allowlist"))
    if isinstance(kind_allowlist, str):
        kind_allowlist = [kind_allowlist]
    if not isinstance(kind_allowlist, list) or not kind_allowlist:
        kind_allowlist = ["message"]
    kinds = [str(kind) for kind in kind_allowlist if str(kind).strip()]
    if not kinds:
        kinds = ["message"]

    max_nodes = selection_cfg.get("max_nodes", cfg.get("max_nodes"))
    max_turns = selection_cfg.get("max_turns", cfg.get("max_turns"))
    try:
        max_nodes = int(max_nodes) if max_nodes is not None else None
    except Exception:
        max_nodes = None
    try:
        max_turns = int(max_turns) if max_turns is not None else None
    except Exception:
        max_turns = None
    if max_nodes is not None and max_nodes < 0:
        max_nodes = None
    if max_turns is not None and max_turns < 0:
        max_turns = None

    pin_latest_user_assistant = selection_cfg.get("pin_latest_user_assistant")
    if pin_latest_user_assistant is None:
        pin_latest_user_assistant = True
    pin_latest_user_assistant = bool(pin_latest_user_assistant)

    pin_first_system = selection_cfg.get("pin_first_system")
    if pin_first_system is None:
        pin_first_system = False
    pin_first_system = bool(pin_first_system)

    max_message_chars = selection_cfg.get("max_message_chars")
    try:
        max_message_chars = int(max_message_chars) if max_message_chars is not None else None
    except Exception:
        max_message_chars = None
    if max_message_chars is not None and max_message_chars <= 0:
        max_message_chars = None

    header_mode = header_cfg.get("mode", cfg.get("header_mode"))
    if not isinstance(header_mode, str) or not header_mode.strip():
        header_mode = "hash_only"
    header_mode = header_mode.strip().lower()
    if header_mode not in {"hash_only", "sanitized_content"}:
        header_mode = "hash_only"

    preview_chars = header_cfg.get("preview_chars", cfg.get("preview_chars"))
    try:
        preview_chars = int(preview_chars) if preview_chars is not None else 160
    except Exception:
        preview_chars = 160
    if preview_chars < 0:
        preview_chars = 0

    redact_secret_like = header_cfg.get("redact_secret_like", cfg.get("redact_secret_like"))
    if redact_secret_like is None:
        redact_secret_like = True
    redact_secret_like = bool(redact_secret_like)

    return {
        "kind_allowlist": kinds,
        "max_nodes": max_nodes,
        "max_turns": max_turns,
        "pin_latest_user_assistant": pin_latest_user_assistant,
        "pin_first_system": pin_first_system,
        "max_message_chars": max_message_chars,
        "header_mode": header_mode,
        "preview_chars": preview_chars,
        "redact_secret_like": redact_secret_like,
    }


def _select_nodes(
    nodes: List[Dict[str, Any]],
    *,
    kind_allowlist: List[str],
    max_turns: Optional[int],
    max_nodes: Optional[int],
    pinned_ids: List[str],
    max_message_chars: Optional[int],
) -> List[Dict[str, Any]]:
    allow = set(kind_allowlist)
    selected = [node for node in nodes if str(node.get("kind") or "") in allow]

    pinned_set = set(str(value) for value in pinned_ids if str(value))

    if max_turns is not None and max_turns > 0 and selected:
        turns_seen: List[int] = []
        for node in selected:
            turn_raw = node.get("turn")
            if isinstance(turn_raw, int) and turn_raw not in turns_seen:
                turns_seen.append(turn_raw)
        if turns_seen:
            keep_turns = set(turns_seen[-max_turns:])
            selected = [
                node
                for node in selected
                if (node.get("turn") is None) or (isinstance(node.get("turn"), int) and node.get("turn") in keep_turns)
            ]

    if max_nodes is not None and max_nodes >= 0 and len(selected) > max_nodes:
        selected = selected[-max_nodes:]

    if max_message_chars is not None and max_message_chars > 0 and selected:
        # Deterministic budgeting in reverse (latest-first), with pins always included.
        kept_rev: List[Dict[str, Any]] = []
        remaining = int(max_message_chars)
        for node in reversed(selected):
            node_id = node.get("id")
            is_pinned = isinstance(node_id, str) and node_id in pinned_set
            if str(node.get("kind") or "") == "message":
                payload = node.get("payload")
                content = payload.get("content") if isinstance(payload, dict) else None
                if content is None and isinstance(payload, dict) and "text" in payload:
                    content = payload.get("text")
                content_len = len(content) if isinstance(content, str) else 0
                if not is_pinned and kept_rev and content_len > remaining:
                    continue
                remaining = max(0, remaining - content_len)
            kept_rev.append(node)
        selected = list(reversed(kept_rev))

    # Ensure pinned nodes are present in the final selection (append-order).
    if pinned_ids and selected:
        node_by_id = {str(node.get("id")): node for node in selected if node.get("id")}
        for node in nodes:
            node_id = node.get("id")
            if not isinstance(node_id, str) or node_id not in pinned_set:
                continue
            if node_id not in node_by_id and str(node.get("kind") or "") in allow:
                selected.append(node)
                node_by_id[node_id] = node
        ids = _ids_in_append_order(nodes, [str(node.get("id") or "") for node in selected])
        selected_by_id = {str(node.get("id")): node for node in selected if node.get("id")}
        selected = [selected_by_id[node_id] for node_id in ids if node_id in selected_by_id]

    return selected


def compile_ctree(
    store: CTreeStore,
    *,
    prompt_summary: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic compiler producing staged summaries + hashes.

    This compiler is intentionally "small output": it avoids embedding full node payloads so that
    `ctree_snapshot` events remain compact and safe-by-default.
    """

    hashes = {}
    try:
        hashes = store.hashes()
    except Exception:
        hashes = {}
    node_count = len(getattr(store, "nodes", []) or [])
    event_count = len(getattr(store, "events", []) or [])

    nodes: List[Dict[str, Any]] = list(getattr(store, "nodes", []) or [])
    kind_counts = _kind_counts(nodes)

    raw_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "node_hash": hashes.get("node_hash"),
        "kind_counts": kind_counts,
    }
    if prompt_summary:
        raw_payload["prompt_summary"] = prompt_summary

    cfg = _compiler_config(config)
    selected_kinds = tuple(cfg["kind_allowlist"])

    pinned_ids: List[str] = []
    if "message" in set(cfg["kind_allowlist"]):
        if cfg.get("pin_latest_user_assistant"):
            pinned_ids.extend(_pin_latest_user_and_assistant_ids(nodes))
        if cfg.get("pin_first_system"):
            system_id = _pin_first_system_id(nodes)
            if system_id and system_id not in pinned_ids:
                pinned_ids.append(system_id)

    selected_nodes = _select_nodes(
        nodes,
        kind_allowlist=list(cfg["kind_allowlist"]),
        max_turns=cfg["max_turns"],
        max_nodes=cfg["max_nodes"],
        pinned_ids=list(pinned_ids),
        max_message_chars=cfg.get("max_message_chars"),
    )
    selected_ids = _ids_in_append_order(nodes, [str(node.get("id") or "") for node in selected_nodes if node.get("id")])
    if selected_ids:
        selected_by_id = {str(node.get("id")): node for node in selected_nodes if node.get("id")}
        selected_nodes = [selected_by_id[node_id] for node_id in selected_ids if node_id in selected_by_id]

    candidate_ids = _ids_in_append_order(
        nodes,
        [
            str(node.get("id") or "")
            for node in nodes
            if node.get("id") and str(node.get("kind") or "") in set(cfg["kind_allowlist"])
        ],
    )

    pinned_ids_ordered = (
        list(_ids_in_append_order(nodes, [str(v) for v in pinned_ids if str(v)])) if pinned_ids else []
    )
    dropped_ids = [node_id for node_id in candidate_ids if node_id not in set(selected_ids)]
    selection_summary: Dict[str, Any] = {
        "kind_allowlist": list(selected_kinds),
        "candidate_ids": list(candidate_ids),
        "candidate_count": len(candidate_ids),
        "selected_ids": list(selected_ids),
        "selected_count": len(selected_ids),
        "kept_ids": list(selected_ids),
        "kept_count": len(selected_ids),
        "dropped_ids": list(dropped_ids),
        "dropped_count": len(dropped_ids),
        "collapsed_ids": [],
        "collapsed_count": 0,
        "pinned_count": len(pinned_ids_ordered),
        "max_nodes": cfg["max_nodes"],
        "max_turns": cfg["max_turns"],
        "max_message_chars": cfg.get("max_message_chars"),
        "pinned_ids": pinned_ids_ordered,
    }
    selection_summary["selection_sha256"] = _hash_payload(selection_summary)

    spec_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "selection": selection_summary,
        "selected": [_node_ref(node) for node in selected_nodes],
    }
    message_nodes = [node for node in selected_nodes if str(node.get("kind") or "") == "message"]
    header_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "policy": "messages_only",
        "header_mode": cfg["header_mode"],
        "node_hash": hashes.get("node_hash"),
        "selected_node_ids": selected_ids,
        "selection_sha256": selection_summary.get("selection_sha256"),
        "messages": [
            _message_header_item(
                node,
                header_mode=cfg["header_mode"],
                preview_chars=cfg["preview_chars"],
                redact_secret_like=cfg["redact_secret_like"],
            )
            for node in message_nodes
        ],
    }

    z1 = _hash_payload(raw_payload)
    z2 = _hash_payload(spec_payload)
    z3 = _hash_payload(header_payload)

    return {
        "kind": "ctree_compiler",
        "compiler_version": CTREE_SCHEMA_VERSION,
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "has_prompt_summary": bool(prompt_summary),
        "hashes": {
            **hashes,
            "z1": z1,
            "z2": z2,
            "z3": z3,
        },
        "stages": {
            "RAW": raw_payload,
            "SPEC": spec_payload,
            "HEADER": header_payload,
            "FROZEN": header_payload,
        },
    }
