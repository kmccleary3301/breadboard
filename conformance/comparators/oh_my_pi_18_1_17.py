"""Canonical offline comparator for the Oh My Pi 18.1.17 packet.

The projector intentionally compares semantic episode fields rather than packet
receipt metadata (ports, elapsed time, SIF paths, or timestamps). It does not
normalize arbitrary values: only placeholders declared by the trace are
admitted, and undeclared placeholder text remains a mismatch.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

COMPARATOR_ID = "oh_my_pi_18_1_17_trace_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
CANONICAL_SCHEMA_VERSION = "bb.e4.omp-episode.v1"
_ALLOWED_PLACEHOLDERS = {
    "<TIMESTAMP>",
    "<WALL_TIME>",
    "<REQUEST_ID>",
    "<PORT>",
    "<STREAM>",
    "<MAX_COMPLETION_TOKENS>",
}
_VOLATILE_KEYS = {"timestamp", "elapsed_seconds", "wall_time_ms", "port", "receiver_base_url", "trace_local_path", "sif_source_root", "stream", "max_completion_tokens", "max_tokens"}
_FIELD_PLACEHOLDERS = {
    "timestamp": "<TIMESTAMP>",
    "elapsed_seconds": "<WALL_TIME>",
    "wall_time_ms": "<WALL_TIME>",
    "port": "<PORT>",
    "stream": "<STREAM>",
    "max_completion_tokens": "<MAX_COMPLETION_TOKENS>",
    "max_tokens": "<MAX_COMPLETION_TOKENS>",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    if path.is_dir():
        trace = path / "trace.json"
        return json.loads(trace.read_text(encoding="utf-8")) if trace.is_file() else {}
    return json.loads(path.read_text(encoding="utf-8"))


def _declared(trace: Mapping[str, Any]) -> set[str]:
    values = trace.get("normalizations", trace.get("declared_placeholders", ()))
    if isinstance(values, Mapping):
        values = values.values()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return set()
    declared: set[str] = set()
    for value in values:
        text = str(value)
        for placeholder in _ALLOWED_PLACEHOLDERS:
            if placeholder in text:
                declared.add(placeholder)
    return declared


def _normalize(value: Any, *, declared: set[str], key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            child_key = str(raw_key)
            placeholder = _FIELD_PLACEHOLDERS.get(child_key)
            if placeholder in declared and child_key in _VOLATILE_KEYS:
                result[child_key] = placeholder
            else:
                result[child_key] = _normalize(item, declared=declared, key=child_key)
        return result
    if isinstance(value, list):
        return [_normalize(item, declared=declared, key=key) for item in value]
    return value


def _requests(trace: Mapping[str, Any], declared: set[str]) -> list[dict[str, Any]]:
    raw = trace.get("requests")
    if not isinstance(raw, list):
        raw = trace.get("request_bodies", [])
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        body = item.get("body", item) if isinstance(item, Mapping) else item
        if not isinstance(body, Mapping):
            body = {"value": body}
        normalized_body = _normalize(body, declared=declared)
        result.append(
            {
                "index": item.get("index", index) if isinstance(item, Mapping) else index,
                "body": normalized_body,
                "messages": normalized_body.get("messages", []),
                "tools": normalized_body.get("tools", []),
            }
        )
    return result
def _request_messages(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages: list[Mapping[str, Any]] = []
    raw_requests = trace.get("requests", trace.get("request_bodies", []))
    if not isinstance(raw_requests, list):
        return messages
    for item in raw_requests:
        body = item.get("body", item) if isinstance(item, Mapping) else item
        if not isinstance(body, Mapping):
            continue
        raw_messages = body.get("messages", [])
        if isinstance(raw_messages, list):
            messages.extend(message for message in raw_messages if isinstance(message, Mapping))
    return messages


def _source_messages(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = _request_messages(trace)
    for field in ("history", "events", "messages"):
        raw = trace.get(field)
        if isinstance(raw, list):
            messages.extend(item for item in raw if isinstance(item, Mapping))
    return messages


def _wire_identity(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _tool_calls(trace: Mapping[str, Any], declared: set[str]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    explicit = trace.get("tool_calls")
    if isinstance(explicit, list):
        candidates.extend(explicit)
    else:
        messages = _source_messages(trace)
        for message in messages:
            calls = message.get("tool_calls", message.get("toolCalls", []))
            if isinstance(calls, list):
                candidates.extend(calls)
            blocks = message.get("content")
            if isinstance(blocks, list):
                candidates.extend(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": block.get("arguments", {}),
                    }
                    for block in blocks
                    if isinstance(block, Mapping) and block.get("type") in {"toolCall", "tool_call"}
                )
            if message.get("type") in {"tool_call", "toolCall"}:
                candidates.append(message)
    output: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for index, call in enumerate(candidates):
        if not isinstance(call, Mapping):
            continue
        call_id = call.get("id", call.get("tool_call_id"))
        identity = _wire_identity(call)
        if call_id is not None:
            prior = seen.get(str(call_id))
            if prior is not None:
                if prior != identity:
                    raise ValueError(f"tool call {call_id!r} repeated with non-identical wire payload")
                continue
            seen[str(call_id)] = identity
        function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
        name = call.get("name", function.get("name", ""))
        arguments = call.get("arguments", function.get("arguments", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                # Preserve malformed source spelling; it is a semantic result.
                pass
        output.append(
            {
                "index": call.get("index", len(output)),
                "id": call_id,
                "name": name,
                "arguments": _normalize(arguments, declared=declared),
            }
        )
    return output


def _results(trace: Mapping[str, Any], declared: set[str]) -> list[dict[str, Any]]:
    raw = trace.get("results", trace.get("tool_results"))
    candidates: list[Any] = list(raw) if isinstance(raw, list) else []
    if not candidates:
        candidates.extend(
            message
            for message in _source_messages(trace)
            if message.get("role") in {"tool", "toolResult", "tool_result"}
        )
    result: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            item = {"output": item}
        result_id = item.get("tool_call_id", item.get("toolCallId", item.get("id")))
        identity = _wire_identity(item)
        if result_id is not None:
            prior = seen.get(str(result_id))
            if prior is not None:
                if prior != identity:
                    raise ValueError(f"tool result {result_id!r} repeated with non-identical wire payload")
                continue
            seen[str(result_id)] = identity
        error = item.get("error")
        if error is None and item.get("isError"):
            error = item.get("content", item.get("output", ""))
        result.append(
            {
                "index": item.get("index", len(result)),
                "id": result_id,
                "name": item.get("tool_name", item.get("toolName", item.get("name"))),
                "output": _normalize(item.get("output", item.get("content", "")), declared=declared),
                "error": _normalize(error, declared=declared),
                "skipped": bool(item.get("skipped", item.get("status") in {"skipped", "length"})),
            }
        )
    return result


def _effects(case_dir: Path | None, trace: Mapping[str, Any]) -> dict[str, str | None]:
    raw = trace.get("effects", {})
    effects: dict[str, str | None] = {}
    if isinstance(raw, Mapping):
        for path, value in raw.items():
            if isinstance(value, Mapping):
                effects[str(path)] = value.get("sha256")
            elif value is None or isinstance(value, str):
                effects[str(path)] = value
    return dict(sorted(effects.items()))


def _termination(trace: Mapping[str, Any]) -> dict[str, Any]:
    exit_value = trace.get("exit", {})
    if not isinstance(exit_value, Mapping):
        exit_value = {}
    reason = exit_value.get("native_stop_reason", exit_value.get("stop_reason", trace.get("stop_reason")))
    kind = exit_value.get("kind", trace.get("termination", trace.get("termination_kind")))
    if kind is None:
        kind = "timed_out" if trace.get("timed_out") else ("submitted" if trace.get("exit_code") == 0 else "error")
    if kind == "Submitted":
        kind = "submitted"
    elif kind == "RequestLimitExceeded":
        kind = "request_limit_exceeded"
    return {"kind": kind, "native_stop_reason": reason}


def _project(trace: Mapping[str, Any], case_dir: Path | None = None) -> dict[str, Any]:
    declared = _declared(trace)
    requests = _requests(trace, declared)
    episode = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "requests": requests,
        "tool_calls": _tool_calls(trace, declared),
        "results": _results(trace, declared),
        "file_effects": _effects(case_dir, trace),
        "termination": _termination(trace),
        "request_count": len(requests) or int(trace.get("receiver_requests", trace.get("request_count", 0)) or 0),
    }
    return episode

def _supplier_trace(path: Path) -> dict[str, Any]:
    trace = _load(path)
    if not path.is_dir() or isinstance(trace.get("requests"), list):
        return trace
    transcript = path / "receiver" / "http-transcript.jsonl"
    if not transcript.is_file():
        return trace
    requests: list[dict[str, Any]] = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, Mapping) and isinstance(row.get("body"), Mapping):
            requests.append({"index": len(requests), "body": dict(row["body"])})
    if requests:
        trace["requests"] = requests
    return trace


def project_supplier_case(case_dir: str | Path) -> dict[str, Any]:
    """Project a supplier capture directory to the canonical episode."""
    path = Path(case_dir)
    trace = _supplier_trace(path)
    return _project(trace, path if path.is_dir() else path.parent)


def project_bb_trace(trace: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Project a BreadBoard trace to the same canonical episode."""
    value = _load(trace)
    case_dir = Path(trace).parent if isinstance(trace, (str, Path)) and Path(trace).is_file() else None
    return _project(value, case_dir)

