#!/usr/bin/env python3
"""
Convert a raw Claude Code session export into the replay-friendly JSON schema.

Usage:
    python scripts/convert_claude_session.py \
        --source misc/claude_code_tests/cc_protofs/session_main.json \
        --output misc/claude_code_tests/protofs_claude_session.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_tool_results(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map tool_use_id -> result payload + message text."""
    results: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        message = entry.get("message") or {}
        content_blocks = message.get("content") or []
        if not isinstance(content_blocks, list):
            continue
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if not tool_id:
                    continue
                results[tool_id] = {
                    "text": block.get("content"),
                    "result": entry.get("toolUseResult"),
                }
    return results


def convert_session(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tool_results = load_tool_results(entries)
    converted: List[Dict[str, Any]] = []
    for entry in entries:
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")

        if role == "user":
            # Skip synthetic user messages that merely echo tool results
            if isinstance(content, str):
                converted.append(
                    {
                        "role": "user",
                        "message_id": entry.get("uuid") or entry.get("messageId"),
                        "parts": [
                            {
                                "id": entry.get("uuid"),
                                "type": "text",
                                "text": content,
                            }
                        ],
                    }
                )
            continue

        if role != "assistant":
            continue

        parts: List[Dict[str, Any]] = []
        for idx, block in enumerate(content or []):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    parts.append(
                        {
                            "id": f"text_{idx}",
                            "type": "text",
                            "text": text,
                        }
                    )
            elif block_type == "tool_use":
                tool_id = block.get("id") or f"tool_{idx}"
                tool_name = block.get("name")
                input_payload = block.get("input")
                result_payload = tool_results.get(tool_id)
                output_value: Any = None
                if result_payload:
                    output_value = result_payload.get("result")
                    if tool_name in {"Bash", "bash", "run_shell"} and isinstance(output_value, dict):
                        stdout = output_value.get("stdout")
                        if stdout is not None:
                            output_value = stdout
                    if output_value is None:
                        output_value = result_payload.get("text")
                    if output_value is None:
                        output_value = result_payload
                parts.append(
                    {
                        "id": tool_id,
                        "type": "tool",
                        "tool": tool_name,
                        "meta": {
                            "state": {
                                "input": input_payload,
                                "status": "completed",
                                "output": output_value,
                            }
                        },
                    }
                )

        if parts:
            converted.append(
                {
                    "role": "assistant",
                    "message_id": entry.get("uuid"),
                    "parts": parts,
                }
            )
    return converted


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Claude session export to replay JSON schema.")
    parser.add_argument("--source", required=True, help="Path to Claude Code session JSON.")
    parser.add_argument("--output", required=True, help="Path to write normalized session JSON.")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    output_path = Path(args.output).resolve()

    entries = json.loads(source_path.read_text(encoding="utf-8"))
    converted = convert_session(entries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(converted)} entries to {output_path}")


if __name__ == "__main__":
    main()
