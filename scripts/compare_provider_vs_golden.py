#!/usr/bin/env python3
"""
Compare normalized provider request prompts against a golden OpenCode transcript.

This script focuses on the tool-call sequence emitted during a live run and
highlights how it diverges from the golden session's tool usage. The output is a
JSON report that can be used by downstream docs or dashboards.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare provider request prompts to a golden transcript."
    )
    parser.add_argument(
        "--normalized-dir",
        required=True,
        type=Path,
        help="Directory created by process_provider_dumps.py (contains turn_*.json).",
    )
    parser.add_argument(
        "--golden-transcript",
        required=True,
        type=Path,
        help="Path to misc/opencode_tests/protofs_todo_session.json (or similar).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Where to write the comparison JSON report.",
    )
    return parser.parse_args()


def load_turn_paths(normalized_dir: Path) -> List[Path]:
    paths = sorted(normalized_dir.glob("turn_*_request.json"))
    if not paths:
        raise FileNotFoundError(f"No turn_*_request.json files in {normalized_dir}")
    return paths


def _extract_prompt(payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    params = payload.get("params")
    if isinstance(params, dict) and isinstance(params.get("prompt"), list):
        return params.get("prompt")
    body = payload.get("body")
    if isinstance(body, dict):
        body_json = body.get("json")
        if isinstance(body_json, dict) and isinstance(body_json.get("messages"), list):
            prompt: List[Dict[str, Any]] = []
            for message in body_json["messages"]:
                role = message.get("role")
                content = message.get("content")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    content_value = " ".join(text_parts)
                elif isinstance(content, str):
                    content_value = content
                else:
                    content_value = ""
                prompt.append({"role": role, "content": content_value})
            return prompt
    return None


def extract_provider_tool_sequence(turn_paths: List[Path]) -> List[Dict[str, Any]]:
    sequence: List[Dict[str, Any]] = []
    previous_prompt: List[Any] = []

    for turn_index, path in enumerate(turn_paths, start=1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        prompt = _extract_prompt(payload)
        if not isinstance(prompt, list):
            continue

        if previous_prompt and prompt[: len(previous_prompt)] != previous_prompt:
            previous_prompt = prompt
            new_messages = prompt
        else:
            new_messages = prompt[len(previous_prompt) :]
            previous_prompt = prompt

        for message in new_messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content", [])
            if isinstance(content, str):
                sequence.append(
                    {
                        "turn": turn_index,
                        "tool": None,
                        "content_type": "text",
                        "text": content,
                    }
                )
                continue
            if not isinstance(content, list):
                continue

            for chunk in content:
                if chunk.get("type") == "tool-call":
                    sequence.append(
                        {
                            "turn": turn_index,
                            "tool": chunk.get("toolName"),
                            "tool_call_id": chunk.get("toolCallId"),
                            "content_type": "tool-call",
                        }
                    )
    return sequence


def extract_golden_tool_sequence(golden_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    sequence: List[Dict[str, Any]] = []
    assistant_turn = 0

    for message in data:
        if message.get("role") != "assistant":
            continue
        assistant_turn += 1
        for part in message.get("parts", []):
            if part.get("type") == "tool":
                sequence.append(
                    {
                        "turn": assistant_turn,
                        "tool": part.get("tool"),
                        "meta": part.get("meta"),
                    }
                )
    return sequence


def build_tool_comparison(
    live_sequence: List[Dict[str, Any]], golden_sequence: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    length = max(len(live_sequence), len(golden_sequence))
    comparison: List[Dict[str, Any]] = []

    for idx in range(length):
        live = live_sequence[idx] if idx < len(live_sequence) else None
        golden = golden_sequence[idx] if idx < len(golden_sequence) else None
        comparison.append(
            {
                "index": idx + 1,
                "live_tool": live.get("tool") if live else None,
                "golden_tool": golden.get("tool") if golden else None,
                "match": (live and golden and live.get("tool") == golden.get("tool")),
                "live_turn": live.get("turn") if live else None,
                "golden_turn": golden.get("turn") if golden else None,
            }
        )
    return comparison


def main() -> int:
    args = parse_args()
    turn_paths = load_turn_paths(args.normalized_dir)
    live_sequence = extract_provider_tool_sequence(turn_paths)
    golden_sequence = extract_golden_tool_sequence(args.golden_transcript)
    comparison = build_tool_comparison(live_sequence, golden_sequence)

    live_tool_names = [
        entry["tool"] for entry in live_sequence if entry.get("tool")
    ]
    golden_tool_names = [
        entry["tool"] for entry in golden_sequence if entry.get("tool")
    ]
    live_tool_set = set(live_tool_names)
    golden_tool_set = set(golden_tool_names)

    report = {
        "normalized_dir": str(args.normalized_dir),
        "golden_transcript": str(args.golden_transcript),
        "live_sequence_count": len(live_sequence),
        "golden_sequence_count": len(golden_sequence),
        "comparison": comparison,
        "live_only_tools": sorted(live_tool_set - golden_tool_set),
        "golden_only_tools": sorted(golden_tool_set - live_tool_set),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(f"[info] Comparison written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
