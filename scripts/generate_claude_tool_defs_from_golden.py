#!/usr/bin/env python3
"""
Generate Claude Code (Anthropic) native-tool YAML definitions from a captured golden
normalized request payload (which contains `.body.json.tools`).

This keeps our Claude tool contracts (names, descriptions, input_schema) aligned
to the exact surfaces Claude Code exposes, while still flowing through our YAML
tool registry + provider schema translators.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml


def _load_tools(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = payload.get("body", {}).get("json", {}).get("tools", [])
    if not isinstance(tools, list) or not tools:
        raise ValueError(f"No tools found at .body.json.tools in {path}")
    out: List[Dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name") and tool.get("input_schema"):
            out.append(tool)
    if not out:
        raise ValueError(f"No valid tool definitions found in {path}")
    return out


def _tool_to_yaml(
    tool: Dict[str, Any],
    *,
    tool_id_prefix: str,
) -> Dict[str, Any]:
    name = str(tool["name"])
    description = str(tool.get("description") or "")
    input_schema = tool.get("input_schema") or {}
    if not isinstance(input_schema, dict):
        raise ValueError(f"Tool {name} has non-dict input_schema")

    properties = input_schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required = input_schema.get("required") or []
    if not isinstance(required, list):
        required = []
    required_set = {str(item) for item in required if item is not None}

    parameters: List[Dict[str, Any]] = []
    for prop_name, prop_schema in properties.items():
        if prop_name is None:
            continue
        prop_name = str(prop_name)
        param: Dict[str, Any] = {
            "name": prop_name,
            "required": prop_name in required_set,
        }
        if isinstance(prop_schema, dict):
            # Copy schema fragment verbatim so provider translators can emit
            # the exact same input_schema that Claude Code uses.
            for key, value in prop_schema.items():
                param[key] = value
        parameters.append(param)

    anthropic_root_additional = input_schema.get("additionalProperties")
    if isinstance(anthropic_root_additional, bool):
        additional_properties = anthropic_root_additional
    else:
        # Claude Code schemas always specify this, but keep a safe default.
        additional_properties = False

    schema_uri = input_schema.get("$schema")
    schema_uri_str = str(schema_uri) if isinstance(schema_uri, str) and schema_uri else None

    return {
        "id": f"{tool_id_prefix}.{name}",
        "name": name,
        "description": description,
        "type_id": "python",
        "syntax_formats_supported": ["native_function_calling"],
        "preferred_formats": ["native_function_calling"],
        "parameters": parameters,
        "execution": {"blocking": False},
        "provider_routing": {
            "anthropic": {
                "native_primary": True,
                # These are read by our Anthropic schema translator to match
                # Claude Code's tool input_schema exactly.
                "additional_properties": additional_properties,
                "schema_uri": schema_uri_str or "http://json-schema.org/draft-07/schema#",
            }
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a normalized Claude golden request JSON containing .body.json.tools",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write YAML tool definitions into (will be created).",
    )
    parser.add_argument(
        "--tool-id-prefix",
        default="cc",
        help="Internal tool id prefix (default: cc).",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    tools = _load_tools(input_path)
    for idx, tool in enumerate(tools, start=1):
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        yaml_payload = _tool_to_yaml(tool, tool_id_prefix=str(args.tool_id_prefix))
        filename = f"{idx:03d}_{name}.yaml"
        (output_dir / filename).write_text(
            yaml.safe_dump(
                yaml_payload,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            ),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

