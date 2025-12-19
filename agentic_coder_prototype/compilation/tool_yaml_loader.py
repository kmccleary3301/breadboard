"""
YAML Tool Loader

Loads individual YAML tool definitions from `implementations/tools/defs/` and
validates them into EnhancedToolDefinition instances with provider routing and
manipulation metadata.

YAML schema (per file):

id: run_shell
name: run_shell
description: Execute a shell command
type_id: python
manipulations:
  - shell.exec
syntax_formats_supported: [native_function_calling, json_block, yaml_command]
preferred_formats: [yaml_command]
parameters:
  - name: command
    type: string
    description: The shell command to run
    required: true
  - name: timeout
    type: integer
    required: false
    default: 30
execution:
  blocking: true
  max_per_turn: 1
provider_routing:
  openai:
    native_primary: true
    fallback_formats: [json_block, yaml_command]

"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import yaml

from ..dialects.enhanced_base_dialect import (
    EnhancedToolDefinition,
    EnhancedToolParameter,
)


@dataclass
class LoadedTools:
    tools: List[EnhancedToolDefinition]
    manipulations_by_id: Dict[str, List[str]]
    aliases: Dict[str, str]


REQUIRED_TOP_LEVEL_FIELDS = ["id", "name", "description", "parameters"]


def _validate_tool_dict(tool_data: Dict[str, Any], file_path: str) -> None:
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in tool_data:
            raise ValueError(f"Tool YAML missing required field '{field}' in {file_path}")


def _to_enhanced_params(params: List[Dict[str, Any]]) -> List[EnhancedToolParameter]:
    result: List[EnhancedToolParameter] = []
    for p in params or []:
        schema: Dict[str, Any] = {}
        try:
            if isinstance(p.get("schema"), dict):
                schema = dict(p.get("schema") or {})
            else:
                # Treat remaining keys as schema (preserve nested items/properties).
                schema = {
                    key: value
                    for key, value in (p or {}).items()
                    if key
                    and key
                    not in {
                        "name",
                        "description",
                        "required",
                        "default",
                        "examples",
                        "validation",
                    }
                }
        except Exception:
            schema = {}
        result.append(
            EnhancedToolParameter(
                name=p.get("name"),
                type=p.get("type"),
                description=p.get("description"),
                default=p.get("default"),
                required=bool(p.get("required", False)),
                validation_rules=p.get("validation", {}),
                examples=p.get("examples", []),
                schema=schema,
            )
        )
    return result


def _to_enhanced_tool(tool_data: Dict[str, Any]) -> EnhancedToolDefinition:
    execution = tool_data.get("execution", {})
    provider_routing = tool_data.get("provider_routing", {})

    supported_formats = set(tool_data.get("syntax_formats_supported", []))
    preferred_formats = list(tool_data.get("preferred_formats", []))

    return EnhancedToolDefinition(
        name=tool_data.get("name"),
        description=tool_data.get("description"),
        parameters=_to_enhanced_params(tool_data.get("parameters", [])),
        type_id=tool_data.get("type_id", "python"),
        blocking=bool(execution.get("blocking", False)),
        supported_formats=supported_formats,
        preferred_formats=preferred_formats,
        max_per_turn=execution.get("max_per_turn"),
        provider_settings=provider_routing or {},
    )


def load_yaml_tools(
    defs_dir: str = "implementations/tools/defs",
    overlays: List[Dict[str, Any]] | None = None,
    aliases: Dict[str, str] | None = None,
) -> LoadedTools:
    """
    Load all YAML tool definitions from the given directory.

    Returns:
        LoadedTools: list of EnhancedToolDefinition and manipulations mapping.
    """
    if not os.path.isdir(defs_dir):
        raise FileNotFoundError(f"Tools definitions directory not found: {defs_dir}")

    tools: List[EnhancedToolDefinition] = []
    manipulations_by_id: Dict[str, List[str]] = {}
    aliases = aliases or {}

    # Build overlay maps
    rename_map: Dict[str, str] = {}
    descriptions_map: Dict[str, str] = {}
    syntax_style_map: Dict[str, str] = {}
    provider_pref_map: Dict[str, Dict[str, Any]] = {}

    for ov in overlays or []:
        try:
            rn = ov.get("rename", {}) or {}
            for k, v in rn.items():
                if k and v:
                    rename_map[k] = v
            desc = ov.get("descriptions", {}) or {}
            for k, v in desc.items():
                if k and v:
                    descriptions_map[k] = v
            syn = ov.get("syntax_style", {}) or {}
            for k, v in syn.items():
                if k and v:
                    syntax_style_map[k] = v
            pp = ov.get("provider_preference", {}) or {}
            # Shape: { tool_name: { provider_id: { native_primary: bool, fallbacks: [...] } } }
            for tool_name, by_provider in pp.items():
                if not tool_name:
                    continue
                provider_pref_map.setdefault(tool_name, {})
                provider_pref_map[tool_name].update(by_provider or {})
        except Exception:
            continue

    for fname in sorted(os.listdir(defs_dir)):
        if not fname.endswith(".yaml") and not fname.endswith(".yml"):
            continue
        path = os.path.join(defs_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        _validate_tool_dict(data, path)
        tool = _to_enhanced_tool(data)

        # Apply overlays
        orig_name = tool.name
        if orig_name in rename_map:
            tool.name = rename_map[orig_name]
        if orig_name in descriptions_map:
            tool.description = descriptions_map[orig_name]
        if orig_name in syntax_style_map:
            # Promote syntax style as preferred format
            style = syntax_style_map[orig_name]
            try:
                # Ensure supported_formats reflects the style
                if style not in tool.supported_formats:
                    tool.supported_formats.add(style)
                # Make it first preference
                prefs = [style] + [p for p in (tool.preferred_formats or []) if p != style]
                tool.preferred_formats = prefs
            except Exception:
                pass
        if orig_name in provider_pref_map:
            try:
                ps = tool.provider_settings or {}
                for prov, settings in (provider_pref_map.get(orig_name) or {}).items():
                    ps.setdefault(prov, {})
                    if isinstance(settings, dict):
                        if "native_primary" in settings:
                            ps[prov]["native_primary"] = bool(settings.get("native_primary"))
                        if "fallbacks" in settings and isinstance(settings.get("fallbacks"), list):
                            ps[prov]["fallback_formats"] = list(settings.get("fallbacks"))
                tool.provider_settings = ps
            except Exception:
                pass
        tools.append(tool)

        tool_id = data.get("id") or data.get("name")
        manipulations_by_id[tool_id] = list(data.get("manipulations", []))

    return LoadedTools(tools=tools, manipulations_by_id=manipulations_by_id, aliases=aliases)

