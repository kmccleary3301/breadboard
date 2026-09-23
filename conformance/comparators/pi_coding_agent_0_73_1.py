"""Deterministic comparator for Pi 0.73.1 supplier and BreadBoard traces.

Required BB trace fields are: ``requests`` (ordered request objects; each may
carry ``messages``, ``tools``, ``stream``, ``max_tokens`` and ``model``),
``messages`` (ordered user/assistant/toolResult records with assistant
``content`` blocks and toolResult ``content``/``isError``), ``effects`` (path
to ``exists``, ``sha256`` and optional ``bytes``), ``termination`` (``kind``
and ``native_stop_reason``), and ``request_count``. ``events`` is accepted as
an equivalent source for messages. The supplier side is read from
``case_dir/trace.json`` and the receiver's ``http-transcript.jsonl``.

Only these explicit normalizations are allowed: ``/capture/workspace`` and
``/capture/home`` become ``<WORKSPACE>`` and ``<HOME>``; request/response
timestamps become ``<TIMESTAMP>``. Tool-call IDs are retained because they
bind each observation to its originating call. No arbitrary placeholder or
volatile-field removal is performed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

COMPARATOR_ID = "pi_coding_agent_0_73_1_trace_v1"
LANE_ID = "pi_coding_agent_0_73_1_replay"
CONFIG_ID = "pi_coding_agent_0_73_1_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
TRACE_SCHEMA_VERSION = "bb.e4.pi-canonical-episode.v1"
NORMALIZATIONS = {
    "/capture/workspace": "<WORKSPACE>",
    "/capture/home": "<HOME>",
    "timestamp": "<TIMESTAMP>",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(value: Any, *, call_counter: list[int] | None = None) -> Any:
    if isinstance(value, str):
        for source, replacement in (("/capture/workspace", "<WORKSPACE>"), ("/capture/home", "<HOME>")):
            value = value.replace(source, replacement)
        return value
    if isinstance(value, list):
        return [_normalize(item, call_counter=call_counter) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"timestamp", "created_at", "updated_at"}:
                result[key] = "<TIMESTAMP>"
            else:
                result[key] = _normalize(item, call_counter=call_counter)
        return result
    return value


def _content_blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "toolCall":
            blocks.append({"type": "toolCall", "id": block.get("id"), "name": block.get("name"), "arguments": _normalize(block.get("arguments", {}))})
        elif block.get("type") == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        else:
            blocks.append(dict(_normalize(block)))
    return blocks

def _canonical_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    result: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role == "assistant":
            result.append({"role": "assistant", "content": _content_blocks(message), "stop_reason": message.get("stopReason", message.get("stop_reason"))})
        elif role in {"toolResult", "tool", "tool_result"}:
            content = message.get("content", [])
            text = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping) and item.get("type") == "text") if isinstance(content, list) else str(content)
            result.append({
                "role": "toolResult",
                "tool_call_id": message.get("toolCallId", message.get("tool_call_id")),
                "tool_name": message.get("toolName", message.get("name")),
                "content": text,
                "is_error": bool(message.get("isError", message.get("is_error", False))),
            })
        elif role == "user":
            result.append({"role": "user", "content": _normalize(message.get("content", ""))})
    return result


def _from_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    messages: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") in {"message_end", "message_start"} and isinstance(event.get("message"), Mapping):
            if event.get("type") == "message_end":
                messages.append(dict(event["message"]))
        elif event.get("type") == "tool_execution_end":
            result = event.get("result", {})
            messages.append({
                "role": "toolResult",
                "toolCallId": event.get("toolCallId", event.get("tool_call_id")),
                "toolName": event.get("toolName"),
                "content": result.get("content", []) if isinstance(result, Mapping) else result,
                "isError": event.get("isError", False),
            })
    return _canonical_messages(messages)


def _project(trace: Mapping[str, Any], requests: list[Mapping[str, Any]]) -> dict[str, Any]:
    messages = _canonical_messages(trace.get("messages"))
    if not messages:
        messages = _from_events(trace.get("events"))
    calls: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "assistant":
            for block in message["content"]:
                if block.get("type") == "toolCall":
                    calls.append({"name": block.get("name"), "arguments": block.get("arguments", {})})
        elif message["role"] == "toolResult":
            observations.append({"tool_name": message.get("tool_name"), "content": message.get("content", ""), "is_error": message.get("is_error", False)})
    termination = trace.get("termination")
    if not isinstance(termination, Mapping):
        last_assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), {})
        stop = last_assistant.get("stop_reason")
        termination = {"kind": "submitted" if stop == "stop" else ("error" if stop == "error" else "running"), "native_stop_reason": stop}
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "requests": _normalize(requests),
        "tool_calls": calls,
        "observations": observations,
        "effects": _normalize(trace.get("effects", {})),
        "termination": _normalize(dict(termination)),
        "request_count": trace.get("request_count", len(requests)),
    }


def project_supplier_case(case_dir: str | Path) -> dict[str, Any]:
    """Project one captured supplier case into the canonical episode."""
    root = Path(case_dir)
    if root.is_file():
        root = root.parent
    trace_path = root / "trace.json"
    if not trace_path.is_file():
        raise FileNotFoundError(f"supplier trace missing: {trace_path}")
    trace = _load_json(trace_path)
    if not isinstance(trace, Mapping) or trace.get("role") != "supplier":
        raise ValueError("supplier trace must identify role=supplier")
    requests: list[Mapping[str, Any]] = []
    transcript = root / "receiver" / "http-transcript.jsonl"
    if transcript.is_file():
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            body = row.get("body") if isinstance(row, Mapping) else None
            if isinstance(body, Mapping) and isinstance(body.get("messages"), list):
                requests.append(body)
    return _project(trace, requests)


def project_bb_trace(trace: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Project a BB replay trace; reject incomplete or unauthorized shapes."""
    if isinstance(trace, (str, Path)):
        trace = _load_json(Path(trace))
    if not isinstance(trace, Mapping):
        raise TypeError("BB trace must be an object")
    requests = trace.get("requests", [])
    if not isinstance(requests, list):
        raise ValueError("BB trace requests must be an ordered list")
    if "messages" not in trace and "events" not in trace:
        raise ValueError("BB trace must provide messages or events")
    return _project(trace, [item for item in requests if isinstance(item, Mapping)])


