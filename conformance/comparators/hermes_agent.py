"""Deterministic Hermes Agent 2026.9.11 episode comparison.

The supplier packet is the rerun3 capture produced by the Hermes profile.  Its
trace stores alternating chat-completion request/served rows, while the
history stores the native tool execution results.  The projection deliberately
keeps both layers: request bodies preserve the advertised tool surface and
AGENTS.md context; canonical tool calls preserve the raw model sample before
case-repair and deduplicate semantically identical calls; tool results keep
visible native errors; and visible correction messages remain explicit.

A BreadBoard replay must provide the canonical fields documented by
``project_bb_trace``.  No wildcard normalization is performed.  The only
accepted repair is the native case/snake-case name repair against the
advertised tool list (for example ``WriteFile`` -> ``write_file``); the raw
sample is retained alongside the repaired name.
"""

from __future__ import annotations

import copy
import os
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from conformance.comparators.protocol import ComparatorInput

COMPARATOR_ID = "hermes_agent_trace_v1"
LANE_ID = "hermes_agent_2026_9_11_replay"
CONFIG_ID = "hermes_agent_2026_9_11_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
TRACE_SCHEMA_VERSION = "bb.e4.hermes-agent-trace.v1"
SUPPLIER_TRACE_SCHEMA_VERSION = "bb.e4.hermes-supplier-trace.v1"

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_WORKSPACE_SEED = {
    "AGENTS.md": "sha256:6fc95531db12a94c2c8a08ccde807b1e266cb0cb92c40e7533552f3460ff6bbd",
}
# Supplier SDK bookkeeping is not a model workspace effect. Apply the same
# typed exclusion to each side; never inspect the trace's self-declared role.
_EFFECT_EXCLUSIONS = {
    "supplier": frozenset({"trajectory_samples.jsonl", "failed_trajectories.jsonl"}),
    "breadboard": frozenset({"trajectory_samples.jsonl", "failed_trajectories.jsonl"}),
}


def _effect_delta(effects: Mapping[str, str | None], *, role: str) -> dict[str, str | None]:
    if role not in _EFFECT_EXCLUSIONS:
        raise ValueError(f"unknown effect projection role: {role}")
    return {
        path: digest
        for path, digest in effects.items()
        if path not in _EFFECT_EXCLUSIONS[role]
        and _WORKSPACE_SEED.get(path) != digest
    }


_CANONICAL_FIELDS = (
    "case_id",
    "profile",
    "context",
    "controls",
    "requests",
    "tool_calls",
    "tool_results",
    "visible_corrections",
    "file_effects",
    "termination",
    "request_count",
)
_SOURCE_CONTROL_VALUES = {
    "api_mode": "chat_completions",
    "streaming": False,
    "max_iterations": 8,
    "max_tokens": 2048,
    "provider_deadline": 45,
    "provider_timeout": 45,
    "native_deadline": 35,
    "tool_deadline": 35,
    "watchdog_deadline": 40,
    "watchdog": 40,
    "terminal_deadline": 30,
    "terminal_timeout": 30,
    "retry": True,
    "api_max_retries": 1,
    "fallback": False,
}
_PACKET_ABSENT_CONTROLS = (
    "provider_deadline", "provider_timeout", "retry", "api_max_retries", "fallback",
)
_SCHEMA_GAP = "hermes-rerun3-unoverlaid-schemas"


