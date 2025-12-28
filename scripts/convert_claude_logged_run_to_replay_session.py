#!/usr/bin/env python3
"""
Convert a claude-code-logged capture (provider_dumps + normalized turn files)
into the replay session JSON schema consumed by agentic_coder_prototype.replay.

Key points:
- Claude Code uses Anthropic streaming (SSE). We reconstruct tool_use blocks from
  the SSE payload and attach the subsequent request's tool_result blocks as the
  expected tool outputs.
- We intentionally scope to the "main" conversation thread by anchoring on the
  first non-warmup user prompt text seen in the model request bodies, then only
  including subsequent /v1/messages calls whose request payload still contains
  that prompt.
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
        # turn_001_request -> 001
        try:
            turn = int(stem.split("_", 2)[1])
        except Exception:
            continue
        yield turn, path


def _is_messages_request(payload: Dict[str, Any]) -> bool:
    url = str(payload.get("targetUrl") or "")
    if not url.startswith("https://api.anthropic.com/v1/messages"):
        return False
    if "count_tokens" in url:
        return False
    body = payload.get("body") or {}
    return isinstance(body, dict) and isinstance(body.get("json"), dict)


def _extract_request_body(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = payload.get("body") or {}
    if not isinstance(body, dict):
        return None
    body_json = body.get("json")
    return body_json if isinstance(body_json, dict) else None


def _iter_user_text_blocks(messages: Any) -> Iterable[str]:
    if not isinstance(messages, list):
        return
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    yield text


def _pick_prompt_text(model_calls: List[ModelCall]) -> Optional[str]:
    """
    Pick the main user prompt by frequency.

    Claude Code requests include many user-role text blocks (system reminders,
    tool_result echoes, etc). The top-level task prompt is typically repeated
    verbatim across most /v1/messages calls in the same run, so we choose the
    most frequently occurring non-warmup text.
    """
    counts: Counter[str] = Counter()
    for call in model_calls:
        body = _extract_request_body(call.request) or {}
        for text in _iter_user_text_blocks(body.get("messages")):
            stripped = text.strip()
            if not stripped:
                continue
            if stripped == "Warmup":
                continue
            if stripped.startswith("<system-reminder>"):
                continue
            counts[text] += 1
    if not counts:
        return None
    best, _ = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))
    return best


def _request_contains_prompt(payload: Dict[str, Any], prompt: str) -> bool:
    body = _extract_request_body(payload) or {}
    for text in _iter_user_text_blocks(body.get("messages")):
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


def _parse_anthropic_sse_message(response_payload: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    body = response_payload.get("body") or {}
    if not isinstance(body, dict):
        return None, []
    sse_text = body.get("text")
    if not isinstance(sse_text, str):
        return None, []

    message_id: Optional[str] = None
    blocks: Dict[int, Dict[str, Any]] = {}

    for event_type, data in _parse_sse_events(sse_text):
        if event_type == "message_start":
            msg = data.get("message") if isinstance(data, dict) else None
            if isinstance(msg, dict):
                mid = msg.get("id")
                if isinstance(mid, str) and mid:
                    message_id = mid
            continue

        if event_type == "content_block_start":
            idx = data.get("index")
            block = data.get("content_block")
            if not isinstance(idx, int) or not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                blocks[idx] = {"type": "text", "text": str(block.get("text") or "")}
            elif btype == "tool_use":
                blocks[idx] = {
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                    "_partial_json": "",
                }
            else:
                # Preserve unknown blocks so we don't accidentally shift indices.
                blocks[idx] = {"type": str(btype or "unknown"), "raw": block}
            continue

        if event_type == "content_block_delta":
            idx = data.get("index")
            delta = data.get("delta")
            if not isinstance(idx, int) or not isinstance(delta, dict):
                continue
            existing = blocks.get(idx)
            if not isinstance(existing, dict):
                continue
            dtype = delta.get("type")
            if existing.get("type") == "text" and dtype == "text_delta":
                existing["text"] = str(existing.get("text") or "") + str(delta.get("text") or "")
            if existing.get("type") == "tool_use" and dtype == "input_json_delta":
                existing["_partial_json"] = str(existing.get("_partial_json") or "") + str(delta.get("partial_json") or "")
            continue

    rendered: List[Dict[str, Any]] = []
    for idx in sorted(blocks):
        block = dict(blocks[idx])
        if block.get("type") == "tool_use":
            partial = str(block.pop("_partial_json", "") or "")
            if partial.strip():
                try:
                    parsed = json.loads(partial)
                    if isinstance(parsed, dict):
                        base = dict(block.get("input") or {})
                        base.update(parsed)
                        block["input"] = base
                except Exception:
                    block["input_raw_json"] = partial
        rendered.append(block)
    return message_id, rendered


def _extract_tool_results_from_request(request_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    body = _extract_request_body(request_payload) or {}
    messages = body.get("messages") or []
    results: Dict[str, Dict[str, Any]] = {}
    if not isinstance(messages, list):
        return results
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            content_value = block.get("content")
            if isinstance(content_value, str):
                rendered = content_value
            else:
                rendered = json.dumps(content_value, ensure_ascii=False)
            agent_ids = _extract_agent_ids_from_tool_output(content_value)
            results[tool_use_id] = {
                "content": rendered,
                "is_error": bool(block.get("is_error", False)),
                "agent_ids": agent_ids,
            }
    return results


def load_model_calls(normalized_dir: Path) -> List[ModelCall]:
    calls: List[ModelCall] = []
    for turn, req_path in _iter_turn_requests(normalized_dir):
        request = _load_json(req_path)
        if not _is_messages_request(request):
            continue
        resp_path = req_path.with_name(req_path.name.replace("_request", "_response"))
        if not resp_path.exists():
            continue
        response = _load_json(resp_path)
        calls.append(ModelCall(turn=turn, request=request, response=response))
    return calls


def convert_run(run_dir: Path) -> Dict[str, Any]:
    normalized_dir = run_dir / "normalized"
    if not normalized_dir.exists():
        raise FileNotFoundError(f"Missing normalized dir: {normalized_dir}")

    model_calls = load_model_calls(normalized_dir)
    prompt = _pick_prompt_text(model_calls)
    if not prompt:
        raise RuntimeError("Failed to locate a non-warmup user prompt in model calls.")

    scoped: List[ModelCall] = []
    started = False
    for call in model_calls:
        if not started:
            if _request_contains_prompt(call.request, prompt):
                started = True
            else:
                continue
        # Claude Code may interleave internal /v1/messages calls (e.g. Bash output
        # file-path extraction) that do not contain the user prompt. Do NOT stop
        # the main thread at the first such call; instead, keep only the calls
        # that still contain the prompt and skip the rest.
        if _request_contains_prompt(call.request, prompt):
            scoped.append(call)

    if not scoped:
        raise RuntimeError("No /v1/messages calls matched the extracted prompt anchor.")

    entries: List[Dict[str, Any]] = [
        {
            "role": "user",
            "message_id": "user_0",
            "parts": [{"id": "user_0", "type": "text", "text": prompt}],
        }
    ]
    subagent_agent_ids: set[str] = set()
    subagent_task_map: Dict[str, List[str]] = {}

    for idx, call in enumerate(scoped):
        next_req = scoped[idx + 1].request if idx + 1 < len(scoped) else None
        tool_results = _extract_tool_results_from_request(next_req) if next_req else {}
        for tool_id, result in tool_results.items():
            agent_ids = result.get("agent_ids") or []
            if agent_ids:
                subagent_agent_ids.update(agent_ids)
                subagent_task_map[tool_id] = list(agent_ids)

        message_id, blocks = _parse_anthropic_sse_message(call.response)
        parts: List[Dict[str, Any]] = []
        text_idx = 0

        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if text.strip():
                    parts.append({"id": f"text_{text_idx}", "type": "text", "text": text})
                    text_idx += 1
            elif btype == "tool_use":
                tool_id = block.get("id")
                tool_name = block.get("name")
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                if not isinstance(tool_id, str) or not tool_id:
                    tool_id = f"tool_{call.turn}_{len(parts)}"
                if not isinstance(tool_name, str) or not tool_name:
                    tool_name = "unknown"

                result = tool_results.get(tool_id) or {}
                status = "error" if result.get("is_error") else "completed"
                output = result.get("content")
                parts.append(
                    {
                        "id": tool_id,
                        "type": "tool",
                        "tool": tool_name,
                        "meta": {
                            "state": {
                                "input": tool_input,
                                "status": status,
                                "output": output,
                                "subagent_ids": result.get("agent_ids") or [],
                            }
                        },
                    }
                )

        if not parts:
            continue
        entries.append(
            {
                "role": "assistant",
                "message_id": message_id or f"assistant_{call.turn}",
                "parts": parts,
            }
        )

    provider_meta = _extract_subagent_meta_from_provider_dumps(run_dir)
    provider_ids = set(provider_meta.get("agent_ids") or [])
    subagent_summary = {
        "agent_ids_from_tool_outputs": sorted(subagent_agent_ids),
        "agent_ids_from_provider_dumps": sorted(provider_ids),
        "tool_use_to_agent_ids": subagent_task_map,
        "provider_events": provider_meta.get("events") or [],
    }
    return {"prompt": prompt, "entries": entries, "subagent_meta": subagent_summary}


_TASK_ID_RE = re.compile(r"<task_id>(.*?)</task_id>", re.S)
_TASK_OUTPUT_RE = re.compile(r"<output>\\s*(.*?)\\s*</output>", re.S)
_AGENT_ID_RE = re.compile(r"agentId:\\s*([a-zA-Z0-9_-]+)")


def _collect_text_blobs(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _collect_text_blobs(item)
        return
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            yield text
        for child in value.values():
            yield from _collect_text_blobs(child)


def _extract_agent_ids_from_tool_output(content_value: Any) -> List[str]:
    if content_value is None:
        return []
    parsed = content_value
    if isinstance(content_value, str):
        try:
            parsed = json.loads(content_value)
        except Exception:
            parsed = content_value
    ids: set[str] = set()
    for blob in _collect_text_blobs(parsed):
        for match in _AGENT_ID_RE.findall(blob):
            if match:
                ids.add(match)
    return sorted(ids)


def _extract_subagent_meta_from_provider_dumps(run_dir: Path) -> Dict[str, Any]:
    provider_dir = run_dir / "provider_dumps"
    if not provider_dir.exists():
        return {"agent_ids": [], "events": []}
    events: List[Dict[str, Any]] = []
    agent_ids: set[str] = set()
    for path in sorted(provider_dir.glob("*_request.json")):
        payload = _load_json(path)
        body = payload.get("body") or {}
        if not isinstance(body, dict):
            continue
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            continue
        for event in parsed.get("events") or []:
            if not isinstance(event, dict):
                continue
            meta = event.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if meta.get("agentType") != "subagent":
                continue
            agent_id = meta.get("agentId")
            if isinstance(agent_id, str) and agent_id:
                agent_ids.add(agent_id)
            events.append(
                {
                    "agent_id": agent_id,
                    "session_id": meta.get("sessionId"),
                    "request_id": meta.get("requestId"),
                    "query_chain_id": meta.get("queryChainId"),
                    "event_name": event.get("eventName"),
                    "source": path.name,
                }
            )
    return {"agent_ids": sorted(agent_ids), "events": events}


def _extract_subagent_outputs(entries: List[Dict[str, Any]]) -> Dict[str, str]:
    outputs: Dict[str, str] = {}
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        for part in entry.get("parts", []) or []:
            if part.get("type") != "tool" or part.get("tool") != "TaskOutput":
                continue
            state = (part.get("meta") or {}).get("state") or {}
            raw_output = state.get("output")
            if raw_output is None:
                continue
            if not isinstance(raw_output, str):
                try:
                    raw_output = json.dumps(raw_output, ensure_ascii=False)
                except Exception:
                    raw_output = str(raw_output)
            task_match = _TASK_ID_RE.search(raw_output)
            if not task_match:
                continue
            task_id = task_match.group(1).strip()
            if not task_id:
                continue
            out_match = _TASK_OUTPUT_RE.search(raw_output)
            text = out_match.group(1).strip() if out_match else raw_output.strip()
            outputs[task_id] = text
    return outputs


def _write_subagent_replays(outputs: Dict[str, str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    index: Dict[str, str] = {}
    for task_id, text in outputs.items():
        payload = [
            {
                "role": "assistant",
                "message_id": f"subagent_{task_id}",
                "parts": [{"id": "text_0", "type": "text", "text": text}],
            }
        ]
        out_path = out_dir / f"subagent_{task_id}.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        index[task_id] = str(out_path)
    if index:
        (out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert claude-code-logged run to replay session JSON.")
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Run directory (contains normalized/ provider dumps).",
    )
    parser.add_argument("--output", required=True, help="Output path for replay session JSON.")
    parser.add_argument("--subagent-output-dir", help="Optional directory to write per-subagent replay stubs.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    out_path = Path(args.output).resolve()
    payload = convert_run(run_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload["entries"], indent=2) + "\n", encoding="utf-8")
    print(f"[claude->replay] wrote {len(payload['entries'])} entries -> {out_path}")
    if args.subagent_output_dir:
        outputs = _extract_subagent_outputs(payload["entries"])
        if outputs:
            _write_subagent_replays(outputs, Path(args.subagent_output_dir))
            print(f"[claude->replay] wrote {len(outputs)} subagent stubs -> {args.subagent_output_dir}")
        meta_path = Path(args.subagent_output_dir) / "subagent_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(payload.get("subagent_meta") or {}, indent=2), encoding="utf-8")
        print(f"[claude->replay] wrote subagent meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
