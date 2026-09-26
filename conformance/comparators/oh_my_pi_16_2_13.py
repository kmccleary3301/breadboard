"""Deterministic comparator for Oh My Pi 16.2.13 upstream captures and BreadBoard replay traces.

Parity target: the r2 ``declared__*`` capture packet (tests/e4_parity/fixtures/omp_16_2_13_supplier_cases,
bound to packet sha256 cf93d5ac...4ec9 by its manifest.json).

Typed normalizations (each application is reported with its JSON paths):

* ``workspace_root``: the declared workspace root becomes ``<WORKSPACE>``;
* ``home_root``: the declared home root becomes ``<HOME>``;
* ``package_dir_documentation_path``: ``<package_dir>/{README.md,docs,examples}`` become ``<OMP_PACKAGE_DIR>/...``;
* ``current_date_reminder``: the ``Today is <date>, and the current working directory is '<cwd>'.`` line of the
  system prompt, where ``<date>`` is the declared date (YYYY-MM-DD), becomes ``Today is <CURRENT_DATE>, and the current working directory is '<WORKSPACE>'.``;
* ``workstation_os_release``: the release token in the system message line ``- OS: <platform> <release>`` becomes ``<OMP_OS_RELEASE>``;
* ``bash_wall_time``: any ``Wall time: <time> seconds`` line in tool output becomes ``Wall time: <WALL_TIME> seconds``;
* ``message_timestamp``: the ``timestamp`` member of each AgentMessage (pinned ``Date.now()``) is removed;
* ``request_limit_cause``: a BB ``RequestLimitExceeded`` terminal that satisfies the declared request cap maps to
  ``{"cause": "request_limit"}`` (only together with the ``bounded_request_cap`` divergence).

Upstream runtime constants come from the capture kit (kit-omp16213-r2 binds ``/testbed`` as the workspace,
``/capture/home`` as HOME, and ``/opt/omp/node_modules/@oh-my-pi/pi-coding-agent`` as the package dir).
BB values come only from ``trace.runtime_inputs``.

Named divergences are REPORTED as divergence records and never normalized away.

Verdicts: ``exact`` (no normalization, no divergence), ``normalized`` (typed normalizations only),
``named_divergence`` (declared divergences, no findings), ``fail`` (at least one finding).
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import posixpath
import re
import signal
from typing import Any, Literal, Mapping

COMPARATOR_ID = "oh_my_pi_16_2_13_trace_v1"
LANE_ID = "oh_my_pi_16_2_13_replay"
CONFIG_ID = "oh_my_pi_16_2_13_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
CANONICAL_SCHEMA_VERSION = "bb.e4.omp-16-2-13-canonical-episode.v1"
BB_TRACE_SCHEMA_VERSION = "bb.e4.omp-replay-trace.v1"
BB_TRACE_KEYS = frozenset({
    "schema_version",
    "role",
    "profile",
    "version",
    "case_id",
    "request_count",
    "stream_fn_issued",
    "messages",
    "effects",
    "termination",
    "requests",
    "runtime_inputs",
})
BB_RUNTIME_INPUT_NAMES = ("cwd", "home", "package_dir", "current_date")
UPSTREAM_TRACE_SCHEMA_VERSION = "bb.e4.omp16213-capture-trace.v2"
UPSTREAM_CONFIG_MODES = frozenset({"declared", "declared_writable"})
FIXTURE_MANIFEST_SCHEMA_VERSION = "bb.e4.omp16213-declared-fixture-manifest.v1"
EFFECT_CONTENT_UTF8_MAX_BYTES = 64 * 1024

UPSTREAM_WORKSPACE_ROOT = "/testbed"
UPSTREAM_HOME_ROOT = "/capture/home"
UPSTREAM_PACKAGE_DIR = "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent"
TARGET_NATIVE_CONFIG = ("config", "e4_targets", "oh_my_pi", "16.2.13-r2", "native-config.json")

SDK_RETRY_COUNT_HEADER = "x-stainless-retry-count"
SDK_RETRYABLE_STATUS = frozenset({408, 409, 429})
SDK_RETRYABLE_MIN_STATUS = 500

MODELED_FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "function_call"})

BB_KIND_BY_STOP = {
    "stop": frozenset({"Submitted"}),
    "length": frozenset({"length"}),
    "error": frozenset({"error", "RequestLimitExceeded"}),
    "aborted": frozenset({"aborted"}),
    "toolUse": frozenset({"running"}),
}
UNMODELED_FINISH_REASON_FAILURE_CODE = "native_unmodeled_finish_reason"
CANCEL_EXIT_CODE = 130
CANCEL_FAILURE_CODE = "CancelledError"
OMP_16_2_13_CONSUMER_ID = "breadboard.oh-my-pi.v16.2.13"
LEDGER_PHASES = frozenset({"initial", "assistant", "observation_batch"})
# Pinned main.ts:927-929 maps --no-rules to options.rules = []; the target config keeps rule discovery,
# whose always-apply rules render as the system-prompt.md:36-42 generic-rules block.
UPSTREAM_NO_RULES_FLAG = "--no-rules"
GENERIC_RULES_BLOCK = re.compile(r"\n<generic-rules>\n.*?\n</generic-rules>", re.S)

RuleName = Literal[
    "workspace_root",
    "home_root",
    "package_dir_documentation_path",
    "current_date_reminder",
    "workstation_os_release",
    "message_timestamp",
    "bash_wall_time",
    "request_limit_cause",
]
NORMALIZATIONS: tuple[RuleName, ...] = (
    "workspace_root",
    "home_root",
    "package_dir_documentation_path",
    "current_date_reminder",
    "workstation_os_release",
    "message_timestamp",
    "bash_wall_time",
    "request_limit_cause",
)

DivergenceName = Literal[
    "sdk_transport_headers",
    "advertised_image_tool_unexercised",
    "json_member_order",
    "bounded_request_cap",
    "compaction_start_then_exit",
    "upstream_cli_no_rules",
    "external_cancel_signal",
    "workspace_regular_files_only",
    "sdk_hidden_transport_retry",
    "truncated_stream_rejected",
    "unmodeled_finish_reason",
    "non_http_transport_failure",
]
DIVERGENCES: tuple[DivergenceName, ...] = (
    "sdk_transport_headers",
    "advertised_image_tool_unexercised",
    "json_member_order",
    "bounded_request_cap",
    "compaction_start_then_exit",
    "upstream_cli_no_rules",
    "external_cancel_signal",
    "workspace_regular_files_only",
    "sdk_hidden_transport_retry",
    "truncated_stream_rejected",
    "unmodeled_finish_reason",
    "non_http_transport_failure",
)

UNCOMPARED = (
    "effects.*.mode: the BB workspace effect snapshot records exists/bytes/sha256/content_utf8 only",
    "upstream JSON event stream beyond message_end/agent_end/post-agent_end events: the BB replay trace has no event stream",
)
CANONICAL_FIELDS = ("requests", "request_count", "tool_calls", "observations", "messages", "effects", "termination")

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_LINE = re.compile(r"(?m)^Today is (?P<date>\d{4}-\d{2}-\d{2}), and the current working directory is '(?P<cwd>[^']*)'\.$")
_DATE_TIME_LINE = re.compile(r"(?m)^Current date and time: (.*)$")
_WORKSTATION_LINE = re.compile(r"(?m)^- (?P<label>OS|Distro|Kernel|Arch|CPU|Terminal): (?P<value>[^\n]+)$")
_BASH_WALL_TIME_LINE = re.compile(r"(?m)^Wall time: \d+\.\d{2} seconds$")


class OhMyPi16213ComparatorError(ValueError):
    """Typed fail-closed comparator input error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class _Runtime:
    workspace_root: str
    home_root: str | None
    package_dir: str | None
    current_date: str
    current_date_time: str | None = None

