from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def mcp_live_tools_enabled(config: Dict[str, Any]) -> bool:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        return bool(mcp_cfg.get("enabled"))
    return False


def load_mcp_servers_from_config(config: Dict[str, Any], workspace: str) -> List[Dict[str, Any]]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    servers = []
    if isinstance(mcp_cfg, dict):
        raw = mcp_cfg.get("servers")
        if isinstance(raw, list):
            servers = [entry for entry in raw if isinstance(entry, dict)]
    return servers


def load_mcp_tools_from_config(config: Dict[str, Any], workspace: str) -> List[Dict[str, Any]]:
    # Placeholder; real MCP tooling surfaces tools via live servers.
    return []


def tool_defs_from_mcp_tools(mcp_tools: List[Dict[str, Any]]) -> List[Any]:
    # Placeholder; tool defs are produced elsewhere once MCP tools are modeled.
    return []


def load_mcp_fixture_results(config: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        fixtures = mcp_cfg.get("fixture_results")
        if isinstance(fixtures, dict):
            return dict(fixtures)
    return {}


def mcp_replay_tape_path(config: Dict[str, Any], workspace: str) -> Optional[str]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        path = mcp_cfg.get("replay_tape")
        if isinstance(path, str) and path.strip():
            return str(Path(workspace) / path) if not Path(path).is_absolute() else path
    return None


def mcp_record_tape_path(config: Dict[str, Any], workspace: str) -> Optional[str]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        path = mcp_cfg.get("record_tape")
        if isinstance(path, str) and path.strip():
            return str(Path(workspace) / path) if not Path(path).is_absolute() else path
    return None


def build_mcp_snapshot(servers: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"servers": servers, "tool_count": len(tools)}

