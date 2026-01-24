from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter


def mcp_live_tools_enabled(config: Dict[str, Any]) -> bool:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    return bool(mcp_cfg.get("enabled")) if isinstance(mcp_cfg, dict) else False


def _expand_path(value: str, workspace: str) -> str:
    tokenized = value.replace("{workspace}", str(workspace)).replace("{home}", str(Path.home()))
    expanded = os.path.expanduser(os.path.expandvars(tokenized))
    path = Path(expanded)
    if not path.is_absolute():
        path = (Path(str(workspace)) / path).resolve()
    return str(path)


def load_mcp_servers_from_config(config: Dict[str, Any], workspace: str) -> List[Dict[str, Any]]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    raw_servers: List[Any] = []
    if isinstance(mcp_cfg, dict):
        raw = mcp_cfg.get("servers")
        if isinstance(raw, list):
            raw_servers = list(raw)
    servers: List[Dict[str, Any]] = []
    for entry in raw_servers:
        if not isinstance(entry, dict):
            continue
        normalized = dict(entry)
        cwd = normalized.get("cwd") or normalized.get("working_dir") or normalized.get("workdir")
        if isinstance(cwd, str) and cwd.strip():
            normalized["cwd"] = _expand_path(cwd.strip(), workspace)
        command = normalized.get("command") or normalized.get("cmd")
        if isinstance(command, str) and command.strip():
            normalized["command"] = command.strip()
        args = normalized.get("args") or normalized.get("arguments")
        if isinstance(args, list):
            normalized["args"] = [str(a) for a in args if isinstance(a, (str, int, float))]
        env = normalized.get("env")
        if isinstance(env, dict):
            normalized["env"] = {str(k): str(v) for k, v in env.items()}
        servers.append(normalized)
    return servers


def load_mcp_tools_from_config(config: Dict[str, Any], workspace: str) -> List[Dict[str, Any]]:
    """Load static MCP tools from config (for mocks/replay/baselines).

    Format:
      mcp:
        tools:
          - name: mcp.files.echo
            description: ...
            schema: { ...json schema... }
    """
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if not isinstance(mcp_cfg, dict):
        return []
    raw = mcp_cfg.get("tools")
    if not isinstance(raw, list):
        return []
    tools: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        schema = entry.get("schema") or entry.get("inputSchema")
        if schema is not None and not isinstance(schema, dict):
            continue
        tools.append(
            {
                "name": name.strip(),
                "description": entry.get("description"),
                "schema": dict(schema or {}),
                "server": entry.get("server"),
                "source": "config",
            }
        )
    return tools


def tool_defs_from_mcp_tools(mcp_tools: List[Dict[str, Any]]) -> List[ToolDefinition]:
    out: List[ToolDefinition] = []
    for tool in mcp_tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = tool.get("description")
        if not isinstance(description, str):
            description = ""
        schema = tool.get("schema") or tool.get("inputSchema") or {}
        if not isinstance(schema, dict):
            schema = {}

        params: List[ToolParameter] = []
        if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
            properties: Dict[str, Any] = schema.get("properties") or {}
            required = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_name, str) or not prop_name.strip():
                    continue
                prop_schema = prop_schema if isinstance(prop_schema, dict) else {}
                param = ToolParameter(
                    name=prop_name,
                    type=str(prop_schema.get("type")) if prop_schema.get("type") else None,
                    description=str(prop_schema.get("description")) if isinstance(prop_schema.get("description"), str) else None,
                )
                try:
                    setattr(param, "schema", dict(prop_schema))
                except Exception:
                    pass
                if prop_name in required:
                    try:
                        setattr(param, "required", True)
                    except Exception:
                        pass
                params.append(param)
        else:
            param = ToolParameter(name="input", type="object", description="Tool input payload")
            try:
                setattr(param, "schema", dict(schema))
                setattr(param, "required", True)
            except Exception:
                pass
            params.append(param)

        tool_def = ToolDefinition(name=name.strip(), description=description.strip(), parameters=params, type_id="mcp", blocking=True)
        try:
            tool_def.provider_settings = {
                "openai": {"native_primary": True},
                "anthropic": {"native_primary": True},
            }
        except Exception:
            pass
        out.append(tool_def)
    return out


def load_mcp_fixture_results(config: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        fixtures = mcp_cfg.get("fixture_results") or mcp_cfg.get("fixtures")
        if isinstance(fixtures, dict):
            return dict(fixtures)
    return {}


def mcp_replay_tape_path(config: Dict[str, Any], workspace: str) -> Optional[str]:
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp_cfg, dict):
        path = mcp_cfg.get("replay_tape")
        if isinstance(path, str) and path.strip():
            return _expand_path(path.strip(), workspace)
    return None


def mcp_record_tape_path(config: Dict[str, Any], workspace: str, run_dir: Path | None = None) -> Optional[str]:
    """Return a writable JSONL tape path for recording live MCP calls.

    Supported values:
      - record_tape: false -> disabled
      - record_tape: true  -> default path (prefer run_dir)
      - record_tape: "path.jsonl" -> explicit path (workspace-relative allowed)
      - record_tape omitted -> default path only when mcp.enabled is true and run_dir is provided
    """
    mcp_cfg = (config or {}).get("mcp") if isinstance(config, dict) else None
    if not isinstance(mcp_cfg, dict):
        return None
    raw = mcp_cfg.get("record_tape")
    if raw is False:
        return None
    if isinstance(raw, str) and raw.strip():
        return _expand_path(raw.strip(), workspace)
    if raw is True:
        if run_dir is not None:
            return str(Path(run_dir) / "mcp_tape.jsonl")
        return str(Path(str(workspace)) / ".breadboard" / "mcp_tape.jsonl")
    # raw is None / omitted
    if mcp_live_tools_enabled(config) and run_dir is not None:
        return str(Path(run_dir) / "mcp_tape.jsonl")
    return None


def build_mcp_snapshot(
    servers: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    fixture_results: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "enabled": True,
        "servers": list(servers or []),
        "tool_count": len(tools or []),
        "tools": list(tools or []),
        "fixture_keys": sorted(list((fixture_results or {}).keys())) if isinstance(fixture_results, dict) else [],
    }

