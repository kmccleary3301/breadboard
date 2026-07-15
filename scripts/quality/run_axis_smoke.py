#!/usr/bin/env python3
"""Run bounded checks for each frozen QC axis without assigning or promoting scores."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .validate_public_contracts import canonical_bytes, load_json, validate_axis_manifest
except ImportError:
    from validate_public_contracts import canonical_bytes, load_json, validate_axis_manifest  # type: ignore[no-redef]

DEFAULT_MANIFEST = ROOT / "contracts" / "public" / "axis_smoke.v1.json"


def _command(argv: list[str]) -> list[str]:
    return [sys.executable, *argv[1:]] if argv and argv[0] == "python" else argv


def run_axis_smoke(
    manifest_path: Path = DEFAULT_MANIFEST,
    repo_root: Path = ROOT,
    *,
    execute: bool = True,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    validate_axis_manifest(manifest)
    check_results: dict[str, dict[str, Any]] = {}
    for check_id in sorted(manifest["checks"]):
        argv = manifest["checks"][check_id]
        if not execute:
            check_results[check_id] = {"command": argv, "exit_code": None, "status": "not_run"}
            continue
        completed = subprocess.run(
            _command(argv), cwd=repo_root, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        check_results[check_id] = {
            "command": argv,
            "exit_code": completed.returncode,
            "output": completed.stdout,
            "status": "passed" if completed.returncode == 0 else "failed",
        }

    axis_results: list[dict[str, Any]] = []
    for axis in manifest["axes"]:
        statuses = [check_results[check_id]["status"] for check_id in axis["check_ids"]]
        if "failed" in statuses:
            status = "smoke_failed"
        elif all(item == "passed" for item in statuses):
            status = "smoke_checks_passed"
        else:
            status = "not_run"
        axis_results.append({
            "axis_id": axis["id"],
            "check_ids": axis["check_ids"],
            "score": None,
            "status": status,
        })

    return {
        "assessment": "smoke_only_no_score",
        "axis_results": axis_results,
        "checks": check_results,
        "contract_id": "bb.public_axis_smoke_result.v1",
        "manifest_sha256": f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}",
        "passed": execute and all(row["status"] == "passed" for row in check_results.values()),
        "score_promoted": False,
        "version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list", action="store_true", help="emit axis/check names without executing checks")
    args = parser.parse_args(argv)
    result = run_axis_smoke(args.manifest, execute=not args.list)
    content = canonical_bytes(result)
    if args.output:
        args.output.write_bytes(content)
    else:
        sys.stdout.buffer.write(content)
    return 0 if args.list or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
