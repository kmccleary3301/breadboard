"""Canonical offline comparator for the Oh My Pi 18.1.17 packet.

The projector intentionally compares semantic episode fields rather than packet
receipt metadata (ports, elapsed time, SIF paths, or timestamps). It does not
normalize arbitrary values: only placeholders declared by the trace are
admitted, and undeclared placeholder text remains a mismatch.
"""
from __future__ import annotations

import hashlib
import json
import re
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


# Pinned 18.1.17 packages/coding-agent/src/system-prompt.ts:351-368 supplies the
# workstation values OS=`${os.platform()} ${os.release()}` and
# Kernel=getKernelIdentity() (trimmed), and prompts/system/project-prompt.md:3-6
# renders them as `- {{label}}: {{value}}` plus `- Model: {{model}}`, where
# model is formatModelString `<provider>/<id>` (config/model-resolver.ts:234-236).
# The OS release, kernel build, and provider route label are host or deployment
# facts. Only those spans are tokenized; every other byte stays compared.
_WORKSTATION_GRAMMAR: dict[str, tuple[re.Pattern[str], str, str]] = {
    "OS": (re.compile(r"(?P<platform>\S+) (?P<release>\S+)"), "release", "<OMP_OS_RELEASE>"),
    "Kernel": (re.compile(r"(?P<build>\S(?:[^\n]*\S)?)"), "build", "<OMP_KERNEL_BUILD>"),
    "Model": (re.compile(r"(?P<provider>[^/\s]+)/(?P<id>[^\n]+)"), "provider", "<OMP_MODEL_PROVIDER>"),
}
_WORKSTATION_LINE = re.compile(r"(?m)^- (?P<label>" + "|".join(_WORKSTATION_GRAMMAR) + r"): (?P<value>[^\n]*)$")


def _tokenize_workstation(body: Mapping[str, Any]) -> None:
    """Tokenize the pinned workstation value spans of each system message in place."""
    messages = body.get("messages")
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "system" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        lines: dict[str, list[re.Match[str]]] = {label: [] for label in _WORKSTATION_GRAMMAR}
        for line in _WORKSTATION_LINE.finditer(content):
            lines[line.group("label")].append(line)
        spans: list[tuple[int, int, str]] = []
        for label, (grammar, group, token) in _WORKSTATION_GRAMMAR.items():
            if len(lines[label]) != 1:
                raise ValueError(f"pinned OMP system prompt must emit exactly one '- {label}:' workstation line, found {len(lines[label])}")
            line = lines[label][0]
            value = grammar.fullmatch(content, line.start("value"), line.end("value"))
            if value is None:
                raise ValueError(f"pinned OMP workstation '- {label}:' value does not match the pinned grammar")
            if label == "Model" and value.group("id") != body.get("model"):
                raise ValueError("pinned OMP workstation Model id does not equal the request body model")
            spans.append((value.start(group), value.end(group), token))
        for start, end, token in sorted(spans, reverse=True):
            content = content[:start] + token + content[end:]
        message["content"] = content


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
        _tokenize_workstation(normalized_body)
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


def _effects(case_dir: Path | None, trace: Mapping[str, Any]) -> dict[str, str]:
    """Map each path holding a regular file at episode end to its digest.

    Every sealed case starts from an empty workspace on both sides. The supplier
    kit (``kit/omp_capture_driver.py`` ``sha`` and its ``probe_paths`` loop)
    records ``null`` for a declared path with no regular file. BB records only
    paths that differ from its pre-episode snapshot, plus ``{"exists": false}``
    for a removed one. All three mean "no file at episode end", so neither side
    keeps an entry for them. Any other value that is not a digest is malformed.
    """
    raw = trace.get("effects", {})
    effects: dict[str, str] = {}
    if isinstance(raw, Mapping):
        for path, value in raw.items():
            if isinstance(value, Mapping):
                if value.get("exists") is False:
                    if set(value) != {"exists"}:
                        raise ValueError(f"absent OMP file effect {path!r} has extra fields")
                    continue
                value = value.get("sha256")
                if not isinstance(value, str):
                    raise ValueError(f"OMP file effect {path!r} has no sha256 digest")
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"OMP file effect {path!r} is neither a digest nor absent")
            effects[str(path)] = value
    return dict(sorted(effects.items()))


_NATIVE_STOP_REASON_UNAVAILABLE = object()
_STREAM_TERMINATIONS = frozenset({"stream_truncated", "transport_error"})


