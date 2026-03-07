from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _make_snapshot_dir(root: Path, name: str, port: int) -> Path:
    snap = root / name
    snap.mkdir(parents=True, exist_ok=True)
    for filename in ("lean.snap", "lean.mem", "rootfs.ext4"):
        (snap / filename).write_bytes(b"x")
    (snap / "serial.log").write_text(
        f"Launching vsock REPL agent on port {port}\n"
        "Vsock REPL agent running with pid=123\n"
        "Mathlib ready for snapshot\n",
        encoding="utf-8",
    )
    return snap


def test_atp_vsock_protocol_conformance_script(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    snapshot_root = tmp_path / "lean_snapshot"
    _make_snapshot_dir(snapshot_root, "genA", 52)
    _make_snapshot_dir(snapshot_root / "pool" / "poolA", "worker00", 52)

    out_path = tmp_path / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "atp_vsock_protocol_conformance.py"),
            "--snapshot-root",
            str(snapshot_root),
            "--out",
            str(out_path),
        ],
        cwd=str(root),
        env=env,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "breadboard.atp.vsock_protocol_conformance.v1"
    assert payload["snapshot_count"] == 2
    assert payload["port_consistent"] is True
    assert payload["expected_port_match"] is True
    assert payload["all_rows_ok"] is True
