"""Canonical OpenClaw 2026.9.4 supplier/replay comparator.

The comparator deliberately compares observable episode data rather than supplier
success labels.  It has a small projection API for synthetic tests and packet
replay, then a report-producing callable compatible with the existing comparator
protocol.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from conformance.comparators.protocol import ComparatorInput

COMPARATOR_ID = "openclaw_2026_9_4_trace_v1"
LANE_ID = "openclaw_2026_9_4_replay"
CONFIG_ID = "openclaw_2026_9_4_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
TRACE_SCHEMA_VERSION = "bb.e4.openclaw-episode.v1"
SOURCE_COMMIT = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
SOURCE_CITATIONS = {
    "tools": "src/agents/core-coding-tools.ts:createCoreCodingTools",
    "title": "src/agents/schema/typebox.ts:executionTitleSchema",
    "edit": "src/agents/sessions/tools/edit.ts:prepareEditArguments",
    "bootstrap": "src/agents/workspace.ts:WORKSPACE_BOOTSTRAP_FILENAMES",
    "exec": "src/agents/bash-tools.exec-run.ts:createExecTool",
    "process": "src/agents/bash-tools.process.ts:resolvePollWaitMs",
    "stream": "packages/agent-core/src/agent-stream-response.ts:finalizeAssistantMessage",
    "recovery": "src/agents/sessions/agent-session-execution.ts:prepareRetry",
}
TOOL_ORDER = ("ls", "read", "edit", "write", "exec", "process")
VOLATILE_PLACEHOLDER_RE = re.compile(r"^<[A-Z][A-Z0-9_.-]*>$")


class ComparatorError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _text_sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _admitted_overlay() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    config = _load_json(root / "config/e4_targets/openclaw/2026.9.4/native-config.json")
    try:
        overlay = config["advertisement"]["tools"]["exec"]
        description = overlay["description"]
        native_sha = overlay["native_sha256"]
    except (KeyError, TypeError):
        raise ComparatorError("OpenClaw exec advertisement overlay is malformed") from None
    if (
        type(description) is not str
        or type(native_sha) is not str
        or _text_sha256(description) == native_sha
    ):
        raise ComparatorError("OpenClaw exec advertisement overlay must differ from native bytes")
    return {
        "tool": "exec",
        "native_sha256": native_sha,
        "overlay_sha256": _text_sha256(description),
        "description": description,
    }


def _apply_supplier_overlay(raw_requests: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    overlay = _admitted_overlay()
    transformed: list[dict[str, Any]] = []
    for body in raw_requests:
        current = json.loads(json.dumps(body))
        for tool in current.get("tools", []):
            function = tool.get("function") if isinstance(tool, Mapping) else None
            if not isinstance(function, MutableMapping) or function.get("name") != "exec":
                continue
            description = function.get("description")
            if type(description) is not str:
                raise ComparatorError("supplier exec description is missing")
            actual = _text_sha256(description)
            if actual not in {overlay["native_sha256"], overlay["overlay_sha256"]}:
                raise ComparatorError(
                    "supplier exec description sha does not match declared native_sha256"
                )
            function["description"] = overlay["description"]
        transformed.append(current)
    return transformed, {key: value for key, value in overlay.items() if key != "description"}


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _json_lines(path: Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, Mapping):
            rows.append(value)
    return rows


def _normalize_declared(value: Any, declared: set[str], path: tuple[Any, ...] = ()) -> Any:
    """Apply only placeholders declared by the trace's ``normalizations`` map."""
    if isinstance(value, Mapping):
        if set(value) == {"placeholder"} and isinstance(value["placeholder"], str):
            placeholder = value["placeholder"]
            if placeholder not in declared:
                raise ComparatorError(f"undeclared placeholder {placeholder} at $.{'.'.join(map(str, path))}")
            return placeholder
        return {str(key): _normalize_declared(item, declared, path + (key,)) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_declared(item, declared, path + (index,)) for index, item in enumerate(value)]
    return value


