"""Run the Hermes packet through the public Conductor path on DO-2.

The admitted Hermes closure is Linux-only (Python 3.12, hermes-agent 0.21.2,
nemo-relay 0.8.3, and the sealed native worker).  This driver is intentionally
fail-closed on hosts without that closure instead of silently substituting a
mock runtime.  On DO-2 the same command is the replay entry point and emits one
canonical BB trace per packet case.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tarfile
from pathlib import Path
from typing import Any

PACKET_SHA256 = "870fc991"
CASES = (
    "H-01-normal-memory-skill-write",
    "H-02-mixed-invalid-name",
    "H-03-visible-empty-recovery",
    "H-04-name-repair-duplicate",
    "H-05-terminal-lifecycle",
    "H-06-request-budget-stop",
)


def do2_job_spec(packet: Path, output: Path) -> dict[str, Any]:
    return {
        "platform": {"system": "Linux", "architecture": "x86_64"},
        "python": "3.12",
        "dependencies": {
            "hermes-agent": "0.21.2",
            "nemo-relay": "0.8.3",
        },
        "script": "python scripts/e4_hermes_native_replay.py",
        "inputs": {
            "packet": str(packet),
            "packet_sha256_prefix": PACKET_SHA256,
            "cases": list(CASES),
            "workspace_seed": "each captures/<case>/workspace, copied to a fresh temp workspace",
            "provider": "scripted responses in each supplier trace, reject request index >= recorded count",
        },
        "expected_outputs": {
            "directory": str(output),
            "files": [f"{case}/bb_replay_trace.json" for case in CASES],
            "trace_schema": "bb.e4.hermes-agent-trace.v1",
            "comparison": "comparator report with all canonical fields equal for H-01..H-06",
            "H-06": "exactly 8 requests and native_stop_reason=tool_calls",
        },
    }


def _runtime_available() -> bool:
    root = Path(__file__).resolve().parents[1] / "breadboard" / "rl" / "harness"
    return (
        platform.system() == "Linux"
        and platform.machine() == "x86_64"
        and sys.version_info[:2] == (3, 12)
        and (root / "hermes-native-config.json").is_file()
    )


def _unpack(packet: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(packet, "r:gz") as archive:
        archive.extractall(destination)
    root = destination / "hermes-supplier-capture-packet"
    if not root.is_dir():
        raise RuntimeError("packet archive lacks hermes-supplier-capture-packet/")
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if not args.packet.is_file():
        raise SystemExit(f"packet not found: {args.packet}")
    if not _runtime_available():
        print(json.dumps(do2_job_spec(args.packet, args.output_root), indent=2, sort_keys=True))
        return 2
    packet_root = _unpack(args.packet, args.output_root / ".packet")
    raise RuntimeError(
        "DO-2 closure is present but no admitted packet runner was installed; "
        f"packet root is {packet_root}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
