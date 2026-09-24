"""Author the DO-2 Hermes public-SIF replay job and fail closed elsewhere."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PACKET_SHA256 = "870fc991ddf72271766b90f113921d1c0bfff3fb58090550691bac1fd9a780d6"
SIF_SHA256 = "70455f244b35912f319363c05c9e163f119c0235656124a797c0aad950103033"
SOURCE_COMMIT = "939e45c91d751fadd94dcd1b873ac3cb44846213"
CASES = (
    "H-01-normal-memory-skill-write", "H-02-mixed-invalid-name",
    "H-03-visible-empty-recovery", "H-04-name-repair-duplicate",
    "H-05-terminal-lifecycle", "H-06-request-budget-stop",
)
REMOTE = "/root/bbe4-do2-20260923/hermes/replay"
SOURCE_DIRS = ("breadboard", "conformance", "config", "scripts", "tests")
KIT_PATHS = (
    Path("hermes_sif_compose.py"),
    *(Path("do2-20260923/hermes/kit") / name for name in (
        "hermes_capture_breadboard.py",
        "hermes_capture_probe.py",
        "hermes_capture_receiver.py",
        "hermes_capture_cases.json",
    )),
)


def _checkout_commit() -> str:
    root = Path(__file__).resolve().parents[1]
    command = ["git", "-C", str(root)]
    head = subprocess.run(
        [*command, "rev-parse", "--verify", "HEAD"], capture_output=True, text=True,
    )
    if head.returncode or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head.stdout.strip()):
        raise ValueError("cannot resolve checkout HEAD")
    status = subprocess.run(
        [*command, "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True, text=True,
    )
    if status.returncode:
        raise ValueError("cannot inspect checkout status")
    if status.stdout:
        raise ValueError("checkout is dirty; commit all changes before preparing replay")
    return head.stdout.strip()


def do2_job_spec(
    packet: Path, output: Path, wheelhouse: Path = Path("/tmp/hermes-conductor-wheelhouse"),
    *, kit_root: Path,
) -> dict[str, Any]:
    checkout = Path(__file__).resolve().parents[1]
    source_paths = {name: checkout / name for name in SOURCE_DIRS}
    if any(not path.is_dir() for path in source_paths.values()):
        raise ValueError(f"source tree missing required directory in {checkout}")
    kit_root = kit_root.resolve()
    kit_paths = {path.name: kit_root / path for path in KIT_PATHS}
    if any(not path.is_file() for path in kit_paths.values()):
        raise ValueError(f"kit tree missing required operator in {kit_root}")
    head_commit = _checkout_commit()
    packet = packet.resolve()
    wheelhouse = wheelhouse.resolve()
    sbatch = f"""#!/bin/bash
#SBATCH --job-name=bb-e4-hermes-public-replay
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output={REMOTE}/%x-%j.out
set -euo pipefail
ROOT=/root/bbe4-do2-20260923/hermes
REPLAY=$ROOT/replay
SIF=$ROOT/images/hermes-public.sif
REPO=$REPLAY/repo
PACKET=$REPLAY/hermes-supplier-capture-packet-rerun3.tar.gz
rm -rf "$REPLAY/out" "$REPLAY/packet" "$REPLAY/bb-deps"
mkdir -p "$REPLAY/out" "$REPLAY/packet" "$REPLAY/bb-deps"
sha256sum "$ROOT/images/hermes-supplier.sif" | grep -F '{SIF_SHA256}'
sha256sum "$PACKET" | grep -F '{PACKET_SHA256}'
tar -xzf "$PACKET" -C "$REPLAY/packet"
python3 -m pip install --no-index --find-links "$REPLAY/wheelhouse" --target "$REPLAY/bb-deps" -r "$REPLAY/bb_mini_reqs.txt"
apptainer exec --containall --cleanenv --net --network none \\
  --bind "$REPO:/bb:ro" --bind "$REPLAY/packet:/packet:ro" \\
  --bind "$REPLAY/out:/out" --bind "$REPLAY/bb-deps:/bb-deps:ro" \\
  --bind "$REPLAY/operators:/opt/breadboard-operators:ro" \\
  "$SIF" env PYTHONPATH=/bb-deps:/bb SLURM_JOB_ID="$SLURM_JOB_ID" \\
  /opt/breadboard-public/venv/bin/python -I -B /opt/breadboard-operators/hermes_capture_probe.py \\
  --packet-root /packet/hermes-supplier-capture-packet --output-root /out \\
  --bb-root /bb --base-commit {head_commit} --sif-sha256 {SIF_SHA256} \\
  --native-manifest-sha256 "$(<$REPLAY/native-manifest.sha256)" \\
  --public-manifest-sha256 "$(<$REPLAY/public-manifest.sha256)"