@dataclass
class _Normalizer:
    runtime: _Runtime
    counts: dict[RuleName, int] = field(default_factory=lambda: {rule: 0 for rule in NORMALIZATIONS})
    paths: dict[RuleName, list[str]] = field(default_factory=lambda: {rule: [] for rule in NORMALIZATIONS})
    findings: list[dict[str, Any]] = field(default_factory=list)

    def add(self, rule: RuleName, path: str, count: int = 1) -> None:
        self.counts[rule] += count
        self.paths[rule].append(path)

    def report(self, side: str) -> list[dict[str, Any]]:
        return [
            {"side": side, "rule": rule, "count": self.counts[rule], "paths": list(self.paths[rule])}
            for rule in NORMALIZATIONS
        ]

    def _token(self, value: str, source: str, replacement: str, rule: RuleName, path: str) -> str:
        pattern = re.compile(rf"(?<![\w.\-/]){re.escape(source)}(?=/|$|\s|[),.;:'\"])")
        value, count = pattern.subn(replacement, value)
        if count:
            self.add(rule, path, count)
        return value

    def string(self, value: str, path: str, *, system: bool) -> str:
        shortened_cwd = (
            ("~/" + posixpath.relpath(self.runtime.workspace_root, self.runtime.home_root))
            if (self.runtime.home_root is not None and self.runtime.workspace_root.startswith(self.runtime.home_root + "/"))
            else None
        )
        if system:
            def replace_ws(match: re.Match[str]) -> str:
                self.add("workstation_os_release", path)
                label = match.group("label")
                return f"- {label}: <OMP_{label.upper()}>"

            value = _WORKSTATION_LINE.sub(replace_ws, value)

            def replace_date(match: re.Match[str]) -> str:
                m_cwd = match.group("cwd")
                if match.group("date") == self.runtime.current_date and (
                    m_cwd == self.runtime.workspace_root or (shortened_cwd and m_cwd == shortened_cwd)
                ):
                    self.add("current_date_reminder", path)
                    self.add("workspace_root", path)
                    return "Today is <CURRENT_DATE>, and the current working directory is '<WORKSPACE>'."
                return match.group(0)

            value = _DATE_LINE.sub(replace_date, value)
            if self.runtime.current_date_time:
                def replace_dt(match: re.Match[str]) -> str:
                    if match.group(1) == self.runtime.current_date_time:
                        self.add("current_date_reminder", path)
                        return "Current date and time: <CURRENT_DATE_TIME>"
                    return match.group(0)

                value = _DATE_TIME_LINE.sub(replace_dt, value)

        def replace_wall_time(match: re.Match[str]) -> str:
            self.add("bash_wall_time", path)
            return "Wall time: <WALL_TIME> seconds"

        value = _BASH_WALL_TIME_LINE.sub(replace_wall_time, value)

        if self.runtime.package_dir is not None:
            for suffix in ("README.md", "docs", "examples"):
                value = self._token(
                    value, f"{self.runtime.package_dir}/{suffix}", f"<OMP_PACKAGE_DIR>/{suffix}",
                    "package_dir_documentation_path", path,
                )
        root_entries: list[tuple[str, str, RuleName]] = [
            (self.runtime.workspace_root, "<WORKSPACE>", "workspace_root"),
        ]
        if self.runtime.home_root is not None:
            root_entries.append((self.runtime.home_root, "<HOME>", "home_root"))
        if shortened_cwd:
            root_entries.append((shortened_cwd, "<WORKSPACE>", "workspace_root"))
        roots = sorted(
            root_entries,
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for root, replacement, rule in roots:
            value = self._token(value, root, replacement, rule, path)  # type: ignore[arg-type]
        return value

    def value(self, value: Any, path: str, *, system: bool = False) -> Any:
        if isinstance(value, str):
            return self.string(value, path, system=system)
        if isinstance(value, list):
            return [self.value(item, f"{path}[{index}]", system=system) for index, item in enumerate(value)]
        if isinstance(value, Mapping):
            if value.get("role") == "user":
                return deepcopy(dict(value))
            is_system = system or value.get("role") == "system"
            return {key: self.value(item, f"{path}.{key}", system=is_system) for key, item in value.items()}
        return value

    def messages(self, messages: list[Any], path: str) -> list[Any]:
        output: list[Any] = []
        for index, message in enumerate(messages):
            item_path = f"{path}[{index}]"
            if not isinstance(message, Mapping):
                self.findings.append({"field": "messages", "detail": f"{item_path}: message is not an object"})
                output.append(message)
                continue
            stripped = dict(message)
            if "timestamp" in stripped and type(stripped["timestamp"]) is int:
                del stripped["timestamp"]
                self.add("message_timestamp", f"{item_path}.timestamp")
            else:
                self.findings.append({"field": "messages", "detail": f"{item_path}: missing integer timestamp"})
            for host_key in ("duration", "ttft", "responseId"):
                if host_key in stripped:
                    del stripped[host_key]
            if "role" in stripped and stripped["role"] == "toolResult" and "details" in stripped and isinstance(stripped["details"], Mapping):
                details = dict(stripped["details"])
                if "wallTimeMs" in details:
                    del details["wallTimeMs"]
                    self.add("bash_wall_time", f"{item_path}.details.wallTimeMs")
                stripped["details"] = details
            output.append(self.value(stripped, item_path))
        return output


def _fail(code: str, message: str) -> OhMyPi16213ComparatorError:
    return OhMyPi16213ComparatorError(code, message)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json_file(path: Path, code: str) -> Any:
    if not path.is_file():
        raise _fail(code, f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require(mapping: Any, key: str, kind: type | tuple[type, ...], code: str, where: str) -> Any:
    if not isinstance(mapping, Mapping) or key not in mapping:
        raise _fail(code, f"{where} lacks {key}")
    value = mapping[key]
    if not isinstance(value, kind) or (kind is int and type(value) is bool):
        raise _fail(code, f"{where}.{key} has the wrong type")
    return value


def _validate_date(value: Any, code: str) -> str:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise _fail(code, f"current date {value!r} is not YYYY-MM-DD")
    return value


def _member_order(value: Any, path: str = "$") -> dict[str, list[str]]:
    orders: dict[str, list[str]] = {}
    if isinstance(value, Mapping):
        orders[path] = list(value.keys())
        for key, item in value.items():
            orders.update(_member_order(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            orders.update(_member_order(item, f"{path}[{index}]"))
    return orders


def _header(headers: Any, name: str, code: str) -> str | None:
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if key.lower() == name.lower() and isinstance(value, str):
                return value
    elif isinstance(headers, list):
        for item in headers:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                if str(item[0]).lower() == name.lower() and isinstance(item[1], str):
                    return item[1]
    return None


def _assistant_indices(messages: list[Any]) -> list[int]:
    return [index for index, message in enumerate(messages) if isinstance(message, Mapping) and message.get("role") == "assistant"]


def _tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping) and message.get("role") == "assistant" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, Mapping) and block.get("type") == "toolCall":
                    calls.append({key: block[key] for key in ("id", "name", "arguments") if key in block})
    return calls


def _observations(messages: list[Any]) -> list[dict[str, Any]]:
    return [
        {key: message[key] for key in ("toolCallId", "toolName", "content", "isError", "details") if key in message}
        for message in messages
        if isinstance(message, Mapping) and message.get("role") == "toolResult"
    ]


def _termination(messages: list[Any]) -> dict[str, Any]:
    indices = _assistant_indices(messages)
    if not indices:
        return {}
    last = messages[indices[-1]]
    return {key: last[key] for key in ("stopReason", "errorMessage") if key in last}


def _canonical(requests: list[Any], messages: list[Any], effects: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requests": requests,
        "request_count": len(requests),
        "tool_calls": _tool_calls(messages),
        "observations": _observations(messages),
        "messages": messages,
        "effects": dict(effects),
        "termination": _termination(messages),
    }


def _effect_record(data: bytes) -> dict[str, Any]:
    record: dict[str, Any] = {"exists": True, "bytes": len(data), "sha256": _sha(data)}
    if len(data) <= EFFECT_CONTENT_UTF8_MAX_BYTES:
        record["content_utf8"] = data.decode("utf-8", "replace")
    return record


@dataclass
class _Upstream:
    case_id: str
    canonical: dict[str, Any]
    normalizer: _Normalizer
    messages: list[Any]
    groups: list[list[dict[str, Any]]]
    orders: list[dict[str, list[str]]]
    header_names: list[str]
    events_after_agent_end: list[dict[str, Any]]
    agent_end: bool
    exit_code: int
    trace: Mapping[str, Any]
    scenario: Mapping[str, Any]
    events: list[dict[str, Any]]


def _fixture_set(case_dir: Path) -> tuple[Path, Mapping[str, Any]]:
    root = case_dir.parent
    manifest = _json_file(root / "manifest.json", "fixture_manifest_invalid")
    if _require(manifest, "schema_version", str, "fixture_manifest_invalid", "manifest") != FIXTURE_MANIFEST_SCHEMA_VERSION:
        raise _fail("fixture_manifest_invalid", "unexpected fixture manifest schema")
    if case_dir.name not in _require(manifest, "cases", list, "fixture_manifest_invalid", "manifest"):
        raise _fail("fixture_manifest_invalid", f"{case_dir.name} is not a declared case")
    return root, manifest


def _upstream_effects(root: Path, pre: Mapping[str, Any], post: Mapping[str, Any]) -> dict[str, Any]:
    effects: dict[str, Any] = {}
    for path in sorted(set(pre) | set(post)):
        before = pre[path] if path in pre else None
        after = post[path] if path in post else None
        if before == after:
            continue
        if after is None:
            if before["type"] != "file":
                raise _fail("unsupported_upstream_effect", f"{path}: non-file deletion")
            effects[path] = {"exists": False}
            continue
        if after["type"] != "file":
            raise _fail("unsupported_upstream_effect", f"{path}: non-file effect {after['type']}")
        if before is not None and before["type"] == "file" and before["sha256"] == after["sha256"]:
            raise _fail("unsupported_upstream_effect", f"{path}: mode-only change is not observable in BB effects")
        blob = root / "blobs" / after["sha256"].removeprefix("sha256:")
        if not blob.is_file():
            raise _fail("upstream_case_invalid", f"{path}: effect blob missing")
        data = blob.read_bytes()
        if _sha(data) != after["sha256"] or len(data) != after["bytes"]:
            raise _fail("upstream_case_invalid", f"{path}: effect blob does not match manifest-post")
        effects[path] = _effect_record(data)
    return effects


def _strip_tool_call_intent(messages: list[Any]) -> list[Any]:
    cleaned: list[Any] = []
    for msg in messages:
        if isinstance(msg, Mapping) and msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if isinstance(block, Mapping) and block.get("type") == "toolCall" and "intent" in block:
                    new_block = dict(block)
                    del new_block["intent"]
                    new_content.append(new_block)
                else:
                    new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)
    return cleaned


def _load_upstream(case_dir: str | Path) -> _Upstream:
    case = Path(case_dir).resolve()
    root, manifest = _fixture_set(case)
    capture = case / "capture"
    code = "upstream_case_invalid"
    trace = _json_file(capture / "trace.json", code)
    if _require(trace, "schema_version", str, code, "trace") != UPSTREAM_TRACE_SCHEMA_VERSION:
        raise _fail(code, "unexpected upstream trace schema")
    if _require(trace, "config_mode", str, code, "trace") not in UPSTREAM_CONFIG_MODES:
        raise _fail(code, "only declared captures are the parity target")
    case_id = _require(trace, "case_id", str, code, "trace")
    files = _require(trace, "files", Mapping, code, "trace")
    for name in (
        "http-transcript.jsonl", "omp-events.jsonl", "workspace-manifest-pre.json",
        "workspace-manifest-post.json", "model.patch", "scenario.json",
    ):
        if name in files:
            if _require(files, name, str, code, f"trace.files.{name}") != _sha((capture / name).read_bytes()):
                raise _fail(code, f"{name} does not match trace.files.{name}")
    exit_code = _require(trace, "exit_code", int, code, "trace")
    scenario = _json_file(capture / "scenario.json", code)
    if _require(scenario, "case_id", str, code, "scenario") != case_id:
        raise _fail(code, "scenario case_id differs")

    rows: list[dict[str, Any]] = []
    transcript_path = capture / "http-transcript.jsonl"
    if transcript_path.is_file():
        for index, line in enumerate(transcript_path.read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            raw = base64.b64decode(_require(row, "raw_body_base64", str, code, "transcript row"), validate=True)
            if _sha(raw) != _require(row, "raw_body_sha256", str, code, "transcript row"):
                raise _fail(code, f"transcript row {index} raw body digest mismatch")
            body = json.loads(raw)
            if body != _require(row, "body", Mapping, code, "transcript row"):
                raise _fail(code, f"transcript row {index} body differs from raw body")
            row["_body"] = body
            retry_raw = _header(_require(row, "headers", (Mapping, list), code, "transcript row"), SDK_RETRY_COUNT_HEADER, code)
            row["_retry_count"] = int(retry_raw) if retry_raw is not None else 0
            rows.append(row)

    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if row["served"] == "request_cap_refused":
            continue
        if row["_retry_count"] == 0:
            groups.append([row])
            continue
        if not groups:
            raise _fail(code, "SDK retry without an initial attempt")
        previous = groups[-1][-1]
        status = previous["status"] if "status" in previous else None
        if (
            row["_retry_count"] != previous["_retry_count"] + 1
            or row["_body"] != previous["_body"]
            or previous["served"] != "http_error"
            or type(status) is not int
            or not (status in SDK_RETRYABLE_STATUS or status >= SDK_RETRYABLE_MIN_STATUS)
        ):
            raise _fail(code, f"transcript row is not an SDK retry of previous")
        groups[-1].append(row)

    events_path = capture / "omp-events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        if events_path.is_file() else []
    )
    message_ends = [event["message"] for event in events if event["type"] == "message_end"]
    agent_ends = [index for index, event in enumerate(events) if event["type"] == "agent_end"]
    if len(agent_ends) > 1:
        raise _fail(code, "more than one agent_end")
    if agent_ends:
        messages_raw = _strip_tool_call_intent(events[agent_ends[0]]["messages"])
        if messages_raw != message_ends:
            raise _fail(code, "agent_end.messages differs from the message_end sequence")
        after = events[agent_ends[0] + 1:]
    else:
        messages_raw = message_ends
        after = []

    date_val = "2026-09-26"
    if groups:
        first_request = groups[0][0]["_body"]
        system_msgs = [m for m in first_request["messages"] if isinstance(m, Mapping) and m.get("role") == "system"]
        if system_msgs:
            m = _DATE_LINE.search(system_msgs[0]["content"])
            if m:
                date_val = _validate_date(m.group("date"), code)

    runtime = _Runtime(UPSTREAM_WORKSPACE_ROOT, UPSTREAM_HOME_ROOT, UPSTREAM_PACKAGE_DIR, date_val)
    normalizer = _Normalizer(runtime)
    messages = normalizer.messages(list(messages_raw), "$.messages")
    if normalizer.findings:
        raise _fail(code, f"upstream messages malformed: {normalizer.findings}")

    requests = [normalizer.value(group[0]["_body"], f"$.requests[{index}]") for index, group in enumerate(groups)]
    effects = _upstream_effects(
        root,
        _json_file(capture / "workspace-manifest-pre.json", code),
        _json_file(capture / "workspace-manifest-post.json", code),
    )

    header_names_set = set()
    for row in rows:
        headers = row["headers"]
        if isinstance(headers, Mapping):
            header_names_set.update(headers.keys())
        elif isinstance(headers, list):
            for item in headers:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    header_names_set.add(str(item[0]))

    return _Upstream(
        case_id=case_id,
        canonical=_canonical(requests, messages, effects),
        normalizer=normalizer,
        messages=messages,
        groups=groups,
        orders=[_member_order(group[0]["_body"]) for group in groups],
        header_names=sorted(header_names_set),
        events_after_agent_end=after,
        agent_end=bool(agent_ends),
        exit_code=exit_code,
        trace=trace,
        scenario=scenario,
        events=events,
    )


def project_upstream_case(case_dir: str | Path) -> dict[str, Any]:
    """Project one declared upstream case into the canonical episode."""
    upstream = _load_upstream(case_dir)
    return {"schema_version": CANONICAL_SCHEMA_VERSION, "case_id": upstream.case_id, **upstream.canonical}


@dataclass
class _Replay:
    trace: Mapping[str, Any] | None
    canonical: dict[str, Any]
    normalizer: _Normalizer
    messages: list[Any]
    orders: list[dict[str, list[str]]]
    process: Mapping[str, Any] | None
    ledger: Mapping[str, Any] | None = None
    unsubmitted_observation_by_call_id: dict[str, Any] = field(default_factory=dict)
    last_commit_state: Mapping[str, Any] | None = None


def _load_bb(trace: Mapping[str, Any] | str | Path, process: Mapping[str, Any] | None) -> _Replay:
    code = "bb_trace_invalid"
    if isinstance(trace, (str, Path)):
        trace = _json_file(Path(trace), code)
    if not isinstance(trace, Mapping):
        raise _fail(code, "BB trace must be an object")
    if set(trace) != BB_TRACE_KEYS:
        raise _fail(code, f"BB trace keys differ: missing {sorted(BB_TRACE_KEYS - set(trace))}, extra {sorted(set(trace) - BB_TRACE_KEYS)}")
    if trace["schema_version"] != BB_TRACE_SCHEMA_VERSION:
        raise _fail(code, f"BB trace schema_version must be {BB_TRACE_SCHEMA_VERSION!r}")
    if trace["role"] != "replay":
        raise _fail(code, "BB trace role must be 'replay'")
    if trace["profile"] not in {"omp", "oh_my_pi"}:
        raise _fail(code, "BB trace profile must be 'omp' or 'oh_my_pi'")
    if trace["version"] != "16.2.13":
        raise _fail(code, "BB trace version must be '16.2.13'")

    runtime_inputs = trace["runtime_inputs"]
    if not isinstance(runtime_inputs, Mapping) or not set(BB_RUNTIME_INPUT_NAMES) <= set(runtime_inputs):
        raise _fail("runtime_inputs_invalid", f"BB runtime_inputs must declare {', '.join(BB_RUNTIME_INPUT_NAMES)}")
    for name in ("cwd", "home", "package_dir"):
        if type(runtime_inputs[name]) is not str or not runtime_inputs[name].startswith("/"):
            raise _fail("runtime_inputs_invalid", f"BB runtime_inputs.{name} must be an absolute path")

    current_date = _validate_date(runtime_inputs["current_date"], "runtime_inputs_invalid")
    current_date_time = runtime_inputs["current_date_time"] if "current_date_time" in runtime_inputs else None

    runtime = _Runtime(
        runtime_inputs["cwd"],
        runtime_inputs["home"],
        runtime_inputs["package_dir"],
        current_date,
        current_date_time,
    )
    for key in ("requests", "messages"):
        if not isinstance(trace[key], list):
            raise _fail(code, f"BB trace {key} must be a list")
    if any(not isinstance(item, Mapping) for item in trace["requests"]):
        raise _fail(code, "BB trace requests must be objects")
    effects = trace["effects"]
    if not isinstance(effects, Mapping):
        raise _fail(code, "BB trace effects must be an object")
    for path, value in effects.items():
        if not isinstance(value, Mapping) or "exists" not in value or type(value["exists"]) is not bool:
            raise _fail(code, f"BB effect {path!r} must declare exists")
        allowed = {"exists", "bytes", "sha256", "content_utf8"} if value["exists"] else {"exists"}
        if not set(value) <= allowed:
            raise _fail(code, f"BB effect {path!r} has undeclared members")
    termination = trace["termination"]
    if not isinstance(termination, Mapping) or set(termination) != {"kind", "native_stop_reason"}:
        raise _fail(code, "BB termination must be {kind, native_stop_reason}")

    normalizer = _Normalizer(runtime)
    messages = normalizer.messages(list(trace["messages"]), "$.messages")
    requests = [normalizer.value(request, f"$.requests[{index}]") for index, request in enumerate(trace["requests"])]
    canonical = _canonical(
        requests,
        messages,
        {path: value for path, value in effects.items() if path != ".git" and not path.startswith(".git/")},
    )
    if process is not None and not isinstance(process, Mapping):
        raise _fail(code, "replay.process must be an object")
    return _Replay(trace, canonical, normalizer, messages, [_member_order(request) for request in trace["requests"]], process)


def _load_bb_ledger(
    ledger: Mapping[str, Any] | str | Path,
    ledger_digest: str,
    transcript: list[Mapping[str, Any]] | str | Path,
    process: Mapping[str, Any] | None,
) -> _Replay:
    code = "bb_ledger_invalid"
    if not isinstance(ledger_digest, str) or not re.match(r"^sha256:[0-9a-f]{64}$", ledger_digest):
        raise _fail("comparator_input_invalid", "ledger_digest must be a sha256:<64 hex> string")

    if isinstance(ledger, (str, Path)):
        ledger_path = Path(ledger)
        if not ledger_path.is_file():
            raise _fail(code, f"missing {ledger_path}")
        raw_bytes = ledger_path.read_bytes()
        computed_digest = _sha(raw_bytes)
        if computed_digest != ledger_digest:
            raise _fail(code, f"ledger digest mismatch: expected {ledger_digest!r}, got {computed_digest!r}")
        try:
            ledger_obj = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(code, f"ledger JSON parse error: {exc}") from exc
    elif isinstance(ledger, Mapping):
        ledger_obj = ledger
    else:
        raise _fail("comparator_input_invalid", "ledger must be an object or file path")

    if not isinstance(ledger_obj, Mapping):
        raise _fail(code, "BB ledger must be an object")
    if "schema_version" not in ledger_obj or ledger_obj["schema_version"] != "bb.rl.runner-event-ledger.v2":
        raise _fail(code, "unexpected BB ledger schema_version")

    if "event_count" not in ledger_obj or type(ledger_obj["event_count"]) is not int:
        raise _fail(code, "ledger lacks integer event_count")
    event_count = ledger_obj["event_count"]
    if "events" not in ledger_obj or not isinstance(ledger_obj["events"], list):
        raise _fail(code, "ledger lacks list events")
    events = ledger_obj["events"]
    if len(events) != event_count:
        raise _fail(code, f"ledger event_count {event_count} != len(events) {len(events)}")
    if "first_sequence" not in ledger_obj or type(ledger_obj["first_sequence"]) is not int:
        raise _fail(code, "ledger lacks integer first_sequence")
    first_sequence = ledger_obj["first_sequence"]
    if "last_sequence" not in ledger_obj or type(ledger_obj["last_sequence"]) is not int:
        raise _fail(code, "ledger lacks integer last_sequence")
    last_sequence = ledger_obj["last_sequence"]
    if first_sequence != 0:
        raise _fail(code, f"ledger first_sequence {first_sequence} != 0")
    if last_sequence - first_sequence + 1 != event_count:
        raise _fail(code, "ledger sequence range does not match event_count")

    for index, row in enumerate(events):
        if not isinstance(row, Mapping):
            raise _fail(code, f"ledger row {index} must be an object")
        if "sequence" not in row or type(row["sequence"]) is not int:
            raise _fail(code, f"ledger row {index} lacks integer sequence")
        seq = row["sequence"]
        expected_seq = first_sequence + index
        if seq != expected_seq:
            raise _fail(code, f"non-contiguous ledger sequence at index {index}: expected {expected_seq}, got {seq}")

    bb_messages_raw: list[dict[str, Any]] = []
    source_commit_rows: list[Mapping[str, Any]] = []
    unsubmitted_obs_by_call_id: dict[str, Any] = {}

    for row in events:
        if "phase" in row:
            phase = row["phase"]
            if phase in LEDGER_PHASES and "source_id" in row and row["source_id"] == OMP_16_2_13_CONSUMER_ID:
                if "events" not in row or not isinstance(row["events"], list):
                    raise _fail(code, f"ledger row {row['sequence']} with phase {phase!r} lacks list events")
                bb_messages_raw.extend(row["events"])
                if "state" not in row or not isinstance(row["state"], Mapping):
                    raise _fail(code, f"ledger row {row['sequence']} with phase {phase!r} lacks state object")
                source_commit_rows.append(row)
        if "call_id" in row and "submitted" in row and row["submitted"] is False and "observation" in row:
            unsubmitted_obs_by_call_id[row["call_id"]] = row["observation"]

    if not bb_messages_raw:
        raise _fail(code, "BB ledger contains no messages")
    if not source_commit_rows:
        raise _fail(code, "BB ledger contains no consumer commit state rows")

    if isinstance(transcript, (str, Path)):
        trans_path = Path(transcript)
        if not trans_path.is_file():
            raise _fail("bb_transcript_invalid", f"missing {trans_path}")
        transcript_rows = []
        for line_number, line in enumerate(trans_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                transcript_rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise _fail("bb_transcript_invalid", f"transcript line {line_number} is not JSON: {exc}") from exc
    elif isinstance(transcript, list):
        transcript_rows = transcript
    else:
        raise _fail("comparator_input_invalid", "transcript must be a list or file path")

    bb_requests_raw: list[dict[str, Any]] = []
    for index, row in enumerate(transcript_rows):
        if not isinstance(row, Mapping):
            raise _fail("bb_transcript_invalid", f"transcript row {index} must be an object")
        if "body" not in row or not isinstance(row["body"], Mapping):
            raise _fail("bb_transcript_invalid", f"transcript row {index} lacks body object")
        bb_requests_raw.append(row["body"])

    if not bb_requests_raw:
        raise _fail("bb_transcript_invalid", "transcript contains no requests")

    first_request = bb_requests_raw[0]
    if "messages" not in first_request or not isinstance(first_request["messages"], list):
        raise _fail("bb_requests_invalid", "first request lacks list messages")
    system_messages = [
        m for m in first_request["messages"]
        if isinstance(m, Mapping) and "role" in m and m["role"] == "system"
    ]
    if len(system_messages) != 1:
        raise _fail("bb_requests_invalid", f"first request must carry exactly one system message, found {len(system_messages)}")
    sys_msg = system_messages[0]
    if "content" not in sys_msg or not isinstance(sys_msg["content"], str):
        raise _fail("bb_requests_invalid", "first request system message must have string content")
    sys_content = sys_msg["content"]

    date_matches = _DATE_LINE.findall(sys_content)
    if len(date_matches) != 1:
        raise _fail("runtime_inputs_invalid", f"system message must carry exactly one Today is ... date line, found {len(date_matches)}")
    date_str, cwd = date_matches[0]
    current_date = _validate_date(date_str, "runtime_inputs_invalid")
    if not cwd.startswith("/"):
        raise _fail("runtime_inputs_invalid", "parsed cwd must be an absolute path")

    runtime = _Runtime(
        workspace_root=cwd,
        home_root=None,
        package_dir=None,
        current_date=current_date,
        current_date_time=None,
    )
    normalizer = _Normalizer(runtime)
    messages = normalizer.messages(list(bb_messages_raw), "$.messages")
    requests = [normalizer.value(request, f"$.requests[{index}]") for index, request in enumerate(bb_requests_raw)]
    canonical = _canonical(requests, messages, {})

    if process is not None and not isinstance(process, Mapping):
        raise _fail("bb_process_invalid", "replay.process must be an object")

    last_commit_state = source_commit_rows[-1]["state"]

    return _Replay(
        trace=None,
        canonical=canonical,
        normalizer=normalizer,
        messages=messages,
        orders=[_member_order(req) for req in bb_requests_raw],
        process=process,
        ledger=ledger_obj,
        unsubmitted_observation_by_call_id=unsubmitted_obs_by_call_id,
        last_commit_state=last_commit_state,
    )


def project_bb_trace(trace: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Project a BB replay trace into the canonical episode; reject undeclared shapes."""
    replay = _load_bb(trace, None)
    assert replay.trace is not None
    return {"schema_version": CANONICAL_SCHEMA_VERSION, "case_id": replay.trace["case_id"], **replay.canonical}


def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    if type(expected) is not type(observed) and not (
        isinstance(expected, (int, float)) and isinstance(observed, (int, float))
        and type(expected) is not bool and type(observed) is not bool
    ):
        return f"{path}: expected {expected!r}, observed {observed!r}"
    if isinstance(expected, Mapping):
        if set(expected) != set(observed):
            return f"{path}: keys differ (missing {sorted(set(expected) - set(observed))}, extra {sorted(set(observed) - set(expected))})"
        for key in expected:
            difference = _first_difference(expected[key], observed[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        for index, (left, right) in enumerate(zip(expected, observed)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
        if len(expected) != len(observed):
            return f"{path}: lengths differ ({len(expected)} != {len(observed)})"
    elif expected != observed:
        return f"{path}: expected {expected!r}, observed {observed!r}"
    return None


def _rows(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in ("index", "served", "status", "finish_reason") if key in row} | {"retry_count": row["_retry_count"]}
        for row in group
    ]


def _scope_prefix(expected: dict[str, Any], upstream: _Upstream, logical_index: int, record: dict[str, Any]) -> list[Any]:
    indices = _assistant_indices(upstream.messages)
    cut = indices[logical_index] if logical_index < len(indices) else len(upstream.messages)
    later_calls = _tool_calls(upstream.messages[cut:])
    record["comparison_scope"] = {"upstream_logical_requests": logical_index + 1, "upstream_messages_before": cut}
    if later_calls:
        record["comparison_scope"]["effects_uncompared_reason"] = (
            f"upstream executed {len(later_calls)} tool call(s) after the divergence point"
        )
    return upstream.messages[:cut]


def _set_expected(expected: dict[str, Any], upstream: _Upstream, logical_index: int, messages: list[Any], record: dict[str, Any]) -> None:
    expected.update(_canonical(expected["requests"][: logical_index + 1], messages, expected["effects"]))
    if "effects_uncompared_reason" in record["comparison_scope"]:
        expected["effects"] = None


def _typed_failure(process: Mapping[str, Any] | None, expected_code: str | None) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    if process is None:
        return {}, ["replay.process (installed run terminal) is required for this divergence"]
    terminal = process["terminal"] if "terminal" in process else None
    status = terminal["status"] if isinstance(terminal, Mapping) and "status" in terminal else None
    primary = terminal["primary_failure"] if isinstance(terminal, Mapping) and "primary_failure" in terminal else None
    failure_code = primary["code"] if isinstance(primary, Mapping) and "code" in primary else None
    exit_code = process["exit_code"] if "exit_code" in process else None
    if status != "failed":
        problems.append(f"BB terminal status {status!r} is not 'failed'")
    if type(failure_code) is not str or (expected_code is not None and failure_code != expected_code):
        problems.append(f"BB primary_failure.code {failure_code!r} does not match {expected_code!r}")
    return {"terminal_status": status, "primary_failure_code": failure_code, "exit_code": exit_code}, problems


def _absent_non_regular_entries(case_dir: Path, manifest: Mapping[str, Any]) -> list[str]:
    pre = _json_file(case_dir / "capture" / "workspace-manifest-pre.json", "upstream_case_invalid")
    replay_workspace = _require(manifest, "replay_workspace", Mapping, "fixture_manifest_invalid", "manifest")
    absent = list(_require(replay_workspace, "extra_directories", list, "fixture_manifest_invalid", "replay_workspace"))
    files = [path for path, entry in pre.items() if entry["type"] == "file"]
    for path, entry in pre.items():
        if entry["type"] == "symlink" or (
            entry["type"] == "directory" and not any(item.startswith(path + "/") for item in files)
        ):
            absent.append(path)
    return sorted(absent)


def _files_only_explained(text: str, observed: Any, root: str, absent: list[str]) -> bool:
    base = posixpath.normpath(root)
    names = {
        posixpath.relpath(entry, base) for entry in absent
        if base == "." or entry.startswith(base + "/")
    }
    lines = text.split("\n")
    kept = [line for line in lines if (line[:-1] if line.endswith("/") else line) not in names]
    return len(kept) < len(lines) and observed == "\n".join(kept)


def _workspace_regular_files_only(
    case_dir: Path, manifest: Mapping[str, Any], expected: dict[str, Any], observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    absent = _absent_non_regular_entries(case_dir, manifest)
    if not absent:
        return None
    calls = {call["id"]: call for call in expected["tool_calls"]}
    observed_by_id = {item["toolCallId"]: item for item in observed["observations"]}
    replaced: dict[str, dict[int, str]] = {}
    for item in expected["observations"]:
        other = observed_by_id[item["toolCallId"]] if item["toolCallId"] in observed_by_id else None
        call = calls[item["toolCallId"]] if item["toolCallId"] in calls else None
        if other is None or call is None or len(other["content"]) != len(item["content"]):
            continue
        arguments = call["arguments"] if isinstance(call["arguments"], Mapping) else {}
        root = arguments["path"] if isinstance(arguments.get("path"), str) else "."
        for index, (part, bb_part) in enumerate(zip(item["content"], other["content"])):
            if (
                part["type"] == "text" and bb_part["type"] == "text" and part["text"] != bb_part["text"]
                and _files_only_explained(part["text"], bb_part["text"], root, absent)
            ):
                replaced.setdefault(item["toolCallId"], {})[index] = bb_part["text"]
    if not replaced:
        return None
    for message in expected["messages"]:
        if message["role"] == "toolResult" and message["toolCallId"] in replaced:
            for index, text in replaced[message["toolCallId"]].items():
                message["content"][index]["text"] = text
    for request in expected["requests"]:
        for message in request["messages"]:
            if message["role"] == "tool" and message["tool_call_id"] in replaced:
                for index, text in replaced[message["tool_call_id"]].items():
                    if isinstance(message["content"], str):
                        message["content"] = text
                    else:
                        message["content"][index]["text"] = text
    expected["observations"] = _observations(expected["messages"])
    return {
        "name": "workspace_regular_files_only",
        "absent_entries": absent,
        "affected_tool_call_ids": sorted(replaced),
        "upstream": "golden pre-run tree includes non-regular entries (workspace-manifest-pre.json, replay_workspace.extra_directories)",
        "bb": "production workspace seed and containment admit regular files only",
    }


def _upstream_cli_no_rules(expected: Mapping[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    # Scope only system prompts that equal upstream once exactly one generic-rules block is removed;
    # any other system prompt difference remains a finding.
    rules_blocks: list[str] = []
    for up_request, bb_request in zip(expected["requests"], observed["requests"]):
        up_system = up_request["messages"][0]
        bb_system = bb_request["messages"][0]
        if (
            up_system["role"] != "system" or bb_system["role"] != "system"
            or type(up_system["content"]) is not str or type(bb_system["content"]) is not str
        ):
            continue
        content = bb_system["content"]
        for match in GENERIC_RULES_BLOCK.finditer(content):
            for start in (match.start() - 1, match.start()):
                if start >= 0 and content[start] == "\n" and content[:start] + content[match.end():] == up_system["content"]:
                    bb_system["content"] = up_system["content"]
                    rules_blocks.append(content[start:match.end()])
                    break
            if bb_system["content"] == up_system["content"]:
                break
    return {
        "name": "upstream_cli_no_rules",
        "upstream_flag": UPSTREAM_NO_RULES_FLAG,
        "bb_generic_rules_blocks": sorted(set(rules_blocks)),
        "requests_scoped": len(rules_blocks),
        "upstream": "the capture passed --no-rules, so pinned main.ts sets options.rules = [] and no generic-rules block renders",
        "bb": "the target configuration keeps pinned rule discovery; discovered always-apply rules render in the system prompt",
    }


class OhMyPi16213Comparator:
    comparator_id = COMPARATOR_ID

    def __call__(self, inp: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(inp, Mapping) or set(inp) != {"capture", "replay"}:
            raise _fail("comparator_input_invalid", "input must be {capture, replay}")
        capture, replay = inp["capture"], inp["replay"]
        if not isinstance(capture, Mapping) or set(capture) != {"case_dir"}:
            raise _fail("comparator_input_invalid", "capture must be {case_dir}")
        if not isinstance(replay, Mapping):
            raise _fail("comparator_input_invalid", "replay must be an object")
        upstream = _load_upstream(capture["case_dir"])
        if "ledger" in replay:
            if "request_guard" not in upstream.trace:
                raise _fail("upstream_case_invalid", "upstream trace lacks request_guard")
            request_guard = upstream.trace["request_guard"]
            has_signal = "signal_sent" in request_guard or "signal_cancel_on_tool_start" in upstream.scenario
            cap_reached = "cap_reached" in request_guard and request_guard["cap_reached"]
            if upstream.agent_end or cap_reached or not has_signal:
                raise _fail("comparator_input_invalid", "ledger replay is admitted only for a signal-cancel case without agent_end")
            if "process" not in replay:
                raise _fail("comparator_input_invalid", "ledger replay requires replay.process")
            if not set(replay) <= {"ledger", "ledger_digest", "transcript", "process"}:
                raise _fail("comparator_input_invalid", "ledger replay must be {ledger, ledger_digest, transcript, process}")
            for key in ("ledger", "ledger_digest", "transcript", "process"):
                if key not in replay:
                    raise _fail("comparator_input_invalid", f"ledger replay lacks {key}")
            bb = _load_bb_ledger(replay["ledger"], replay["ledger_digest"], replay["transcript"], replay["process"])
        else:
            if "trace" not in replay or not set(replay) <= {"trace", "process"}:
                raise _fail("comparator_input_invalid", "replay must be {trace, process?}")
            bb = _load_bb(replay["trace"], replay["process"] if "process" in replay else None)
        expected = deepcopy(upstream.canonical)
        observed = deepcopy(bb.canonical)
        divergences: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = list(bb.normalizer.findings)
        expected_counts_extra = 0

        divergences.append({
            "name": "sdk_transport_headers",
            "upstream_request_header_names": upstream.header_names,
            "bb": "the BB replay trace records sent request bodies only; transport headers are not captured",
        })

        has_image_tool = False
        for req in upstream.canonical["requests"]:
            if "tools" in req and isinstance(req["tools"], list):
                for t in req["tools"]:
                    if isinstance(t, Mapping):
                        name = t["name"] if "name" in t else (t["function"]["name"] if "function" in t and "name" in t["function"] else None)
                        if name == "generate_image":
                            has_image_tool = True
                            break
        executed_image = any(call["name"] == "generate_image" for call in upstream.canonical["tool_calls"])
        if has_image_tool and not executed_image:
            divergences.append({
                "name": "advertised_image_tool_unexercised",
                "tool_name": "generate_image",
                "upstream": "generate_image advertised by sdk.ts in all turns; no case invokes it",
                "bb": "advertised in request body tools; no case invokes it; running tool would use pinned code without network",
            })

        order_paths = [
            {"request": index, "path": path, "upstream": up_order[path], "bb": bb_order[path]}
            for index, (up_order, bb_order) in enumerate(zip(upstream.orders, bb.orders))
            for path in up_order
            if path in bb_order and up_order[path] != bb_order[path]
        ]
        if order_paths:
            divergences.append({"name": "json_member_order", "differences": order_paths})

        compaction_events = [ev for ev in upstream.events if "type" in ev and "auto_compaction" in ev["type"]]
        if compaction_events:
            divergences.append({
                "name": "compaction_start_then_exit",
                "upstream_compaction_events": compaction_events,
                "bb": "compaction disabled under print mode; no compaction event emitted; no summary request was sent upstream",
            })

        argv = _require(upstream.trace, "argv", list, "upstream_case_invalid", "trace")
        if any(type(item) is not str for item in argv):
            raise _fail("upstream_case_invalid", "upstream trace argv must be text")
        if UPSTREAM_NO_RULES_FLAG in argv:
            divergences.append(_upstream_cli_no_rules(expected, observed))

        hidden = [index for index, group in enumerate(upstream.groups) if len(group) > 1]
        typed = None
        for index, group in enumerate(upstream.groups):
            last = group[-1]
            if "finish_reason" in last:
                fr = last["finish_reason"]
                if fr is not None and fr not in MODELED_FINISH_REASONS:
                    typed = ("unmodeled_finish_reason", index, UNMODELED_FINISH_REASON_FAILURE_CODE)
                    break
            if last["served"] not in {"completion", "http_error", "broken_stream"}:
                typed = ("non_http_transport_failure", index, None)
                break

        if hidden and (typed is None or hidden[0] <= typed[1]):
            index = hidden[0]
            group = upstream.groups[index]
            outcome = "recovered" if group[-1]["served"] == "completion" else "exhausted"
            record: dict[str, Any] = {
                "name": "sdk_hidden_transport_retry",
                "contract": "RL-PROVIDER-1",
                "logical_request_index": index,
                "outcome": outcome,
                "upstream_http_rows": _rows(group),
                "upstream_http_request_count": sum(len(item) for item in upstream.groups),
                "upstream_logical_request_count": len(upstream.groups),
                "bb_request_count": len(observed["requests"]),
            }
            if outcome == "exhausted":
                if index != len(upstream.groups) - 1:
                    raise _fail("upstream_case_invalid", "OMP continued after SDK retry exhaustion")
            divergences.append(record)
        elif typed is not None:
            name, index, failure_code = typed
            record = {"name": name, "logical_request_index": index, "upstream_http_rows": _rows(upstream.groups[index])}
            _set_expected(expected, upstream, index, _scope_prefix(expected, upstream, index, record), record)
            if expected["effects"] is None:
                observed["effects"] = None
            record["bb"], problems = _typed_failure(bb.process, failure_code)
            findings.extend({"field": "process", "detail": f"{name}: {problem}"} for problem in problems)
            divergences.append(record)
        elif not upstream.agent_end:
            request_guard = upstream.trace["request_guard"]
            if "cap_reached" in request_guard and request_guard["cap_reached"]:
                pass
            else:
                signal_sent = request_guard["signal_sent"] if "signal_sent" in request_guard else {}
                anchor_raw = signal_sent["anchor"] if "anchor" in signal_sent else ""
                anchor = anchor_raw.split(":", 1)[1] if ":" in anchor_raw else anchor_raw
                if not anchor and "signal_cancel_on_tool_start" in upstream.scenario:
                    anchor = upstream.scenario["signal_cancel_on_tool_start"]
                last = upstream.messages[-1] if upstream.messages else None
                started = {event["toolCallId"] for event in upstream.events if event["type"] == "tool_execution_start"}
                ended = {event["toolCallId"] for event in upstream.events if event["type"] == "tool_execution_end"}
                bb_info, problems = _typed_failure(bb.process, CANCEL_FAILURE_CODE)
                if bb_info and bb_info["exit_code"] != CANCEL_EXIT_CODE:
                    problems.append(f"BB exit code {bb_info['exit_code']!r} is not {CANCEL_EXIT_CODE}")
                findings.extend({"field": "process", "detail": f"external_cancel_signal: {problem}"} for problem in problems)
                cut = len(upstream.messages)
                after = bb.messages[cut:]
                observed.update(_canonical(observed["requests"], bb.messages[:cut], observed["effects"]))
                cancel_record: dict[str, Any] = {
                    "name": "external_cancel_signal",
                    "anchor_tool_call_id": anchor,
                    "upstream": {"signal": signal_sent["signal"] if "signal" in signal_sent else "SIGTERM", "exit_code": upstream.exit_code, "agent_end": False},
                    "bb": {"signal": "SIGINT", **bb_info},
                    "bb_messages_after_anchor": after,
                    "comparison_scope": {"messages_through_anchor": cut},
                }
                if bb.ledger is not None:
                    cancel_record["runtime_inputs_source"] = "derived from sent system prompt"
                    cancel_record["anchor_unsubmitted_observation"] = (
                        bb.unsubmitted_observation_by_call_id[anchor]
                        if anchor in bb.unsubmitted_observation_by_call_id
                        else None
                    )
                    cancel_record["workspace_state"] = {
                        "proven": False,
                        "upstream_effects": expected["effects"],
                        "detail": "cancelled BB run did not record post-execution effect snapshot; workspace state unproven",
                    }
                    if expected["effects"]:
                        findings.append({
                            "field": "effects",
                            "detail": "cancelled BB run did not record post-execution effect snapshot; upstream produced file changes that cannot be proven",
                        })
                    expected["effects"] = None
                    observed["effects"] = None
                divergences.append(cancel_record)

        if bb.trace is not None:
            kind = bb.trace["termination"]["kind"]
            stop = bb.trace["termination"]["native_stop_reason"]
            last_bb_stop = _termination(bb.messages)
            if "stopReason" in last_bb_stop and stop != last_bb_stop["stopReason"]:
                findings.append({"field": "termination", "detail": f"BB native_stop_reason {stop!r} differs from last assistant {last_bb_stop['stopReason']!r}"})
            if stop not in BB_KIND_BY_STOP or kind not in BB_KIND_BY_STOP[stop]:
                findings.append({"field": "termination", "detail": f"BB termination kind {kind!r} is inconsistent with stop {stop!r}"})
            if bb.trace["request_count"] != len(bb.trace["requests"]):
                findings.append({"field": "request_count", "detail": f"BB request_count {bb.trace['request_count']} != sent requests {len(bb.trace['requests'])}"})
            if kind == "RequestLimitExceeded":
                terminal = bb.messages[-1] if bb.messages else None
                cap_terminal = (
                    stop == "error"
                    and isinstance(terminal, Mapping) and terminal.get("stopReason") == "error" and "errorMessage" not in terminal
                )
                cap = 8
                request_guard = upstream.trace["request_guard"]
                cap_reached = (
                    ("cap_reached" in request_guard and request_guard["cap_reached"])
                    or len(upstream.groups) >= cap
                )
                if (
                    cap_terminal
                    and cap_reached
                    and bb.trace["stream_fn_issued"] == cap + 1
                    and len(bb.trace["requests"]) == cap
                ):
                    record = {
                        "name": "bounded_request_cap",
                        "request_cap": cap,
                        "upstream_logical_request_count": len(upstream.groups),
                        "bb_terminal_message": terminal,
                    }
                    expected.update(_canonical(expected["requests"][:cap], upstream.messages, expected["effects"]))
                    observed.update(_canonical(observed["requests"], bb.messages[:-1], observed["effects"]))
                    expected["termination"] = {"cause": "request_limit"}
                    observed["termination"] = {"cause": "request_limit"}
                    expected_counts_extra = 1
                    bb.normalizer.add("request_limit_cause", "$.termination")
                    divergences.append(record)
                else:
                    findings.append({"field": "termination", "detail": "BB RequestLimitExceeded does not satisfy the declared request cap against this upstream case"})
        else:
            if bb.last_commit_state is None:
                raise _fail("bb_ledger_invalid", "ledger replay carries no consumer commit state")
            last_commit = bb.last_commit_state
            stop = last_commit["native_stop_reason"] if "native_stop_reason" in last_commit else None
            last_bb_stop = _termination(bb.messages)
            anchor_stop = last_bb_stop["stopReason"] if "stopReason" in last_bb_stop else None
            if stop != anchor_stop:
                findings.append({"field": "termination", "detail": f"BB native_stop_reason {stop!r} differs from anchor assistant {anchor_stop!r}"})
            if stop != "toolUse":
                findings.append({"field": "termination", "detail": f"BB native_stop_reason {stop!r} is not 'toolUse'"})
            req_count = last_commit["request_count"] if "request_count" in last_commit else None
            stream_fn = last_commit["stream_fn_issued"] if "stream_fn_issued" in last_commit else None
            sent_reqs = len(observed["requests"])
            if req_count != sent_reqs or stream_fn != sent_reqs or req_count != stream_fn:
                findings.append({
                    "field": "request_count",
                    "detail": f"BB request_count {req_count} and stream_fn_issued {stream_fn} must equal transcript requests {sent_reqs}",
                })

        if expected_counts_extra:
            upstream.normalizer.add("request_limit_cause", "$.termination")

        files_only = _workspace_regular_files_only(
            Path(capture["case_dir"]).resolve(),
            _fixture_set(Path(capture["case_dir"]).resolve())[1],
            expected,
            observed,
        )
        if files_only is not None:
            divergences.append(files_only)

        for name in CANONICAL_FIELDS:
            difference = _first_difference(expected[name], observed[name], f"$.{name}")
            if difference:
                findings.append({"field": name, "detail": difference})

        normalizations = upstream.normalizer.report("upstream") + bb.normalizer.report("bb")
        if findings:
            verdict = "fail"
        elif divergences:
            verdict = "named_divergence"
        elif any(item["count"] for item in normalizations):
            verdict = "normalized"
        else:
            verdict = "exact"

        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "comparator_id": COMPARATOR_ID,
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "case_id": upstream.case_id,
            "passed": verdict != "fail",
            "verdict": verdict,
            "normalizations": normalizations,
            "divergences": divergences,
            "findings": findings,
            "uncompared": list(UNCOMPARED),
            "assertions": [{
                "assertion_id": "canonical_episode",
                "status": "failed" if findings else "passed",
                "expected": expected,
                "observed": observed,
                "detail": verdict if not findings else "; ".join(item["detail"] for item in findings),
            }],
        }

    compare = __call__


def compare(inp: Mapping[str, Any]) -> dict[str, Any]:
    return OhMyPi16213Comparator()(inp)


__all__ = [
    "CONFIG_ID",
    "COMPARATOR_ID",
    "DIVERGENCES",
    "LANE_ID",
    "NORMALIZATIONS",
    "OhMyPi16213Comparator",
    "OhMyPi16213ComparatorError",
    "compare",
    "project_bb_trace",
    "project_upstream_case",
]
