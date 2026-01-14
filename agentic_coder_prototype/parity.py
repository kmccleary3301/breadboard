from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_optional_json(path: Path) -> Optional[Any]:
    if not path:
        return None
    try:
        return _load_json(path)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def load_guardrail_expectations(path: Path) -> List[Dict[str, Any]]:
    return sanitize_guardrail_events(_load_json(path))


def load_todo_expectations(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def sanitize_guardrail_events(events: Any) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    if not isinstance(events, list):
        return sanitized
    for event in events:
        normalized = _normalize_guardrail_event(event)
        if normalized:
            sanitized.append(normalized)
    return sanitized


def _normalize_guardrail_event(event: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None
    normalized: Dict[str, Any] = {"type": str(event.get("type") or "unknown")}
    payload = event.get("payload")
    if isinstance(payload, dict):
        sanitized_payload = {}
        for key, value in payload.items():
            if key == "timestamp":
                continue
            sanitized_payload[key] = value
        if sanitized_payload:
            normalized["payload"] = sanitized_payload
    return normalized


def compare_guardrail_events(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    if len(actual) != len(expected):
        mismatches.append(f"Guardrail event length mismatch: actual {len(actual)} vs expected {len(expected)}")
    for idx in range(min(len(actual), len(expected))):
        if actual[idx] != expected[idx]:
            mismatches.append(f"Guardrail event #{idx + 1} differs: actual={actual[idx]} expected={expected[idx]}")
    return mismatches


def compare_workspace_manifests(lhs: Dict[str, Any], rhs: Dict[str, Any], ignore: Optional[Iterable[str]] = None) -> List[str]:
    ignore_set = {str(item) for item in (ignore or [])}

    def _skip(path: str) -> bool:
        for pattern in ignore_set:
            if not pattern:
                continue
            if path == pattern:
                return True
            if pattern.endswith("/") and path.startswith(pattern):
                return True
            if path.startswith(pattern.rstrip("/") + "/"):
                return True
        return False

    def _build_index(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        files = manifest.get("files")
        if not isinstance(files, list):
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for entry in files:
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            if _skip(path):
                continue
            index[path] = {
                "sha256": entry.get("sha256"),
                "size": entry.get("size"),
                "is_binary": bool(entry.get("is_binary", False)),
            }
        return index

    mismatches: List[str] = []
    left_index = _build_index(lhs or {})
    right_index = _build_index(rhs or {})
    for path, meta in left_index.items():
        if path not in right_index:
            mismatches.append(f"Missing file in manifest: {path}")
            continue
        other = right_index[path]
        if meta.get("sha256") != other.get("sha256"):
            mismatches.append(f"Hash mismatch for {path}")
        elif meta.get("size") != other.get("size"):
            mismatches.append(f"Size mismatch for {path}: {meta.get('size')} vs {other.get('size')}")
        elif meta.get("is_binary") != other.get("is_binary"):
            mismatches.append(f"Binary flag mismatch for {path}")
    for path in right_index:
        if path not in left_index:
            mismatches.append(f"Extra file in manifest: {path}")
    return mismatches


def compare_todo_journal(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> List[str]:
    def _build_id_map(events: List[Dict[str, Any]]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        counter = 1

        def _register(raw: Any) -> None:
            nonlocal counter
            if not isinstance(raw, str):
                return
            if not raw.startswith("todo_"):
                return
            if raw in mapping:
                return
            mapping[raw] = f"todo_{counter}"
            counter += 1

        for entry in events:
            if not isinstance(entry, dict):
                continue
            _register(entry.get("todo_id"))
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            todo_block = payload.get("todo")
            if isinstance(todo_block, dict):
                _register(todo_block.get("id"))
            order_block = payload.get("order")
            if isinstance(order_block, list):
                for item in order_block:
                    _register(item)
        return mapping

    def _normalize(event: Dict[str, Any], *, id_map: Dict[str, str]) -> Dict[str, Any]:
        normalized = dict(event)
        normalized.pop("timestamp", None)
        normalized.pop("todo_id", None)
        payload = normalized.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            todo_block = payload.get("todo")
            if isinstance(todo_block, dict):
                todo_block = dict(todo_block)
                for key in ("id", "created_at", "updated_at", "version"):
                    todo_block.pop(key, None)
                payload["todo"] = todo_block
            note_block = payload.get("note")
            if isinstance(note_block, dict):
                note_block = dict(note_block)
                note_block.pop("created_at", None)
                payload["note"] = note_block
            order_block = payload.get("order")
            if isinstance(order_block, list):
                payload["order"] = [id_map.get(item, item) for item in order_block]
            for key in ("updated_at", "version"):
                payload.pop(key, None)
            normalized["payload"] = payload
        return normalized

    mismatches: List[str] = []
    if len(actual) != len(expected):
        mismatches.append(f"Todo journal length mismatch: actual {len(actual)} vs expected {len(expected)}")
    actual_id_map = _build_id_map(actual)
    expected_id_map = _build_id_map(expected)
    for idx in range(min(len(actual), len(expected))):
        actual_norm = _normalize(actual[idx], id_map=actual_id_map)
        expected_norm = _normalize(expected[idx], id_map=expected_id_map)
        if actual_norm != expected_norm:
            mismatches.append(f"Todo event #{idx + 1} differs: actual={actual_norm} expected={expected_norm}")
    return mismatches


def _load_multi_agent_events(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except Exception:
        return []
    return events


_MULTI_AGENT_ID_KEYS = {
    "job_id",
    "jobid",
    "task_id",
    "taskid",
    "session_id",
    "sessionid",
    "task_session_id",
    "task_sessionid",
    "taskid",
}


def _normalize_multi_agent_events(
    events: List[Dict[str, Any]],
    prefixes: List[str],
    *,
    ordering_model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    id_maps: Dict[str, Dict[str, str]] = {}
    counters: Dict[str, int] = {}
    job_seq: Dict[str, Any] = {}

    ordering = (ordering_model or "").lower()
    if ordering in {"total_event_id", "event_id", "eventid"}:
        events = sorted(events, key=lambda ev: int((ev or {}).get("event_id") or 0))

    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        job_id = payload.get("job_id") or payload.get("jobId") or payload.get("jobID")
        seq = payload.get("seq")
        if isinstance(job_id, str) and job_id and seq is not None:
            job_seq[job_id] = seq

    def _map_id(key: str, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        canon = key.lower()
        if canon not in _MULTI_AGENT_ID_KEYS:
            return value
        mapping = id_maps.setdefault(canon, {})
        if value in mapping:
            return mapping[value]
        counters[canon] = counters.get(canon, 0) + 1
        mapped = f"{canon}_{counters[canon]}"
        mapping[value] = mapped
        return mapped

    def _normalize_scalar(value: Any) -> Any:
        if isinstance(value, str):
            normalized = value
            for prefix in prefixes:
                if not prefix:
                    continue
                variants = {prefix, prefix.replace("\\", "/")}
                if not prefix.endswith("/"):
                    variants.add(prefix + "/")
                    variants.add(prefix.replace("\\", "/") + "/")
                for variant in variants:
                    normalized = normalized.replace(variant, "<PATH>")
            return normalized
        return value

    def _normalize_payload(payload: Any, *, event_type: str) -> Any:
        if isinstance(payload, dict):
            cleaned: Dict[str, Any] = {}
            for key, value in payload.items():
                if key in {"timestamp", "created_at", "updated_at"}:
                    continue
                if event_type in {"agent.job_completed", "agent.job_failed"} and key in {"output", "error"}:
                    continue
                if event_type == "run.started" and key in {"user_prompt"}:
                    continue
                if event_type == "agent.wakeup_emitted" and key == "message" and isinstance(value, str):
                    normalized_message = value
                    if normalized_message.startswith("Task "):
                        marker = normalized_message.find(" (")
                        if marker != -1:
                            normalized_message = "Task <TASK>" + normalized_message[marker:]
                    cleaned[key] = _normalize_scalar(normalized_message)
                    continue
                if key.lower() in _MULTI_AGENT_ID_KEYS:
                    cleaned[key] = _map_id(key, value)
                else:
                    cleaned[key] = _normalize_payload(value, event_type=event_type)
            return cleaned
        if isinstance(payload, list):
            return [_normalize_payload(item, event_type=event_type) for item in payload]
        return _normalize_scalar(payload)

    normalized_events: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        normalized: Dict[str, Any] = {
            "type": event_type,
            "agent_id": event.get("agent_id"),
            "parent_agent_id": event.get("parent_agent_id"),
            "causal_parent_event_id": event.get("causal_parent_event_id"),
        }
        payload = event.get("payload")
        if payload is not None:
            normalized["payload"] = _normalize_payload(payload, event_type=event_type)
        normalized_events.append(normalized)

    def _sort_key(event: Dict[str, Any]) -> tuple:
        event_type = str(event.get("type") or "")
        if event_type == "run.started":
            return (-1, event_type, "", "")
        payload = event.get("payload") or {}
        seq = payload.get("seq")
        job_id = payload.get("job_id") or payload.get("jobId") or payload.get("jobID")
        if seq is None and isinstance(job_id, str) and job_id in job_seq:
            seq = job_seq[job_id]
        seq_val = int(seq) if isinstance(seq, int) else 0
        agent_id = str(event.get("agent_id") or "")
        return (seq_val, event_type, agent_id, str(job_id or ""))

    if ordering not in {"total_event_id", "event_id", "eventid"}:
        normalized_events.sort(key=_sort_key)
    return normalized_events


def compare_multi_agent_events(
    actual: List[Dict[str, Any]],
    expected: List[Dict[str, Any]],
    *,
    ordering_model: Optional[str] = None,
) -> List[str]:
    mismatches: List[str] = []
    if not actual and not expected:
        return mismatches
    if ordering_model:
        actual = _normalize_multi_agent_events(actual, [], ordering_model=ordering_model)
        expected = _normalize_multi_agent_events(expected, [], ordering_model=ordering_model)
    if len(actual) != len(expected):
        mismatches.append(f"Multi-agent event count mismatch: actual={len(actual)} expected={len(expected)}")
    for idx in range(min(len(actual), len(expected))):
        if actual[idx] != expected[idx]:
            mismatches.append(f"Multi-agent event #{idx + 1} differs: actual={actual[idx]} expected={expected[idx]}")
    return mismatches


def _normalize_lifecycle_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for entry in events or []:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.pop("timestamp", None)
        normalized.append(
            {
                "type": entry.get("type"),
                "turn": entry.get("turn"),
                "seq": entry.get("seq"),
                "payload": payload,
            }
        )
    return normalized


def compare_lifecycle_events(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    actual_norm = _normalize_lifecycle_events(actual)
    expected_norm = _normalize_lifecycle_events(expected)
    if len(actual_norm) != len(expected_norm):
        mismatches.append(
            f"Lifecycle event count mismatch: actual={len(actual_norm)} expected={len(expected_norm)}"
        )
    if [e.get("type") for e in actual_norm] != [e.get("type") for e in expected_norm]:
        mismatches.append("Lifecycle event type sequence mismatch")
        return mismatches
    if actual_norm != expected_norm:
        mismatches.append("Lifecycle event payload mismatch")
    return mismatches


def _normalize_turn_tool_usage(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        entries: List[Dict[str, Any]] = []
        for key, value in raw.items():
            turn = None
            try:
                turn = int(key)
            except Exception:
                turn = key
            tools = []
            if isinstance(value, dict):
                tools = value.get("tools") or []
            entries.append({"turn": turn, "tools": tools})
        entries.sort(key=lambda item: item["turn"] if isinstance(item.get("turn"), int) else str(item.get("turn")))
        return entries
    if isinstance(raw, list):
        normalized: List[Dict[str, Any]] = []
        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            turn = entry.get("turn")
            if turn is None:
                turn = idx + 1
            tools = entry.get("tools") or []
            normalized.append({"turn": turn, "tools": tools})
        return normalized
    return []


def _normalize_turn_tools(tools: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(tools, list):
        return normalized
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        payload = {"name": entry.get("name")}
        if "success" in entry:
            payload["success"] = bool(entry.get("success"))
        normalized.append(payload)
    return normalized


def compare_tool_usage_summary(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    keys = set(actual.keys()) | set(expected.keys())
    for key in sorted(keys):
        if actual.get(key) != expected.get(key):
            mismatches.append(f"Tool usage summary mismatch for {key}: actual={actual.get(key)} expected={expected.get(key)}")
    return mismatches


def compare_turn_tool_usage(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    actual_norm = _normalize_turn_tool_usage(actual)
    expected_norm = _normalize_turn_tool_usage(expected)
    actual_map = {entry.get("turn"): entry for entry in actual_norm}
    expected_map = {entry.get("turn"): entry for entry in expected_norm}

    if len(actual_map) != len(expected_map):
        mismatches.append(
            f"Turn tool usage count mismatch: actual={len(actual_map)} expected={len(expected_map)}"
        )
    for turn, expected_entry in expected_map.items():
        actual_entry = actual_map.get(turn)
        if not actual_entry:
            mismatches.append(f"Missing turn tool usage for turn {turn}")
            continue
        actual_tools = _normalize_turn_tools(actual_entry.get("tools"))
        expected_tools = _normalize_turn_tools(expected_entry.get("tools"))
        if actual_tools != expected_tools:
            actual_names = [tool.get("name") for tool in actual_tools]
            expected_names = [tool.get("name") for tool in expected_tools]
            if actual_names != expected_names:
                mismatches.append(
                    f"Turn tool order mismatch at turn {turn}: actual={actual_names} expected={expected_names}"
                )
            else:
                mismatches.append(
                    f"Turn tool usage mismatch at turn {turn}: actual={actual_tools} expected={expected_tools}"
                )
    for turn in actual_map:
        if turn not in expected_map:
            mismatches.append(f"Extra turn tool usage entry for turn {turn}")
    return mismatches


def compare_workspace_files(actual: Path, golden: Path, ignore: Optional[Iterable[str]] = None) -> List[str]:
    ignore_set = {Path(item) for item in (ignore or [])}
    mismatches: List[str] = []

    def _is_binary(path: Path) -> bool:
        try:
            chunk = path.read_bytes()[:8192]
        except Exception:
            return False
        if b"\x00" in chunk:
            return True
        # Heuristic: treat as binary if high ratio of non-text bytes.
        if not chunk:
            return False
        text_chars = sum(1 for b in chunk if 9 <= b <= 13 or 32 <= b <= 126)
        return (text_chars / len(chunk)) < 0.85

    def _should_ignore(rel_path: Path) -> bool:
        for ign in ignore_set:
            ign_parts = ign.parts
            rel_parts = rel_path.parts
            if len(ign_parts) <= len(rel_parts) and rel_parts[: len(ign_parts)] == ign_parts:
                return True
        return False

    for golden_file in golden.rglob("*"):
        if golden_file.is_dir():
            continue
        rel = golden_file.relative_to(golden)
        if _should_ignore(rel):
            continue
        actual_file = actual / rel
        if not actual_file.exists():
            mismatches.append(f"Missing file in replay workspace: {rel}")
            continue
        if _is_binary(golden_file) or _is_binary(actual_file):
            continue
        if golden_file.read_bytes() != actual_file.read_bytes():
            mismatches.append(f"Content mismatch for {rel}")

    for actual_file in actual.rglob("*"):
        if actual_file.is_dir():
            continue
        rel = actual_file.relative_to(actual)
        if _should_ignore(rel):
            continue
        golden_file = golden / rel
        if not golden_file.exists():
            mismatches.append(f"Extra file present in replay workspace: {rel}")

    return mismatches


class EquivalenceLevel(str, Enum):
    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    NORMALIZED_TRACE = "normalized_trace"
    BITWISE_TRACE = "bitwise_trace"


@dataclass
class RunIR:
    workspace_path: Path
    completion_summary: Dict[str, Any] = field(default_factory=dict)
    guard_events: List[Dict[str, Any]] = field(default_factory=list)
    guard_counters: Dict[str, Any] = field(default_factory=dict)
    todo_journal: List[Dict[str, Any]] = field(default_factory=list)
    todo_snapshot: Dict[str, Any] = field(default_factory=dict)
    prompt_hashes: Dict[str, Any] = field(default_factory=dict)
    surface_snapshot: Dict[str, Any] = field(default_factory=dict)
    surface_manifest: Dict[str, Any] = field(default_factory=dict)
    tool_usage_summary: Dict[str, Any] = field(default_factory=dict)
    turn_tool_usage: List[Dict[str, Any]] = field(default_factory=list)
    env_fingerprint: Dict[str, Any] = field(default_factory=dict)
    turn_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    workspace_manifest: Optional[Dict[str, Any]] = None
    multi_agent_events: List[Dict[str, Any]] = field(default_factory=list)
    multi_agent_ordering: Optional[str] = None
    lifecycle_events: List[Dict[str, Any]] = field(default_factory=list)
    ignore_workspace: List[str] = field(
        default_factory=lambda: [".breadboard", ".git", "summary.json", ".claude", "session_result.json"]
    )


def _load_workspace_manifest(run_dir: Path) -> Optional[Dict[str, Any]]:
    manifest_path = run_dir / "meta" / "workspace.manifest.json"
    if not manifest_path.exists():
        return None
    return _load_optional_json(manifest_path)


def _load_todo_snapshot(workspace_path: Path) -> Dict[str, Any]:
    primary = workspace_path / ".breadboard" / "todos.json"
    fallback = workspace_path / ".kyle" / "todos.json"
    todo_path = primary if primary.exists() else fallback
    if not todo_path.exists():
        return {}
    try:
        data = _load_json(todo_path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_todo_journal(todo_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    journal = todo_snapshot.get("journal")
    if isinstance(journal, list):
        return [entry for entry in journal if isinstance(entry, dict)]
    return []


def build_run_ir_from_run_dir(run_dir: Path, workspace_override: Optional[Path] = None) -> RunIR:
    run_dir = Path(run_dir)
    summary_path = run_dir / "meta" / "run_summary.json"
    summary = _load_optional_json(summary_path) or {}
    workspace_path = workspace_override or (run_dir / "final_container_dir")
    if not workspace_override:
        hint = summary.get("workspace_path")
        if isinstance(hint, str) and hint:
            candidate = Path(hint)
            if candidate.exists():
                try:
                    empty_final_dir = not workspace_path.exists() or not any(workspace_path.iterdir())
                except Exception:
                    empty_final_dir = not workspace_path.exists()
                if empty_final_dir:
                    workspace_path = candidate

    guard_events = summary.get("guardrail_events") or []
    guard_counters = summary.get("guardrails") or {}
    completion_summary = summary.get("completion_summary") or {}
    prompt_hashes = summary.get("prompt_hashes") or summary.get("prompts") or {}
    tool_usage_summary = summary.get("tool_usage_summary") or summary.get("tool_usage") or {}
    turn_tool_usage = _normalize_turn_tool_usage(summary.get("turn_tool_usage"))
    env_fingerprint = summary.get("env_fingerprint") or {}
    turn_diagnostics = summary.get("turn_diagnostics") or []
    surface_snapshot = summary.get("surface_snapshot") or {}
    surface_manifest = summary.get("surface_manifest") or {}
    multi_agent_ordering = summary.get("multi_agent_ordering") or None

    todo_snapshot = _load_todo_snapshot(workspace_path)
    todo_journal = _extract_todo_journal(todo_snapshot)
    workspace_manifest = _load_workspace_manifest(run_dir)
    multi_agent_path = run_dir / "meta" / "multi_agent_events.jsonl"
    if not multi_agent_path.exists():
        multi_agent_path = workspace_path / ".breadboard" / "multi_agent_events.jsonl"
    multi_agent_events = _load_multi_agent_events(multi_agent_path)
    if not multi_agent_ordering and multi_agent_events:
        multi_agent_ordering = "total_event_id"
    lifecycle_events = summary.get("lifecycle_events") or []

    return RunIR(
        workspace_path=workspace_path,
        completion_summary=completion_summary if isinstance(completion_summary, dict) else {},
        guard_events=guard_events if isinstance(guard_events, list) else [],
        guard_counters=guard_counters if isinstance(guard_counters, dict) else {},
        todo_journal=todo_journal,
        todo_snapshot=todo_snapshot,
        prompt_hashes=prompt_hashes if isinstance(prompt_hashes, dict) else {},
        surface_snapshot=surface_snapshot if isinstance(surface_snapshot, dict) else {},
        surface_manifest=surface_manifest if isinstance(surface_manifest, dict) else {},
        tool_usage_summary=tool_usage_summary if isinstance(tool_usage_summary, dict) else {},
        turn_tool_usage=turn_tool_usage,
        env_fingerprint=env_fingerprint if isinstance(env_fingerprint, dict) else {},
        turn_diagnostics=turn_diagnostics if isinstance(turn_diagnostics, list) else [],
        workspace_manifest=workspace_manifest if isinstance(workspace_manifest, dict) else None,
        multi_agent_events=multi_agent_events,
        multi_agent_ordering=multi_agent_ordering,
        lifecycle_events=lifecycle_events if isinstance(lifecycle_events, list) else [],
    )


def build_expected_run_ir(
    workspace_path: Path,
    *,
    guardrail_path: Optional[Path] = None,
    todo_journal_path: Optional[Path] = None,
    summary_path: Optional[Path] = None,
) -> RunIR:
    workspace_path = Path(workspace_path)
    summary = _load_optional_json(summary_path) or {}

    guard_events: List[Dict[str, Any]] = []
    if guardrail_path:
        guard_events = load_guardrail_expectations(guardrail_path)
    elif summary.get("guardrail_events"):
        guard_events = summary.get("guardrail_events") or []

    completion_summary = summary.get("completion_summary") or {}
    guard_counters = summary.get("guardrails") or {}
    prompt_hashes = summary.get("prompt_hashes") or summary.get("prompts") or {}
    tool_usage_summary = summary.get("tool_usage_summary") or summary.get("tool_usage") or {}
    turn_tool_usage = _normalize_turn_tool_usage(summary.get("turn_tool_usage"))
    env_fingerprint = summary.get("env_fingerprint") or {}
    turn_diagnostics = summary.get("turn_diagnostics") or []
    surface_snapshot = summary.get("surface_snapshot") or {}
    surface_manifest = summary.get("surface_manifest") or {}
    lifecycle_events = summary.get("lifecycle_events") or []
    multi_agent_ordering = summary.get("multi_agent_ordering") or None

    todo_snapshot = _load_todo_snapshot(workspace_path)
    todo_journal: List[Dict[str, Any]] = []
    if todo_journal_path:
        todo_journal = load_todo_expectations(todo_journal_path)
    else:
        todo_journal = _extract_todo_journal(todo_snapshot)

    multi_agent_path = None
    if summary_path:
        try:
            summary_dir = Path(summary_path).parent
            candidate = summary_dir / "multi_agent_events.jsonl"
            if candidate.exists():
                multi_agent_path = candidate
        except Exception:
            multi_agent_path = None
    if multi_agent_path is None:
        candidate = workspace_path / ".breadboard" / "multi_agent_events.jsonl"
        if candidate.exists():
            multi_agent_path = candidate
    multi_agent_events = _load_multi_agent_events(multi_agent_path)
    if not multi_agent_ordering and multi_agent_events:
        multi_agent_ordering = "total_event_id"

    return RunIR(
        workspace_path=workspace_path,
        completion_summary=completion_summary if isinstance(completion_summary, dict) else {},
        guard_events=guard_events if isinstance(guard_events, list) else [],
        guard_counters=guard_counters if isinstance(guard_counters, dict) else {},
        todo_journal=todo_journal,
        todo_snapshot=todo_snapshot,
        prompt_hashes=prompt_hashes if isinstance(prompt_hashes, dict) else {},
        surface_snapshot=surface_snapshot if isinstance(surface_snapshot, dict) else {},
        surface_manifest=surface_manifest if isinstance(surface_manifest, dict) else {},
        tool_usage_summary=tool_usage_summary if isinstance(tool_usage_summary, dict) else {},
        turn_tool_usage=turn_tool_usage,
        env_fingerprint=env_fingerprint if isinstance(env_fingerprint, dict) else {},
        turn_diagnostics=turn_diagnostics if isinstance(turn_diagnostics, list) else [],
        workspace_manifest=None,
        multi_agent_events=multi_agent_events,
        multi_agent_ordering=multi_agent_ordering,
        lifecycle_events=lifecycle_events if isinstance(lifecycle_events, list) else [],
    )


def _compare_summary_fields(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    for key, value in expected.items():
        if actual.get(key) != value:
            mismatches.append(f"Completion summary mismatch for {key}: actual={actual.get(key)} expected={value}")
    return mismatches


def _compare_guard_counters(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    for key, value in expected.items():
        if actual.get(key) != value:
            mismatches.append(f"Guard counter mismatch for {key}: actual={actual.get(key)} expected={value}")
    return mismatches


def _compare_todo_snapshot(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches

    def _normalize(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {}
        todos = snapshot.get("todos") or []
        order = snapshot.get("order") or []

        id_map: Dict[str, str] = {}

        def _register(raw: Any) -> None:
            if not isinstance(raw, str):
                return
            if raw in id_map:
                return
            id_map[raw] = f"todo_{len(id_map) + 1}"

        for tid in order:
            _register(tid)
        for todo in todos:
            if isinstance(todo, dict):
                _register(todo.get("id"))

        todo_by_id = {todo.get("id"): todo for todo in todos if isinstance(todo, dict)}

        def _normalize_todo(todo: Dict[str, Any]) -> Dict[str, Any]:
            normalized = dict(todo)
            normalized.pop("id", None)
            normalized.pop("created_at", None)
            normalized.pop("updated_at", None)
            normalized.pop("version", None)
            notes = normalized.get("notes")
            if isinstance(notes, list):
                cleaned_notes = []
                for note in notes:
                    if isinstance(note, dict):
                        note = dict(note)
                        note.pop("created_at", None)
                    cleaned_notes.append(note)
                normalized["notes"] = cleaned_notes
            blocked_by = normalized.get("blocked_by")
            if isinstance(blocked_by, list):
                normalized["blocked_by"] = [id_map.get(item, item) for item in blocked_by]
            return normalized

        ordered_todos: List[Dict[str, Any]] = []
        if order:
            for tid in order:
                todo = todo_by_id.get(tid)
                if isinstance(todo, dict):
                    ordered_todos.append(_normalize_todo(todo))
        else:
            ordered_todos = [_normalize_todo(todo) for todo in todos if isinstance(todo, dict)]

        normalized_order = [id_map.get(item, item) for item in order] if order else []
        return {"todos": ordered_todos, "order": normalized_order}

    actual_norm = _normalize(actual)
    expected_norm = _normalize(expected)

    for key in ("todos", "order"):
        if key in expected_norm and actual_norm.get(key) != expected_norm.get(key):
            mismatches.append(f"Todo snapshot mismatch for {key}")
    return mismatches


def _guard_types(events: List[Dict[str, Any]]) -> List[str]:
    types: List[str] = []
    for event in events:
        if isinstance(event, dict):
            types.append(str(event.get("type") or "unknown"))
    return types


def _todo_event_types(events: List[Dict[str, Any]]) -> List[str]:
    types: List[str] = []
    for event in events:
        if isinstance(event, dict):
            types.append(str(event.get("type") or "unknown"))
    return types


def compare_prompt_hashes(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches

    expected_system = expected.get("system_hash")
    if expected_system:
        if actual.get("system_hash") != expected_system:
            mismatches.append(
                f"System prompt hash mismatch: actual={actual.get('system_hash')} expected={expected_system}"
            )

    expected_turns = expected.get("per_turn_hashes") or []
    if not expected_turns:
        return mismatches

    actual_turns = actual.get("per_turn_hashes") or []

    def _to_map(entries: List[Dict[str, Any]]) -> Dict[int, str]:
        mapping: Dict[int, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            turn = entry.get("turn")
            digest = entry.get("hash")
            if isinstance(turn, int) and isinstance(digest, str):
                mapping[turn] = digest
        return mapping

    expected_map = _to_map(expected_turns if isinstance(expected_turns, list) else [])
    actual_map = _to_map(actual_turns if isinstance(actual_turns, list) else [])

    if expected_map and len(actual_map) != len(expected_map):
        mismatches.append(
            f"Per-turn prompt hash count mismatch: actual={len(actual_map)} expected={len(expected_map)}"
        )

    for turn, expected_hash in expected_map.items():
        actual_hash = actual_map.get(turn)
        if actual_hash != expected_hash:
            mismatches.append(
                f"Per-turn prompt hash mismatch (turn {turn}): actual={actual_hash} expected={expected_hash}"
            )

    return mismatches


def compare_surface_snapshots(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches

    exp_provider = expected.get("provider_tools")
    if isinstance(exp_provider, dict):
        act_provider = actual.get("provider_tools") if isinstance(actual.get("provider_tools"), dict) else {}
        for key, value in exp_provider.items():
            if act_provider.get(key) != value:
                mismatches.append(
                    f"Provider tools config mismatch for {key}: actual={act_provider.get(key)} expected={value}"
                )

    exp_mode = expected.get("tool_prompt_mode")
    if exp_mode and actual.get("tool_prompt_mode") != exp_mode:
        mismatches.append(
            f"Tool prompt mode mismatch: actual={actual.get('tool_prompt_mode')} expected={exp_mode}"
        )

    exp_catalog = expected.get("tool_catalog")
    if isinstance(exp_catalog, dict):
        act_catalog = actual.get("tool_catalog") if isinstance(actual.get("tool_catalog"), dict) else {}
        for key in ("catalog_hash", "tool_count", "tool_ids"):
            exp_val = exp_catalog.get(key)
            if exp_val is None:
                continue
            act_val = act_catalog.get(key)
            if act_val != exp_val:
                mismatches.append(
                    f"Tool catalog mismatch for {key}: actual={act_val} expected={exp_val}"
                )

    exp_roles = expected.get("system_roles")
    if exp_roles and actual.get("system_roles") != exp_roles:
        mismatches.append(
            f"System roles mismatch: actual={actual.get('system_roles')} expected={exp_roles}"
        )

    def _entry_map(entries: Any) -> Dict[int, Dict[str, Any]]:
        mapping: Dict[int, Dict[str, Any]] = {}
        if not isinstance(entries, list):
            return mapping
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            turn = entry.get("turn")
            if isinstance(turn, int):
                mapping[turn] = entry
        return mapping

    def _compare_snapshot_entries(label: str, expected_entries: Any, actual_entries: Any, fields: List[str]) -> None:
        exp_map = _entry_map(expected_entries)
        if not exp_map:
            return
        act_map = _entry_map(actual_entries)
        for turn, exp_entry in exp_map.items():
            act_entry = act_map.get(turn)
            if not act_entry:
                mismatches.append(f"Missing {label} snapshot for turn {turn}")
                continue
            for field in fields:
                exp_val = exp_entry.get(field)
                if exp_val is None:
                    continue
                act_val = act_entry.get(field)
                if act_val != exp_val:
                    mismatches.append(
                        f"{label} snapshot mismatch (turn {turn}) for {field}: actual={act_val} expected={exp_val}"
                    )

    _compare_snapshot_entries(
        "tool_schema",
        expected.get("tool_schema_snapshots"),
        actual.get("tool_schema_snapshots"),
        ["schema_hash", "schema_hash_ordered", "tool_names_sorted", "tool_names"],
    )
    _compare_snapshot_entries(
        "tool_allowlist",
        expected.get("tool_allowlist_snapshots"),
        actual.get("tool_allowlist_snapshots"),
        ["allowlist_hash", "allowlist_hash_ordered", "tool_names_sorted", "tool_names"],
    )

    return mismatches


def compare_surface_manifest(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    if not expected:
        return mismatches
    exp_surfaces = expected.get("surfaces")
    act_surfaces = actual.get("surfaces")
    if not isinstance(exp_surfaces, list):
        return mismatches
    if not isinstance(act_surfaces, list):
        mismatches.append("Surface manifest missing in actual run")
        return mismatches
    act_map: Dict[str, Dict[str, Any]] = {}
    for entry in act_surfaces:
        if isinstance(entry, dict) and entry.get("name"):
            act_map[str(entry.get("name"))] = entry
    for entry in exp_surfaces:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        act_entry = act_map.get(str(name))
        if not act_entry:
            mismatches.append(f"Missing surface manifest entry for {name}")
            continue
        exp_hash = entry.get("hash")
        act_hash = act_entry.get("hash")
        if exp_hash and act_hash != exp_hash:
            mismatches.append(
                f"Surface manifest hash mismatch for {name}: actual={act_hash} expected={exp_hash}"
            )
        exp_det = entry.get("determinism")
        if exp_det and act_entry.get("determinism") != exp_det:
            mismatches.append(
                f"Surface manifest determinism mismatch for {name}: actual={act_entry.get('determinism')} expected={exp_det}"
            )
        exp_scope = entry.get("scope")
        if exp_scope and act_entry.get("scope") != exp_scope:
            mismatches.append(
                f"Surface manifest scope mismatch for {name}: actual={act_entry.get('scope')} expected={exp_scope}"
            )
    exp_required = expected.get("required")
    if isinstance(exp_required, list) and exp_required:
        act_missing = actual.get("missing")
        if not isinstance(act_missing, list):
            mismatches.append("Surface manifest missing 'missing' list for required surfaces")
        else:
            exp_missing = expected.get("missing") or []
            if exp_missing != act_missing:
                mismatches.append(
                    f"Surface manifest missing list mismatch: actual={act_missing} expected={exp_missing}"
                )
    exp_unclassified = expected.get("unclassified")
    if isinstance(exp_unclassified, list):
        act_unclassified = actual.get("unclassified") or []
        if act_unclassified != exp_unclassified:
            mismatches.append(
                f"Surface manifest unclassified list mismatch: actual={act_unclassified} expected={exp_unclassified}"
            )
    # Enforce required surfaces declared by the actual run (E4 gating)
    act_required = actual.get("required")
    act_missing = actual.get("missing")
    if isinstance(act_required, list) and act_required and isinstance(act_missing, list) and act_missing:
        mismatches.append(f"Surface manifest required surfaces missing in actual run: {act_missing}")
    return mismatches


def compare_run_ir(lhs: RunIR, rhs: RunIR, level: EquivalenceLevel) -> List[str]:
    mismatches: List[str] = []
    ordering_model = rhs.multi_agent_ordering or lhs.multi_agent_ordering
    extra_ignore: List[str] = []
    raw_ignore = os.environ.get("BREADBOARD_PARITY_IGNORE_WORKSPACE") or os.environ.get("PARITY_IGNORE_WORKSPACE")
    if raw_ignore:
        extra_ignore = [token.strip() for token in raw_ignore.split(",") if token.strip()]

    ladder = [
        EquivalenceLevel.SEMANTIC,
        EquivalenceLevel.STRUCTURAL,
        EquivalenceLevel.NORMALIZED_TRACE,
        EquivalenceLevel.BITWISE_TRACE,
    ]
    try:
        max_index = ladder.index(level)
    except ValueError:
        max_index = 0

    def _do_semantic() -> None:
        ignore_workspace = list(dict.fromkeys(list(rhs.ignore_workspace) + extra_ignore))
        if lhs.workspace_manifest and rhs.workspace_manifest:
            mismatches.extend(compare_workspace_manifests(lhs.workspace_manifest, rhs.workspace_manifest, ignore_workspace))
        else:
            mismatches.extend(compare_workspace_files(lhs.workspace_path, rhs.workspace_path, ignore_workspace))
        mismatches.extend(_compare_summary_fields(lhs.completion_summary, rhs.completion_summary))
        mismatches.extend(_compare_guard_counters(lhs.guard_counters, rhs.guard_counters))
        mismatches.extend(_compare_todo_snapshot(lhs.todo_snapshot, rhs.todo_snapshot))

    def _do_structural() -> None:
        if rhs.guard_events:
            if _guard_types(lhs.guard_events) != _guard_types(rhs.guard_events):
                mismatches.append("Guardrail event type sequence mismatch")
        if rhs.todo_journal or lhs.todo_journal:
            if _todo_event_types(lhs.todo_journal) != _todo_event_types(rhs.todo_journal):
                mismatches.append("Todo journal event type sequence mismatch")
        if rhs.prompt_hashes:
            mismatches.extend(compare_prompt_hashes(lhs.prompt_hashes, rhs.prompt_hashes))
        if rhs.surface_snapshot:
            mismatches.extend(compare_surface_snapshots(lhs.surface_snapshot, rhs.surface_snapshot))
        if rhs.surface_manifest:
            mismatches.extend(compare_surface_manifest(lhs.surface_manifest, rhs.surface_manifest))
        if rhs.tool_usage_summary or lhs.tool_usage_summary:
            mismatches.extend(compare_tool_usage_summary(lhs.tool_usage_summary, rhs.tool_usage_summary))
        if rhs.turn_tool_usage or lhs.turn_tool_usage:
            mismatches.extend(compare_turn_tool_usage(lhs.turn_tool_usage, rhs.turn_tool_usage))
        if rhs.multi_agent_ordering and lhs.multi_agent_ordering != rhs.multi_agent_ordering:
            mismatches.append(
                f"Multi-agent ordering mismatch: actual={lhs.multi_agent_ordering} expected={rhs.multi_agent_ordering}"
            )
        if rhs.multi_agent_events:
            actual_norm = _normalize_multi_agent_events(
                lhs.multi_agent_events,
                [str(lhs.workspace_path)],
                ordering_model=ordering_model,
            )
            expected_norm = _normalize_multi_agent_events(
                rhs.multi_agent_events,
                [str(rhs.workspace_path)],
                ordering_model=ordering_model,
            )
            if [e.get("type") for e in actual_norm] != [e.get("type") for e in expected_norm]:
                mismatches.append("Multi-agent event type sequence mismatch")
        if rhs.lifecycle_events:
            actual_types = [e.get("type") for e in lhs.lifecycle_events if isinstance(e, dict)]
            expected_types = [e.get("type") for e in rhs.lifecycle_events if isinstance(e, dict)]
            if actual_types != expected_types:
                mismatches.append("Lifecycle event type sequence mismatch")

    def _do_normalized() -> None:
        if rhs.guard_events or lhs.guard_events:
            mismatches.extend(compare_guardrail_events(sanitize_guardrail_events(lhs.guard_events), sanitize_guardrail_events(rhs.guard_events)))
        if rhs.todo_journal or lhs.todo_journal:
            mismatches.extend(compare_todo_journal(lhs.todo_journal, rhs.todo_journal))
        if rhs.multi_agent_events:
            actual_norm = _normalize_multi_agent_events(
                lhs.multi_agent_events,
                [str(lhs.workspace_path)],
                ordering_model=ordering_model,
            )
            expected_norm = _normalize_multi_agent_events(
                rhs.multi_agent_events,
                [str(rhs.workspace_path)],
                ordering_model=ordering_model,
            )
            mismatches.extend(compare_multi_agent_events(actual_norm, expected_norm))
        if rhs.lifecycle_events:
            mismatches.extend(compare_lifecycle_events(lhs.lifecycle_events, rhs.lifecycle_events))

    def _do_bitwise() -> None:
        if (rhs.guard_events or lhs.guard_events) and lhs.guard_events != rhs.guard_events:
            mismatches.append("Guardrail event trace mismatch (bitwise)")
        if (rhs.todo_journal or lhs.todo_journal) and lhs.todo_journal != rhs.todo_journal:
            mismatches.append("Todo journal trace mismatch (bitwise)")
        if rhs.multi_agent_events:
            actual_norm = _normalize_multi_agent_events(
                lhs.multi_agent_events,
                [str(lhs.workspace_path)],
                ordering_model=ordering_model,
            )
            expected_norm = _normalize_multi_agent_events(
                rhs.multi_agent_events,
                [str(rhs.workspace_path)],
                ordering_model=ordering_model,
            )
            if actual_norm != expected_norm:
                mismatches.append("Multi-agent event trace mismatch (bitwise)")

    steps = [_do_semantic, _do_structural, _do_normalized, _do_bitwise]
    for idx, step in enumerate(steps):
        if idx > max_index:
            break
        step()
        if mismatches:
            break

    return mismatches
