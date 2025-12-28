from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


def _hash_payload(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _extract_tool_name(tool: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tool, dict):
        return None
    func = tool.get("function")
    if isinstance(func, dict):
        name = func.get("name")
        if name:
            return str(name)
    name = tool.get("name")
    if name:
        return str(name)
    return None


def _tool_sort_key(tool: Dict[str, Any]) -> str:
    name = _extract_tool_name(tool)
    if name:
        return name
    try:
        return json.dumps(tool, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return str(tool)


def build_tool_schema_snapshot(
    tools_schema: Any,
    *,
    turn_index: int,
) -> Optional[Dict[str, Any]]:
    if not isinstance(tools_schema, list) or not tools_schema:
        return None
    tools_list = [tool for tool in tools_schema if isinstance(tool, dict)]
    if not tools_list:
        return None
    tool_names = [_extract_tool_name(tool) for tool in tools_list]
    tool_names = [name for name in tool_names if name]
    ordered_hash = _hash_payload(tools_list)
    sorted_tools = sorted(tools_list, key=_tool_sort_key)
    sorted_hash = _hash_payload(sorted_tools)
    names_sorted = sorted(set(tool_names))
    return {
        "turn": int(turn_index),
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "tool_names_sorted": names_sorted,
        "schema_hash": sorted_hash,
        "schema_hash_ordered": ordered_hash,
    }


def record_tool_schema_snapshot(
    session_state: Any,
    tools_schema: Any,
    *,
    turn_index: int,
) -> None:
    entry = build_tool_schema_snapshot(tools_schema, turn_index=turn_index)
    if not entry:
        return
    try:
        snapshots = session_state.get_provider_metadata("tool_schema_snapshots", [])
    except Exception:
        snapshots = []
    if not isinstance(snapshots, list):
        snapshots = []
    snapshots.append(entry)
    try:
        session_state.set_provider_metadata("tool_schema_snapshots", snapshots)
    except Exception:
        pass


def record_tool_allowlist_snapshot(
    session_state: Any,
    tool_names: List[str],
    *,
    turn_index: int,
) -> None:
    if not tool_names:
        return
    ordered = [str(name) for name in tool_names if name]
    if not ordered:
        return
    ordered_hash = _hash_payload(ordered)
    sorted_names = sorted(set(ordered))
    sorted_hash = _hash_payload(sorted_names)
    entry = {
        "turn": int(turn_index),
        "tool_count": len(ordered),
        "tool_names": ordered,
        "tool_names_sorted": sorted_names,
        "allowlist_hash": sorted_hash,
        "allowlist_hash_ordered": ordered_hash,
    }
    try:
        snapshots = session_state.get_provider_metadata("tool_allowlist_snapshots", [])
    except Exception:
        snapshots = []
    if not isinstance(snapshots, list):
        snapshots = []
    snapshots.append(entry)
    try:
        session_state.set_provider_metadata("tool_allowlist_snapshots", snapshots)
    except Exception:
        pass


def build_surface_snapshot(
    conductor: Any,
    session_state: Any,
    *,
    prompt_summary: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    snapshot: Dict[str, Any] = {}

    if prompt_summary:
        snapshot["prompts"] = prompt_summary

    tool_prompt_mode = getattr(session_state, "last_tool_prompt_mode", None)
    if tool_prompt_mode:
        snapshot["tool_prompt_mode"] = tool_prompt_mode

    provider_tools_cfg = getattr(conductor, "_provider_tools_effective", None)
    if provider_tools_cfg is None:
        try:
            provider_tools_cfg = (conductor.config or {}).get("provider_tools")
        except Exception:
            provider_tools_cfg = None
    if isinstance(provider_tools_cfg, dict) and provider_tools_cfg:
        snapshot["provider_tools"] = dict(provider_tools_cfg)

    try:
        system_roles = sorted(
            {
                str(msg.get("role"))
                for msg in getattr(session_state, "messages", []) or []
                if isinstance(msg, dict) and msg.get("role") in {"system", "developer"}
            }
        )
        if system_roles:
            snapshot["system_roles"] = system_roles
    except Exception:
        pass

    try:
        tool_schema_snapshots = session_state.get_provider_metadata("tool_schema_snapshots", [])
        if tool_schema_snapshots:
            snapshot["tool_schema_snapshots"] = tool_schema_snapshots
    except Exception:
        pass

    try:
        tool_allowlist_snapshots = session_state.get_provider_metadata("tool_allowlist_snapshots", [])
        if tool_allowlist_snapshots:
            snapshot["tool_allowlist_snapshots"] = tool_allowlist_snapshots
    except Exception:
        pass

    return snapshot or None
