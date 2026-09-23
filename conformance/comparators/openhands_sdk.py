"""Deterministic OpenHands SDK 1.47.0 episode comparison.

JSON object member order is intentionally not a discriminator; ordered arrays and
wire message/tool list order remain significant. Workspace roots are replaced
only from the case-recorded roots, never by wildcard regexes.

The supplier input is a case directory containing ``trace.json`` and, when
available, ``receiver/http-transcript.jsonl`` plus ``workspace/``.  The
projector counts only transcript rows with a ``body`` (the receiver records a
request and a response as separate rows); ``controls.http_attempts`` is used
as a consistency check, never as a substitute for the request bodies.

The BreadBoard port MUST emit a mapping with these fields for
:func:`project_bb_trace`:

``schema_version``
    ``bb.e4.openhands-sdk-trace.v1``.
``case_id``
    Supplier case identifier.
``normalizations``
    A list of exactly the normalization rules applied.  The admitted rules
    are ``event_uuid:<EVENT_UUID>``, ``timestamp:<TIMESTAMP>``,
    ``hostname:<HOSTNAME>``, ``tmp_dir_suffix:<TMP_DIR_SUFFIX>``,
    ``call_id:<CALL_ID>``, and ``response_id:<RESPONSE_ID>``.
``requests``
    Ordered objects ``{index, body, response?}``; ``body`` contains the model
    request as sent, including its ordered ``messages`` and ``tools`` lists.
    ``response`` (when present) contains ordered ``choices`` with each
    ``finish_reason`` and ``message``.
``tool_calls``
    Ordered objects ``{tool_name, arguments, security_risk}``, including
    prepared calls which were later cut off or rejected by validation.
``observations``
    Ordered objects ``{event_kind, tool_name, is_error, result?, error_text?}``.
    AgentErrorEvent and ConversationErrorEvent are represented here so their
    error text remains comparable.
``file_effects``
    BB-owned measured records, ``{path: {exists, bytes, sha256,
    content_utf8?}}``.  The comparator projects these records, and the
    supplier's effect records, to the same path-to-digest map.
``termination``
    ``{kind, native_stop_reason}``, where ``native_stop_reason`` is the final
    model choice finish reason (or null).
``request_count``
    Number of real HTTP requests, equal to the number of request objects.

The measured effects use only content hashes; mtime and worker-reported
details are not accepted as authority.  A repository workspace's root
``.git`` tree is excluded symmetrically from both sides.

UUIDs, ISO timestamps, hostnames, temporary-directory suffixes, call IDs and
response IDs are the only volatile values normalized.  A placeholder found in
an input without its declared rule is rejected; no arbitrary wildcarding is
performed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from conformance.comparators.protocol import ComparatorInput

COMPARATOR_ID = "openhands_sdk_trace_v1"
LANE_ID = "openhands_sdk_1_47_0_replay"
CONFIG_ID = "openhands_sdk_1_47_0_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
TRACE_SCHEMA_VERSION = "bb.e4.openhands-sdk-trace.v1"

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")
CALL_ID_RE = re.compile(r"^(?:oh-capture|call[-_])[A-Za-z0-9_.-]+$")
RESPONSE_ID_RE = re.compile(r"^oh-capture-response-[A-Za-z0-9_.-]+$")
TMP_SUFFIX_RE = re.compile(r"^(.*(?:/tmp|/private/tmp)/[^/]*?)(?:_[A-Za-z0-9]{6,})(/.*)?$")
WORKSPACE_PATH_RE = re.compile(r"^(.+/workspace)(?:/.*)?$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

NORMALIZATION_BY_PLACEHOLDER = {
    "<EVENT_UUID>": "event_uuid:<EVENT_UUID>",
    "<TIMESTAMP>": "timestamp:<TIMESTAMP>",
    "<HOSTNAME>": "hostname:<HOSTNAME>",
    "<TMP_DIR_SUFFIX>": "tmp_dir_suffix:<TMP_DIR_SUFFIX>",
    "<CALL_ID>": "call_id:<CALL_ID>",
    "<RESPONSE_ID>": "response_id:<RESPONSE_ID>",
    "<WORKSPACE>": "workspace:<WORKSPACE>",
}
ALLOWED_NORMALIZATIONS = frozenset(NORMALIZATION_BY_PLACEHOLDER.values())
PLACEHOLDERS = tuple(NORMALIZATION_BY_PLACEHOLDER)


def _is_number(value: Any) -> bool:
    return type(value) in (int, float)


def _json_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    """Return a useful JSON difference; object member order is not semantic."""
    if _is_number(expected) and _is_number(observed):
        if expected == observed:
            return None
        return f"first difference at {path}: expected {_json_value(expected)}, observed {_json_value(observed)}"
    if type(expected) is not type(observed):
        return f"first difference at {path}: expected {_json_value(expected)}, observed {_json_value(observed)} (types differ)"
    if isinstance(expected, Mapping):
        expected_keys = set(expected)
        observed_keys = set(observed)
        if expected_keys != observed_keys:
            return f"first difference at {path}: expected keys {_json_value(sorted(expected_keys))}, observed {_json_value(sorted(observed_keys))}"
        for key in expected:
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


def _workspace_roots(value: Any) -> tuple[str, ...]:
    roots: set[str] = set()
    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            match = WORKSPACE_PATH_RE.match(item)
            if match:
                roots.add(match.group(1))
    walk(value)
    return tuple(sorted(roots, key=len, reverse=True))


class _Normalizer:
    def __init__(self, workspace_roots: Sequence[str] = ()) -> None:
        self.applied: set[str] = set()
        self.workspace_roots = tuple(workspace_roots)

    def _mark(self, placeholder: str) -> str:
        self.applied.add(NORMALIZATION_BY_PLACEHOLDER[placeholder])
        return placeholder

    def value(self, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {str(k): self.value(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(item, key=key) for item in value]
        if not isinstance(value, str):
            return value
        if value in PLACEHOLDERS:
            return value
        for root in self.workspace_roots:
            if value == root:
                return self._mark("<WORKSPACE>")
            if value.startswith(root + "/"):
                self._mark("<WORKSPACE>")
                return "<WORKSPACE>" + value[len(root):]
        if key in {"hostname", "host_name"}:
            return self._mark("<HOSTNAME>")
        if key in {"timestamp", "created_at", "updated_at"} or ISO_TIMESTAMP_RE.fullmatch(value):
            return self._mark("<TIMESTAMP>")
        if UUID_RE.fullmatch(value):
            return self._mark("<EVENT_UUID>")
        if key in {"response_id"} or RESPONSE_ID_RE.fullmatch(value):
            return self._mark("<RESPONSE_ID>")
        if key in {"id", "tool_call_id", "responses_item_id", "llm_response_id"} and CALL_ID_RE.fullmatch(value):
            return self._mark("<CALL_ID>")
        match = TMP_SUFFIX_RE.fullmatch(value)
        if match:
            prefix, suffix = match.group(1), match.group(2) or ""
            return f"{prefix}_<TMP_DIR_SUFFIX>{suffix}"
        return value


def _placeholder_problems(trace: Mapping[str, Any]) -> list[str]:
    declared = trace.get("normalizations")
    if not isinstance(declared, list) or any(type(item) is not str for item in declared):
        return ["normalizations must be a list of strings"]
    problems: list[str] = []
    if len(declared) != len(set(declared)):
        problems.append("normalizations must be unique")
    unknown = sorted(set(declared) - ALLOWED_NORMALIZATIONS)
    if unknown:
        problems.append(f"normalizations not admitted: {', '.join(unknown)}")

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            for placeholder, rule in NORMALIZATION_BY_PLACEHOLDER.items():
                if placeholder in value and rule not in declared:
                    problems.append(f"placeholder {placeholder} at {path} lacks declared {rule}")

    for key in ("requests", "tool_calls", "observations", "file_effects", "termination"):
        walk(trace.get(key), f"$.{key}")
    applied = {
        rule
        for placeholder, rule in NORMALIZATION_BY_PLACEHOLDER.items()
        if any(placeholder in _json_value(trace.get(key)) for key in ("requests", "tool_calls", "observations", "file_effects", "termination"))
    }
    for rule in sorted(applied - set(declared)):
        problems.append(f"normalization {rule} is applied but undeclared")
    for rule in sorted(set(declared) & ALLOWED_NORMALIZATIONS - applied):
        problems.append(f"normalization {rule} is declared but not applied")
    return problems


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc


def _read_receiver_rows(case_dir: Path) -> list[Mapping[str, Any]]:
    transcript = case_dir / "receiver" / "http-transcript.jsonl"
    if not transcript.is_file():
        return []
    rows: list[Mapping[str, Any]] = []
    try:
        for line in transcript.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, Mapping):
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load receiver transcript {transcript}: {exc}") from exc
    return rows


def _request_rows(trace: Mapping[str, Any], case_dir: Path) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Return request and response rows from either packet trace layout.

    Rerun2 stores separate ``requests`` and ``responses`` arrays.  Older
    captures and the receiver JSONL interleave request/response records.
    """
    raw_requests = trace.get("requests")
    if isinstance(raw_requests, list):
        request_rows = [
            row for row in raw_requests if isinstance(row, Mapping) and isinstance(row.get("body"), Mapping)
        ]
    else:
        request_rows = []
    raw_responses = trace.get("responses")
    if isinstance(raw_responses, list):
        response_rows = [
            row for row in raw_responses if isinstance(row, Mapping) and isinstance(row.get("response"), Mapping)
        ]
    else:
        rows = list(raw_requests) if isinstance(raw_requests, list) else _read_receiver_rows(case_dir)
        response_rows = [
            row for row in rows if isinstance(row, Mapping) and isinstance(row.get("response"), Mapping)
        ]
        if not request_rows:
            request_rows = [
                row for row in rows if isinstance(row, Mapping) and isinstance(row.get("body"), Mapping)
            ]
    controls = trace.get("controls")
    if isinstance(controls, Mapping) and type(controls.get("http_attempts")) is int:
        expected = controls["http_attempts"]
        if expected != len(request_rows):
            raise ValueError(f"controls.http_attempts={expected} but found {len(request_rows)} request bodies")
    return request_rows, response_rows


