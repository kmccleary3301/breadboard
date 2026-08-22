from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f1_preflight import IMAGE_REF, canonical_json_bytes
from scripts.rl_phase5.build_f1_preflight_bundle import build_bundle

_ATTEMPT = re.compile(r"^f1-[a-z0-9-]{8,80}$")


def _zip_member(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    mode: int,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, payload)


def build_payload(
    *,
    breadboard_root: Path,
    wrapper_root: Path,
    output: Path,
    attempt_id: str,
) -> dict[str, object]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid F1 attempt id")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f1-payload-build-") as temporary:
        bundle_path = Path(temporary) / "f1-source-bundle.tar.gz"
        source_inventory = build_bundle(
            breadboard_root.resolve(), wrapper_root.resolve(), bundle_path
        )
        bundle = bundle_path.read_bytes()
    run_script = (
        "#!/bin/sh\n"
        "set -eu\n"
        "work=$(mktemp -d)\n"
        "cleanup() { rm -rf \"$work\"; }\n"
        "trap cleanup EXIT INT TERM\n"
        "tar -xzf ./f1-source-bundle.tar.gz -C \"$work\"\n"
        f"python3 \"$work/scripts/rl_phase5/run_f1_target_command.py\" remote "
        f"--bundle-root \"$work\" --attempt-id {attempt_id}\n"
    ).encode("utf-8")
    manifest = {
        "schema_version": "bb.rl.f1.phase3-payload.v1",
        "attempt_id": attempt_id,
        "image_reference": IMAGE_REF,
        "source_bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "source_bundle_size_bytes": len(bundle),
        "source_inventory": source_inventory,
        "run_script_sha256": hashlib.sha256(run_script).hexdigest(),
    }
    with zipfile.ZipFile(output, mode="x") as archive:
        _zip_member(
            archive,
            "f1-source-bundle.tar.gz",
            bundle,
            mode=0o600,
        )
        _zip_member(
            archive,
            "F1_PAYLOAD_MANIFEST.json",
            canonical_json_bytes(manifest),
            mode=0o600,
        )
        _zip_member(archive, "run.sh", run_script, mode=0o700)
    result = {
        "schema_version": "bb.rl.f1.phase3-payload-build.v1",
        "attempt_id": attempt_id,
        "payload_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "payload_size_bytes": output.stat().st_size,
        "source_bundle_sha256": manifest["source_bundle_sha256"],
        "source_tree_sha256": source_inventory["tree_sha256"],
        "breadboard_head": source_inventory["breadboard_head"],
        "wrapper_head": source_inventory["wrapper_head"],
        "image_reference": IMAGE_REF,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic F1 payload executed by the Phase 3 Slurm runner"
    )
    parser.add_argument("--breadboard-root", type=Path, required=True)
    parser.add_argument("--wrapper-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-id", default="f1-" + uuid.uuid4().hex)
    args = parser.parse_args()
    result = build_payload(
        breadboard_root=args.breadboard_root,
        wrapper_root=args.wrapper_root,
        output=args.output,
        attempt_id=args.attempt_id,
    )
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
