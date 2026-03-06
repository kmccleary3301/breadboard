#!/usr/bin/env python3
"""Convert Codex CLI rollout JSONL into replay_session JSON.

Supports both legacy Codex event shape (response_item/payload) and newer event
shape (item.completed/item.started with `item` payloads).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def _extract_user_prompt(records: Iterable[Dict[str, Any]], *, fallback_prompt: str) -> str:
    candidates: List[str] = []
    for record in records:
        record_type = record.get("type")
        if record_type == "response_item":
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
                if text.strip():
                    candidates.append(text)
        elif record_type == "item.completed":
            item = record.get("item") or {}
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            role = str(item.get("role") or "")
            if item_type not in {"message", "user_message"} and role != "user":
                continue
            text = str(item.get("text") or "")
            if text.strip():
                candidates.append(text)
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        maybe_text = str(part.get("text") or "")
                        if maybe_text.strip():
                            candidates.append(maybe_text)

    for text in candidates:
        if "<environment_context>" in text:
            continue
        return text
    return fallback_prompt.strip()


def _extract_assistant_text(records: Iterable[Dict[str, Any]]) -> str:
    candidates: List[str] = []
    for record in records:
        record_type = record.get("type")
        if record_type == "response_item":
            payload = record.get("payload") or {}
            if payload.get("type") != "message":
                continue
            if payload.get("role") != "assistant":
                continue
            content = payload.get("content") or []
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict):
                    text = str(part.get("text") or "")
                    if text.strip():
                        candidates.append(text)
        elif record_type == "item.completed":
            item = record.get("item") or {}
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            role = str(item.get("role") or "")
            if item_type not in {"agent_message", "assistant_message", "message"} and role != "assistant":
                continue
            text = str(item.get("text") or "")
            if text.strip():
                candidates.append(text)
    return candidates[-1] if candidates else ""

def _index_tool_outputs(records: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    for record in records:
        record_type = record.get("type")
        if record_type == "response_item":
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
        elif record_type in {"item.completed", "item.started"}:
            item = record.get("item") or {}
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"function_call_output", "custom_tool_call_output", "tool_result"}:
                continue
            call_id = item.get("call_id") or item.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            out = item.get("output", item.get("content"))
            if isinstance(out, str):
                outputs[call_id] = out
            else:
                try:
                    outputs[call_id] = json.dumps(out, ensure_ascii=False)
                except Exception:
                    outputs[call_id] = str(out)
    return outputs


def _parse_jsonish_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _extract_tool_calls(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    outputs_by_id = _index_tool_outputs(records)
    for record in records:
        record_type = record.get("type")
        if record_type == "response_item":
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
                args = _parse_jsonish_dict(payload.get("arguments"))
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
        elif record_type == "item.completed":
            item = record.get("item") or {}
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "custom_tool_call":
                name = str(item.get("name") or "")
                if name != "apply_patch":
                    continue
                patch_text = item.get("input")
                if not isinstance(patch_text, str) or not patch_text.strip():
                    continue
                call_id = item.get("call_id") or item.get("id")
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
            elif item_type == "function_call":
                name = str(item.get("name") or "")
                args = _parse_jsonish_dict(item.get("arguments"))
                call_id = item.get("call_id") or item.get("id")
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
            elif item_type == "command_execution":
                command = item.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                call_id = item.get("call_id") or item.get("id")
                tool_calls.append(
                    {
                        "tool": "shell_command",
                        "input": {"command": command},
                        "output": item.get("aggregated_output"),
                        "status": "completed",
                        "metadata": {"call_id": call_id} if call_id else None,
                    }
                )
            elif item_type == "collab_tool_call":
                tool_name = str(item.get("tool") or "")
                if not tool_name:
                    continue
                call_id = item.get("call_id") or item.get("id")
                input_payload: Dict[str, Any] = {}
                for key in ("prompt", "sender_thread_id", "receiver_thread_ids"):
                    if key in item:
                        input_payload[key] = item.get(key)
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "input": input_payload,
                        "output": item.get("agents_states"),
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


def _make_assistant_entry(tool_calls: List[Dict[str, Any]], assistant_text: str) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = []
    if assistant_text.strip():
        parts.append(
            {
                "id": "part_assistant_text_1",
                "type": "text",
                "text": assistant_text,
                "meta": {"synthetic": True, "time": {"start": 0, "end": 0}},
            }
        )
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
    parser.add_argument(
        "--fallback-user-prompt",
        default="",
        help="Fallback prompt used when rollout JSONL does not include a user message item.",
    )
    parser.add_argument(
        "--allow-empty-tools",
        action="store_true",
        help="Allow conversion when no eligible tool calls are present.",
    )
    args = parser.parse_args()

    records = list(_iter_jsonl(args.input))
    prompt = _extract_user_prompt(records, fallback_prompt=args.fallback_user_prompt)
    assistant_text = _extract_assistant_text(records)
    tool_calls = _extract_tool_calls(records)
    if not prompt:
        raise SystemExit("[codex-rollout] failed to locate a usable user prompt in the rollout log (and no fallback provided)")
    if not tool_calls and not args.allow_empty_tools:
        raise SystemExit("[codex-rollout] failed to locate any tool calls (apply_patch/shell_command) in the rollout log")

    session = [_make_user_entry(prompt), _make_assistant_entry(tool_calls, assistant_text)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
