#!/usr/bin/env python3
"""
Convert OpenAI Responses API provider dumps (normalized turn_*_request/response)
into replay-session JSON entries for a single session_id.

This mirrors the Claude conversion flow but targets the OpenAI Responses API:
- Pick the main user prompt by frequency across requests.
- Parse response.completed SSE events to recover message text + function_call items.
- Attach function_call_output payloads from subsequent requests by call_id.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ModelCall:
    turn: int
    request: Dict[str, Any]
    response: Dict[str, Any]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_turn_requests(normalized_dir: Path) -> Iterable[Tuple[int, Path]]:
    for path in sorted(normalized_dir.glob("turn_*_request.json")):
        stem = path.stem
        try:
            turn = int(stem.split("_", 2)[1])
        except Exception:
            continue
        yield turn, path


def _is_responses_request(payload: Dict[str, Any]) -> bool:
    url = str(payload.get("targetUrl") or "")
    if not url.startswith("https://api.openai.com/v1/responses"):
        return False
    body = payload.get("body") or {}
    return isinstance(body, dict) and isinstance(body.get("json"), dict)


def _extract_request_body(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = payload.get("body") or {}
    if not isinstance(body, dict):
        return None
    body_json = body.get("json")
    return body_json if isinstance(body_json, dict) else None


def _iter_user_text_blocks(inputs: Any) -> Iterable[str]:
    if not isinstance(inputs, list):
        return
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            text = content
            if text.strip():
                yield text
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "input_text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    yield text


def _pick_prompt_text(model_calls: List[ModelCall]) -> Optional[str]:
    counts: Counter[str] = Counter()
    for call in model_calls:
        body = _extract_request_body(call.request) or {}
        for text in _iter_user_text_blocks(body.get("input")):
            stripped = text.strip()
            if not stripped:
                continue
            counts[text] += 1
    if not counts:
        return None
    best, _ = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))
    return best


def _request_contains_prompt(payload: Dict[str, Any], prompt: str) -> bool:
    body = _extract_request_body(payload) or {}
    for text in _iter_user_text_blocks(body.get("input")):
        if text == prompt:
            return True
    return False


def _parse_sse_events(text: str) -> Iterable[Tuple[str, Dict[str, Any]]]:
    event_type: Optional[str] = None
    data_lines: List[str] = []

    def flush() -> Optional[Tuple[str, Dict[str, Any]]]:
        nonlocal event_type, data_lines
        if not event_type or not data_lines:
            event_type = None
            data_lines = []
            return None
        raw = "\n".join(data_lines).strip()
        event = event_type
        event_type = None
        data_lines = []
        try:
            return event, json.loads(raw)
        except Exception:
            return None

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            evt = flush()
            if evt:
                yield evt
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
            continue
    evt = flush()
    if evt:
        yield evt


def _parse_openai_response(response_payload: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    body = response_payload.get("body") or {}
    if not isinstance(body, dict):
        return None, []
    sse_text = body.get("text")
    if not isinstance(sse_text, str):
        return None, []

    message_id: Optional[str] = None
    items: List[Dict[str, Any]] = []

    for event_type, data in _parse_sse_events(sse_text):
        if event_type != "response.completed":
            continue
        response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(response, dict):
            continue
        message_id = response.get("id") if isinstance(response.get("id"), str) else message_id
        output = response.get("output") or []
        if not isinstance(output, list):
            continue
        for item in output:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "message":
                content = item.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            text = block.get("text")
                            if isinstance(text, str) and text.strip():
                                items.append({"type": "text", "text": text})
            elif itype == "function_call":
                items.append(
                    {
                        "type": "tool_use",
                        "id": item.get("call_id") or item.get("id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                        "status": item.get("status"),
                    }
                )
    return message_id, items


def _extract_tool_outputs_from_request(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    body = _extract_request_body(request_payload) or {}
    outputs: Dict[str, Any] = {}
    for item in body.get("input") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call_output":
            continue
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        outputs[call_id] = item.get("output")
    return outputs


def load_model_calls(normalized_dir: Path, session_id: str) -> List[ModelCall]:
    calls: List[ModelCall] = []
    for turn, req_path in _iter_turn_requests(normalized_dir):
        request = _load_json(req_path)
        if request.get("sessionId") != session_id:
            continue
        if not _is_responses_request(request):
            continue
        resp_path = req_path.with_name(req_path.name.replace("_request", "_response"))
        if not resp_path.exists():
            continue
        response = _load_json(resp_path)
        calls.append(ModelCall(turn=turn, request=request, response=response))
    return calls


def convert_session(normalized_dir: Path, session_id: str) -> List[Dict[str, Any]]:
    model_calls = load_model_calls(normalized_dir, session_id)
    prompt = _pick_prompt_text(model_calls)
    if not prompt:
        raise RuntimeError(f"No user prompt found for session {session_id}")

    scoped: List[ModelCall] = []
    started = False
    for call in model_calls:
        if not started:
            if _request_contains_prompt(call.request, prompt):
                started = True
            else:
                continue
        if _request_contains_prompt(call.request, prompt):
            scoped.append(call)

    if not scoped:
        raise RuntimeError(f"No calls matched prompt for session {session_id}")

    tool_outputs: Dict[str, Any] = {}
    for call in model_calls:
        tool_outputs.update(_extract_tool_outputs_from_request(call.request))

    entries: List[Dict[str, Any]] = [
        {
            "role": "user",
            "message_id": f"user_{session_id}",
            "parts": [{"id": f"user_{session_id}", "type": "text", "text": prompt}],
        }
    ]

    for call in scoped:
        message_id, items = _parse_openai_response(call.response)
        parts: List[Dict[str, Any]] = []
        text_idx = 0
        for item in items:
            if item.get("type") == "text":
                text = str(item.get("text") or "")
                if text.strip():
                    parts.append({"id": f"text_{text_idx}", "type": "text", "text": text})
                    text_idx += 1
            elif item.get("type") == "tool_use":
                call_id = item.get("id")
                tool_name = item.get("name")
                raw_args = item.get("arguments") or "{}"
                args: Dict[str, Any] = {}
                if isinstance(raw_args, str) and raw_args.strip():
                    try:
                        parsed = json.loads(raw_args)
                        if isinstance(parsed, dict):
                            args = parsed
                    except Exception:
                        args = {}
                output = tool_outputs.get(call_id)
                parts.append(
                    {
                        "id": call_id or f"tool_{call.turn}_{len(parts)}",
                        "type": "tool",
                        "tool": tool_name or "unknown",
                        "meta": {
                            "state": {
                                "input": args,
                                "status": item.get("status") or "completed",
                                "output": output,
                            }
                        },
                    }
                )

        if parts:
            entries.append(
                {
                    "role": "assistant",
                    "message_id": message_id or f"assistant_{call.turn}",
                    "parts": parts,
                }
            )

    return entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert OpenAI normalized turn logs to replay session JSON.")
    parser.add_argument("--normalized-dir", required=True, help="Path to normalized/ directory")
    parser.add_argument("--session-id", required=True, help="Session ID to extract")
    parser.add_argument("--out", required=True, help="Output replay session JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    normalized_dir = Path(args.normalized_dir).resolve()
    out_path = Path(args.out).resolve()
    entries = convert_session(normalized_dir, args.session_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[openai->replay] wrote {len(entries)} entries -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