def _declared_placeholders(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    raw = value.get("normalizations", {})
    if isinstance(raw, Mapping):
        names = set(str(key) for key in raw)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        names = set(str(item) for item in raw)
    else:
        names = set()
    for name in names:
        if not VOLATILE_PLACEHOLDER_RE.match(name) and not name.startswith("<"):
            raise ComparatorError(f"invalid normalization placeholder {name!r}")
    return names


def _trace_requests_from_transcript(case_dir: Path) -> list[dict[str, Any]]:
    rows = _json_lines(case_dir / "receiver" / "http-transcript.jsonl")
    requests: list[dict[str, Any]] = []
    for row in rows:
        body = row.get("body")
        if isinstance(body, Mapping):
            requests.append(dict(body))
    return requests


def _project_request_bodies(raw_requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project sent wire bodies to the canonical request contract."""
    return [
        {"messages": body.get("messages", []), "tools": body.get("tools", [])}
        for body in raw_requests
    ]


def _wire_identity(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _call_identity(call: Mapping[str, Any]) -> str:
    return _wire_identity(call)


def _tool_calls_from_requests(requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for request in requests:
        for message in request.get("messages", []):
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                continue
            for index, call in enumerate(message.get("tool_calls", ()) or ()):
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function") if isinstance(call.get("function"), Mapping) else call
                name = function.get("name")
                if not isinstance(name, str):
                    continue
                raw = function.get("arguments", call.get("arguments", {}))
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except ValueError:
                        pass
                projected = {
                    "id": str(call.get("id", call.get("tool_call_id", f"call_{len(calls)}_{index}"))),
                    "name": name,
                    "arguments": raw,
                }
                identity = _call_identity(call)
                previous = seen.get(projected["id"])
                if previous is not None:
                    if previous != identity:
                        raise ComparatorError(
                            f"repeated tool call {projected['id']} differs across request snapshots"
                        )
                    continue
                seen[projected["id"]] = identity
                calls.append(projected)
    return calls




def _tool_calls_from_scenario(scenario: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for step_index, step in enumerate(scenario.get("steps", [])):
        if not isinstance(step, Mapping):
            continue
        for call_index, call in enumerate(step.get("tool_calls", [])):
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
            name = call.get("name") or function.get("name")
            args = call.get("arguments", function.get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass
            calls.append({
                "id": str(call.get("id", f"call_{step_index}_{call_index}")),
                "name": name,
                "arguments": args,
            })
    return calls


def _results_from_scenario(
    scenario: Mapping[str, Any], workspace: Path, calls: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    oracle = scenario.get("oracle") if isinstance(scenario.get("oracle"), Mapping) else {}
    expected_file = oracle.get("file") if isinstance(oracle, Mapping) else None
    for call in calls:
        result: dict[str, Any] = {
            "tool_call_id": call.get("id"),
            "name": call.get("name"),
            "error": None,
        }
        if call.get("name") in {"write", "edit"}:
            result["status"] = (
                "completed" if expected_file and (workspace / str(expected_file)).exists() else "unknown"
            )
        elif call.get("name") == "read" and expected_file:
            target = workspace / str(expected_file)
            result["content"] = target.read_text(encoding="utf-8") if target.exists() else None
        elif call.get("name") == "exec":
            result["status"] = "completed"
        results.append(result)
    return results


def _results_from_requests(
    requests: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Project unique model-visible tool messages from captured request bodies."""
    names = {str(call.get("id")): call.get("name") for call in calls}
    results: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for request in requests:
        for message in request.get("messages", []):
            if not isinstance(message, Mapping) or message.get("role") not in {"tool", "toolResult"}:
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    str(item.get("text", item)) if isinstance(item, Mapping) else str(item)
                    for item in content
                )
            call_id = message.get("tool_call_id", message.get("toolCallId"))
            is_error = bool(message.get("is_error", message.get("isError", False)))
            error_value = message.get("error")
            if error_value is None and is_error:
                error_value = str(content)
            projected = {
                "tool_call_id": call_id,
                "name": message.get(
                    "name",
                    message.get("tool_name", message.get("toolName", names.get(str(call_id)))),
                ),
                "content": content,
                "isError": is_error,
                "error": error_value,
            }
            if call_id is None:
                results.append(projected)
                continue
            identity = _wire_identity(message)
            previous = seen.get(str(call_id))
            if previous is not None:
                if previous != identity:
                    raise ComparatorError(
                        f"repeated tool result {call_id} differs across request snapshots"
                    )
                continue
            seen[str(call_id)] = identity
            results.append(projected)
    return results


def _termination(scenario: Mapping[str, Any], request_count: int) -> dict[str, Any]:
    steps = scenario.get("steps", [])
    last = steps[-1] if isinstance(steps, Sequence) and steps else {}
    failure = any(
        isinstance(step, Mapping) and step.get("kind") == "http_error" for step in steps
    )
    malformed = False
    for step in steps if isinstance(steps, Sequence) else ():
        if not isinstance(step, Mapping):
            continue
        for call in step.get("tool_calls", ()):
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
            raw = call.get("arguments", function.get("arguments", {}))
            if isinstance(raw, str):
                try:
                    decoded = json.loads(raw)
                except ValueError:
                    malformed = True
                else:
                    malformed |= not isinstance(decoded, Mapping)
    if failure:
        kind = "provider_failure"
        native_stop_reason = last.get("finish_reason") if isinstance(last, Mapping) else "error"
        error_value = None
    elif malformed:
        kind = "malformed_tool_call"
        native_stop_reason = last.get("finish_reason") if isinstance(last, Mapping) else "tool_calls"
        error_value = None
    elif scenario.get("max_requests") is not None:
        kind = "request_budget"
        native_stop_reason = "429"
        error_value = "bbe4 capture request cap"
    else:
        kind = "stop"
        native_stop_reason = last.get("finish_reason") if isinstance(last, Mapping) else None
        error_value = None
    termination = {
        "kind": kind,
        "native_stop_reason": native_stop_reason,
    }
    if error_value is not None:
        termination.update({"isError": True, "status": 429, "error": error_value})
    return termination


def _effects(workspace: Path, scenario: Mapping[str, Any]) -> dict[str, str | None]:
    paths: list[str] = []
    raw_paths = scenario.get("probe_paths", [])
    if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes)):
        paths.extend(str(path) for path in raw_paths)
    oracle = scenario.get("oracle")
    if isinstance(oracle, Mapping) and oracle.get("file"):
        path = str(oracle["file"])
        if path not in paths:
            paths.append(path)
    effects: dict[str, str | None] = {}
    for relative in paths:
        target = (workspace / relative).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            effects[relative] = None
            continue
        effects[relative] = _sha256(target) if target.is_file() else None
    # Include every non-bootstrap workspace effect so unexpected writes fail.
    try:
        for candidate in workspace.rglob("*"):
            if candidate.is_file() and candidate.name not in {"AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "BOOTSTRAP.md", "MEMORY.md"}:
                effects.setdefault(str(candidate.relative_to(workspace)), _sha256(candidate))
    except OSError:
        pass
    return effects




def _validate_trace(trace: Mapping[str, Any]) -> None:
    required = {"requests", "tool_calls", "results", "effects", "termination", "request_count"}
    missing = sorted(required - set(trace))
    if missing:
        raise ComparatorError(f"trace missing required fields: {', '.join(missing)}")
    if not isinstance(trace["requests"], list) or not isinstance(trace["tool_calls"], list) or not isinstance(trace["results"], list):
        raise ComparatorError("requests, tool_calls, and results must be arrays")
    if int(trace["request_count"]) != len(trace["requests"]):
        raise ComparatorError("request_count must equal ordered requests length")
    for index, call in enumerate(trace["tool_calls"]):
        if not isinstance(call, Mapping) or call.get("name") not in TOOL_ORDER:
            raise ComparatorError(f"tool_calls[{index}] contains an undeclared tool")
    termination = trace["termination"]
    if not isinstance(termination, Mapping) or not termination.get("kind"):
        raise ComparatorError("termination must declare kind and native stop reason")


def _canonicalize(trace: Mapping[str, Any]) -> dict[str, Any]:
    declared = _declared_placeholders(trace)
    canonical = {
        "schema_version": str(trace.get("schema_version", TRACE_SCHEMA_VERSION)),
        "source_commit": str(trace.get("source_commit", SOURCE_COMMIT)),
        "requests": _normalize_declared(trace.get("requests", []), declared, ("requests",)),
        "tool_calls": _normalize_declared(trace.get("tool_calls", []), declared, ("tool_calls",)),
        "results": _normalize_declared(trace.get("results", []), declared, ("results",)),
        "effects": _normalize_declared(trace.get("effects", {}), declared, ("effects",)),
        "termination": _normalize_declared(trace.get("termination", {}), declared, ("termination",)),
        "request_count": int(trace.get("request_count", 0)),
    }
    if "classification" in trace:
        canonical["classification"] = _normalize_declared(trace["classification"], declared, ("classification",))
    if "envelope" in trace:
        canonical["envelope"] = _normalize_declared(trace["envelope"], declared, ("envelope",))
    elif "final_envelope" in trace:
        canonical["envelope"] = _normalize_declared(trace["final_envelope"], declared, ("envelope",))
    if "normalizations" in trace:
        canonical["normalizations"] = trace["normalizations"]
    _validate_trace(canonical)
    return canonical

def project_supplier_case(case_dir: str | Path) -> dict[str, Any]:
    """Project a captured supplier case directory into one canonical episode."""
    root = Path(case_dir)
    scenario = _load_json(root / "scenario.json", {})
    if not isinstance(scenario, Mapping):
        raise ComparatorError(f"invalid scenario at {root / 'scenario.json'}")
    raw_requests = _trace_requests_from_transcript(root)
    if not raw_requests:
        closed = _load_json(root / "receiver" / "closed.json", {})
        request_count = int(closed.get("requests", 0)) if isinstance(closed, Mapping) else 0
        raw_requests = [{"messages": [], "tools": []} for _ in range(request_count)]
    raw_requests, _ = _apply_supplier_overlay(raw_requests)
    requests = _project_request_bodies(raw_requests)
    calls = _tool_calls_from_requests(requests) or _tool_calls_from_scenario(scenario)
    workspace = root / "workspace"
    trace = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "source_commit": SOURCE_COMMIT,
        "requests": requests,
        "tool_calls": calls,
        "results": _results_from_requests(requests, calls) or _results_from_scenario(scenario, workspace, calls),
        "effects": _effects(workspace, scenario),
        "termination": _termination(scenario, len(requests)),
        "request_count": len(requests),
        "normalizations": scenario.get("normalizations", {}),
    }
    receipt = _load_json(root / "case-receipt.json", {})
    if isinstance(receipt, Mapping):
        if "classification" in receipt:
            trace["classification"] = receipt["classification"]
        if "envelope" in receipt:
            trace["envelope"] = receipt["envelope"]
        elif "final_envelope" in receipt:
            trace["envelope"] = receipt["final_envelope"]
    return _canonicalize(trace)