def _request_difference(
    expected: list[dict[str, Any]], observed: list[dict[str, Any]],
    overlay: Mapping[str, Any],
) -> tuple[str | None, bool, bool]:
    left, right = copy.deepcopy(expected), copy.deepcopy(observed)
    if len(left) != len(right):
        return _first_difference(left, right, "$.requests"), False, False
    used_overlay = False
    unoverlaid = False
    for row_index, (source, candidate) in enumerate(zip(left, right)):
        source_tools = source.get("body", {}).get("tools", [])
        candidate_tools = candidate.get("body", {}).get("tools", [])
        if not isinstance(source_tools, list) or not isinstance(candidate_tools, list) or len(source_tools) != len(candidate_tools):
            return _first_difference(left, right, "$.requests"), False, False
        for tool_index, (native, approved) in enumerate(zip(source_tools, candidate_tools)):
            name = native.get("function", {}).get("name") if isinstance(native, Mapping) else None
            if name not in overlay:
                continue
            declaration = overlay[name]
            native_bytes = json.dumps(native, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            approved_bytes = json.dumps(approved, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if hashlib.sha256(native_bytes).hexdigest() != declaration["native_sha256"]:
                return f"unoverlaid supplier schema differs at $.requests[{row_index}].body.tools[{tool_index}]", False, True
            if approved_bytes == native_bytes:
                unoverlaid = True
                continue
            if (
                hashlib.sha256(approved_bytes).hexdigest() != declaration["approved_sha256"]
                or approved_bytes != declaration["approved_schema_json"].encode()
            ):
                return _first_difference(left, right, "$.requests"), False, False
            source_tools[tool_index] = candidate_tools[tool_index] = name
            used_overlay = True
    difference = _first_difference(left, right, "$.requests")
    if unoverlaid and difference is None:
        difference = "unoverlaid native schemas in BreadBoard request"
    return difference, used_overlay, unoverlaid




def _json_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _object_keys(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Static symmetric JSON rule: object member order is not semantic."""
    return tuple(sorted(value))


def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    """Return a strict JSON difference except for object member order."""
    if type(expected) is not type(observed):
        return f"first difference at {path}: expected {_json_value(expected)}, observed {_json_value(observed)} (types differ)"
    if isinstance(expected, Mapping):
        expected_keys = _object_keys(expected)
        observed_keys = _object_keys(observed)
        if expected_keys != observed_keys:
            return f"first difference at {path}: expected keys {_json_value(expected_keys)}, observed keys {_json_value(observed_keys)}"
        for key in expected_keys:
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
        return f"first difference at {path}: expected {_json_value(expected)}, observed {_json_value(observed)}"
    return None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc

def _receiver_requests(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load receiver HTTP transcript {path}: {exc}") from exc
    requests: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("receiver HTTP transcript row must be an object")
        if "body" not in row:
            continue
        if not isinstance(row["body"], Mapping):
            raise ValueError("receiver HTTP request body must be an object")
        requests.append({"index": row.get("index"), "body": row["body"]})
    return requests


def _parse_json_or_text(value: Any) -> Any:
    if not isinstance(value, str):
        return copy.deepcopy(value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _tool_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"tool call name must be a non-empty string: {value!r}")
    return value


def _repair_name(raw_name: str, advertised: Sequence[str]) -> str:
    """Apply source-style separator/camel-case repair, without inventing tools."""
    if raw_name in advertised:
        return raw_name
    candidates = [raw_name.strip()]
    # Native transport may return an XML-fragment-wrapped name.
    candidates.extend(re.findall(r"[A-Za-z0-9_.-]+", raw_name))
    for candidate in list(candidates):
        candidates.append(_CAMEL_BOUNDARY_RE.sub(r"\1_\2", candidate))
    folded = {name.casefold(): name for name in advertised}
    for candidate in candidates:
        normalized = re.sub(r"[-./\s]+", "_", candidate).lower()
        if normalized in advertised:
            return normalized
        if normalized.casefold() in folded:
            return folded[normalized.casefold()]
    return raw_name


def _tool_names(body: Mapping[str, Any]) -> list[str]:
    raw_tools = body.get("tools", [])
    if not isinstance(raw_tools, list):
        raise ValueError("request body tools must be a list")
    names: list[str] = []
    for item in raw_tools:
        if not isinstance(item, Mapping):
            raise ValueError("request body tools must contain objects")
        function = item.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("request body tools must contain function objects")
        names.append(_tool_name(function.get("name")))
    return names


def _message_projection(message: Mapping[str, Any]) -> dict[str, Any]:
    """Retain message order and all fields that affect the Hermes episode."""
    projected: dict[str, Any] = {}
    for key, value in message.items():
        if key == "timestamp":
            continue
        projected[str(key)] = copy.deepcopy(value)
    return projected
def _declared_workspace_root(trace: Mapping[str, Any], *, fallback: Path | None = None) -> str:
    runtime = trace.get("runtime")
    cwd = runtime.get("cwd") if isinstance(runtime, Mapping) else None
    if isinstance(cwd, str) and cwd.startswith("/") and cwd:
        return cwd.rstrip("/")
    if fallback is not None:
        return str(fallback.resolve())
    raise ValueError("trace is missing declared runtime.cwd")


def _replace_workspace(value: Any, root: str, changed: list[bool]) -> Any:
    if isinstance(value, str):
        if value == root:
            changed[0] = True
            return "<WORKSPACE>"
        prefix = root + "/"
        if value.startswith(prefix):
            changed[0] = True
            return "<WORKSPACE>" + value[len(root):]
        return value
    if isinstance(value, list):
        return [_replace_workspace(item, root, changed) for item in value]
    if isinstance(value, Mapping):
        return {key: _replace_workspace(item, root, changed) for key, item in value.items()}
    return value


def _body_projection(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body.get("messages"), list):
        raise ValueError("request body messages must be a list")
    if not isinstance(body.get("tools"), list):
        raise ValueError("request body tools must be a list")
    return copy.deepcopy(dict(body))


def _response_projection(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices", [])
    if not isinstance(choices, list):
        raise ValueError("served response choices must be a list")
    projected_choices: list[dict[str, Any]] = []
    for choice in choices:
        if not isinstance(choice, Mapping):
            projected_choices.append(copy.deepcopy(choice))
            continue
        message = choice.get("message")
        if isinstance(message, Mapping):
            message_projection = _message_projection(message)
        else:
            message_projection = copy.deepcopy(message)
        projected_choices.append(
            {
                "index": choice.get("index"),
                "finish_reason": choice.get("finish_reason"),
                "message": message_projection,
            }
        )
    return {"choices": projected_choices}


def _request_projection(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for row in rows:
        body = row.get("body")
        if not isinstance(body, Mapping):
            continue
        _tool_names(body)
        item: dict[str, Any] = {
            "index": row.get("index"),
            "body": _body_projection(body),
        }
        requests.append(item)
    responses = {
        row.get("index"): row
        for row in rows
        if row.get("kind") in {"served", "response"} and isinstance(row.get("response"), Mapping)
    }
    for item in requests:
        response_row = responses.get(item["index"])
        if response_row is not None:
            item["response"] = _response_projection(response_row["response"])
        else:
            item["error"] = copy.deepcopy(response_row.get("error")) if response_row else None
    return requests


def _advertised_tools(requests: Sequence[Mapping[str, Any]]) -> list[str]:
    if not requests:
        raise ValueError("supplier trace has no request bodies")
    body = requests[0].get("body")
    if not isinstance(body, Mapping) or not isinstance(body.get("tools"), list):
        raise ValueError("projected request has no tools")
    return [item["function"]["name"] for item in body["tools"]]


def _tool_call_samples(requests: Sequence[Mapping[str, Any]], advertised: Sequence[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for request in requests:
        response = request.get("response")
        choices = response.get("choices", []) if isinstance(response, Mapping) else []
        for choice in choices if isinstance(choices, list) else []:
            message = choice.get("message") if isinstance(choice, Mapping) else None
            raw_calls = message.get("tool_calls", []) if isinstance(message, Mapping) else []
            for raw_call in raw_calls if isinstance(raw_calls, list) else []:
                if not isinstance(raw_call, Mapping):
                    continue
                function = raw_call.get("function")
                if not isinstance(function, Mapping):
                    continue
                raw_name = _tool_name(function.get("name"))
                raw_arguments = copy.deepcopy(function.get("arguments", {}))
                repaired_name = _repair_name(raw_name, advertised)
                arguments = _parse_json_or_text(raw_arguments)
                key = json.dumps([repaired_name, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if key in by_key:
                    continue
                item = {
                    "raw_sample": {
                        "name": raw_name,
                        "arguments": raw_arguments,
                        "id": raw_call.get("id"),
                    },
                    "raw_tool_name": raw_name,
                    "repaired_name": repaired_name,
                    "tool_name": repaired_name,
                    "arguments": arguments,
                    "raw_arguments": raw_arguments,
                    "call_id": raw_call.get("id"),
                }
                by_key[key] = item
                calls.append(item)
    return calls


def _tool_results(history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in history:
        if item.get("role") != "tool":
            continue
        name = item.get("name") or item.get("tool_name")
        if not isinstance(name, str):
            raise ValueError("tool result has no name")
        content = _parse_json_or_text(item.get("content"))
        is_error = name == "NOT_A_TOOL" or (
            isinstance(content, str) and content.startswith("Tool '") and " does not exist." in content
        )
        result: dict[str, Any] = {
            "tool_name": name,
            "call_id": item.get("tool_call_id"),
            "is_error": is_error,
            "result": content,
        }
        results.append(result)
    return results


def _visible_corrections(history: Sequence[Mapping[str, Any]]) -> list[str]:
    # The empty assistant response is visible, and the following user nudge is
    # the native recovery message.  Keep all post-initial user messages so a
    # missing nudge cannot be hidden by a narrower heuristic.
    seen_initial_user = False
    corrections: list[str] = []
    for item in history:
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not seen_initial_user:
            seen_initial_user = True
            continue
        corrections.append(content if isinstance(content, str) else _parse_json_or_text(content))
    return corrections


def _context_projection(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contexts: list[str] = []
    for request in requests:
        body = request.get("body")
        messages = body.get("messages", []) if isinstance(body, Mapping) else []
        for message in messages if isinstance(messages, list) else []:
            if isinstance(message, Mapping) and message.get("role") == "system":
                content = message.get("content")
                if isinstance(content, str) and content not in contexts:
                    contexts.append(content)
    agents = [content for content in contexts if "AGENTS.md" in content]
    return {"system_messages": contexts, "agents_md": agents}


def _file_effects(trace: Mapping[str, Any], case_dir: Path) -> dict[str, str | None]:
    raw_effects = trace.get("effects", {})
    raw_files = raw_effects.get("files", {}) if isinstance(raw_effects, Mapping) else {}
    if not isinstance(raw_files, Mapping):
        raise ValueError("trace.effects.files must be an object")
    effects: dict[str, str | None] = {}
    for path, value in raw_files.items():
        if isinstance(value, Mapping):
            digest = value.get("sha256") if value.get("exists", True) else None
        else:
            digest = value if isinstance(value, str) else None
        if digest is not None and (not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None):
            raise ValueError(f"invalid file digest for {path!r}: {digest!r}")
        effects[str(path)] = digest
    workspace = case_dir / "workspace"
    if workspace.is_dir():
        for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            effects.setdefault(path.relative_to(workspace).as_posix(), digest)
    return _effect_delta(effects, role="supplier")


def _controls_projection(
    trace: Mapping[str, Any], requests: Sequence[Mapping[str, Any]],
    advertised: Sequence[str], *, role: str,
) -> dict[str, Any]:
    controls = trace.get("controls", {})
    if not isinstance(controls, Mapping):
        raise ValueError("trace.controls must be an object")
    if role == "breadboard":
        missing = _SOURCE_CONTROL_VALUES.keys() - controls.keys()
        if missing:
            raise ValueError("BreadBoard controls missing fields: " + ", ".join(sorted(missing)))
    elif role != "supplier":
        raise ValueError(f"unknown controls role: {role}")
    body_count = len(requests)
    attempts = controls.get("http_attempts")
    if attempts is not None and (type(attempts) is not int or attempts != body_count):
        raise ValueError(f"controls.http_attempts={attempts!r} but found {body_count} request bodies")
    projected = {
        key: controls.get(key, source_value)
        for key, source_value in _SOURCE_CONTROL_VALUES.items()
    }
    if type(projected["streaming"]) is not bool or type(projected["retry"]) is not bool or type(projected["fallback"]) is not bool:
        raise ValueError("streaming, retry and fallback controls must be boolean")
    projected["advertised_tools"] = list(advertised)
    return projected

def _canonical_from_supplier(trace: Mapping[str, Any], case_dir: Path) -> dict[str, Any]:
    raw_rows = trace.get("requests")
    if not isinstance(raw_rows, list):
        raise ValueError("trace.requests must be a list")
    requests = _request_projection([row for row in raw_rows if isinstance(row, Mapping)])
    advertised = _advertised_tools(requests)
    history = trace.get("history")
    if not isinstance(history, list) or any(not isinstance(item, Mapping) for item in history):
        raise ValueError("trace.history must be a list of objects")
    projected: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "role": "supplier",
        "case_id": trace.get("case_id") or case_dir.name,
        "profile": trace.get("profile"),
        "runtime": {
            "cwd": _declared_workspace_root(trace, fallback=case_dir / "workspace"),
        },
        "context": _context_projection(requests),
        "controls": _controls_projection(trace, requests, advertised, role="supplier"),
        "requests": requests,
        "tool_calls": _tool_call_samples(requests, advertised),
        "tool_results": _tool_results(history),
        "visible_corrections": _visible_corrections(history),
        "file_effects": _file_effects(trace, case_dir),
        "termination": {
            "kind": str((trace.get("exit") or {}).get("status", "unknown")),
            "native_stop_reason": _final_stop_reason(requests),
        },
        "request_count": len(requests),
    }
    if projected["termination"]["kind"] == "completed":
        projected["termination"]["kind"] = "completed"
    projected["normalizations"] = []
    return projected


def _final_stop_reason(requests: Sequence[Mapping[str, Any]]) -> Any:
    for request in reversed(requests):
        response = request.get("response")
        choices = response.get("choices", []) if isinstance(response, Mapping) else []
        if isinstance(choices, list) and choices:
            choice = choices[-1]
            if isinstance(choice, Mapping):
                return choice.get("finish_reason")
    return None


def project_supplier_case(case_dir: Path | str) -> dict[str, Any]:
    path = Path(case_dir)
    trace = _load_json(path / "trace.json")
    if not isinstance(trace, Mapping):
        raise ValueError("supplier trace must be an object")
    if trace.get("schema_version") != SUPPLIER_TRACE_SCHEMA_VERSION:
        raise ValueError(f"supplier trace schema_version must be {SUPPLIER_TRACE_SCHEMA_VERSION}")
    return _canonical_from_supplier(trace, path)


def _validate_canonical(value: Mapping[str, Any]) -> None:
    missing = [field for field in _CANONICAL_FIELDS if field not in value]
    if missing:
        raise ValueError("BreadBoard trace missing fields: " + ", ".join(missing))
    if value.get("schema_version") not in {None, TRACE_SCHEMA_VERSION}:
        raise ValueError(f"BreadBoard trace schema_version must be {TRACE_SCHEMA_VERSION}")
    requests = value.get("requests")
    if not isinstance(requests, list):
        raise ValueError("BreadBoard requests must be a list")
    if value.get("request_count") != len(requests):
        raise ValueError("BreadBoard request_count must equal len(requests)")
    controls = value.get("controls")
    if not isinstance(controls, Mapping):
        raise ValueError("BreadBoard controls must be an object")
    if type(controls.get("streaming")) is not bool:
        raise ValueError("BreadBoard controls.streaming must be boolean")
    context = value.get("context")
    if not isinstance(context, Mapping) or not isinstance(context.get("agents_md"), list):
        raise ValueError("BreadBoard context.agents_md must be a list")
    termination = value.get("termination")
    if not isinstance(termination, Mapping) or "kind" not in termination or "native_stop_reason" not in termination:
        raise ValueError("BreadBoard termination must contain kind and native_stop_reason")


def project_bb_trace(trace: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(trace, (Path, str)):
        value = _load_json(Path(trace))
    else:
        value = copy.deepcopy(trace)
    if not isinstance(value, Mapping):
        raise ValueError("BreadBoard trace must be an object")
    _validate_canonical(value)
    raw_rows = value.get("requests")
    requests = _request_projection([row for row in raw_rows if isinstance(row, Mapping)]) if isinstance(raw_rows, list) else []
    advertised = _advertised_tools(requests)
    projected = {"schema_version": TRACE_SCHEMA_VERSION, "role": "breadboard"}
    for field in _CANONICAL_FIELDS:
        projected[field] = copy.deepcopy(value[field])
    projected["controls"] = _controls_projection(value, requests, advertised, role="breadboard")
    if not isinstance(projected["file_effects"], Mapping):
        raise ValueError("BreadBoard file_effects must be an object")
    projected["file_effects"] = _effect_delta(projected["file_effects"], role="breadboard")
    declared = value.get("normalizations", [])
    if not isinstance(declared, list) or any(type(item) is not str for item in declared):
        raise ValueError("normalizations must be a list of strings")
    projected["normalizations"] = list(declared)
    return projected

def _assertion(assertion_id: str, expected: Any, observed: Any, detail: str | None = None) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "name": assertion_id,
        "status": "passed" if detail is None else "failed",
        "observed": observed,
        "expected": expected,
        "detail": detail or "observed value equals expected value",
    }


def _report(
    assertions: list[dict[str, Any]],
    errors: list[str] | None = None,
    normalizations: list[str] | None = None,
    *,
    gaps: list[dict[str, str]] | None = None,
    source_derived_controls: Sequence[str] = (),
    bb_trace_sha256: str | None = None,
    job_id: str | None = None,
    mode: str = "fixture",
) -> dict[str, Any]:
    failed = sum(item["status"] == "failed" for item in assertions)
    report = {
        "comparator_id": COMPARATOR_ID,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "mode": mode,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ok": failed == 0 and not errors,
        "passed": len(assertions) - failed,
        "failed": failed,
        "errors": errors or [],
        "normalizations": normalizations or [],
        "gaps": gaps or [],
        "source_derived_controls": list(source_derived_controls),
        "assertions": assertions,
    }
    if bb_trace_sha256 is not None:
        report["bb_trace_sha256"] = bb_trace_sha256
    if job_id is not None:
        report["job_id"] = job_id
    return report


def _supplier_input(
    supplier_case: Path | str | Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if isinstance(supplier_case, (Path, str)):
        path = Path(supplier_case)
        trace = _load_json(path / "trace.json")
        if not isinstance(trace, Mapping):
            raise ValueError("supplier trace must be an object")
        if trace.get("schema_version") != SUPPLIER_TRACE_SCHEMA_VERSION:
            raise ValueError(f"trace schema_version must be {SUPPLIER_TRACE_SCHEMA_VERSION}")
        return _canonical_from_supplier(trace, path), _declared_workspace_root(
            trace, fallback=path / "workspace"
        )
    if not isinstance(supplier_case, Mapping):
        raise ValueError("supplier trace must be an object")
    if supplier_case.get("schema_version") != SUPPLIER_TRACE_SCHEMA_VERSION:
        raise ValueError(f"trace schema_version must be {SUPPLIER_TRACE_SCHEMA_VERSION}")
    return _canonical_from_supplier(supplier_case, Path(".")), _declared_workspace_root(supplier_case)


def compare_cases(
    supplier_case: Path | str | Mapping[str, Any],
    bb_trace: Mapping[str, Any] | Path | str,
    *,
    installed_replay: bool = False,
    job_id: str | None = None,
    receiver_transcript: Path | str | None = None,
) -> dict[str, Any]:
    normalizations: list[str] = []
    trace_hash: str | None = None
    receiver_difference: str | None = None
    mode = "installed-replay" if installed_replay else "fixture"
    try:
        expected, supplier_root = _supplier_input(supplier_case)
        if installed_replay and not isinstance(bb_trace, (Path, str)):
            raise ValueError("installed replay requires a persisted BreadBoard trace path")
        if isinstance(bb_trace, (Path, str)):
            raw = Path(bb_trace).read_bytes()
        else:
            raw = json.dumps(
                bb_trace, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        trace_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        bb_value = json.loads(raw)
        if not isinstance(bb_value, Mapping):
            raise ValueError("BreadBoard trace must be an object")
        candidate_job = bb_value.get("job_id")
        if installed_replay:
            recorded_job = os.environ.get("SLURM_JOB_ID")
            if not isinstance(recorded_job, str) or re.fullmatch(r"[1-9][0-9]*", recorded_job) is None:
                raise ValueError("installed replay requires a numeric SLURM_JOB_ID from the run environment")
            if job_id is not None and job_id != recorded_job:
                raise ValueError("replay job_id differs from installed SLURM_JOB_ID")
            if candidate_job is not None and candidate_job != recorded_job:
                raise ValueError("BreadBoard trace job_id differs from installed SLURM_JOB_ID")
            job_id = recorded_job
        elif candidate_job is not None and (not isinstance(candidate_job, str) or not candidate_job.strip()):
            raise ValueError("BreadBoard job_id must be a nonempty string")
        elif job_id is not None and (not isinstance(job_id, str) or not job_id.strip()):
            raise ValueError("replay job_id must be a nonempty string")
        elif job_id is not None and candidate_job is not None and job_id != candidate_job:
            raise ValueError("replay job_id differs from BreadBoard trace job_id")
        else:
            job_id = job_id or candidate_job
        observed_root = _declared_workspace_root(bb_value)
        if installed_replay:
            if receiver_transcript is None:
                raise ValueError("installed replay requires a persisted receiver HTTP transcript path")
            trace_requests = bb_value.get("requests")
            if not isinstance(trace_requests, list) or any(
                not isinstance(row, Mapping) or not isinstance(row.get("body"), Mapping)
                for row in trace_requests
            ):
                raise ValueError("BreadBoard trace request bodies must be objects")
            recorded = [
                {"index": row.get("index"), "body": row["body"]}
                for row in trace_requests
            ]
            receiver_difference = _first_difference(
                recorded, _receiver_requests(Path(receiver_transcript)), "$.receiver_http_requests",
            )
        observed = project_bb_trace(bb_value)
        expected_changed, observed_changed = [False], [False]
        expected = _replace_workspace(expected, supplier_root, expected_changed)
        observed = _replace_workspace(observed, observed_root, observed_changed)
        if expected_changed[0] or observed_changed[0]:
            normalizations.append("workspace_root:<WORKSPACE>")
    except (OSError, TypeError, ValueError) as exc:
        return _report([], [str(exc)], normalizations, bb_trace_sha256=trace_hash, job_id=job_id, mode=mode)
    assertions: list[dict[str, Any]] = []
    overlay_config = _load_json(
        Path(__file__).resolve().parents[2] / "config/e4_targets/hermes_agent/2026.9.11/native-config.json"
    )
    if installed_replay:
        assertions.append(_assertion(
            f"{expected['case_id']}.receiver_http_requests_equal",
            "recorded BreadBoard request bodies", "receiver-observed HTTP request bodies",
            receiver_difference,
        ))
    overlay = overlay_config["schema_overlay"]
    request_difference, used_overlay, unoverlaid = _request_difference(expected["requests"], observed["requests"], overlay)
    for field in _CANONICAL_FIELDS:
        difference = request_difference if field == "requests" else _first_difference(
            expected.get(field), observed.get(field), f"$.{field}"
        )
        assertions.append(_assertion(f"{expected['case_id']}.{field}_equal", expected.get(field), observed.get(field), difference))
    return _report(
        assertions, normalizations=normalizations,
        gaps=[{"id": _SCHEMA_GAP, "evidence": "source-derived"}] if used_overlay or unoverlaid else [],
        source_derived_controls=_PACKET_ABSENT_CONTROLS,
        bb_trace_sha256=trace_hash, job_id=job_id, mode=mode,
    )


class HermesAgentComparator:
    """Callable Mini-compatible comparator with direct case projection support."""

    def __call__(self, inp: ComparatorInput | Mapping[str, Any]) -> dict[str, Any]:
        return compare(inp)

    def compare(self, supplier_case: Path | str | Mapping[str, Any], bb_trace: Mapping[str, Any] | Path | str) -> dict[str, Any]:
        return compare_cases(supplier_case, bb_trace)


def _manifest_case_path(ref: Any, manifest_path: Path) -> Path:
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        raise ValueError("case reference must contain a path")
    raw = Path(ref["path"])
    if raw.is_absolute():
        raise ValueError("case reference path must be relative")
    path = (manifest_path.parent / raw).resolve()
    if not path.exists():
        raise ValueError(f"missing case reference {raw}")
    return path


def compare(inp: ComparatorInput | Mapping[str, Any]) -> dict[str, Any]:
    """Compare a direct supplier case/BB trace pair or Mini-style manifests."""
    if "supplier_case" in inp or "bb_trace" in inp:
        if "supplier_case" not in inp or "bb_trace" not in inp:
            return _report([], ["supplier_case and bb_trace are both required"])
        return compare_cases(inp["supplier_case"], inp["bb_trace"])

    capture = inp.get("capture", {})
    replay = inp.get("replay", {})
    artifacts = inp.get("artifacts", {})
    repo_root = Path(inp.get("repo_root") or Path(__file__).resolve().parents[2])
    capture_ref = Path(artifacts.get("capture_ref", repo_root / "capture.json"))
    replay_ref = Path(artifacts.get("replay_ref", repo_root / "replay.json"))
    if not isinstance(capture, Mapping) or not isinstance(replay, Mapping):
        return _report([], ["capture and replay manifests are required"])
    capture_cases = capture.get("cases", {})
    replay_cases = replay.get("cases", {})
    if not isinstance(capture_cases, Mapping) or not isinstance(replay_cases, Mapping):
        return _report([], ["capture.cases and replay.cases must be objects"])
    assertions: list[dict[str, Any]] = []
    errors: list[str] = []
    for case_id in sorted(set(capture_cases) | set(replay_cases)):
        if case_id not in capture_cases or case_id not in replay_cases:
            assertions.append(_assertion(f"{case_id}.case_present", True, False, f"first difference at $.case_ids: missing {case_id}"))
            continue
        try:
            supplier = _manifest_case_path(capture_cases[case_id], capture_ref)
            replay_path = _manifest_case_path(replay_cases[case_id], replay_ref)
            result = compare_cases(supplier, _load_json(replay_path))
            assertions.extend(result["assertions"])
            errors.extend(result["errors"])
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"{case_id}: {exc}")
    return _report(assertions, errors)


COMPARATOR = HermesAgentComparator()
