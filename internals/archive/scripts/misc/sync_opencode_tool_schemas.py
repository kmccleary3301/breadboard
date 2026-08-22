#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync OpenCode tool YAMLs from provider dump.")
    parser.add_argument("--dump", required=True, type=Path, help="Provider request dump JSON (contains body.json.tools)")
    parser.add_argument(
        "--tools-dir",
        default=Path("implementations/tools/defs_oc"),
        type=Path,
        help="Directory containing opencode tool YAML files",
    )
    return parser.parse_args()


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def load_dump(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_tool_map(dump: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    tools = (
        dump.get("body", {})
        .get("json", {})
        .get("tools", [])
    )
    tool_map: Dict[str, Dict[str, Any]] = {}
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("function", {}).get("name")
        if not name:
            continue
        tool_map[str(name)] = entry
    return tool_map


def sync_tool(tool_path: Path, tool_def: Dict[str, Any]) -> None:
    if tool_path.exists():
        data = yaml.safe_load(tool_path.read_text(encoding="utf-8")) or {}
    else:
        data = {
            "id": tool_path.stem.replace("opencode_", ""),
            "name": tool_path.stem.replace("opencode_", ""),
            "type_id": "python",
            "manipulations": [],
            "syntax_formats_supported": ["native_function_calling"],
            "preferred_formats": ["native_function_calling"],
            "execution": {"blocking": False},
            "provider_routing": {"openai": {"native_primary": True, "function_call": "auto"}},
        }

    description = tool_def.get("description") or tool_def.get("function", {}).get("description") or ""
    data["description"] = sanitize_text(description)

    params_schema = tool_def.get("parameters") or tool_def.get("function", {}).get("parameters") or {}
    properties = params_schema.get("properties") or {}
    required = params_schema.get("required") or []
    if not isinstance(required, list):
        required = []

    params_out = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            schema = {}
        param = {
            "name": str(name),
            "type": schema.get("type"),
            "description": sanitize_text(schema.get("description") or ""),
            "required": name in required,
            "schema": schema,
        }
        params_out.append(param)
    data["parameters"] = params_out

    # Align OpenAI schema flags to OpenCode dump defaults
    provider_routing = data.get("provider_routing") or {}
    if isinstance(provider_routing, dict):
        openai_cfg = provider_routing.get("openai") or {}
        if isinstance(openai_cfg, dict):
            openai_cfg.setdefault("additional_properties", False)
            openai_cfg.setdefault("strict", False)
            provider_routing["openai"] = openai_cfg
            data["provider_routing"] = provider_routing

    tool_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    dump = load_dump(args.dump)
    tool_map = build_tool_map(dump)

    tools_dir: Path = args.tools_dir
    if not tools_dir.exists():
        raise SystemExit(f"Tools dir not found: {tools_dir}")

    for tool_name, tool_def in tool_map.items():
        tool_file = tools_dir / f"opencode_{tool_name}.yaml"
        sync_tool(tool_file, tool_def)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
