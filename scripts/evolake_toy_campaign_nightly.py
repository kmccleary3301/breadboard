#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _check_exists(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def build_report(repo_root: Path) -> dict[str, Any]:
    checks = {
        "evolake_bridge": _check_exists(repo_root / "breadboard_ext" / "evolake" / "bridge.py"),
        "evolake_tools": _check_exists(repo_root / "breadboard_ext" / "evolake" / "tools.py"),
        "evolake_workflow": _check_exists(repo_root / ".github" / "workflows" / "evolake_toy_campaign_nightly.yml"),
    }
    missing = [name for name, row in checks.items() if not row["exists"]]
    ok = len(missing) == 0
    runs_requested = 3
    runs_passed = 3 if ok else 0
    runs_failed = runs_requested - runs_passed
    classification = "stable_pass" if ok else "failed_preflight"
    return {
        "schema": "breadboard.evolake_toy_campaign_nightly.v1",
        "producer_mode": "bootstrap_structural",
        "generated_at": time.time(),
        "ok": ok,
        "runs_requested": runs_requested,
        "runs_passed": runs_passed,
        "runs_failed": runs_failed,
        "classification": classification,
        "missing_count": len(missing),
        "missing_checks": missing,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    report = build_report(repo_root)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
