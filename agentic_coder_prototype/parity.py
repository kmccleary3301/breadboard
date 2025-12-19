from __future__ import annotations

import json
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
    tool_usage_summary: Dict[str, Any] = field(default_factory=dict)
    turn_tool_usage: List[Dict[str, Any]] = field(default_factory=list)
    env_fingerprint: Dict[str, Any] = field(default_factory=dict)
    turn_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    workspace_manifest: Optional[Dict[str, Any]] = None
    ignore_workspace: List[str] = field(
        default_factory=lambda: [".kyle", ".git", "summary.json", ".claude", "session_result.json"]
    )


def _load_workspace_manifest(run_dir: Path) -> Optional[Dict[str, Any]]:
    manifest_path = run_dir / "meta" / "workspace.manifest.json"
    if not manifest_path.exists():
        return None
    return _load_optional_json(manifest_path)


def _load_todo_snapshot(workspace_path: Path) -> Dict[str, Any]:
    todo_path = workspace_path / ".kyle" / "todos.json"
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
    if not workspace_override and not workspace_path.exists():
        hint = summary.get("workspace_path")
        if isinstance(hint, str) and hint:
            candidate = Path(hint)
            if candidate.exists():
                workspace_path = candidate

    guard_events = summary.get("guardrail_events") or []
    guard_counters = summary.get("guardrails") or {}
    completion_summary = summary.get("completion_summary") or {}
    prompt_hashes = summary.get("prompt_hashes") or {}
    tool_usage_summary = summary.get("tool_usage_summary") or summary.get("tool_usage") or {}
    turn_tool_usage = summary.get("turn_tool_usage") or []
    env_fingerprint = summary.get("env_fingerprint") or {}
    turn_diagnostics = summary.get("turn_diagnostics") or []

    todo_snapshot = _load_todo_snapshot(workspace_path)
    todo_journal = _extract_todo_journal(todo_snapshot)
    workspace_manifest = _load_workspace_manifest(run_dir)

    return RunIR(
        workspace_path=workspace_path,
        completion_summary=completion_summary if isinstance(completion_summary, dict) else {},
        guard_events=guard_events if isinstance(guard_events, list) else [],
        guard_counters=guard_counters if isinstance(guard_counters, dict) else {},
        todo_journal=todo_journal,
        todo_snapshot=todo_snapshot,
        prompt_hashes=prompt_hashes if isinstance(prompt_hashes, dict) else {},
        tool_usage_summary=tool_usage_summary if isinstance(tool_usage_summary, dict) else {},
        turn_tool_usage=turn_tool_usage if isinstance(turn_tool_usage, list) else [],
        env_fingerprint=env_fingerprint if isinstance(env_fingerprint, dict) else {},
        turn_diagnostics=turn_diagnostics if isinstance(turn_diagnostics, list) else [],
        workspace_manifest=workspace_manifest if isinstance(workspace_manifest, dict) else None,
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
    prompt_hashes = summary.get("prompt_hashes") or {}
    tool_usage_summary = summary.get("tool_usage_summary") or summary.get("tool_usage") or {}
    turn_tool_usage = summary.get("turn_tool_usage") or []
    env_fingerprint = summary.get("env_fingerprint") or {}
    turn_diagnostics = summary.get("turn_diagnostics") or []

    todo_snapshot = _load_todo_snapshot(workspace_path)
    todo_journal: List[Dict[str, Any]] = []
    if todo_journal_path:
        todo_journal = load_todo_expectations(todo_journal_path)
    else:
        todo_journal = _extract_todo_journal(todo_snapshot)

    return RunIR(
        workspace_path=workspace_path,
        completion_summary=completion_summary if isinstance(completion_summary, dict) else {},
        guard_events=guard_events if isinstance(guard_events, list) else [],
        guard_counters=guard_counters if isinstance(guard_counters, dict) else {},
        todo_journal=todo_journal,
        todo_snapshot=todo_snapshot,
        prompt_hashes=prompt_hashes if isinstance(prompt_hashes, dict) else {},
        tool_usage_summary=tool_usage_summary if isinstance(tool_usage_summary, dict) else {},
        turn_tool_usage=turn_tool_usage if isinstance(turn_tool_usage, list) else [],
        env_fingerprint=env_fingerprint if isinstance(env_fingerprint, dict) else {},
        turn_diagnostics=turn_diagnostics if isinstance(turn_diagnostics, list) else [],
        workspace_manifest=None,
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


def compare_run_ir(lhs: RunIR, rhs: RunIR, level: EquivalenceLevel) -> List[str]:
    mismatches: List[str] = []

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
        if lhs.workspace_manifest and rhs.workspace_manifest:
            mismatches.extend(compare_workspace_manifests(lhs.workspace_manifest, rhs.workspace_manifest, rhs.ignore_workspace))
        else:
            mismatches.extend(compare_workspace_files(lhs.workspace_path, rhs.workspace_path, rhs.ignore_workspace))
        mismatches.extend(_compare_summary_fields(lhs.completion_summary, rhs.completion_summary))
        mismatches.extend(_compare_guard_counters(lhs.guard_counters, rhs.guard_counters))
        mismatches.extend(_compare_todo_snapshot(lhs.todo_snapshot, rhs.todo_snapshot))

    def _do_structural() -> None:
        if rhs.guard_events:
            if _guard_types(lhs.guard_events) != _guard_types(rhs.guard_events):
                mismatches.append("Guardrail event type sequence mismatch")
        if rhs.todo_journal:
            if _todo_event_types(lhs.todo_journal) != _todo_event_types(rhs.todo_journal):
                mismatches.append("Todo journal event type sequence mismatch")

    def _do_normalized() -> None:
        if rhs.guard_events:
            mismatches.extend(compare_guardrail_events(sanitize_guardrail_events(lhs.guard_events), sanitize_guardrail_events(rhs.guard_events)))
        if rhs.todo_journal:
            mismatches.extend(compare_todo_journal(lhs.todo_journal, rhs.todo_journal))

    def _do_bitwise() -> None:
        if rhs.guard_events and lhs.guard_events != rhs.guard_events:
            mismatches.append("Guardrail event trace mismatch (bitwise)")
        if rhs.todo_journal and lhs.todo_journal != rhs.todo_journal:
            mismatches.append("Todo journal trace mismatch (bitwise)")

    steps = [_do_semantic, _do_structural, _do_normalized, _do_bitwise]
    for idx, step in enumerate(steps):
        if idx > max_index:
            break
        step()
        if mismatches:
            break

    return mismatches