def _response_projection(response: Mapping[str, Any], normalizer: _Normalizer) -> dict[str, Any]:
    choices = response.get("choices", [])
    if not isinstance(choices, list):
        choices = []
    result: dict[str, Any] = {"choices": []}
    for choice in choices:
        if not isinstance(choice, Mapping):
            result["choices"].append(normalizer.value(choice))
            continue
        result["choices"].append(
            {
                "index": choice.get("index"),
                "finish_reason": choice.get("finish_reason"),
                "message": normalizer.value(choice.get("message")),
            }
        )
    return result


def _tool_call_projection(event: Mapping[str, Any], normalizer: _Normalizer) -> dict[str, Any]:
    call = event.get("tool_call")
    arguments: Any = None
    if isinstance(call, Mapping):
        arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = normalizer.value(arguments)
    elif arguments is None and event.get("action") is not None:
        arguments = event.get("action")
    return {
        "tool_name": event.get("tool_name"),
        "arguments": normalizer.value(arguments),
        "security_risk": event.get("security_risk"),
    }


def _observation_projection(events: Sequence[Any], normalizer: _Normalizer) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        kind = event.get("kind")
        if kind == "ObservationEvent":
            observation = event.get("observation")
            if not isinstance(observation, Mapping):
                observation = {"value": observation}
            observations.append(
                {
                    "event_kind": kind,
                    "tool_name": event.get("tool_name"),
                    "is_error": bool(observation.get("is_error", False)),
                    "result": normalizer.value(observation),
                }
            )
        elif kind in {"AgentErrorEvent", "ConversationErrorEvent"}:
            error = event.get("error") or event.get("detail") or event.get("code")
            observations.append(
                {
                    "event_kind": kind,
                    "tool_name": event.get("tool_name"),
                    "is_error": True,
                    "error_text": normalizer.value(error),
                    "classification": normalizer.value(event.get("classification")),
                }
            )
    return observations

