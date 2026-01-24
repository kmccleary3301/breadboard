#!/usr/bin/env python3
"""Render a compact transcript from session.jsonl."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.state.session_jsonl import SessionJsonlStore, SessionTree


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def _render_message(entry: Dict[str, Any], *, max_chars: int) -> str:
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    role = message.get("role") or "unknown"
    content = message.get("content") or ""
    text = _truncate(str(content), max_chars)
    return f"**{role}**: {text}"


def _render_compaction(entry: Dict[str, Any], *, max_chars: int) -> str:
    summary = _truncate(str(entry.get("summary") or ""), max_chars)
    return f"**compaction**: {summary}"


def _render_branch(entry: Dict[str, Any], *, max_chars: int) -> str:
    summary = _truncate(str(entry.get("summary") or ""), max_chars)
    from_id = entry.get("from_id")
    suffix = f" (from {from_id})" if from_id else ""
    return f"**branch_summary**: {summary}{suffix}"


def _render_tool_call(entry: Dict[str, Any], *, max_chars: int) -> str:
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    name = data.get("tool_name") or data.get("tool") or "tool"
    args_text = data.get("args_text") or ""
    text = _truncate(str(args_text), max_chars)
    return f"**tool_call** `{name}`: {text}" if text else f"**tool_call** `{name}`"


def _iter_context_entries(store: SessionJsonlStore) -> List[Dict[str, Any]]:
    entries = list(store.iter_entries())
    if not entries:
        return []
    tree = SessionTree(entries)
    return tree.build_context_path()


def render_transcript(
    entries: Iterable[Dict[str, Any]],
    *,
    max_chars: int,
    include_custom: bool,
    include_tool_calls: bool,
) -> List[str]:
    lines: List[str] = []
    for entry in entries:
        entry_type = entry.get("type")
        if entry_type == "message":
            lines.append(_render_message(entry, max_chars=max_chars))
        elif entry_type == "compaction":
            lines.append(_render_compaction(entry, max_chars=max_chars))
        elif entry_type == "branch_summary":
            lines.append(_render_branch(entry, max_chars=max_chars))
        elif entry_type == "custom" and include_custom:
            if entry.get("custom_type") == "tool_call" and include_tool_calls:
                lines.append(_render_tool_call(entry, max_chars=max_chars))
            elif include_tool_calls:
                custom_type = entry.get("custom_type") or "custom"
                lines.append(f"**{custom_type}**")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a compact transcript from session.jsonl.")
    parser.add_argument("session_jsonl", help="Path to session.jsonl")
    parser.add_argument("--max-chars", type=int, default=240, help="Max chars per entry (default: 240)")
    parser.add_argument(
        "--include-custom",
        action="store_true",
        help="Include custom entries (tool calls, etc.)",
    )
    parser.add_argument(
        "--include-tool-calls",
        action="store_true",
        help="Include tool call custom entries",
    )
    parser.add_argument("--out", help="Optional output path (markdown)")
    args = parser.parse_args()

    store = SessionJsonlStore(Path(args.session_jsonl))
    entries = _iter_context_entries(store)
    lines = render_transcript(
        entries,
        max_chars=max(args.max_chars, 80),
        include_custom=bool(args.include_custom),
        include_tool_calls=bool(args.include_tool_calls),
    )

    text = "\n\n".join(lines) + ("\n" if lines else "")
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
