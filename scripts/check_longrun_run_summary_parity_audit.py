#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_run_summary(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    longrun = payload.get("longrun")
    if not isinstance(longrun, dict):
        raise ValueError("run_summary.longrun is required")
    parity = longrun.get("parity_audit")
    if not isinstance(parity, dict):
        raise ValueError("run_summary.longrun.parity_audit is required")
    required = ["schema_version", "enabled", "effective_config_hash", "episodes_run", "artifact_list"]
    for key in required:
        if key not in parity:
            raise ValueError(f"run_summary.longrun.parity_audit.{key} is required")
    if not isinstance(parity.get("artifact_list"), list):
        raise ValueError("run_summary.longrun.parity_audit.artifact_list must be a list")
    return {"ok": True, "path": str(path), "parity_audit": parity}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate run_summary longrun parity audit block.")
    parser.add_argument("run_summary", help="Path to meta/run_summary.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_run_summary(Path(args.run_summary))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