def _project_effects(raw: Any) -> dict[str, str | None]:
    files = raw.get("files", {}) if isinstance(raw, Mapping) and "files" in raw else raw
    if not isinstance(files, Mapping):
        return {}
    result: dict[str, str | None] = {}
    for path, value in files.items():
        if not isinstance(path, str) or not path:
            raise ValueError(f"invalid file effect path: {path!r}")
        if path == ".git" or path.startswith(".git/"):
            continue
        if isinstance(value, Mapping):
            exists = value.get("exists", True)
            if type(exists) is not bool:
                raise ValueError(f"invalid file existence for {path!r}")
            digest = None if not exists else value.get("sha256")
        elif value is None or isinstance(value, str):
            digest = value
        else:
            raise ValueError(f"invalid file effect for {path!r}: {value!r}")
        if digest is not None and (
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
        ):
            raise ValueError(f"invalid file digest for {path!r}: {digest!r}")
        result[path] = digest
    return result


def _workspace_effects(case_dir: Path) -> dict[str, str]:
    root = case_dir / "workspace"
    if not root.is_dir():
        return {}
    effects: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        effects[relative] = f"sha256:{digest}"
    return effects


def _file_effects(trace: Mapping[str, Any], case_dir: Path) -> dict[str, str | None]:
    result = _project_effects(trace.get("effects", {}))
    for path, digest in _workspace_effects(case_dir).items():
        result.setdefault(path, digest)
    return result


