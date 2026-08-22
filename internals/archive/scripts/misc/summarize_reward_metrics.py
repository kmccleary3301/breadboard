#!/usr/bin/env python3
"""Summarize reward metrics from telemetry outputs.

Supports both JSONL telemetry streams and the SQLite database produced by the
reward metrics writer. Prints aggregate statistics suitable for quick checks or
notebook import.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize reward metrics")
    parser.add_argument("--telemetry", help="Path to telemetry JSONL file", default=None)
    parser.add_argument("--sqlite", help="Path to reward metrics SQLite DB", default=None)
    return parser.parse_args()


def summarize_jsonl(path: Path) -> Dict[str, Any]:
    summary: Dict[str, Dict[str, float]] = {}
    turns = 0
    if not path.exists():
        return {"turns": 0, "metrics": {}}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "reward_metrics":
                continue
            for turn_payload in event.get("turns", []):
                turns += 1
                for name, value in (turn_payload.get("metrics") or {}).items():
                    try:
                        value_f = float(value)
                    except (TypeError, ValueError):
                        continue
                    stat = summary.setdefault(name, {"count": 0, "sum": 0.0})
                    stat["count"] += 1
                    stat["sum"] += value_f
    for name, stat in summary.items():
        count = stat["count"] or 1
        stat["avg"] = stat["sum"] / count
    return {"turns": turns, "metrics": summary}


def summarize_sqlite(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"turns": 0, "metrics": {}}
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT metric, COUNT(*), AVG(value) FROM reward_metrics GROUP BY metric"
        ).fetchall()
        turn_count = conn.execute("SELECT COUNT(DISTINCT turn) FROM reward_metrics").fetchone()[0]
    metrics = {
        name: {"count": count, "avg": avg}
        for name, count, avg in rows
    }
    return {"turns": turn_count, "metrics": metrics}


def main() -> int:
    args = parse_args()
    if args.telemetry:
        result = summarize_jsonl(Path(args.telemetry))
    elif args.sqlite:
        result = summarize_sqlite(Path(args.sqlite))
    else:
        print("Provide either --telemetry or --sqlite")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
