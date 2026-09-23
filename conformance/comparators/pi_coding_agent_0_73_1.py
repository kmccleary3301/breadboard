"""Deterministic comparator for Pi 0.73.1 supplier and BreadBoard traces.

Only these typed normalizations are allowed:

* the declared workspace root becomes ``<WORKSPACE>``;
* the declared home root becomes ``<HOME>``;
* the declared current date becomes ``<CURRENT_DATE>`` only on an exact
  ``Current date: <value>`` line in a system message;
* the three declared Pi documentation paths become ``<PI_PACKAGE_DIR>/...``;
* the supplier read-tool description is replaced by the declared advertisement
  after its native SHA-256 is verified; and
* each declared supplier advertisement prompt string is removed exactly once
  from each system message.

Supplier roots and package directory are fixed capture-lane declarations. BB
roots, date, and package directory must be supplied in the top-level
``runtime_inputs`` object. No arbitrary placeholder or volatile-field removal
is performed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping

COMPARATOR_ID = "pi_coding_agent_0_73_1_trace_v1"
LANE_ID = "pi_coding_agent_0_73_1_replay"
CONFIG_ID = "pi_coding_agent_0_73_1_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
TRACE_SCHEMA_VERSION = "bb.e4.pi-canonical-episode.v1"

SUPPLIER_WORKSPACE_ROOT = "/capture/workspace"
SUPPLIER_HOME_ROOT = "/capture/home"
SUPPLIER_CURRENT_DATE = "2026-09-23"
SUPPLIER_PACKAGE_DIR = "/opt/pi/app"

RuleName = Literal[
    "workspace_root",
    "home_root",
    "current_date_system_line",
    "package_dir_documentation_path",
    "advertisement_read_description",
    "advertisement_prompt_removal",
]
NORMALIZATIONS: tuple[RuleName, ...] = (
    "workspace_root",
    "home_root",
    "current_date_system_line",
    "package_dir_documentation_path",
    "advertisement_read_description",
    "advertisement_prompt_removal",
)


@dataclass(frozen=True)
class _RuntimeInputs:
    workspace_root: str
    home_root: str
    current_date: str
    package_dir: str


@dataclass
class _RuleCounts:
    values: dict[RuleName, int] = field(default_factory=lambda: {rule: 0 for rule in NORMALIZATIONS})

    def add(self, rule: RuleName, count: int = 1) -> None:
        self.values[rule] += count

    def report(self, side: Literal["supplier", "bb"]) -> list[dict[str, Any]]:
        return [{"side": side, "rule": rule, "count": self.values[rule]} for rule in NORMALIZATIONS]


@dataclass(frozen=True)
class _Advertisement:
    read_description: str
    native_sha256: str
    remove_exact: tuple[str, ...]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_text(value: Any, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _supplier_runtime_inputs(trace: Mapping[str, Any]) -> _RuntimeInputs:
    if "current_date" in trace and trace["current_date"] != SUPPLIER_CURRENT_DATE:
        raise ValueError(f"supplier trace current_date must equal capture constant {SUPPLIER_CURRENT_DATE}")
    runtime_inputs = trace.get("runtime_inputs")
    if isinstance(runtime_inputs, Mapping) and "current_date" in runtime_inputs:
        declared = runtime_inputs["current_date"]
        if declared != SUPPLIER_CURRENT_DATE:
            raise ValueError(f"supplier runtime_inputs.current_date must equal capture constant {SUPPLIER_CURRENT_DATE}")
    return _RuntimeInputs(
        workspace_root=SUPPLIER_WORKSPACE_ROOT,
        home_root=SUPPLIER_HOME_ROOT,
        current_date=SUPPLIER_CURRENT_DATE,
        package_dir=SUPPLIER_PACKAGE_DIR,
    )


def _bb_runtime_inputs(trace: Mapping[str, Any]) -> _RuntimeInputs:
    declared = trace.get("runtime_inputs")
    if not isinstance(declared, Mapping):
        raise ValueError("BB trace runtime_inputs must declare cwd, home, current_date, and package_dir")
    missing = [name for name in ("cwd", "home", "current_date", "package_dir") if name not in declared]
    if missing:
        raise ValueError(f"BB trace runtime_inputs missing: {', '.join(missing)}")
    return _RuntimeInputs(
        workspace_root=_required_text(declared["cwd"], "BB runtime_inputs.cwd"),
        home_root=_required_text(declared["home"], "BB runtime_inputs.home"),
        current_date=_required_text(declared["current_date"], "BB runtime_inputs.current_date"),
        package_dir=_required_text(declared["package_dir"], "BB runtime_inputs.package_dir"),
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_advertisement() -> _Advertisement:
    config_path = _repo_root() / "config" / "e4_targets" / "pi" / "0.73.1" / "native-config.json"
    config = _load_json(config_path)
    if not isinstance(config, Mapping):
        raise ValueError(f"Pi native config must be an object: {config_path}")
    advertisement = config.get("advertisement")
    if not isinstance(advertisement, Mapping):
        raise ValueError("Pi native config advertisement must be an object")
    tools = advertisement.get("tools")
    read = tools.get("read") if isinstance(tools, Mapping) else None
    if not isinstance(read, Mapping):
        raise ValueError("Pi advertisement must declare tools.read")
    read_description = _required_text(read.get("description"), "Pi advertisement read description")
    native_sha256 = _required_text(read.get("native_sha256"), "Pi advertisement read native_sha256")
    remove_exact_raw = advertisement.get("prompt", {}).get("remove_exact") if isinstance(advertisement.get("prompt"), Mapping) else None
    if not isinstance(remove_exact_raw, list) or any(type(item) is not str or not item for item in remove_exact_raw):
        raise ValueError("Pi advertisement prompt.remove_exact must be a non-empty list of strings")
    if not native_sha256.startswith("sha256:"):
        raise ValueError("Pi advertisement read native_sha256 must use sha256:<hex>")
    return _Advertisement(read_description, native_sha256, tuple(remove_exact_raw))


def _replace_path_token(value: str, source: str, replacement: str, rule: RuleName, counts: _RuleCounts) -> str:
    pattern = re.compile(rf"{re.escape(source)}(?=/|$|\s|[),.;:])")

    def replace(match: re.Match[str]) -> str:
        counts.add(rule)
        return replacement

    return pattern.sub(replace, value)


def _replace_root(value: str, root: str, replacement: str, rule: RuleName, counts: _RuleCounts) -> str:
    return _replace_path_token(value, root, replacement, rule, counts)


def _replace_package_paths(value: str, package_dir: str, counts: _RuleCounts) -> str:
    replacement = "<PI_PACKAGE_DIR>"
    for suffix in ("README.md", "docs", "examples"):
        source = f"{package_dir}/{suffix}"
        value = _replace_path_token(value, source, replacement + f"/{suffix}", "package_dir_documentation_path", counts)
    return value


def _normalize_string(value: str, runtime: _RuntimeInputs, counts: _RuleCounts, *, system_message: bool) -> str:
    if system_message:
        pattern = re.compile(rf"(?m)^Current date: {re.escape(runtime.current_date)}(?=\r?$)")
        value, date_count = pattern.subn("Current date: <CURRENT_DATE>", value)
        if date_count:
            counts.add("current_date_system_line", date_count)
    value = _replace_root(value, runtime.workspace_root, "<WORKSPACE>", "workspace_root", counts)
    value = _replace_root(value, runtime.home_root, "<HOME>", "home_root", counts)
    return _replace_package_paths(value, runtime.package_dir, counts)


def _normalize(value: Any, runtime: _RuntimeInputs, counts: _RuleCounts, *, system_message: bool = False) -> Any:
    if isinstance(value, str):
        return _normalize_string(value, runtime, counts, system_message=system_message)
    if isinstance(value, list):
        return [_normalize(item, runtime, counts, system_message=system_message) for item in value]
    if isinstance(value, Mapping):
        is_system = system_message or value.get("role") == "system"
        return {key: _normalize(item, runtime, counts, system_message=is_system) for key, item in value.items()}
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
            blocks.append({"type": "toolCall", "id": block.get("id"), "name": block.get("name"), "arguments": block.get("arguments", {})})
        elif block.get("type") == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        else:
            blocks.append(dict(block))
    return blocks


def _canonical_messages(messages: Any, runtime: _RuntimeInputs, counts: _RuleCounts) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    result: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role == "assistant":
            raw = {"role": "assistant", "content": _content_blocks(message), "stop_reason": message.get("stopReason", message.get("stop_reason"))}
        elif role in {"toolResult", "tool", "tool_result"}:
            content = message.get("content", [])
            text = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping) and item.get("type") == "text") if isinstance(content, list) else str(content)
            raw = {
                "role": "toolResult",
                "tool_call_id": message.get("toolCallId", message.get("tool_call_id")),
                "tool_name": message.get("toolName", message.get("tool_name", message.get("name"))),
                "content": text,
                "is_error": bool(message.get("isError", message.get("is_error", False))),
            }
        elif role == "user":
            raw = {"role": "user", "content": message.get("content", "")}
        else:
            continue
        result.append(_normalize(raw, runtime, counts))
    return result


def _from_events(events: Any, runtime: _RuntimeInputs, counts: _RuleCounts) -> list[dict[str, Any]]:
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
    return _canonical_messages(messages, runtime, counts)


def _project(trace: Mapping[str, Any], requests: list[Mapping[str, Any]], runtime: _RuntimeInputs, counts: _RuleCounts) -> dict[str, Any]:
    messages = _canonical_messages(trace.get("messages"), runtime, counts)
    if not messages:
        messages = _from_events(trace.get("events"), runtime, counts)
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
        "requests": _normalize(requests, runtime, counts),
        "tool_calls": calls,
        "observations": observations,
        "effects": _normalize(trace.get("effects", {}), runtime, counts),
        "termination": _normalize(dict(termination), runtime, counts),
        "request_count": trace.get("request_count", len(requests)),
    }


def _replace_prompt_strings(value: Any, needle: str) -> tuple[Any, int]:
    if isinstance(value, str):
        count = value.count(needle)
        return value.replace(needle, ""), count
    if isinstance(value, list):
        output: list[Any] = []
        count = 0
        for item in value:
            replaced, item_count = _replace_prompt_strings(item, needle)
            output.append(replaced)
            count += item_count
        return output, count
    if isinstance(value, Mapping):
        output: dict[Any, Any] = {}
        count = 0
        for key, item in value.items():
            replaced, item_count = _replace_prompt_strings(item, needle)
            output[key] = replaced
            count += item_count
        return output, count
    return value, 0


def _apply_supplier_advertisement(requests: list[Mapping[str, Any]], counts: _RuleCounts) -> list[Mapping[str, Any]]:
    advertisement = _load_advertisement()
    output: list[Mapping[str, Any]] = []
    for request_index, request in enumerate(requests):
        body = deepcopy(dict(request))
        tools = body.get("tools")
        if not isinstance(tools, list):
            raise ValueError(f"supplier request {request_index} tools must be a list")
        read_count = 0
        for tool_index, tool in enumerate(tools):
            if not isinstance(tool, Mapping):
                raise ValueError(f"supplier request {request_index} tool {tool_index} must be an object")
            if tool.get("type") != "function":
                raise ValueError(f"supplier request {request_index} tool {tool_index} type must be function")
            function = tool.get("function")
            if not isinstance(function, Mapping):
                raise ValueError(f"supplier request {request_index} tool {tool_index} function must be an object")
            name = function.get("name")
            if not isinstance(name, str):
                raise ValueError(f"supplier request {request_index} tool {tool_index} function.name must be a string")
            if name != "read":
                continue
            read_count += 1
            description = function.get("description")
            if type(description) is not str:
                raise ValueError(f"supplier request {request_index} read description is missing")
            actual = "sha256:" + hashlib.sha256(description.encode("utf-8")).hexdigest()
            if actual != advertisement.native_sha256:
                raise ValueError(
                    f"supplier request {request_index} read description sha256 mismatch: expected {advertisement.native_sha256}, observed {actual}"
                )
            updated_function = dict(function)
            updated_function["description"] = advertisement.read_description
            updated_tool = dict(tool)
            updated_tool["function"] = updated_function
            tools[tool_index] = updated_tool
            counts.add("advertisement_read_description")
        if read_count != 1:
            raise ValueError(
                f"supplier request {request_index} tools must contain exactly one read function, observed {read_count}"
            )
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError(f"supplier request {request_index} messages must be a list")
        for needle in advertisement.remove_exact:
            total = 0
            replaced_messages: list[Any] = []
            for message in messages:
                if isinstance(message, Mapping) and message.get("role") == "system":
                    replaced, item_count = _replace_prompt_strings(message, needle)
                    replaced_messages.append(replaced)
                    total += item_count
                else:
                    replaced_messages.append(message)
            if total != 1:
                raise ValueError(
                    f"supplier request {request_index} advertisement prompt string must occur exactly once, observed {total}"
                )
            body["messages"] = replaced_messages
            messages = replaced_messages
            counts.add("advertisement_prompt_removal")
        output.append(body)
    return output


def _project_supplier_trace(trace: Mapping[str, Any], requests: list[Mapping[str, Any]]) -> tuple[dict[str, Any], _RuleCounts]:
    if not isinstance(trace, Mapping) or trace.get("role") != "supplier":
        raise ValueError("supplier trace must identify role=supplier")
    counts = _RuleCounts()
    runtime = _supplier_runtime_inputs(trace)
    return _project(trace, _apply_supplier_advertisement(requests, counts), runtime, counts), counts


def _project_supplier_case(case_dir: str | Path) -> tuple[dict[str, Any], _RuleCounts]:
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
    return _project_supplier_trace(trace, requests)


def project_supplier_case(case_dir: str | Path) -> dict[str, Any]:
    """Project one captured supplier case into the canonical episode."""
    projected, _ = _project_supplier_case(case_dir)
    return projected


def _project_bb_trace(trace: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], _RuleCounts]:
    if isinstance(trace, (str, Path)):
        trace = _load_json(Path(trace))
    if not isinstance(trace, Mapping):
        raise TypeError("BB trace must be an object")
    requests = trace.get("requests", [])
    if not isinstance(requests, list):
        raise ValueError("BB trace requests must be an ordered list")
    if "messages" not in trace and "events" not in trace:
        raise ValueError("BB trace must provide messages or events")
    runtime = _bb_runtime_inputs(trace)
    counts = _RuleCounts()
    return _project(trace, [item for item in requests if isinstance(item, Mapping)], runtime, counts), counts


def project_bb_trace(trace: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Project a BB replay trace; reject incomplete or unauthorized shapes."""
    projected, _ = _project_bb_trace(trace)
    return projected


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
            expected, expected_counts = _project_supplier_case(capture_case or capture["path"])
        elif isinstance(capture, Mapping) and capture.get("role") == "supplier":
            expected, expected_counts = _project_supplier_trace(capture, [item for item in capture.get("requests", []) if isinstance(item, Mapping)])
        else:
            raise ValueError("capture must provide a supplier case directory or supplier trace")
        if isinstance(replay, Mapping) and "trace" in replay:
            observed, observed_counts = _project_bb_trace(replay["trace"])
        elif isinstance(replay, Mapping) and "path" in replay:
            observed, observed_counts = _project_bb_trace(replay["path"])
        else:
            observed, observed_counts = _project_bb_trace(replay)
        difference = _first_difference(expected, observed)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "comparator_id": COMPARATOR_ID,
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "passed": difference is None,
            "normalizations": expected_counts.report("supplier") + observed_counts.report("bb"),
            "assertions": [{"assertion_id": "canonical_episode", "status": "passed" if difference is None else "failed", "expected": expected, "observed": observed, "detail": "exact canonical episode match" if difference is None else difference}],
        }

    compare = __call__


def compare(inp: Mapping[str, Any]) -> dict[str, Any]:
    return PiCodingAgent0731Comparator()(inp)


__all__ = ["CONFIG_ID", "COMPARATOR_ID", "LANE_ID", "PiCodingAgent0731Comparator", "compare", "project_bb_trace", "project_supplier_case"]