def _load_trace_input(trace: Any) -> Mapping[str, Any]:
    if isinstance(trace, Mapping):
        return trace
    if isinstance(trace, (str, Path)):
        path = Path(trace)
        loaded = _load_json(path)
        if not isinstance(loaded, Mapping):
            raise ComparatorError(f"trace is not a JSON object: {path}")
        return loaded
    raise ComparatorError("trace must be a mapping or JSON path")

def project_bb_trace(trace: Any) -> dict[str, Any]:
    """Project a BreadBoard trace object/file into the same canonical episode."""
    value = _load_trace_input(trace)
    if "episode" in value and isinstance(value["episode"], Mapping):
        value = value["episode"]
    value = dict(value)
    value.setdefault("normalizations", {})
    raw_requests = value.get("requests")
    if isinstance(raw_requests, list):
        if any(not isinstance(request, Mapping) for request in raw_requests):
            raise ComparatorError("requests must contain only mapping bodies")
        requests = _project_request_bodies(raw_requests)
        value["requests"] = requests
        calls = _tool_calls_from_requests(requests)
        value["tool_calls"] = calls
        value["results"] = _results_from_requests(requests, calls)
    elif "tool_calls" not in value and isinstance(value.get("events"), list):
        value["tool_calls"] = [
            event for event in value["events"]
            if isinstance(event, Mapping) and event.get("type") == "tool_call"
        ]
        value["results"] = [
            event for event in value["events"]
            if isinstance(event, Mapping) and event.get("type") == "tool_result"
        ]
    return _canonicalize(value)


