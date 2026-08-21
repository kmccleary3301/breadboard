#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_edges(path: Path) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":") and not line.startswith("-"):
            current = line[:-1]
            edges[current] = set()
            continue
        if line.startswith("-") and current is not None:
            edges[current].add(line[1:].strip())
    return edges


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tool lifecycle transition traces.")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--allowed-edges", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()

    trace_payload = _load_json(_root_path(args.trace))
    edges = _load_edges(_root_path(args.allowed_edges))
    events = trace_payload.get("events") if isinstance(trace_payload, dict) else None
    if not isinstance(events, list):
        events = []
    violations: list[str] = []
    for index, pair in enumerate(zip(events, events[1:])):
        before, after = pair
        if not isinstance(before, dict) or not isinstance(after, dict):
            violations.append(f"events {index}/{index + 1} must be objects")
            continue
        if before.get("tool_call_id") != after.get("tool_call_id"):
            violations.append(f"events {index}/{index + 1} switch tool_call_id")
            continue
        from_state = str(before.get("state"))
        to_state = str(after.get("state"))
        if to_state not in edges.get(from_state, set()):
            violations.append(f"invalid transition {from_state}->{to_state} at events {index}/{index + 1}")
    report = {
        "schema_version": "bb.ct.tool_transition_semantics_report.v1",
        "ok": not violations,
        "trace_path": args.trace,
        "allowed_edges_path": args.allowed_edges,
        "trace_length": len(events),
        "violations": violations,
    }
    out = _root_path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
