#!/usr/bin/env python3
"""
Convert a Codex CLI rollout recorder JSONL file into the ReplaySession JSON schema
consumed by `agentic_coder_prototype.replay.load_replay_session()`.

This intentionally focuses on the model-visible interface (MVI):
- Extract the user prompt
- Extract tool calls that affect workspace state (`apply_patch` + `shell_command`)
- Preserve tool arguments (including `workdir`, `timeout_ms`, etc.)
- Preserve tool outputs (Codex CLI returns formatted strings) for replay validation.
 - Preserve UX tool calls (`update_plan`) so we can validate plan-surface parity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def _extract_user_prompt(records: Iterable[Dict[str, Any]]) -> str:
    candidates: List[str] = []
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "message":
            continue
        if payload.get("role") != "user":
            continue
        content = payload.get("content") or []
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "input_text":
                continue
            text = str(part.get("text") or "")
            if not text.strip():
                continue
            candidates.append(text)

    for text in candidates:
        if "<environment_context>" in text:
            continue
        return text
    return ""

def _index_tool_outputs(records: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        if payload.get("type") not in {"function_call_output", "custom_tool_call_output"}:
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        out = payload.get("output")
        if isinstance(out, str):
            outputs[call_id] = out
        else:
            try:
                outputs[call_id] = json.dumps(out, ensure_ascii=False)
            except Exception:
                outputs[call_id] = str(out)
    return outputs


def _extract_tool_calls(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    outputs_by_id = _index_tool_outputs(records)
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        payload_type = payload.get("type")
        if payload_type == "custom_tool_call":
            name = str(payload.get("name") or "")
            if name != "apply_patch":
                continue
            patch_text = payload.get("input")
            if not isinstance(patch_text, str) or not patch_text.strip():
                continue
            call_id = payload.get("call_id")
            output = outputs_by_id.get(call_id) if isinstance(call_id, str) else None
            tool_calls.append(
                {
                    "tool": "apply_patch",
                    "input": {"input": patch_text},
                    "output": output,
                    "status": "completed",
                    "metadata": {"call_id": call_id} if call_id else None,
                }
            )
        elif payload_type == "function_call":
            name = str(payload.get("name") or "")
            raw_args = payload.get("arguments")
            args: Dict[str, Any] = {}
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            call_id = payload.get("call_id")
            output = outputs_by_id.get(call_id) if isinstance(call_id, str) else None
            if name == "shell_command":
                command = args.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                tool_calls.append(
                    {
                        "tool": "shell_command",
                        "input": args,
                        "output": output,
                        "status": "completed",
                        "metadata": {"call_id": call_id} if call_id else None,
                    }
                )
            elif name == "update_plan":
                if not isinstance(args, dict) or not args:
                    continue
                tool_calls.append(
                    {
                        "tool": "update_plan",
                        "input": args,
                        "output": output,
                        "status": "completed",
                        "metadata": {"call_id": call_id} if call_id else None,
                    }
                )
    return tool_calls


def _make_user_entry(prompt: str) -> Dict[str, Any]:
    return {
        "role": "user",
        "message_id": "user_1",
        "parts": [
            {
                "id": "part_user_1",
                "type": "text",
                "text": prompt,
                "meta": {"synthetic": True, "time": {"start": 0, "end": 0}},
            }
        ],
    }


def _make_assistant_entry(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = []
    for idx, call in enumerate(tool_calls, start=1):
        state: Dict[str, Any] = {
            "input": call.get("input") or {},
            "status": call.get("status") or "completed",
            "output": call.get("output"),
        }
        metadata = call.get("metadata")
        if isinstance(metadata, dict) and metadata:
            state["metadata"] = metadata
        parts.append(
            {
                "id": f"part_tool_{idx}",
                "type": "tool",
                "tool": call["tool"],
                "meta": {
                    "state": {
                        **state,
                    }
                },
            }
        )
    return {
        "role": "assistant",
        "message_id": "assistant_1",
        "parts": parts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Codex rollout JSONL into replay_session.json.")
    parser.add_argument("--input", required=True, type=Path, help="Path to Codex rollout JSONL file.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write replay session JSON.")
    args = parser.parse_args()

    records = list(_iter_jsonl(args.input))
    prompt = _extract_user_prompt(records)
    tool_calls = _extract_tool_calls(records)
    if not prompt:
        raise SystemExit("[codex-rollout] failed to locate a usable user prompt in the rollout log")
    if not tool_calls:
        raise SystemExit("[codex-rollout] failed to locate any tool calls (apply_patch/shell_command) in the rollout log")

    session = [_make_user_entry(prompt), _make_assistant_entry(tool_calls)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

