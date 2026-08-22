from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import tempfile
import zipfile
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.rl_phase5.build_f2_source_bundle import build_bundle


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def member(archive: zipfile.ZipFile, name: str, raw: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--breadboard-root", type=Path, required=True)
    parser.add_argument("--wrapper-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--offline-image-target-path", required=True)
    parser.add_argument("--host-python-target-path", required=True)
    parser.add_argument("--host-runtime-report-target-path", required=True)
    parser.add_argument("--host-runtime-report-sha256", required=True)
    parser.add_argument("--progress-target-path", required=True)
    parser.add_argument("--image-id")
    parser.add_argument("--source-image-digest")
    parser.add_argument("--storage-driver", choices=("vfs", "overlay2"), default="vfs")
    parser.add_argument("--stage-diagnostic", action="store_true")
    args = parser.parse_args()
    if (
        not args.offline_image_target_path.startswith("/")
        or not args.progress_target_path.startswith("/")
    ):
        raise ValueError("offline image and progress target paths must be absolute")
    if (
        not args.host_python_target_path.startswith("/")
        or not args.host_runtime_report_target_path.startswith("/")
        or not args.host_runtime_report_sha256.startswith("sha256:")
        or len(args.host_runtime_report_sha256) != 71
    ):
        raise ValueError("host runtime authority is not exact")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f2-broker-payload-") as temporary:
        bundle_path = Path(temporary) / "source.tar.gz"
        inventory = build_bundle(args.breadboard_root, args.wrapper_root, bundle_path)
        bundle = bundle_path.read_bytes()
    optional = ""
    if args.image_id:
        optional += " --image-id " + shlex.quote(args.image_id)
    if args.source_image_digest:
        optional += " --source-image-digest " + shlex.quote(args.source_image_digest)
    if args.stage_diagnostic:
        optional += " --stage-diagnostic"
    report_hex = args.host_runtime_report_sha256.removeprefix("sha256:")
    run_script = (
        "#!/bin/sh\nset -eu\n"
        "work=$(mktemp -d)\n"
        "cleanup() { rm -rf \"$work\"; }\n"
        "trap cleanup EXIT INT TERM\n"
        f"test \"$(sha256sum {shlex.quote(args.host_runtime_report_target_path)}"
        " | cut -d' ' -f1)\" = "
        f"{shlex.quote(report_hex)}\n"
        "tar -xzf ./f2-source-bundle.tar.gz -C \"$work\"\n"
        "set +e\n"
        f"PYTHONPATH=\"$work\" {shlex.quote(args.host_python_target_path)}"
        " \"$work/scripts/rl_phase5/f2_private_broker_lifecycle_probe.py\""
        f" --attempt-id {shlex.quote(args.attempt_id)}"
        f" --offline-image-tar {shlex.quote(args.offline_image_target_path)}"
        f" --progress-path {shlex.quote(args.progress_target_path)}"
        f" --storage-driver {args.storage_driver}{optional}\n"
        "rc=$?\nset -e\n"
        f"test \"$(sha256sum {shlex.quote(args.host_runtime_report_target_path)}"
        " | cut -d' ' -f1)\" = "
        f"{shlex.quote(report_hex)}\n"
        "cleanup\ntrap - EXIT INT TERM\nexit \"$rc\"\n"
    ).encode("ascii")
    manifest = {
        "schema_version": "bb.rl.f2.private-broker-probe-payload.v1",
        "attempt_id": args.attempt_id,
        "offline_image_target_path": args.offline_image_target_path,
        "image_id": args.image_id,
        "source_image_digest": args.source_image_digest,
        "progress_target_path": args.progress_target_path,
        "stage_diagnostic": args.stage_diagnostic,
        "storage_driver": args.storage_driver,
        "host_python_target_path": args.host_python_target_path,
        "host_runtime_report_target_path": args.host_runtime_report_target_path,
        "host_runtime_report_sha256": args.host_runtime_report_sha256,
        "source_bundle_sha256": "sha256:" + hashlib.sha256(bundle).hexdigest(),
        "source_inventory": inventory,
        "run_script_sha256": "sha256:" + hashlib.sha256(run_script).hexdigest(),
    }
    with zipfile.ZipFile(output, "x") as archive:
        member(archive, "f2-source-bundle.tar.gz", bundle, 0o600)
        member(archive, "F2_PRIVATE_BROKER_PROBE.json", canonical(manifest), 0o600)
        member(archive, "run.sh", run_script, 0o700)
    raw = output.read_bytes()
    print(canonical({
        "path": str(output),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        **manifest,
    }).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
