#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _check_exists(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def build_digest(repo_root: Path) -> dict[str, Any]:
    checks = {
        "atp_firecracker_ci_script": _check_exists(repo_root / "scripts" / "atp_firecracker_ci.sh"),
        "atp_load_nightly_script": _check_exists(repo_root / "scripts" / "atp_firecracker_repl_load_nightly.sh"),
        "atp_pool_stability_script": _check_exists(repo_root / "scripts" / "atp_snapshot_pool_stability.py"),
        "atp_recovery_script": _check_exists(repo_root / "scripts" / "atp_state_ref_regression_recovery.py"),
        "atp_threshold_policy": _check_exists(repo_root / "config" / "atp_threshold_policy.json"),
    }
    missing = [name for name, row in checks.items() if not row["exists"]]
    overall_ok = len(missing) == 0
    decision_state = "green" if overall_ok else "incident"
    return {
        "schema": "breadboard.atp_ops_digest.v1",
        "producer_mode": "bootstrap_structural",
        "generated_at": time.time(),
        "overall_ok": overall_ok,
        "decision_state": decision_state,
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
    digest = build_digest(repo_root)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(digest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(digest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