def _canonical_from_trace(trace: Mapping[str, Any], case_dir: Path, *, role: str) -> dict[str, Any]:
    request_rows, response_rows = _request_rows(trace, case_dir)
    response_by_index = {
        row.get("index"): row["response"]
        for row in response_rows
        if isinstance(row.get("response"), Mapping)
    }
    normalizer = _Normalizer(_workspace_roots(trace))
    requests: list[dict[str, Any]] = []
    for row in request_rows:
        item: dict[str, Any] = {
            "index": row.get("index"),
            "body": normalizer.value(row["body"]),
        }
        index = row.get("index")
        if index in response_by_index:
            item["response"] = _response_projection(response_by_index[index], normalizer)
        requests.append(item)

    events = trace.get("events", [])
    if not isinstance(events, list):
        raise ValueError("trace.events must be a list")
    tool_calls = [
        _tool_call_projection(event, normalizer)
        for event in events
        if isinstance(event, Mapping) and event.get("kind") == "ActionEvent"
    ]
    observations = _observation_projection(events, normalizer)
    exit_info = trace.get("exit", {})
    if not isinstance(exit_info, Mapping):
        exit_info = {}
    final_stop: Any = None
    if response_by_index:
        last_response = response_by_index[max(response_by_index)]
        choices = last_response.get("choices", []) if isinstance(last_response, Mapping) else []
        if isinstance(choices, list) and choices and isinstance(choices[-1], Mapping):
            final_stop = choices[-1].get("finish_reason")
    status = exit_info.get("status")
    kind = "finished" if status == "finished" else (str(status) if status is not None else "unknown")
    projected = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "role": role,
        "case_id": trace.get("case_id") or case_dir.name,
        "requests": requests,
        "tool_calls": tool_calls,
        "observations": observations,
        "file_effects": _file_effects(trace, case_dir),
        "termination": {"kind": kind, "native_stop_reason": normalizer.value(final_stop)},
        "request_count": len(request_rows),
    }
    projected["normalizations"] = sorted(normalizer.applied)
    problems = _placeholder_problems(projected)
    if problems:
        raise ValueError("invalid normalization: " + "; ".join(problems))
    return projected


def project_supplier_case(case_dir: Path | str) -> dict[str, Any]:
    """Project one OpenHands supplier case directory into a canonical episode."""
    path = Path(case_dir)
    trace = _load_json(path / "trace.json")
    if not isinstance(trace, Mapping):
        raise ValueError("supplier trace must be a JSON object")
    return _canonical_from_trace(trace, path, role="supplier")