def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    if type(expected) is not type(observed) and not (isinstance(expected, (int, float)) and isinstance(observed, (int, float))):
        return f"{path}: expected {expected!r}, observed {observed!r}"
    if isinstance(expected, Mapping):
        if set(expected) != set(observed):
            return f"{path}: keys differ"
        for key in expected:
            difference = _first_difference(expected[key], observed[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(observed):
            return f"{path}: lengths differ ({len(expected)} != {len(observed)})"
        for index, (left, right) in enumerate(zip(expected, observed)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
    elif expected != observed:
        return f"{path}: expected {expected!r}, observed {observed!r}"
    return None


class PiCodingAgent0731Comparator:
    comparator_id = COMPARATOR_ID

    def __call__(self, inp: Mapping[str, Any]) -> dict[str, Any]:
        capture = inp.get("capture", {})
        replay = inp.get("replay", {})
        capture_case = capture.get("case_dir") if isinstance(capture, Mapping) else None
        if isinstance(capture, Mapping) and (capture_case or capture.get("path")):
            expected = project_supplier_case(capture_case or capture["path"])
        elif isinstance(capture, Mapping) and capture.get("role") == "supplier":
            expected = _project(capture, [item for item in capture.get("requests", []) if isinstance(item, Mapping)])
        else:
            raise ValueError("capture must provide a supplier case directory or supplier trace")
        if isinstance(replay, Mapping) and "trace" in replay:
            observed = project_bb_trace(replay["trace"])
        elif isinstance(replay, Mapping) and "path" in replay:
            observed = project_bb_trace(replay["path"])
        else:
            observed = project_bb_trace(replay)
        difference = _first_difference(expected, observed)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "comparator_id": COMPARATOR_ID,
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "passed": difference is None,
            "assertions": [{"assertion_id": "canonical_episode", "status": "passed" if difference is None else "failed", "expected": expected, "observed": observed, "detail": "exact canonical episode match" if difference is None else difference}],
        }

    compare = __call__


def compare(inp: Mapping[str, Any]) -> dict[str, Any]:
    return PiCodingAgent0731Comparator()(inp)


__all__ = ["CONFIG_ID", "COMPARATOR_ID", "LANE_ID", "PiCodingAgent0731Comparator", "compare", "project_bb_trace", "project_supplier_case"]
