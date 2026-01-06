from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _latest_snapshot(entries: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(entries, list):
        return None
    latest: Optional[Dict[str, Any]] = None
    latest_turn = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        turn = entry.get("turn")
        if isinstance(turn, int):
            if latest_turn is None or turn >= latest_turn:
                latest_turn = turn
                latest = entry
        elif latest is None:
            latest = entry
    return latest


_KNOWN_SURFACE_DETERMINISM: Dict[str, str] = {
    "tool_schema_latest": "deterministic",
    "tool_catalog": "deterministic",
    "tool_allowlist_latest": "deterministic",
    "tool_prompt_mode": "deterministic",
    "provider_tools_config": "deterministic",
    "system_roles": "deterministic",
    "prompt_hashes": "deterministic",
    "mcp_snapshot": "replayable",
    "plugin_snapshot": "deterministic",
    "skill_catalog": "deterministic",
    "hook_snapshot": "deterministic",
    "ctrees_snapshot": "deterministic",
    "ctrees_compiler": "deterministic",
    "ctrees_collapse": "deterministic",
    "ctrees_runner": "deterministic",
}


def _mcp_determinism(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "replayable"
    servers = payload.get("servers")
    transports = set()
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict):
                transport = server.get("transport")
                if isinstance(transport, str):
                    transports.add(transport.lower())
    if transports & {"http", "https", "sse", "ws", "wss", "websocket"}:
        return "external"
    return "replayable" if transports else "deterministic"


def _determinism_for_surface(name: str, payload: Any) -> Tuple[str, bool]:
    if name == "mcp_snapshot":
        return _mcp_determinism(payload), True
    if name in _KNOWN_SURFACE_DETERMINISM:
        return _KNOWN_SURFACE_DETERMINISM[name], True
    return "deterministic", False


def _surface_entry(
    name: str,
    payload: Any,
    *,
    determinism: str,
    scope: str = "run",
) -> Dict[str, Any]:
    return {
        "name": name,
        "hash": _hash_payload(payload),
        "determinism": determinism,
        "scope": scope,
        "payload": payload,
    }


def build_surface_manifest(
    *,
    prompt_summary: Optional[Dict[str, Any]] = None,
    surface_snapshot: Optional[Dict[str, Any]] = None,
    required_surfaces: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    surfaces: List[Dict[str, Any]] = []
    unclassified: List[str] = []
    snapshot = surface_snapshot or {}
    mcp_determinism: Optional[str] = None
    mcp_snapshot = snapshot.get("mcp_snapshot")
    if isinstance(mcp_snapshot, dict) and mcp_snapshot:
        try:
            mcp_determinism, _ = _determinism_for_surface("mcp_snapshot", mcp_snapshot)
        except Exception:
            mcp_determinism = None

    tool_schema_latest = _latest_snapshot(snapshot.get("tool_schema_snapshots"))
    if tool_schema_latest:
        determinism, classified = _determinism_for_surface("tool_schema_latest", tool_schema_latest)
        if mcp_determinism in {"external", "replayable"}:
            determinism = mcp_determinism
        if not classified:
            unclassified.append("tool_schema_latest")
        surfaces.append(_surface_entry("tool_schema_latest", tool_schema_latest, determinism=determinism))

    tool_catalog = snapshot.get("tool_catalog")
    if isinstance(tool_catalog, dict) and tool_catalog:
        determinism, classified = _determinism_for_surface("tool_catalog", tool_catalog)
        if mcp_determinism in {"external", "replayable"}:
            determinism = mcp_determinism
        if not classified:
            unclassified.append("tool_catalog")
        surfaces.append(_surface_entry("tool_catalog", tool_catalog, determinism=determinism))

    tool_allow_latest = _latest_snapshot(snapshot.get("tool_allowlist_snapshots"))
    if tool_allow_latest:
        determinism, classified = _determinism_for_surface("tool_allowlist_latest", tool_allow_latest)
        if not classified:
            unclassified.append("tool_allowlist_latest")
        surfaces.append(_surface_entry("tool_allowlist_latest", tool_allow_latest, determinism=determinism))

    tool_prompt_mode = snapshot.get("tool_prompt_mode")
    if tool_prompt_mode:
        determinism, classified = _determinism_for_surface("tool_prompt_mode", tool_prompt_mode)
        if not classified:
            unclassified.append("tool_prompt_mode")
        surfaces.append(_surface_entry("tool_prompt_mode", tool_prompt_mode, determinism=determinism))

    provider_tools = snapshot.get("provider_tools")
    if isinstance(provider_tools, dict) and provider_tools:
        determinism, classified = _determinism_for_surface("provider_tools_config", provider_tools)
        if not classified:
            unclassified.append("provider_tools_config")
        surfaces.append(_surface_entry("provider_tools_config", provider_tools, determinism=determinism))

    system_roles = snapshot.get("system_roles")
    if isinstance(system_roles, list) and system_roles:
        determinism, classified = _determinism_for_surface("system_roles", system_roles)
        if not classified:
            unclassified.append("system_roles")
        surfaces.append(_surface_entry("system_roles", system_roles, determinism=determinism))

    prompt_payload = prompt_summary or snapshot.get("prompts")
    if isinstance(prompt_payload, dict) and prompt_payload:
        determinism, classified = _determinism_for_surface("prompt_hashes", prompt_payload)
        if not classified:
            unclassified.append("prompt_hashes")
        surfaces.append(_surface_entry("prompt_hashes", prompt_payload, determinism=determinism))

    if isinstance(mcp_snapshot, dict) and mcp_snapshot:
        determinism, classified = _determinism_for_surface("mcp_snapshot", mcp_snapshot)
        if not classified:
            unclassified.append("mcp_snapshot")
        surfaces.append(_surface_entry("mcp_snapshot", mcp_snapshot, determinism=determinism))

    plugin_snapshot = snapshot.get("plugin_snapshot")
    if isinstance(plugin_snapshot, dict) and plugin_snapshot:
        determinism, classified = _determinism_for_surface("plugin_snapshot", plugin_snapshot)
        if not classified:
            unclassified.append("plugin_snapshot")
        surfaces.append(_surface_entry("plugin_snapshot", plugin_snapshot, determinism=determinism))

    skill_catalog = snapshot.get("skill_catalog")
    if isinstance(skill_catalog, dict) and skill_catalog:
        determinism, classified = _determinism_for_surface("skill_catalog", skill_catalog)
        if not classified:
            unclassified.append("skill_catalog")
        surfaces.append(_surface_entry("skill_catalog", skill_catalog, determinism=determinism))

    hook_snapshot = snapshot.get("hook_snapshot")
    if isinstance(hook_snapshot, dict) and hook_snapshot:
        determinism, classified = _determinism_for_surface("hook_snapshot", hook_snapshot)
        if not classified:
            unclassified.append("hook_snapshot")
        surfaces.append(_surface_entry("hook_snapshot", hook_snapshot, determinism=determinism))

    ctrees_snapshot = snapshot.get("ctrees_snapshot")
    if isinstance(ctrees_snapshot, dict) and ctrees_snapshot:
        determinism, classified = _determinism_for_surface("ctrees_snapshot", ctrees_snapshot)
        if not classified:
            unclassified.append("ctrees_snapshot")
        surfaces.append(_surface_entry("ctrees_snapshot", ctrees_snapshot, determinism=determinism))

    ctrees_compiler = snapshot.get("ctrees_compiler")
    if isinstance(ctrees_compiler, dict) and ctrees_compiler:
        determinism, classified = _determinism_for_surface("ctrees_compiler", ctrees_compiler)
        if not classified:
            unclassified.append("ctrees_compiler")
        surfaces.append(_surface_entry("ctrees_compiler", ctrees_compiler, determinism=determinism))

    ctrees_collapse = snapshot.get("ctrees_collapse")
    if isinstance(ctrees_collapse, dict) and ctrees_collapse:
        determinism, classified = _determinism_for_surface("ctrees_collapse", ctrees_collapse)
        if not classified:
            unclassified.append("ctrees_collapse")
        surfaces.append(_surface_entry("ctrees_collapse", ctrees_collapse, determinism=determinism))

    ctrees_runner = snapshot.get("ctrees_runner")
    if isinstance(ctrees_runner, dict) and ctrees_runner:
        determinism, classified = _determinism_for_surface("ctrees_runner", ctrees_runner)
        if not classified:
            unclassified.append("ctrees_runner")
        surfaces.append(_surface_entry("ctrees_runner", ctrees_runner, determinism=determinism))

    if not surfaces and not required_surfaces:
        return None
    required = [str(name) for name in (required_surfaces or []) if name]
    present = {entry.get("name") for entry in surfaces if isinstance(entry, dict)}
    missing = [name for name in required if name not in present]

    payload: Dict[str, Any] = {
        "surface_count": len(surfaces),
        "surfaces": surfaces,
    }
    if unclassified:
        payload["unclassified"] = sorted(set(unclassified))
    if required:
        payload["required"] = required
        payload["missing"] = missing
    return payload
