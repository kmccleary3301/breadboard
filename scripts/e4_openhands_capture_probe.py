#!/usr/bin/env python3
"""Run supplier and BreadBoard OpenHands capture lanes for identical scenarios."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
CASES = HERE / "openhands_capture_cases.json"
SUPPLIER = HERE / "openhands_capture_supplier.py"
BREADBOARD = HERE / "openhands_capture_breadboard.py"

def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def run_role(script: Path, case_id: str, output: Path, args: argparse.Namespace) -> dict[str, Any]:
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if script == SUPPLIER:
        command = [sys.executable, "-I", "-B", str(script), "--case-id", case_id, "--cases", str(args.scenario_file), "--output", str(output)]
    else:
        command = [sys.executable, "-I", "-B", str(script), "--case-id", case_id, "--output", str(output), "--repo", str(args.repo), "--scenario-file", str(args.scenario_file), "--base-commit", args.base_commit, "--native-manifest-sha256", args.native_manifest_sha256, "--public-manifest-sha256", args.public_manifest_sha256]
    started = time.monotonic()
    stdout_path, stderr_path = output / "operator.stdout", output / "operator.stderr"
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=240)
        if process.returncode != 0:
            raise RuntimeError(f"{script.name} exited {process.returncode}")
        trace_path = output / "trace.json"
        trace = json.loads(trace_path.read_bytes())
        expected_schema = "bb.e4.openhands-sdk-trace.v1" if script == BREADBOARD else "bb.e4.openhands-supplier-trace.v1"
        if trace.get("schema_version") != expected_schema:
            raise ValueError(f"invalid trace schema in {trace_path}")
        packet_path = output.parent / f"{case_id}.json"
        packet_path.write_bytes(trace_path.read_bytes())
        return {"trace": str(packet_path), "sha256": sha(packet_path), "elapsed_seconds": round(time.monotonic() - started, 3)}
    except BaseException as exc:
        write_json(output / "operator-failure.json", {"case_id": case_id, "script": str(script), "exception": repr(exc), "elapsed_seconds": round(time.monotonic() - started, 3)})
        raise

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path("/testbed"))
    parser.add_argument("--scenario-file", type=Path, default=CASES)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--native-manifest-sha256", required=True)
    parser.add_argument("--public-manifest-sha256", required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--supplier-only", action="store_true")
    parser.add_argument("--breadboard-only", action="store_true")
    args = parser.parse_args()
    if args.supplier_only and args.breadboard_only:
        parser.error("--supplier-only and --breadboard-only are exclusive")
    cases_payload = json.loads(args.scenario_file.read_bytes())
    selected = args.case_ids or sorted(cases_payload.get("cases", {}))
    if any(case_id not in cases_payload.get("cases", {}) for case_id in selected):
        parser.error("unknown case id")
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    capture_manifest: dict[str, Any] = {"schema_version": "bb.e4.openhands-capture-manifest.v1", "role": "supplier", "cases": {}}
    replay_manifest: dict[str, Any] = {"schema_version": "bb.e4.openhands-capture-manifest.v1", "role": "breadboard", "cases": {}}
    records: list[dict[str, Any]] = []
    for case_id in selected:
        if not args.breadboard_only:
            role = run_role(SUPPLIER, case_id, out / "capture" / f"{case_id}.evidence", args)
            capture_manifest["cases"][case_id] = {"path": f"capture/{case_id}.json", "sha256": role["sha256"]}
            records.append({"case_id": case_id, "role": "supplier", **role})
        if not args.supplier_only:
            role = run_role(BREADBOARD, case_id, out / "replay" / f"{case_id}.evidence", args)
            replay_manifest["cases"][case_id] = {"path": f"replay/{case_id}.json", "sha256": role["sha256"]}
            records.append({"case_id": case_id, "role": "breadboard", **role})
    write_json(out / "raw_capture_manifest.json", capture_manifest)
    write_json(out / "bb_replay_result.json", replay_manifest)
    write_json(out / "run-receipt.json", {"schema_version": "bb.e4.openhands-capture-run-receipt.v1", "case_ids": selected, "records": records, "trace_schema": "bb.e4.openhands-sdk-trace.v1", "outer_case_deadline_seconds": 240, "same_receiver_script": str(HERE / "openhands_capture_receiver.py")})
    print(json.dumps({"out": str(out), "capture_cases": len(capture_manifest["cases"]), "replay_cases": len(replay_manifest["cases"])}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
