#!/usr/bin/env python3
"""Run bounded checks for each frozen QC axis without assigning or promoting scores."""

from __future__ import annotations

import argparse
import hashlib
import os
import selectors
import signal
import subprocess
import sys
import time
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


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    for termination_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, termination_signal)
        except (ProcessLookupError, PermissionError):
            pass
        if termination_signal is signal.SIGTERM:
            time.sleep(0.1)
    process.wait()


def _run_check(argv: list[str], repo_root: Path) -> dict[str, Any]:
    process = subprocess.Popen(
        _command(argv), cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    selector, retained, truncated = selectors.DefaultSelector(), bytearray(), False
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline, timed_out = time.monotonic() + CHECK_TIMEOUT_SECONDS, False
    while selector.get_map():
        if time.monotonic() >= deadline:
            timed_out = True
            _stop_group(process)
            selector.unregister(process.stdout)
            process.stdout.close()
            break
        events = selector.select(0.05)
        for key, _ in events:
            chunk = os.read(key.fd, 8192)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            available = MAX_OUTPUT_BYTES - len(retained)
            retained.extend(chunk[:max(available, 0)])
            truncated |= len(chunk) > max(available, 0)
    selector.close()
    exit_code = None if timed_out else process.wait()
    output = retained.decode("utf-8", errors="replace")
    if truncated:
        output += f"\n[output truncated at {MAX_OUTPUT_BYTES} bytes]"
    failure = f"timed out after {CHECK_TIMEOUT_SECONDS} seconds" if timed_out else None
    return {"command": argv, "exit_code": exit_code, "failure": failure, "output": output, "output_truncated": truncated, "status": "passed" if exit_code == 0 else "failed"}


def run_axis_smoke(manifest_path: Path = DEFAULT_MANIFEST, repo_root: Path = ROOT, *, execute: bool = True) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    validate_axis_manifest(manifest)
    check_results: dict[str, dict[str, Any]] = {}
    for check_id in sorted(manifest["checks"]):
        argv = manifest["checks"][check_id]
        check_results[check_id] = _run_check(argv, repo_root) if execute else {"command": argv, "exit_code": None, "status": "not_run"}
    axis_results: list[dict[str, Any]] = []
    for axis in manifest["axes"]:
        statuses = [check_results[check_id]["status"] for check_id in axis["check_ids"]]
        status = "smoke_failed" if "failed" in statuses else "smoke_checks_passed" if all(item == "passed" for item in statuses) else "not_run"
        axis_results.append({"axis_id": axis["id"], "check_ids": axis["check_ids"], "score": None, "status": status})
    return {"assessment": "smoke_only_no_score", "axis_results": axis_results, "checks": check_results, "contract_id": "bb.public_axis_smoke_result.v1", "manifest_sha256": f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}", "passed": execute and all(row["status"] == "passed" for row in check_results.values()), "score_promoted": False, "version": 1}


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
