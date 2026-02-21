#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


PATTERNS: Sequence[str] = (
    "*e4*.yaml",
    "*parity*.yaml",
    "oh_my_opencode_phase8_async_subagents_v1_replay.yaml",
    "opencode_phase8_async_subagents_v1_replay.yaml",
    "claude_code_haiku45_phase8_wakeup_replay.yaml",
    "codex_cli_gpt51mini_e4_live.yaml",
)


def discover_parity_configs(root: Path) -> List[Path]:
    agent_configs = root / "agent_configs"
    if not agent_configs.exists():
        return []
    found: List[Path] = []
    seen: set[Path] = set()
    for pattern in PATTERNS:
        for path in agent_configs.rglob(pattern):
            rel = path.relative_to(root)
            if rel in seen:
                continue
            seen.add(rel)
            found.append(path)
    return sorted(found)


def _longrun_enabled(loaded: Dict[str, Any]) -> bool:
    long_running = loaded.get("long_running")
    if not isinstance(long_running, dict):
        return False
    return bool(long_running.get("enabled", False))


def audit_configs(paths: Iterable[Path], repo_root: Path) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for path in paths:
        rel = str(path.relative_to(repo_root))
        entry: Dict[str, Any] = {"config": rel, "load_error": None, "long_running_enabled": False}
        try:
            loaded = load_agent_config(str(path))
            enabled = _longrun_enabled(loaded if isinstance(loaded, dict) else {})
            entry["long_running_enabled"] = enabled
            if enabled:
                failures.append({"config": rel, "reason": "long_running.enabled=true"})
        except Exception as exc:  # defensive: audit should report load failures
            entry["load_error"] = f"{exc.__class__.__name__}: {exc}"
            failures.append({"config": rel, "reason": "load_error", "error": entry["load_error"]})
        results.append(entry)
    return {
        "schema_version": "longrun_parity_audit_v1",
        "checked": len(results),
        "failures": failures,
        "results": results,
        "ok": len(failures) == 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify long-running mode is disabled across parity/E4 configs.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root containing agent_configs/ (default: current directory).",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write machine-readable audit payload.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    paths = discover_parity_configs(repo_root)
    payload = audit_configs(paths, repo_root)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