def _response_token(raw: Any, index: int) -> dict[str, str]:
    """Reduce one native response record to its termination token.

    A response that reached a finish chunk yields ``{"finish_reason": reason}``.
    A response whose stream ended without one is recorded as
    ``{"stream_termination": {"reason": ..., "chunks": [...]}}`` and yields
    ``{"stream_termination": reason}``. That record carries no other field, so
    it can never also claim a ``finish_reason`` or ``choices``.
    """
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError(f"native response wire record {index} is malformed")
    if "stream_termination" in raw:
        extra = sorted(str(key) for key in raw if key != "stream_termination")
        if extra:
            raise ValueError(f"native response wire record {index} carries {extra} beside stream_termination")
        termination = raw["stream_termination"]
        if (
            not isinstance(termination, Mapping)
            or set(termination) != {"reason", "chunks"}
            or not isinstance(termination["reason"], str)
            or termination["reason"] not in _STREAM_TERMINATIONS
            or not isinstance(termination["chunks"], list)
            or not termination["chunks"]
            or not all(isinstance(chunk, Mapping) for chunk in termination["chunks"])
        ):
            raise ValueError(f"native response wire record {index} has invalid stream_termination")
        return {"stream_termination": termination["reason"]}
    reasons: list[str] = []
    choices = raw.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str):
                if not choice["finish_reason"]:
                    raise ValueError(f"native response wire record {index} has invalid finish_reason")
                reasons.append(choice["finish_reason"])
    if "finish_reason" in raw:
        reason = raw["finish_reason"]
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"native response wire record {index} has invalid finish_reason")
        reasons.append(reason)
    if not reasons:
        raise ValueError(f"native response wire record {index} is missing finish_reason")
    return {"finish_reason": reasons[-1]}