"""
    expected = {
        "summary": "summary.json",
        "cases": {case: ["bb-trace.json", "comparator-report.json"] for case in CASES},
        "trace_schema": "bb.e4.hermes-agent-trace.v1",
        "comparison": "conformance/comparators/hermes_agent.py compare_cases, all canonical fields equal",
        "H-06": "exactly 8 requests and native_stop_reason=tool_calls",
    }
    return {
        "profile": "hermes",
        "packet": {"path": f"{REMOTE}/hermes-supplier-capture-packet-rerun3.tar.gz", "sha256": f"sha256:{PACKET_SHA256}"},
        "sif": {"path": "/root/bbe4-do2-20260923/hermes/images/hermes-public.sif", "supplier_sha256": f"sha256:{SIF_SHA256}"},
        "source": {"commit": head_commit, "native_source_commit": SOURCE_COMMIT},
        "puts": [
            {"local": str(packet), "remote": f"{REMOTE}/hermes-supplier-capture-packet-rerun3.tar.gz"},
            {"local": str(wheelhouse), "remote": f"{REMOTE}/wheelhouse"},
            {"local": "/tmp/bb_mini_reqs.txt", "remote": f"{REMOTE}/bb_mini_reqs.txt"},
            *({"local": str(path), "remote": f"{REMOTE}/repo/{name}"} for name, path in source_paths.items()),
            *({"local": str(path), "remote": f"{REMOTE}/operators/{name}"} for name, path in kit_paths.items()),
        ],
        "sbatch_script": sbatch,
        "resources": {"cpus": 16, "memory_gb": 64, "time_minutes": 240},
        "gets": [
            {"remote": f"{REMOTE}/out/summary.json", "local": str(output / "summary.json")},
            *({"remote": f"{REMOTE}/out/{case}/{name}", "local": str(output / case / name)} for case in CASES for name in ("bb-trace.json", "comparator-report.json")),
            {"remote": f"{REMOTE}/bb-e4-hermes-public-replay-*.out", "local": str(output / "operator.out")},
        ],
        "expected_outputs": expected,
        "runner": "/opt/breadboard-public/venv/bin/python -I -B hermes_capture_probe.py",
    }


def _runtime_available() -> bool:
    return platform.system() == "Linux" and platform.machine() == "x86_64" and sys.version_info[:2] == (3, 12)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--kit-root", type=Path, required=True, help="admission root containing hermes_sif_compose.py and do2-20260923/hermes/kit")
    parser.add_argument("--spec", type=Path, default=Path("/tmp/hermes-conductor-replay-spec.json"))
    parser.add_argument("--run", action="store_true", help="reserved for the DO-2 operator")
    args = parser.parse_args()
    if not args.packet.is_file():
        raise SystemExit(f"packet not found: {args.packet}")
    spec = do2_job_spec(args.packet, args.output_root, kit_root=args.kit_root)
    args.spec.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.run and not _runtime_available():
        print(json.dumps({"error": "Hermes public replay requires the Linux x86_64 Python 3.12 SIF"}, sort_keys=True))
        return 2
    print(json.dumps(spec, indent=2, sort_keys=True))
    return 2 if not args.run else 0


if __name__ == "__main__":
    raise SystemExit(main())
