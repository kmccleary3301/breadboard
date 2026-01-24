#!/usr/bin/env python3
"""
Build an index of Claude Code golden captures under misc/claude_code_runs/goldens.

This is a convenience for parity work: it lets us consistently refer to the
"current" run for each scenario (and its metadata) without hand-inspecting the tree.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioIndexEntry:
    scenario: str
    scenario_dir: str
    current_run_dir: Optional[str]
    run_id: Optional[str]
    captured_at_utc: Optional[str]
    cli_version: Optional[str]
    model: Optional[str]
    max_budget_usd: Optional[str]
    extra_args: List[str]
    provider_dump_files: Optional[int]
    normalized_turns: Optional[int]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_current_run(scenario_dir: Path) -> Optional[Path]:
    current = scenario_dir / "current"
    if current.is_symlink():
        try:
            return current.resolve()
        except Exception:
            return None
    runs_dir = scenario_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = [p for p in runs_dir.iterdir() if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def build_index(version_dir: Path) -> List[ScenarioIndexEntry]:
    entries: List[ScenarioIndexEntry] = []
    for scenario_dir in sorted([p for p in version_dir.iterdir() if p.is_dir()]):
        scenario = scenario_dir.name
        run_dir = _resolve_current_run(scenario_dir)

        scenario_json = _read_json(run_dir / "scenario.json") if run_dir else None
        extra_args_raw = scenario_json.get("extra_args") if isinstance(scenario_json, dict) else None
        extra_args: List[str] = []
        if isinstance(extra_args_raw, list):
            extra_args = [str(x) for x in extra_args_raw if x is not None]

        provider_dump_files = None
        normalized_turns = None
        if run_dir:
            pd = run_dir / "provider_dumps"
            if pd.exists():
                provider_dump_files = len(list(pd.glob("*.json")))
            norm = run_dir / "normalized"
            if norm.exists():
                normalized_turns = len(list(norm.glob("turn_*_request.json")))

        entries.append(
            ScenarioIndexEntry(
                scenario=scenario,
                scenario_dir=str(scenario_dir),
                current_run_dir=str(run_dir) if run_dir else None,
                run_id=(scenario_json or {}).get("run_id") if isinstance(scenario_json, dict) else None,
                captured_at_utc=(scenario_json or {}).get("captured_at_utc") if isinstance(scenario_json, dict) else None,
                cli_version=(scenario_json or {}).get("cli_version") if isinstance(scenario_json, dict) else None,
                model=(scenario_json or {}).get("model") if isinstance(scenario_json, dict) else None,
                max_budget_usd=(scenario_json or {}).get("max_budget_usd") if isinstance(scenario_json, dict) else None,
                extra_args=extra_args,
                provider_dump_files=provider_dump_files,
                normalized_turns=normalized_turns,
            )
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Index Claude Code golden capture runs.")
    parser.add_argument(
        "--goldens-root",
        default="misc/claude_code_runs/goldens",
        help="Root directory containing version/scenario goldens.",
    )
    parser.add_argument("--version", default="2.0.72", help="Version label to index (default: 2.0.72).")
    parser.add_argument(
        "--output",
        default="",
        help="Optional output path (default: <goldens-root>/<version>/index.json).",
    )
    args = parser.parse_args()

    version_dir = (REPO_ROOT / args.goldens_root / args.version).resolve()
    if not version_dir.exists():
        raise SystemExit(f"[claude-goldens-index] missing: {version_dir}")

    out_path = Path(args.output).resolve() if args.output else (version_dir / "index.json")
    entries = build_index(version_dir)
    payload = {
        "version": args.version,
        "root": str(version_dir),
        "scenarios": [e.__dict__ for e in entries],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[claude-goldens-index] wrote {len(entries)} scenarios -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

