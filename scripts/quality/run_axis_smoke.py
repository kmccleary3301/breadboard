#!/usr/bin/env python3
"""Run bounded checks for each frozen QC axis without assigning or promoting scores."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
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
CHECK_TIMEOUT_SECONDS = 180
MAX_OUTPUT_BYTES = 32 * 1024


def _command(argv: list[str]) -> list[str]:
    return [sys.executable, *argv[1:]] if argv and argv[0] == "python" else argv


def _run_check(argv: list[str], repo_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryFile() as captured:
        try:
            completed = subprocess.run(
                _command(argv), cwd=repo_root, stdout=captured, stderr=subprocess.STDOUT,
                check=False, timeout=CHECK_TIMEOUT_SECONDS,
            )
            exit_code, failure = completed.returncode, None
        except subprocess.TimeoutExpired:
            exit_code, failure = None, f"timed out after {CHECK_TIMEOUT_SECONDS} seconds"
        captured.seek(0)
        raw = captured.read(MAX_OUTPUT_BYTES + 1)
    truncated = len(raw) > MAX_OUTPUT_BYTES
    output = raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    if truncated:
        output += f"\n[output truncated at {MAX_OUTPUT_BYTES} bytes]"
    return {
        "command": argv, "exit_code": exit_code, "failure": failure, "output": output,
        "output_truncated": truncated, "status": "passed" if exit_code == 0 else "failed",
    }


def run_axis_smoke(
    manifest_path: Path = DEFAULT_MANIFEST, repo_root: Path = ROOT, *, execute: bool = True,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    validate_axis_manifest(manifest)
    check_results: dict[str, dict[str, Any]] = {}
    for check_id in sorted(manifest["checks"]):
        argv = manifest["checks"][check_id]
        check_results[check_id] = _run_check(argv, repo_root) if execute else {
            "command": argv, "exit_code": None, "status": "not_run",
        }

    axis_results: list[dict[str, Any]] = []
    for axis in manifest["axes"]:
        statuses = [check_results[check_id]["status"] for check_id in axis["check_ids"]]
        status = "smoke_failed" if "failed" in statuses else "smoke_checks_passed" if all(item == "passed" for item in statuses) else "not_run"
        axis_results.append({"axis_id": axis["id"], "check_ids": axis["check_ids"], "score": None, "status": status})

    return {
        "assessment": "smoke_only_no_score", "axis_results": axis_results, "checks": check_results,
        "contract_id": "bb.public_axis_smoke_result.v1",
        "manifest_sha256": f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}",
        "passed": execute and all(row["status"] == "passed" for row in check_results.values()),
        "score_promoted": False, "version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list", action="store_true", help="emit axis/check names without executing checks")
    args = parser.parse_args(argv)
    result = run_axis_smoke(args.manifest, execute=not args.list)
    content = canonical_bytes(result)
    args.output.write_bytes(content) if args.output else sys.stdout.buffer.write(content)
    return 0 if args.list or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