def project_bb_trace(trace: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    """Project and validate a BreadBoard OpenHands replay trace.

    The runtime may provide raw event/request values in the documented field
    layout.  A canonical trace (for example one returned by
    :func:`project_supplier_case`) is accepted too, which makes a self-replay
    useful without inventing a second serialization format.
    """
    if isinstance(trace, (str, Path)):
        value = _load_json(Path(trace))
    else:
        value = copy.deepcopy(trace)
    if not isinstance(value, Mapping):
        raise ValueError("BreadBoard trace must be an object")
    required = ("case_id", "requests", "tool_calls", "observations", "file_effects", "termination", "request_count")
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError("BreadBoard trace missing fields: " + ", ".join(missing))
    if value.get("schema_version") not in {None, TRACE_SCHEMA_VERSION}:
        raise ValueError(f"BreadBoard trace schema_version must be {TRACE_SCHEMA_VERSION}")
    normalizer = _Normalizer(_workspace_roots(value))
    projected = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "role": "breadboard",
        "case_id": value["case_id"],
        "requests": normalizer.value(value["requests"]),
        "tool_calls": normalizer.value(value["tool_calls"]),
        "observations": normalizer.value(value["observations"]),
        "file_effects": normalizer.value(_project_effects(value["file_effects"])),
        "termination": normalizer.value(value["termination"]),
        "request_count": value["request_count"],
    }
    projected["normalizations"] = sorted(normalizer.applied)
    # Preserve declarations from a runtime trace and reject placeholders that
    # were already present without their declaration.
    declared = value.get("normalizations")
    if declared is not None:
        if not isinstance(declared, list) or any(type(item) is not str for item in declared):
            raise ValueError("normalizations must be a list of strings")
        projected["normalizations"] = list(declared)
    problems = _placeholder_problems(projected)
    if problems:
        raise ValueError("invalid normalization: " + "; ".join(problems))
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


def _report(assertions: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    failed = sum(assertion["status"] == "failed" for assertion in assertions)
    return {
        "comparator_id": COMPARATOR_ID,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ok": failed == 0 and not errors,
        "passed": len(assertions) - failed,
        "failed": failed,
        "errors": errors or [],
        "assertions": assertions,
    }


def compare_cases(supplier_case: Path | str | Mapping[str, Any], bb_trace: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    errors: list[str] = []
    try:
        expected = project_supplier_case(supplier_case) if isinstance(supplier_case, (Path, str)) else project_bb_trace(supplier_case)
        observed = project_bb_trace(bb_trace)
    except (OSError, TypeError, ValueError) as exc:
        return _report([], [str(exc)])
    assertions: list[dict[str, Any]] = []
    for field in ("case_id", "requests", "tool_calls", "observations", "file_effects", "termination", "request_count"):
        difference = _first_difference(expected.get(field), observed.get(field), f"$.{field}")
        assertions.append(_assertion(f"{expected['case_id']}.{field}_equal", expected.get(field), observed.get(field), difference))
    return _report(assertions, errors)


class OpenHandsSDKComparator:
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

    errors: list[str] = []
    capture = inp.get("capture", {})
    replay = inp.get("replay", {})
    artifacts = inp.get("artifacts", {})
    repo_root = Path(inp.get("repo_root") or Path(__file__).resolve().parents[2])
    capture_ref = Path(artifacts.get("capture_ref", repo_root / "capture.json"))
    replay_ref = Path(artifacts.get("replay_ref", repo_root / "replay.json"))
    assertions: list[dict[str, Any]] = []
    if not isinstance(capture, Mapping) or not isinstance(replay, Mapping):
        return _report([], ["capture and replay manifests are required"])
    capture_cases = capture.get("cases", {})
    replay_cases = replay.get("cases", {})
    if not isinstance(capture_cases, Mapping) or not isinstance(replay_cases, Mapping):
        return _report([], ["capture.cases and replay.cases must be objects"])
    case_ids = sorted(set(capture_cases) | set(replay_cases))
    for case_id in case_ids:
        if case_id not in capture_cases or case_id not in replay_cases:
            assertions.append(_assertion(f"{case_id}.case_present", True, False, f"first difference at $.case_ids: missing {case_id}"))
            continue
        try:
            supplier = _manifest_case_path(capture_cases[case_id], capture_ref)
            replay_path = _manifest_case_path(replay_cases[case_id], replay_ref)
            observed = _load_json(replay_path) if replay_path.is_file() else None
            result = compare_cases(supplier, observed)
            assertions.extend(result["assertions"])
            errors.extend(result["errors"])
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"{case_id}: {exc}")
    return _report(assertions, errors)


COMPARATOR = OpenHandsSDKComparator()
