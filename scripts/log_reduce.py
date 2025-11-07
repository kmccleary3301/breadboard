"""CLI utility for summarising KyleCode logging directories.

Provides a tree-style directory overview and a truncated view of raw
request/response payloads so that long LLM transcripts remain readable.

Usage examples:
    python scripts/log_reduce.py logging/20250925-233436_agent_ws_opencode
    python scripts/log_reduce.py logging/latest --turn-limit 3 --max-length 80

Future work: plug in an LLM summariser for condensed conversation blocks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reduce and inspect KyleCode logs")
    parser.add_argument(
        "log_dir",
        help="Path to a logging run directory (e.g. logging/20250925-233436_agent_ws_opencode)",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=120,
        help="Maximum characters to display for any string field (default: 120)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=25,
        help="Maximum lines shown per content block before truncation (default: 25)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Maximum depth for the directory tree output (default: 2)",
    )
    parser.add_argument(
        "--turn-limit",
        type=int,
        default=None,
        help="Limit the number of turns displayed from the conversation",
    )
    parser.add_argument(
        "--include-roles",
        type=str,
        default=None,
        help="Comma-separated list of roles to include (e.g. system,user,assistant)",
    )
    parser.add_argument(
        "--skip-roles",
        type=str,
        default=None,
        help="Comma-separated list of roles to exclude",
    )
    parser.add_argument(
        "--tool-only",
        action="store_true",
        help="Show only per-turn summary, tool calls, and tool results",
    )
    parser.add_argument(
        "--show-diffs",
        action="store_true",
        help="Display full diff content instead of summarised patch summaries",
    )
    parser.add_argument(
        "--skip-tree",
        action="store_true",
        help="Suppress the directory tree output",
    )
    parser.add_argument(
        "--skip-conversation",
        action="store_true",
        help="Suppress the conversation summary output",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Tree rendering helpers
# ---------------------------------------------------------------------------

def print_tree(root: Path, max_depth: int) -> None:
    print("== Directory Tree ==")
    if not root.exists():
        print(f"[missing] {root}")
        return
    _print_tree(root, prefix="", depth=0, max_depth=max_depth)


def _print_tree(path: Path, prefix: str, depth: int, max_depth: int) -> None:
    if depth == 0:
        print(path)
    if depth >= max_depth:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        print(f"{prefix} [error opening directory: {exc}]")
        return

    count = len(entries)
    for index, entry in enumerate(entries):
        connector = "└──" if index == count - 1 else "├──"
        line_prefix = f"{prefix}{connector} "
        display_name = entry.name
        if entry.is_file():
            size = _format_size(entry.stat().st_size)
            print(f"{line_prefix}{display_name} ({size})")
        else:
            print(f"{line_prefix}{display_name}/")
            extension_prefix = f"{prefix}{'    ' if index == count - 1 else '│   '}"
            _print_tree(entry, extension_prefix, depth + 1, max_depth)


def _format_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


# ---------------------------------------------------------------------------
# Conversation summariser
# ---------------------------------------------------------------------------

def summarise_conversation(
    root: Path,
    max_length: int,
    max_lines: int,
    turn_limit: Optional[int],
    include_roles: Optional[Set[str]],
    skip_roles: Set[str],
    tool_only: bool,
    show_diffs: bool,
) -> None:
    print("== Conversation Summary ==")

    requests_dir = root / "raw" / "requests"
    responses_dir = root / "raw" / "responses"
    if not requests_dir.exists():
        print("[no raw/requests directory found]")
        return

    request_files = sorted(requests_dir.glob("turn_*.request.json"), key=_turn_key)
    if turn_limit is not None:
        request_files = request_files[:turn_limit]

    seen_fingerprints: Dict[str, str] = {}
    tool_synopsis: Dict[str, int] = {}
    error_turns_seen: Set[int] = set()

    for request_file in request_files:
        turn = _extract_turn(request_file.name)
        request_payload = _load_json(request_file)
        if request_payload is None:
            continue

        model = request_payload.get("model") or request_payload.get("provider", {}).get("model_id")
        provider = request_payload.get("provider", {}).get("name")
        meta_bits = [bit for bit in (model, provider) if bit]
        meta = f" ({', '.join(meta_bits)})" if meta_bits else ""
        print(f"\nTurn {turn:03d} — Request{meta}")

        response_file = responses_dir / f"turn_{turn}.response.json"
        response_payload = _load_json(response_file) if response_file.exists() else None

        tool_dir = root / "artifacts" / "tool_results" / f"turn_{turn}"
        summary = _gather_turn_summary(
            request_payload.get("messages", []),
            response_payload,
            tool_dir,
            max_length,
            show_diffs,
        )
        error_info = _load_error(root, turn)
        if error_info:
            summary["error_info"] = error_info
            error_turns_seen.add(turn)
        _print_turn_summary(summary)
        for name in summary.get("tool_call_names", []):
            tool_synopsis[name] = tool_synopsis.get(name, 0) + 1

        if not tool_only:
            _print_messages(
                request_payload.get("messages", []),
                max_length,
                max_lines,
                include_roles,
                skip_roles,
                seen_fingerprints,
                show_diffs,
            )
        else:
            if summary.get("user_preview"):
                print(f"  user: {summary['user_preview']}")
            if summary.get("assistant_preview"):
                print(f"  assistant: {summary['assistant_preview']}")

        if response_payload is not None:
            _print_response_summary(
                response_payload,
                max_length,
                max_lines,
                show_content=not tool_only,
                show_diffs=show_diffs,
            )

        _print_tool_results(tool_dir, max_length, max_lines)

    if tool_synopsis:
        print("\n== Tool Synopsis ==")
        for name, count in sorted(tool_synopsis.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {name}: {count} turn(s)")

    extra_errors = _load_unmatched_errors(root, error_turns_seen)
    if extra_errors:
        print("\n== Provider Errors ==")
        for turn, info in extra_errors:
            error_type = info.get("error_type") or "error"
            error_msg = info.get("error", "")
            short = error_msg.splitlines()[0].strip() if error_msg else ""
            print(f"  turn {turn}: {error_type} {short}")


def _turn_key(path: Path) -> int:
    return _extract_turn(path.name)


def _extract_turn(filename: str) -> int:
    digits = ''.join(ch for ch in filename if ch.isdigit())
    return int(digits or 0)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [error reading {path}: {exc}]")
        return None


def _print_messages(
    messages: Iterable[Dict[str, Any]],
    max_length: int,
    max_lines: int,
    include_roles: Optional[Set[str]],
    skip_roles: Set[str],
    seen_fingerprints: Dict[str, str],
    show_diffs: bool,
) -> None:
    for message in messages:
        role = message.get("role", "unknown")
        role_key = role.lower()

        if include_roles and role_key not in include_roles:
            continue
        if role_key in skip_roles:
            continue

        fingerprint = _fingerprint_message(message)
        if seen_fingerprints.get(role_key) == fingerprint:
            print(f"  role={role} (same as previous)")
            continue

        seen_fingerprints[role_key] = fingerprint
        print(f"  role={role}")
        content = message.get("content")
        if content is None:
            continue
        lines = _render_content(content, max_length, show_diffs)
        if role_key != "assistant" and not show_diffs:
            if any(line.startswith(prefix) for line in lines for prefix in ("patch ", "conflict ")):
                lines = _render_content(content, max_length, show_diffs=True)
        _emit_lines(lines, max_lines, indent="    ")


def _render_content(content: Any, max_length: int, show_diffs: bool) -> List[str]:
    if isinstance(content, str):
        if not show_diffs and "<<<<<<<" in content and ">>>>>>>" in content:
            summary = _summarize_conflict(content)
            if summary:
                return summary
        if not show_diffs and "*** Begin Patch" in content:
            summary = _summarize_patch(content)
            if summary:
                return summary
        return _wrap_lines(content, max_length)
    if isinstance(content, list):
        rendered: List[str] = []
        for idx, part in enumerate(content):
            header = f"part[{idx}]"
            if isinstance(part, dict) and part.get("type"):
                header += f" type={part['type']}"
            rendered.append(header + ":")
            nested = ["  " + line for line in _render_content(part, max_length, show_diffs)]
            rendered.extend(nested)
        return rendered
    if isinstance(content, dict):
        if content.get("type") == "text" and "text" in content:
            text = content.get("text", "")
            if not show_diffs and "<<<<<<<" in text and ">>>>>>>" in text:
                summary = _summarize_conflict(text)
                if summary:
                    return summary
            if not show_diffs and "*** Begin Patch" in text:
                summary = _summarize_patch(text)
                if summary:
                    return summary
            return _wrap_lines(text, max_length)
        if content.get("type") == "tool_use":
            summary = {
                k: _truncate_string(v, max_length)
                for k, v in content.items()
                if k not in {"input", "status"}
            }
            lines = [f"tool_use: {json.dumps(summary, ensure_ascii=False)}"]
            if "input" in content:
                input_summary = _truncate_json(content["input"], max_length)
                lines.append(f"tool_input: {json.dumps(input_summary, ensure_ascii=False)}")
            if "status" in content:
                lines.append(f"status: {_truncate_string(content['status'], max_length)}")
            return lines
        truncated = _truncate_json(content, max_length)
        return json.dumps(truncated, ensure_ascii=False, indent=2).splitlines()
    return [repr(content)]


def _print_response_summary(
    payload: Dict[str, Any],
    max_length: int,
    max_lines: int,
    show_content: bool,
    show_diffs: bool,
) -> None:
    choices = payload.get("choices") or []
    if not choices:
        print("  [no response choices]")
        return

    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    print(f"  response finish_reason={finish_reason}")
    message = choice.get("message", {})
    role = message.get("role", "assistant")
    print(f"  role={role}")
    content = message.get("content")
    if content is not None:
        if show_content:
            lines = _render_content(content, max_length, show_diffs)
            _emit_lines(lines, max_lines, indent="    ")
        else:
            preview = _preview_lines(content, max_length, show_diffs)
            if preview:
                print(f"    {preview}")
    tool_calls = message.get("tool_calls") or []
    for call in tool_calls:
        name = call.get("function", {}).get("name") or call.get("name")
        args = call.get("function", {}).get("arguments") or call.get("arguments")
        print(f"    tool_call name={name}")
        if args:
            truncated = _truncate_string(args, max_length) if isinstance(args, str) else json.dumps(
                _truncate_json(args, max_length), ensure_ascii=False
            )
            lines = _wrap_lines(truncated, max_length)
            _emit_lines(lines, max_lines, indent="      ")


def _print_tool_results(tool_dir: Path, max_length: int, max_lines: int) -> None:
    if not tool_dir.exists():
        return
    files = sorted(tool_dir.iterdir())
    if not files:
        return
    print("  tool_results:")
    for entry in files:
        try:
            with entry.open("r", encoding="utf-8", errors="replace") as handle:
                snippet = handle.read(max_length * 2)
        except OSError as exc:
            print(f"    {entry.name}: [error reading file: {exc}]")
            continue
        first_line = snippet.strip().splitlines()[0] if snippet.strip() else ""
        text = _truncate_string(first_line, max_length)
        _emit_lines([text], max_lines, indent="    ")


# ---------------------------------------------------------------------------
# Truncation utilities
# ---------------------------------------------------------------------------

def _truncate_string(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return str(value)
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _truncate_json(obj: Any, max_length: int) -> Any:
    if isinstance(obj, str):
        return _truncate_string(obj, max_length)
    if isinstance(obj, list):
        return [_truncate_json(item, max_length) for item in obj]
    if isinstance(obj, dict):
        return {key: _truncate_json(value, max_length) for key, value in obj.items()}
    return obj


def _wrap_lines(text: str, max_length: int) -> List[str]:
    lines: List[str] = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.rstrip()
        if not line:
            lines.append("")
            continue
        while len(line) > max_length:
            lines.append(line[: max_length - 1] + "…")
            line = line[max_length - 1 :]
        lines.append(line)
    return lines


def _emit_lines(lines: List[str], max_lines: int, indent: str) -> None:
    if not lines:
        return
    if max_lines is None or len(lines) <= max_lines:
        for line in lines:
            print(f"{indent}{line}")
        return

    visible = max_lines - 1 if max_lines > 1 else 1
    for line in lines[:visible]:
        print(f"{indent}{line}")
    remaining = len(lines) - visible
    print(f"{indent}… ({remaining} more line(s) truncated)")


def _summarize_patch(content: str) -> List[str]:
    additions: List[str] = []
    updates: List[str] = []
    deletions: List[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("*** Add File:"):
            target = stripped.split(":", 1)[1].strip()
            additions.append(target)
        elif stripped.startswith("*** Update File:"):
            target = stripped.split(":", 1)[1].strip()
            updates.append(target)
        elif stripped.startswith("*** Delete File:"):
            target = stripped.split(":", 1)[1].strip()
            deletions.append(target)
    summary: List[str] = []
    for name in additions[:10]:
        summary.append(f"patch add {name}")
    for name in updates[:10]:
        summary.append(f"patch update {name}")
    for name in deletions[:10]:
        summary.append(f"patch delete {name}")
    if not summary:
        summary.append("patch (content truncated)")
    return summary


def summarise_guardrails(root: Path) -> None:
    """Highlight guardrail triggers surfaced during the run."""
    convo_path = root / "conversation" / "conversation.md"
    guard_counts = {
        "validation_errors": 0,
        "zero_tool_warnings": 0,
        "zero_tool_abort": 0,
        "patch_policy_violations": 0,
        "shell_write_blocked": 0,
    }
    if convo_path.exists():
        try:
            text = convo_path.read_text(encoding="utf-8", errors="ignore")
            guard_counts["validation_errors"] = text.count("<VALIDATION_ERROR>")
            lower_text = text.lower()
            guard_counts["zero_tool_warnings"] = (
                lower_text.count("zero-tool warning")
                + lower_text.count("no tool usage detected. use read/list/diff/bash tools")
            )
            guard_counts["zero_tool_abort"] = (
                lower_text.count("zero tool usage streak")
                + lower_text.count("no tool usage detected across multiple turns")
            )
            guard_counts["patch_policy_violations"] = (
                lower_text.count("patch_policy_violation")
                + lower_text.count("multi-file patches are disabled")
            )
            guard_counts["shell_write_blocked"] = (
                lower_text.count("use the write/patch tools instead")
                + lower_text.count("do not use bash heredocs")
            )
        except Exception as exc:
            print(f"[guardrails] unable to read conversation: {exc}")

    summary_path = root / "meta" / "run_summary.json"
    artifact_issues: Optional[List[str]] = None
    payload = _load_json(summary_path)
    if isinstance(payload, dict):
        artifact_issues = payload.get("artifact_issues") or payload.get("artifactIssues")
        guard_metadata = payload.get("guardrails") or {}
        for key, value in guard_metadata.items():
            if isinstance(value, int) and key in guard_counts:
                guard_counts[key] = value

    print("\n== Guardrail Signals ==")
    for key, value in guard_counts.items():
        label = key.replace("_", " ")
        print(f"- {label}: {value}")
    if artifact_issues:
        print("- artifact issues:")
        for issue in artifact_issues:
            print(f"    • {issue}")


def _summarize_conflict(content: str) -> List[str]:
    lines = content.splitlines()
    summary: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("<<<<<<"):
            # find filename by looking backwards for first non-empty line
            filename = "unknown"
            for j in range(i - 1, -1, -1):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("<"):
                    filename = candidate
                    break
            parts = line.split()
            kind = parts[1] if len(parts) > 1 else "conflict"
            summary.append(f"conflict {filename} ({kind})")
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                i += 1
        i += 1
    if not summary:
        summary.append("conflict (content truncated)")
    return summary


def _load_error(root: Path, turn: int) -> Optional[Dict[str, Any]]:
    error_dir = root / "errors"
    if not error_dir.exists():
        return None
    path = error_dir / f"turn_{turn}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _load_unmatched_errors(root: Path, seen_turns: Set[int]) -> List[tuple[int, Dict[str, Any]]]:
    error_dir = root / "errors"
    if not error_dir.exists():
        return []
    extras: List[tuple[int, Dict[str, Any]]] = []
    for path in sorted(error_dir.glob("turn_*.json"), key=lambda p: _extract_turn(p.name)):
        turn = _extract_turn(path.name)
        if turn in seen_turns:
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                info = json.load(handle)
            extras.append((turn, info))
        except (OSError, json.JSONDecodeError):
            continue
    return extras


def _preview_lines(content: Any, max_length: int, show_diffs: bool) -> Optional[str]:
    lines = _render_content(content, max_length, show_diffs)
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.startswith("<") and cleaned.endswith(">"):
            continue
        if cleaned:
            return cleaned
    return None


def _fingerprint_message(message: Dict[str, Any]) -> str:
    data = json.dumps(message, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _parse_role_list(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    roles = {item.strip().lower() for item in value.split(",") if item.strip()}
    return roles or None


def _content_to_plaintext(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_content_to_plaintext(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "text" and "text" in content:
            return str(content.get("text", ""))
        parts = []
        for value in content.values():
            parts.append(_content_to_plaintext(value))
        return "\n".join(parts)
    return str(content)


def _detect_validation_error(messages: Iterable[Dict[str, Any]]) -> bool:
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            content = _content_to_plaintext(message.get("content"))
            lowered = content.lower()
            return "<validation_error>" in lowered or "validation failed" in lowered
    return False


_TOOL_CALL_BLOCK_RE = re.compile(r"<TOOL_CALL>(.*?)</TOOL_CALL>", re.DOTALL | re.IGNORECASE)


def _extract_text_tool_calls(text: str) -> List[str]:
    names: List[str] = []
    for match in _TOOL_CALL_BLOCK_RE.finditer(text):
        inside = match.group(1).strip()
        if not inside:
            continue
        token = inside.split()[0]
        name = token.split("(")[0]
        if name:
            names.append(name)
    if "*** Begin Patch" in text:
        names.append("apply_patch")
    if "<BASH>" in text:
        names.append("run_shell")
    if "<BASH_RESULT>" in text and "run_shell" not in names:
        names.append("run_shell")
    return names


def _gather_turn_summary(
    request_messages: Iterable[Dict[str, Any]],
    response_payload: Optional[Dict[str, Any]],
    tool_dir: Path,
    max_length: int,
    show_diffs: bool,
) -> Dict[str, Any]:
    request_messages_list = list(request_messages)
    summary: Dict[str, Any] = {
        "tool_call_count": 0,
        "tool_call_names": [],
        "tool_results_count": 0,
        "validation_error": _detect_validation_error(request_messages_list),
    }

    if response_payload:
        choices = response_payload.get("choices") or []
        if choices:
            choice = choices[0]
            summary["finish_reason"] = choice.get("finish_reason")
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls") or []
            summary["tool_call_count"] = len(tool_calls)
            summary["tool_call_names"] = [
                call.get("function", {}).get("name") or call.get("name")
                for call in tool_calls
            ]
            content = message.get("content")
            if content is not None:
                preview = _preview_lines(content, max_length, show_diffs)
                if preview:
                    summary["assistant_preview"] = preview
        usage = response_payload.get("usage") or {}
        summary["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if usage.get(key) is not None
        }

    text_tool_calls: List[str] = []
    for message in request_messages_list:
        if message.get("role") == "assistant":
            text = _content_to_plaintext(message.get("content"))
            text_tool_calls.extend(_extract_text_tool_calls(text))

    if text_tool_calls:
        existing = summary.get("tool_call_names", [])
        merged = existing + text_tool_calls
        dedup: List[str] = []
        for name in merged:
            if name and name not in dedup:
                dedup.append(name)
        summary["tool_call_names"] = dedup
        summary["tool_call_count"] = len(dedup)

    tool_entries = [entry for entry in tool_dir.iterdir() if entry.is_file()] if tool_dir.exists() else []
    summary["tool_results_count"] = len(tool_entries)

    for message in reversed(request_messages_list):
        if message.get("role") == "user":
            preview = _preview_lines(message.get("content"), max_length, show_diffs)
            if preview:
                summary["user_preview"] = preview
            break

    return summary


def _print_turn_summary(summary: Dict[str, Any]) -> None:
    finish = summary.get("finish_reason") or "?"
    tool_calls = summary.get("tool_call_count", 0)
    tool_names = [name for name in summary.get("tool_call_names", []) if name]
    tool_results = summary.get("tool_results_count", 0)
    validation = "yes" if summary.get("validation_error") else "no"
    usage = summary.get("usage", {})

    usage_bits = []
    if usage.get("prompt_tokens") is not None:
        usage_bits.append(f"prompt={usage['prompt_tokens']}")
    if usage.get("completion_tokens") is not None:
        usage_bits.append(f"completion={usage['completion_tokens']}")
    if usage.get("total_tokens") is not None:
        usage_bits.append(f"total={usage['total_tokens']}")

    usage_str = f" tokens({', '.join(usage_bits)})" if usage_bits else ""
    tool_part = f" tool_calls={tool_calls}"
    if tool_names:
        tool_part += f" [{', '.join(tool_names)}]"
    tool_res_part = f" tool_results={tool_results}"
    validation_part = f" validation_error={validation}"
    print(f"  summary: finish={finish}{tool_part}{tool_res_part}{validation_part}{usage_str}")
    error_info = summary.get("error_info")
    if error_info:
        error_type = error_info.get("error_type") or "error"
        error_msg = error_info.get("error") or ""
        short = error_msg.splitlines()[0].strip() if error_msg else ""
        print(f"  provider_error: {error_type} {short}")
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    root = Path(args.log_dir).expanduser().resolve()
    include_roles = _parse_role_list(args.include_roles)
    skip_roles = _parse_role_list(args.skip_roles) or set()
    tool_only = args.tool_only
    show_diffs = args.show_diffs
    if not args.skip_tree:
        print_tree(root, max_depth=args.max_depth)
    if not args.skip_conversation:
        summarise_conversation(
            root,
            max_length=args.max_length,
            max_lines=args.max_lines,
            turn_limit=args.turn_limit,
            include_roles=include_roles,
            skip_roles=skip_roles,
            tool_only=tool_only,
            show_diffs=show_diffs,
        )
    summarise_guardrails(root)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