def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    if type(expected) is not type(observed) and not (isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(observed, (int, float)) and not isinstance(observed, bool)):
        return f"first difference at {path}: expected {expected!r}, observed {observed!r} (types differ)"
    if isinstance(expected, Mapping):
        for key in sorted(set(expected) | set(observed), key=str):
            if key not in expected or key not in observed:
                return f"first difference at {path}.{key}: expected {expected.get(key, '<missing>')!r}, observed {observed.get(key, '<missing>')!r}"
            difference = _first_difference(expected[key], observed[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(observed):
            return f"first difference at {path}: expected list length {len(expected)}, observed {len(observed)}"
        for index, (left, right) in enumerate(zip(expected, observed)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
        return None
    if expected != observed:
        return f"first difference at {path}: expected {expected!r}, observed {observed!r}"
    return None


def _assertion(assertion_id: str, expected: Any, observed: Any) -> dict[str, Any]:
    difference = _first_difference(expected, observed, f"$.{assertion_id.removesuffix('_equal')}")
    return {"assertion_id": assertion_id, "status": "passed" if difference is None else "failed", "expected": expected, "observed": observed, "detail": difference or "observed value equals expected value"}


class OhMyPi18Comparator:
    """Comparator class with the same callable interface as Mini's comparator."""

    def compare_episodes(self, expected: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
        assertions = [_assertion(f"episode.{field}_equal", expected.get(field), observed.get(field)) for field in ("requests", "tool_calls", "results", "file_effects", "termination", "request_count")]
        failed = sum(item["status"] == "failed" for item in assertions)
        return {"schema_version": REPORT_SCHEMA_VERSION, "comparator_id": COMPARATOR_ID, "assertions": assertions, "passed": len(assertions) - failed, "failed": failed, "warned": 0, "errors": [], "ok": failed == 0}

    def __call__(self, inp: Mapping[str, Any]) -> dict[str, Any]:
        capture = inp.get("capture") or inp.get("supplier")
        replay = inp.get("replay") or inp.get("breadboard")
        try:
            expected = project_supplier_case(capture) if isinstance(capture, (str, Path)) else _project(capture or {})
            observed = project_bb_trace(replay) if isinstance(replay, (str, Path)) else _project(replay or {})
        except (OSError, TypeError, ValueError) as exc:
            return {
                "schema_version": REPORT_SCHEMA_VERSION,
                "comparator_id": COMPARATOR_ID,
                "assertions": [],
                "passed": 0,
                "failed": 0,
                "warned": 0,
                "errors": [str(exc)],
                "ok": False,
            }
        return self.compare_episodes(expected, observed)

    compare = __call__


Comparator = OhMyPi18Comparator


def compare(inp: Mapping[str, Any]) -> dict[str, Any]:
    return OhMyPi18Comparator()(inp)


__all__ = ["COMPARATOR_ID", "CANONICAL_SCHEMA_VERSION", "OhMyPi18Comparator", "Comparator", "compare", "project_bb_trace", "project_supplier_case"]
