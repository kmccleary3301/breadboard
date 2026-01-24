#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List


GRAPH_V0_SURFACES = [
    "tool_schema_latest",
    "tool_allowlist_latest",
    "prompt_hashes",
    "tool_prompt_mode",
    "system_roles",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 11 surface coverage audit.")
    parser.add_argument("--run-dir", help="Run directory containing meta/run_summary.json")
    parser.add_argument("--summary", help="Path to run_summary.json")
    parser.add_argument(
        "--require",
        action="append",
        help="Surface name to require (repeatable). Defaults to Controllable Surface Graph v0 list.",
    )
    parser.add_argument(
        "--allow-unclassified",
        action="store_true",
        help="Do not fail if surface_manifest.unclassified is non-empty.",
    )
    return parser.parse_args()


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = _parse_args()
    if not args.run_dir and not args.summary:
        raise SystemExit("Provide --run-dir or --summary")
    summary_path = Path(args.summary) if args.summary else Path(args.run_dir) / "meta" / "run_summary.json"
    if not summary_path.exists():
        raise SystemExit(f"run_summary.json not found: {summary_path}")
    summary = _load_summary(summary_path)
    manifest = summary.get("surface_manifest") or {}
    manifest_required = manifest.get("required") if isinstance(manifest, dict) else None
    surfaces = manifest.get("surfaces") or []
    surface_names = {entry.get("name") for entry in surfaces if isinstance(entry, dict)}
    if args.require:
        required: List[str] = list(args.require)
    elif isinstance(manifest_required, list) and manifest_required:
        required = [str(name) for name in manifest_required if name]
    else:
        required = list(GRAPH_V0_SURFACES)
        surface_snapshot = summary.get("surface_snapshot") or {}
        if isinstance(surface_snapshot, dict) and surface_snapshot.get("provider_tools"):
            required.append("provider_tools_config")
        mcp_calls_present = False
        mcp_live_or_fixture = False
        if args.run_dir:
            mcp_calls_path = Path(args.run_dir) / "meta" / "mcp_calls.jsonl"
            if mcp_calls_path.exists():
                mcp_calls_present = True
                try:
                    for line in mcp_calls_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        if not isinstance(payload, dict):
                            continue
                        source = str(payload.get("source") or "").lower()
                        if source and source not in {"builtin"}:
                            mcp_live_or_fixture = True
                            break
                        if payload.get("server"):
                            mcp_live_or_fixture = True
                            break
                except Exception:
                    mcp_live_or_fixture = False
        if (isinstance(surface_snapshot, dict) and surface_snapshot.get("mcp_snapshot")) or mcp_calls_present:
            if mcp_live_or_fixture or (isinstance(surface_snapshot, dict) and surface_snapshot.get("mcp_snapshot")):
                required.append("mcp_snapshot")
    missing = [name for name in required if name not in surface_names]
    if missing:
        print("Missing surfaces:", ", ".join(missing))
        return 1
    unclassified = manifest.get("unclassified") or []
    if unclassified and not args.allow_unclassified:
        print("Unclassified surfaces:", ", ".join(str(x) for x in unclassified))
        return 2
    print("Surface coverage audit OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
