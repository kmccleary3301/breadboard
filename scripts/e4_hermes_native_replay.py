"""Author the DO-2 Hermes public-SIF replay job and fail closed elsewhere."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PACKET_SHA256 = "870fc991ddf72271766b90f113921d1c0bfff3fb58090550691bac1fd9a780d6"
SUPPLIER_SIF_SHA256 = "70455f244b35912f319363c05c9e163f119c0235656124a797c0aad950103033"
SOURCE_COMMIT = "939e45c91d751fadd94dcd1b873ac3cb44846213"
CASES = (
    "H-01-normal-memory-skill-write", "H-02-mixed-invalid-name",
    "H-03-visible-empty-recovery", "H-04-name-repair-duplicate",
)
REMOTE = "/root/bbe4-do2-20260923/hermes/replay"
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


def _prepare_bundle(head: str, bundle: Path) -> str:
    checkout = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "-C", str(checkout), "bundle", "create", str(bundle), "HEAD"], check=True)
    return hashlib.sha256(bundle.read_bytes()).hexdigest()


def verify_local_bundle(bundle: Path, head: str) -> None:
    with tempfile.TemporaryDirectory(prefix="hermes-head-check-") as work:
        checkout = Path(work) / "repo"
        subprocess.run(["git", "clone", "--quiet", str(bundle), str(checkout)], check=True)
        actual = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"],
                                check=True, capture_output=True, text=True).stdout
        if actual != head or status:
            raise ValueError("bundle checkout HEAD differs or repository is dirty")


def do2_job_spec(
    packet: Path, output: Path, wheelhouse: Path = Path("/tmp/hermes-conductor-wheelhouse"),
    *, kit_root: Path, bundle: Path = Path("/tmp/hermes-conductor-head.bundle"),
    sidecar: Path | None = None,
) -> dict[str, Any]:
    checkout = Path(__file__).resolve().parents[1]
    if not (checkout / "breadboard").is_dir() or not (checkout / "conformance").is_dir():
        raise ValueError(f"source tree missing required directory in {checkout}")
    kit_root = kit_root.resolve()
    kit_paths = {path.name: kit_root / path for path in KIT_PATHS}
    if any(not path.is_file() for path in kit_paths.values()):
        raise ValueError(f"kit tree missing required operator in {kit_root}")
    head_commit = _checkout_commit()
    bundle = bundle.resolve()
    bundle_sha256 = _prepare_bundle(head_commit, bundle)
    verify_local_bundle(bundle, head_commit)
    composer_sha = hashlib.sha256(kit_paths["hermes_sif_compose.py"].read_bytes()).hexdigest()
    expected_sidecar = Path(
        f"/root/bbe4-do2-20260923/hermes/images/hermes-public-{head_commit[:12]}-{composer_sha[:12]}.sif.json"
    )
    if sidecar is not None and sidecar != expected_sidecar:
        raise ValueError("sidecar path differs from sealed public SIF name")
    sidecar = expected_sidecar
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
SIDECAR={sidecar}
REPO=$REPLAY/repo
PACKET=$REPLAY/hermes-supplier-capture-packet-rerun3.tar.gz
rm -rf "$REPLAY/out" "$REPLAY/packet" "$REPLAY/bb-deps" "$REPO"
mkdir -p "$REPLAY/out" "$REPLAY/packet" "$REPLAY/bb-deps"
printf '%s  %s\\n' '{bundle_sha256}' "$REPLAY/hermes-head.bundle" | sha256sum --check --status
git clone --quiet "$REPLAY/hermes-head.bundle" "$REPO"
test "$(git -C "$REPO" rev-parse HEAD)" = '{head_commit}'
test -z "$(git -C "$REPO" status --porcelain)"
printf '%s  %s\\n' '{PACKET_SHA256}' "$PACKET" | sha256sum --check --status
export REPLAY
python3 - "$SIDECAR" '{head_commit}' '{SUPPLIER_SIF_SHA256}' <<'PY'
import hashlib,json,re,sys
from pathlib import Path
record=json.loads(Path(sys.argv[1]).read_bytes())
if record.get("schema_version") != "bb.e4.hermes-public-sif.v1" or record.get("head_commit") != sys.argv[2] or record.get("supplier_sha256") != sys.argv[3]:
    raise SystemExit("public SIF provenance differs")
composer=Path(__import__("os").environ["REPLAY"])/"operators/hermes_sif_compose.py"
if record.get("composer_sha256") != hashlib.sha256(composer.read_bytes()).hexdigest():
    raise SystemExit("composer bytes differ from public SIF")
sif=Path(record.get("sif_path",""))
if not re.fullmatch(r"hermes-public-[0-9a-f]{{12}}-[0-9a-f]{{12}}\\.sif",sif.name) or sif.name != f'hermes-public-{{sys.argv[2][:12]}}-{{record["composer_sha256"][:12]}}.sif' or sif.parent != Path("/root/bbe4-do2-20260923/hermes/images"):
    raise SystemExit("public SIF path is not the sealed version")
if hashlib.sha256(sif.read_bytes()).hexdigest() != record.get("sif_sha256"):
    raise SystemExit("public SIF bytes differ from sidecar")
print(sif)
PY
SIF=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sif_path"])' "$SIDECAR")
SIF_SHA=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sif_sha256"])' "$SIDECAR")
tar -xzf "$PACKET" -C "$REPLAY/packet"
python3 -m pip install --no-index --find-links "$REPLAY/wheelhouse" --target "$REPLAY/bb-deps" -r "$REPLAY/bb_mini_reqs.txt"
apptainer exec --containall --cleanenv --net --network none \\
  --bind "$REPO:/bb:ro" --bind "$REPLAY/packet:/packet:ro" \\
  --bind "$REPLAY/out:/out" --bind "$REPLAY/bb-deps:/bb-deps:ro" \\
  --bind "$REPLAY/operators:/opt/breadboard-operators:ro" \\
  "$SIF" env PYTHONPATH=/bb-deps:/bb SLURM_JOB_ID="$SLURM_JOB_ID" \\
  /bin/sh -c 'test "$(git -C /bb rev-parse HEAD)" = "$1" && test -z "$(git -C /bb status --porcelain)" && exec /opt/breadboard-public/venv/bin/python -I -B /opt/breadboard-operators/hermes_capture_probe.py --packet-root /packet/hermes-supplier-capture-packet --output-root /out --bb-root /bb --base-commit "$1" --sif-sha256 "$2" --native-manifest-sha256 "$3" --public-manifest-sha256 "$4"' sh \\
  '{head_commit}' "$SIF_SHA" "$(<$REPLAY/native-manifest.sha256)" "$(<$REPLAY/public-manifest.sha256)"
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
        "sif": {"sidecar": str(sidecar), "supplier_sha256": f"sha256:{SUPPLIER_SIF_SHA256}"},
        "source": {"commit": head_commit, "native_source_commit": SOURCE_COMMIT, "bundle_sha256": f"sha256:{bundle_sha256}"},
        "puts": [
            {"local": str(packet), "remote": f"{REMOTE}/hermes-supplier-capture-packet-rerun3.tar.gz"},
            {"local": str(wheelhouse), "remote": f"{REMOTE}/wheelhouse"},
            {"local": "/tmp/bb_mini_reqs.txt", "remote": f"{REMOTE}/bb_mini_reqs.txt"},
            {"local": str(bundle), "remote": f"{REMOTE}/hermes-head.bundle"},
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--kit-root", type=Path, required=True, help="admission root containing hermes_sif_compose.py and do2-20260923/hermes/kit")
    parser.add_argument("--sidecar", type=Path, required=True, help="versioned public SIF sidecar on DO-2")
    parser.add_argument("--bundle", type=Path, default=Path("/tmp/hermes-conductor-head.bundle"))
    parser.add_argument("--spec", type=Path, default=Path("/tmp/hermes-conductor-replay-spec.json"))
    args = parser.parse_args()
    if not args.packet.is_file():
        raise SystemExit(f"packet not found: {args.packet}")
    spec = do2_job_spec(args.packet, args.output_root, kit_root=args.kit_root, bundle=args.bundle, sidecar=args.sidecar)
    args.spec.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(spec, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
