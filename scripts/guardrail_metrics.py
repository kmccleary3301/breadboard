#!/usr/bin/env python3
"""
Extract guardrail counters/events from BreadBoard Logging v2 runs.

Example:
    python scripts/guardrail_metrics.py logging/20251111-222024_tmp_guard_inspect
    python scripts/guardrail_metrics.py logging/*agent_ws* --format jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

COLUMNS = [
    "run_dir",
    "exit_kind",
    "steps_taken",
    "zero_tool_warnings",
    "zero_tool_abort",
    "shell_write_blocked",
    "todo_plan_violation",
    "completion_guard_blocks",
    "total_guard_events",
    "event_types",
]


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _collect_metrics(run_dir: Path) -> Dict[str, Any]:
    summary_path = run_dir / "meta" / "run_summary.json"
    payload = _load_json(summary_path) or {}
    guard_section = payload.get("guardrails") or {}
    event_section = payload.get("guardrail_events") or []
    event_counts: Dict[str, int] = {}
    if isinstance(event_section, list):
        for event in event_section:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "unknown")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
    data = {
        "run_dir": str(run_dir),
        "exit_kind": payload.get("exit_kind", "unknown"),
        "steps_taken": payload.get("steps_taken", 0),
        "zero_tool_warnings": int(guard_section.get("zero_tool_warnings", 0)),
        "zero_tool_abort": int(guard_section.get("zero_tool_abort", 0)),
        "shell_write_blocked": int(guard_section.get("shell_write_blocked", 0)),
        "todo_plan_violation": int(guard_section.get("todo_plan_violation", 0)),
        "completion_guard_blocks": int(guard_section.get("completion_guard_blocks", 0)),
        "total_guard_events": sum(event_counts.values()),
        "event_types": ",".join(f"{k}:{v}" for k, v in sorted(event_counts.items())),
    }
    return data


def _iter_runs(paths: List[str]) -> List[Path]:
    resolved: List[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        if p.is_dir():
            resolved.append(p)
    return resolved


def _format_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No runs found."
    widths = {col: len(col) for col in COLUMNS}
    for row in rows:
        for col in COLUMNS:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))
    lines = []
    header = " | ".join(f"{col:{widths[col]}}" for col in COLUMNS)
    sep = "-+-".join("-" * widths[col] for col in COLUMNS)
    lines.append(header)
    lines.append(sep)
    for row in rows:
        line = " | ".join(f"{str(row.get(col, '')):{widths[col]}}" for col in COLUMNS)
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract guardrail metrics from logging runs.")
    parser.add_argument("runs", nargs="+", help="Run directories (e.g., logging/2025*-agent_ws_*).")
    parser.add_argument(
        "--format",
        choices={"table", "jsonl"},
        default="table",
        help="Output format (default: table).",
    )
    args = parser.parse_args()

    run_dirs = _iter_runs(args.runs)
    rows = [_collect_metrics(run_dir) for run_dir in sorted(run_dirs)]

    if args.format == "jsonl":
        for row in rows:
            print(json.dumps(row))
    else:
        print(_format_table(rows))


if __name__ == "__main__":
    main()
