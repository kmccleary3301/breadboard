#!/usr/bin/env python3
"""
Render TPSL (tool prompt synthesis) catalogs for a given config and mode without
calling any provider. Useful for verifying template selection and content.

Usage:
  python scripts/compile_tpsl_preview.py agent_configs/opencode_grok4fast_c_fs_v2.yaml plan
  python scripts/compile_tpsl_preview.py agent_configs/opencode_grok4fast_c_fs_v2.yaml build
"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Any, Dict, List

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.compilation.system_prompt_compiler import get_compiler
from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools
from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter


def _to_tool_defs_from_dirs(paths: List[str], include: List[str]) -> List[ToolDefinition]:
    tools: List[ToolDefinition] = []
    seen = set()
    for p in paths:
        loaded = load_yaml_tools(defs_dir=p)
        for t in loaded.tools:
            name = getattr(t, "name", None) or getattr(t, "id", None)
            if not name:
                continue
            if include and name not in include:
                continue
            if name in seen:
                continue
            seen.add(name)
            params = []
            for ep in getattr(t, "parameters", []) or []:
                params.append(ToolParameter(
                    name=getattr(ep, "name", None),
                    type=getattr(ep, "type", None),
                    description=getattr(ep, "description", None),
                    default=getattr(ep, "default", None),
                ))
            tools.append(ToolDefinition(
                name=name,
                description=getattr(t, "description", ""),
                parameters=params,
                type_id=getattr(t, "type_id", "python"),
                blocking=bool(getattr(t, "blocking", False)),
            ))
    return tools


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: compile_tpsl_preview.py <config.yaml> <mode>")
        sys.exit(2)
    cfg_path = Path(sys.argv[1]).resolve()
    mode = sys.argv[2]
    config = load_agent_config(str(cfg_path))
    compiler = get_compiler()

    # Assemble tools from registry.paths (v2 normalized) with include filter
    reg = (config.get("tools", {}) or {}).get("registry", {}) or {}
    paths = reg.get("paths") or []
    include = reg.get("include") or []
    if not paths:
        # v2 normalizer sets tools.defs_dir when possible; use it as fallback
        defs_dir = (config.get("tools", {}) or {}).get("defs_dir")
        if defs_dir:
            paths = [defs_dir]
    tool_defs: List[ToolDefinition] = _to_tool_defs_from_dirs(paths, include)

    # We don’t need dialect names for TPSL; pass empty to rely on config selection
    compiled = compiler.compile_v2_prompts(config, mode, tool_defs, dialects=[])
    print("=== SYSTEM ===")
    print(compiled.get("system", ""))
    print("\n=== PER_TURN ===")
    print(compiled.get("per_turn", ""))
    # Emit metadata if present
    if isinstance(compiled.get("tpsl"), dict):
        meta = compiled["tpsl"]
        print("\n=== META ===")
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()