def _response_tokens(trace: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_responses = trace.get("native_responses")
    if not isinstance(raw_responses, list):
        raise ValueError("BB trace must carry native_responses for every request")
    return [_response_token(raw, index) for index, raw in enumerate(raw_responses)]


def _wire_stop_reason(trace: Mapping[str, Any]) -> str | None | object:
    if not isinstance(trace.get("native_responses"), list):
        return _NATIVE_STOP_REASON_UNAVAILABLE
    tokens = _response_tokens(trace)
    return tokens[-1].get("finish_reason") if tokens else None


def _validate_native_responses(
    trace: Mapping[str, Any],
    request_count: int,
) -> list[dict[str, str]]:
    raw_responses = trace.get("native_responses")
    if not isinstance(raw_responses, list) or len(raw_responses) != request_count:
        raise ValueError("BB trace must carry exactly one native_responses record per request")
    return _response_tokens(trace)

def _termination(trace: Mapping[str, Any], *, supplier_capture: bool = False) -> dict[str, Any]:
    if not supplier_capture and "native_stop_reason_source" in trace:
        raise ValueError("BB trace cannot declare native stop reason provenance")
    exit_value = trace.get("exit", {})
    if not isinstance(exit_value, Mapping):
        exit_value = {}
    reason = exit_value.get("native_stop_reason", exit_value.get("stop_reason", trace.get("stop_reason")))
    kind = exit_value.get("kind", trace.get("termination", trace.get("termination_kind")))
    if kind is None:
        kind = "timed_out" if trace.get("timed_out") else ("submitted" if trace.get("exit_code") == 0 else "error")
    if kind == "Submitted":
        kind = "submitted"
    if kind == "RequestLimitExceeded":
        kind = "request_limit_exceeded"
    wire_reason = _wire_stop_reason(trace)
    if wire_reason is not _NATIVE_STOP_REASON_UNAVAILABLE:
        if reason is None and supplier_capture:
            reason = wire_reason
        elif reason != wire_reason:
            raise ValueError("native stop reason does not match recorded response bytes")
        reason = wire_reason
    elif supplier_capture:
        reason = None
    return {"kind": kind, "native_stop_reason": reason}


def _validate_runtime_inputs(
    trace: Mapping[str, Any],
    *,
    supplier_capture: bool = False,
) -> dict[str, str] | None:
    if "runtime_inputs" not in trace:
        if supplier_capture:
            return None
        raise ValueError("BB trace must declare runtime_inputs")
    runtime_inputs = trace["runtime_inputs"]
    required = {"cwd", "home", "current_date", "package_dir"}
    if (
        not isinstance(runtime_inputs, Mapping)
        or set(runtime_inputs) != required
        or any(type(runtime_inputs[name]) is not str or not runtime_inputs[name] for name in required)
    ):
        raise ValueError("runtime_inputs must declare non-empty cwd, home, current_date, and package_dir")
    return {name: runtime_inputs[name] for name in sorted(required)}


def _project(
    trace: Mapping[str, Any],
    case_dir: Path | None = None,
    *,
    supplier_capture: bool = False,
) -> dict[str, Any]:
    _validate_runtime_inputs(trace, supplier_capture=supplier_capture)
    declared = _declared(trace)
    requests = _requests(trace, declared)
    if not supplier_capture:
        _validate_native_responses(trace, len(requests))
    episode = {
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "requests": requests,
        "tool_calls": _tool_calls(trace, declared),
        "results": _results(trace, declared),
        "file_effects": _effects(case_dir, trace),
        "termination": _termination(trace, supplier_capture=supplier_capture),
        "request_count": len(requests) or int(trace.get("receiver_requests", trace.get("request_count", 0)) or 0),
    }
    return episode
def _supplier_trace(path: Path) -> dict[str, Any]:
    trace = _load(path)
    if not path.is_dir():
        return trace
    transcript = path / "receiver" / "http-transcript.jsonl"
    if not transcript.is_file():
        return trace
    requests: list[dict[str, Any]] = []
    native_responses: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(transcript.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, Mapping):
            continue
        if isinstance(row.get("body"), Mapping):
            requests.append({"index": len(requests), "body": dict(row["body"])})
        events = row.get("events")
        if isinstance(events, list):
            reasons: list[str] = []
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                if isinstance(event.get("finish_reason"), str) and event["finish_reason"]:
                    reasons.append(event["finish_reason"])
                choices = event.get("choices")
                if isinstance(choices, list):
                    for choice in choices:
                        if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
                            reasons.append(choice["finish_reason"])
            # The kit receiver (kit/omp_capture_receiver.py) records exactly the
            # events it sent and ``broken = kind == "broken_stream"``. send_sse
            # writes [DONE] only when the row is not broken, and
            # break_mid_arguments returns before the finish chunk. So a broken
            # row without a finish chunk is a cut stream. A row that is not
            # broken but has no finish chunk ended on [DONE] alone, which BB's
            # openai client never surfaces; that gap fails closed.
            if reasons:
                native_responses.append({"finish_reason": reasons[-1]})
            elif row.get("broken") is True:
                native_responses.append(
                    {"stream_termination": {"reason": "stream_truncated", "chunks": list(events)}}
                )
            else:
                raise ValueError(
                    f"supplier transcript line {line_number} is not a broken stream but has no finish chunk"
                    " (omp-done-without-finish-reason)"
                )
    if requests and not isinstance(trace.get("requests"), list):
        trace["requests"] = requests
    if native_responses:
        trace["native_responses"] = native_responses
    return trace

def project_supplier_case(case_dir: str | Path) -> dict[str, Any]:
    """Project a supplier capture directory to the canonical episode."""
    path = Path(case_dir)
    trace = _supplier_trace(path)
    return _project(trace, path if path.is_dir() else path.parent, supplier_capture=True)


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
            capture_value = (
                _supplier_trace(Path(capture))
                if isinstance(capture, (str, Path))
                else capture or {}
            )
            replay_value = (
                _load(replay)
                if isinstance(replay, (str, Path))
                else replay or {}
            )
            supplier_runtime_inputs = _validate_runtime_inputs(
                capture_value,
                supplier_capture=True,
            )
            replay_runtime_inputs = _validate_runtime_inputs(replay_value)
            if (
                supplier_runtime_inputs is not None
                and supplier_runtime_inputs != replay_runtime_inputs
            ):
                raise ValueError("BB runtime_inputs do not match supplier declared values")
            expected = _project(
                capture_value,
                Path(capture) if isinstance(capture, (str, Path)) else None,
                supplier_capture=True,
            )
            observed = _project(
                replay_value,
                Path(replay).parent
                if isinstance(replay, (str, Path)) and Path(replay).is_file()
                else None,
            )
            report = self.compare_episodes(expected, observed)
            supplier_tokens = (
                _response_tokens(capture_value)
                if (
                    isinstance(capture_value.get("native_responses"), list)
                    and len(capture_value["native_responses"])
                    == len(_requests(capture_value, _declared(capture_value)))
                )
                else None
            )
            if supplier_tokens is not None:
                assertion = _assertion(
                    "native_response_terminations_equal",
                    supplier_tokens,
                    _response_tokens(replay_value),
                )
                report["assertions"].append(assertion)
                if assertion["status"] == "failed":
                    report["failed"] += 1
                else:
                    report["passed"] += 1
                report["ok"] = report["failed"] == 0
            return report
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

    compare = __call__


Comparator = OhMyPi18Comparator


def compare(inp: Mapping[str, Any]) -> dict[str, Any]:
    return OhMyPi18Comparator()(inp)


__all__ = ["COMPARATOR_ID", "CANONICAL_SCHEMA_VERSION", "OhMyPi18Comparator", "Comparator", "compare", "project_bb_trace", "project_supplier_case"]
