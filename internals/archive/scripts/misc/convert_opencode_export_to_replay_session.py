#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except Exception:
        return None


def _convert_tool_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tool = _coerce_str(part.get("tool"))
    if not tool:
        return None

    state = part.get("state") or {}
    if not isinstance(state, dict):
        state = {}

    meta_state: Dict[str, Any] = {
        "status": state.get("status"),
        "input": state.get("input") or {},
    }
    if "output" in state:
        meta_state["output"] = state.get("output")
    if "error" in state:
        meta_state["error"] = state.get("error")
    if "metadata" in state:
        meta_state["metadata"] = state.get("metadata")

    call_id = _coerce_str(part.get("callID") or part.get("call_id") or "")
    part_id = call_id or _coerce_str(part.get("id") or "")
    if not part_id:
        part_id = f"tool_{tool}"

    return {
        "id": part_id,
        "type": "tool",
        "tool": tool,
        "meta": {"state": meta_state},
    }


def _convert_text_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "text" not in part:
        return None
    return {
        "id": _coerce_str(part.get("id") or "text"),
        "type": "text",
        "text": _coerce_str(part.get("text")),
    }


def convert_opencode_export(export_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = export_payload.get("messages") or []
    if not isinstance(messages, list):
        raise ValueError("OpenCode export payload missing 'messages' list")

    out: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        info = msg.get("info") or {}
        if not isinstance(info, dict):
            info = {}

        role = _coerce_str(info.get("role"))
        if not role:
            continue

        created = None
        time_payload = info.get("time")
        if isinstance(time_payload, dict):
            created = _coerce_int(time_payload.get("created"))

        entry: Dict[str, Any] = {
            "role": role,
            "message_id": _coerce_str(info.get("id") or info.get("messageID") or ""),
            "created": created,
            "parts": [],
        }

        parts = msg.get("parts") or []
        if not isinstance(parts, list):
            parts = []

        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "tool":
                converted = _convert_tool_part(part)
                if converted is not None:
                    entry["parts"].append(converted)
            elif part_type == "text":
                converted = _convert_text_part(part)
                if converted is not None:
                    entry["parts"].append(converted)

        if entry["parts"]:
            out.append(entry)

    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert OpenCode export.json to BreadBoard replay-session JSON.")
    parser.add_argument("--in", dest="in_path", required=True, help="Path to OpenCode export.json")
    parser.add_argument("--out", dest="out_path", required=True, help="Path to write replay session JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    in_path = Path(args.in_path).expanduser().resolve()
    out_path = Path(args.out_path).expanduser().resolve()

    payload = json.loads(in_path.read_text(encoding="utf-8"))
    converted = convert_opencode_export(payload)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(converted)} messages to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