def _supplier_trace_with_overlay(trace: Any) -> tuple[dict[str, Any], dict[str, str]]:
    projected = project_bb_trace(trace)
    requests, overlay = _apply_supplier_overlay(projected["requests"])
    projected["requests"] = _project_request_bodies(requests)
    projected["tool_calls"] = _tool_calls_from_requests(projected["requests"])
    projected["results"] = _results_from_requests(projected["requests"], projected["tool_calls"])
    return _canonicalize(projected), overlay


def _difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    if type(expected) is not type(observed) and not (isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(observed, (int, float)) and not isinstance(observed, bool)):
        return f"first difference at {path}: expected {expected!r}, observed {observed!r}"
    if isinstance(expected, Mapping):
        if set(expected) != set(observed):
            return f"first difference at {path}: keys differ"
        for key in expected:
            found = _difference(expected[key], observed[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(observed):
            return f"first difference at {path}: lengths differ ({len(expected)} != {len(observed)})"
        for index, (left, right) in enumerate(zip(expected, observed)):
            found = _difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if expected == observed else f"first difference at {path}: expected {expected!r}, observed {observed!r}"


def _assertion(assertion_id: str, expected: Any, observed: Any, detail: str | None = None) -> dict[str, Any]:
    return {"assertion_id": assertion_id, "status": "passed" if detail is None else "failed", "expected": expected, "observed": observed, "detail": detail or ""}


class OpenClawComparator:
    comparator_id = COMPARATOR_ID
    lane_id = LANE_ID
    config_id = CONFIG_ID

    def compare_traces(self, expected: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
        expected_canonical = _canonicalize(expected)
        observed_canonical = _canonicalize(observed)
        detail = _difference(expected_canonical, observed_canonical)
        return _assertion("episode_equal", expected_canonical, observed_canonical, detail)

    def __call__(self, inp: ComparatorInput) -> dict[str, Any]:
        return compare(inp)


def compare(inp: ComparatorInput) -> dict[str, Any]:
    """Compare supplier/replay role artifacts with explicit negative gates."""
    errors: list[str] = []
    assertions: list[dict[str, Any]] = []
    capture = inp.get("capture", {})
    replay = inp.get("replay", {})
    overlay_info: dict[str, str] | None = None
    try:
        if isinstance(capture, (str, Path)):
            expected = project_supplier_case(capture)
            _, overlay_info = _apply_supplier_overlay(expected["requests"])
        else:
            expected, overlay_info = _supplier_trace_with_overlay(
                capture.get("trace", capture) if isinstance(capture, Mapping) else capture
            )
    except (ComparatorError, OSError, TypeError, ValueError) as exc:
        errors.append(f"capture: {exc}")
        expected = None
    try:
        observed = project_bb_trace(replay.get("trace", replay) if isinstance(replay, Mapping) else replay)
    except (ComparatorError, OSError, TypeError, ValueError) as exc:
        errors.append(f"replay: {exc}")
        observed = None
    if expected is not None and observed is not None:
        assertions.append(_assertion("episode_equal", expected, observed, _difference(expected, observed)))
        assertions.append(_assertion("request_count_equal", expected["request_count"], observed["request_count"], None if expected["request_count"] == observed["request_count"] else "request count differs"))
        assertions.append(_assertion("tool_order_equal", expected["tool_calls"], observed["tool_calls"], _difference(expected["tool_calls"], observed["tool_calls"])))
        assertions.append(_assertion("effects_equal", expected["effects"], observed["effects"], _difference(expected["effects"], observed["effects"])))
    if overlay_info is not None:
        assertions.append(_assertion("supplier_exec_overlay", overlay_info, overlay_info))
    passed = sum(assertion["status"] == "passed" for assertion in assertions)
    passed = sum(assertion["status"] == "passed" for assertion in assertions)
    failed = sum(assertion["status"] == "failed" for assertion in assertions)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "comparator_id": COMPARATOR_ID,
        "lane_id": str((inp.get("scope") or {}).get("lane_id", LANE_ID)),
        "config_id": str((inp.get("scope") or {}).get("config_id", CONFIG_ID)),
        "scope": dict(inp.get("scope") or {}),
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "overlay": overlay_info,
        "assertions": assertions,
        "details": [{"assertion_id": item["assertion_id"], "status": item["status"], "detail": item["detail"]} for item in assertions],
        "errors": errors,
        "passed": passed,
        "failed": failed,
        "warned": 0,
        "ok": not errors and failed == 0,
    }


comparator = OpenClawComparator()
