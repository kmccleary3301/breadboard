#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List


PORT_RE = re.compile(r"Launching vsock REPL agent on port (\d+)")
AGENT_UP_RE = re.compile(r"Vsock REPL agent running with pid=\d+")


def _is_snapshot_dir(path: Path) -> bool:
    required = ("lean.snap", "lean.mem", "rootfs.ext4", "serial.log")
    return all((path / name).is_file() for name in required)


def _discover_snapshot_dirs(snapshot_root: Path) -> List[Path]:
    dirs: List[Path] = []
    if _is_snapshot_dir(snapshot_root):
        dirs.append(snapshot_root)
    for candidate in sorted(snapshot_root.rglob("*")):
        if candidate.is_dir() and _is_snapshot_dir(candidate):
            dirs.append(candidate)
    unique: Dict[str, Path] = {}
    for path in dirs:
        unique[str(path.resolve())] = path
    return list(unique.values())


def _analyze_snapshot_dir(snapshot_dir: Path) -> Dict[str, object]:
    serial_path = snapshot_dir / "serial.log"
    text = serial_path.read_text(encoding="utf-8", errors="replace")
    port_match = PORT_RE.search(text)
    agent_up = bool(AGENT_UP_RE.search(text))
    has_ready = "Mathlib ready for snapshot" in text
    return {
        "snapshot_dir": str(snapshot_dir),
        "serial_log": str(serial_path),
        "port": int(port_match.group(1)) if port_match else None,
        "agent_up": agent_up,
        "mathlib_ready": has_ready,
        "ok": bool(port_match and agent_up and has_ready),
    }


def _summarize(rows: Iterable[Dict[str, object]], expected_port: int | None) -> Dict[str, object]:
    rows = list(rows)
    ports = sorted({int(r["port"]) for r in rows if isinstance(r.get("port"), int)})
    bad_rows = [r for r in rows if not bool(r.get("ok"))]
    consistent_port = len(ports) <= 1
    expected_ok = True
    if expected_port is not None:
        expected_ok = ports == [] or ports == [expected_port]
    return {
        "schema": "breadboard.atp.vsock_protocol_conformance.v1",
        "snapshot_count": len(rows),
        "ports_detected": ports,
        "port_consistent": consistent_port,
        "expected_port": expected_port,
        "expected_port_match": expected_ok,
        "all_rows_ok": len(bad_rows) == 0,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ATP vsock protocol conformance across snapshot generations.")
    parser.add_argument(
        "--snapshot-root",
        default="lean_snapshot",
        help="Root directory containing snapshot generations.",
    )
    parser.add_argument(
        "--expected-port",
        type=int,
        default=52,
        help="Expected REPL vsock port. Set to -1 to disable strict port expectation.",
    )
    parser.add_argument(
        "--out",
        default="artifacts/atp_vsock_protocol_conformance.json",
        help="Output JSON report path.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    snapshot_root = (repo_root / args.snapshot_root).resolve()
    if not snapshot_root.exists():
        raise SystemExit(f"Snapshot root not found: {snapshot_root}")

    snapshot_dirs = _discover_snapshot_dirs(snapshot_root)
    rows = [_analyze_snapshot_dir(path) for path in snapshot_dirs]
    expected_port = None if args.expected_port < 0 else args.expected_port
    summary = _summarize(rows, expected_port=expected_port)
    summary["snapshot_root"] = str(snapshot_root)

    out_path = (repo_root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"atp_vsock_protocol_conformance: {out_path}")

    if summary["snapshot_count"] == 0:
        return 2
    if not summary["all_rows_ok"] or not summary["port_consistent"] or not summary["expected_port_match"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
