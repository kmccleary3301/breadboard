from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f2_terminal import F1_PREREQUISITE_ID, F1_PREREQUISITE_REF, F1_PREREQUISITE_ROOT
from scripts.rl_phase5.build_f2_source_bundle import build_bundle
from scripts.rl_phase5.run_f2_target_command import HOST_RUNTIME_ROOT, WRAPPER_BASE_IMAGE_REF

_ATTEMPT = re.compile(r"^f2-[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _member(archive: zipfile.ZipFile, name: str, raw: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (mode & 0o777) << 16
    archive.writestr(info, raw)


def build_payload(*, breadboard_root: Path, wrapper_root: Path, output: Path, attempt_id: str, f1_prerequisite_ref: str, config_ref: str, task_ref: str, verifier_ref: str, policy_ref: str) -> dict[str, object]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid F2 attempt id")
    authorities = {"f1_prerequisite_ref": f1_prerequisite_ref, "config_ref": config_ref, "task_ref": task_ref, "verifier_ref": verifier_ref, "policy_ref": policy_ref}
    for name, value in authorities.items():
        if not _SHA.fullmatch(value):
            raise ValueError(f"{name} must be canonical lowercase sha256 ref")
    if f1_prerequisite_ref != F1_PREREQUISITE_REF:
        raise ValueError("F2 requires the independently approved canonical F1 prerequisite")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f2-payload-build-") as temporary:
        bundle_path = Path(temporary) / "f2-source-bundle.tar.gz"
        inventory = build_bundle(breadboard_root, wrapper_root, bundle_path)
        bundle = bundle_path.read_bytes()
    quoted = " ".join(f"--{name.replace('_', '-')} {value}" for name, value in authorities.items())
    run_script = ("#!/bin/sh\nset -eu\nwork=$(mktemp -d)\ncleanup() { rm -rf \"$work\"; }\ntrap cleanup EXIT INT TERM\n"
                  "tar -xzf ./f2-source-bundle.tar.gz -C \"$work\"\n"
                  "if [ -n \"${F2_CREDENTIAL_FILE:-}\" ]; then set -- --credential-file \"$F2_CREDENTIAL_FILE\"; else set --; fi\n"
                  f"exec {HOST_RUNTIME_ROOT}/bin/python \"$work/scripts/rl_phase5/run_f2_target_command.py\" remote --bundle-root \"$work\" --attempt-id {attempt_id} {quoted} \"$@\"\n").encode()
    forbidden = (b"ssh ", b"scp ", b"srun ", b"SECRET", b"/Users/", b"/home/")
    if any(token in run_script for token in forbidden):
        raise ValueError("run.sh contains forbidden nested transport, secret, or host path")
    manifest = {"schema_version": "bb.rl.f2.phase3-payload.v1", "attempt_id": attempt_id, "scratch_wrapper_base_image_ref": WRAPPER_BASE_IMAGE_REF, "canonical_wrapper_image_authority": "composition_required", "f1_prerequisite_id": F1_PREREQUISITE_ID, "f1_prerequisite_root": F1_PREREQUISITE_ROOT, **authorities, "source_bundle_sha256": "sha256:" + hashlib.sha256(bundle).hexdigest(), "source_bundle_size_bytes": len(bundle), "source_inventory": inventory, "run_script_sha256": "sha256:" + hashlib.sha256(run_script).hexdigest()}
    with zipfile.ZipFile(output, mode="x") as archive:
        _member(archive, "f2-source-bundle.tar.gz", bundle, 0o600)
        _member(archive, "F2_PAYLOAD_MANIFEST.json", canonical(manifest), 0o600)
        _member(archive, "run.sh", run_script, 0o700)
    payload = output.read_bytes()
    return {"schema_version": "bb.rl.f2.phase3-payload-build.v1", "attempt_id": attempt_id, "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(), "payload_size_bytes": len(payload), "source_bundle_sha256": manifest["source_bundle_sha256"], "source_tree_sha256": inventory["tree_sha256"], "breadboard_head": inventory["breadboard_head"], "wrapper_head": inventory["wrapper_head"], "scratch_wrapper_base_image_ref": WRAPPER_BASE_IMAGE_REF, "canonical_wrapper_image_authority": "composition_required", **authorities}


def build_image_payload(*, breadboard_root: Path, wrapper_root: Path, output: Path, attempt_id: str) -> dict[str, object]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid F2 attempt id")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f2-image-payload-") as temporary:
        bundle_path = Path(temporary) / "f2-source-bundle.tar.gz"
        inventory = build_bundle(breadboard_root, wrapper_root, bundle_path)
        bundle = bundle_path.read_bytes()
    source_key = str(inventory["tree_sha256"]).removeprefix("sha256:")
    shared = f"/shared/breadboard-f2/wrapper-images/{source_key}"
    inventory_raw = canonical(inventory)
    run_script = (
        "#!/bin/sh\nset -eu\n"
        "work=$(mktemp -d)\n"
        f"out={shared}\n"
        "test ! -e \"$out\"\n"
        "mkdir -p \"$out\"\n"
        "cleanup() { rc=$?; rm -rf \"$work\"; if [ \"$rc\" -ne 0 ]; then rm -rf \"$out\"; fi; exit \"$rc\"; }\n"
        "trap cleanup EXIT INT TERM\n"
        "tar -xzf ./f2-source-bundle.tar.gz -C \"$work\"\n"
        f"{HOST_RUNTIME_ROOT}/bin/python \"$work/scripts/rl_phase5/run_f2_target_command.py\" build-image "
        "--source-bundle ./f2-source-bundle.tar.gz --source-inventory ./F2_SOURCE_INVENTORY.json "
        "--output-tar \"$out/wrapper-image.tar\" --report \"$out/build-report.json\" --emit-phase3-component\n"
    ).encode()
    manifest = {"schema_version": "bb.rl.f2.scratch-image-phase3-payload.v1", "attempt_id": attempt_id, "source_tree_ref": inventory["tree_sha256"], "source_bundle_ref": "sha256:" + hashlib.sha256(bundle).hexdigest(), "shared_output": shared, "run_script_sha256": "sha256:" + hashlib.sha256(run_script).hexdigest()}
    with zipfile.ZipFile(output, "x") as archive:
        _member(archive, "f2-source-bundle.tar.gz", bundle, 0o600)
        _member(archive, "F2_SOURCE_INVENTORY.json", inventory_raw, 0o600)
        _member(archive, "F2_IMAGE_BUILD_PAYLOAD_MANIFEST.json", canonical(manifest), 0o600)
        _member(archive, "run.sh", run_script, 0o700)
    payload_raw = output.read_bytes()
    return {**manifest, "payload_ref": "sha256:" + hashlib.sha256(payload_raw).hexdigest(), "payload_size": len(payload_raw)}


def build_host_runtime_payload(*, breadboard_root: Path, wrapper_root: Path, output: Path, attempt_id: str, uv_path: str, uv_ref: str) -> dict[str, object]:
    if not _ATTEMPT.fullmatch(attempt_id) or not _SHA.fullmatch(uv_ref):
        raise ValueError("host runtime payload authorities are invalid")
    if not re.fullmatch(r"/[A-Za-z0-9._/+:-]+", uv_path):
        raise ValueError("managed uv path must be an exact safe absolute path")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f2-host-runtime-payload-") as temporary:
        bundle_path = Path(temporary) / "f2-source-bundle.tar.gz"
        inventory = build_bundle(breadboard_root, wrapper_root, bundle_path)
        bundle = bundle_path.read_bytes()
    source_key = str(inventory["tree_sha256"]).removeprefix("sha256:")
    shared = f"/shared/breadboard-f2/host-runtime/{source_key}"
    inventory_raw = canonical(inventory)
    uv_digest = uv_ref.removeprefix("sha256:")
    run_script = (
        "#!/bin/sh\nset -eu\n"
        f"uv={uv_path}\n"
        f"printf '%s  %s\\n' {uv_digest} \"$uv\" | sha256sum -c -\n"
        "work=$(mktemp -d)\n"
        f"out={shared}\n"
        "test ! -e \"$out\"\n"
        "cleanup() { rc=$?; rm -rf \"$work\"; if [ \"$rc\" -ne 0 ]; then chmod -R u+w \"$out\" 2>/dev/null || true; rm -rf \"$out\"; fi; exit \"$rc\"; }\n"
        "trap cleanup EXIT INT TERM\n"
        "tar -xzf ./f2-source-bundle.tar.gz -C \"$work\"\n"
        "\"$uv\" run --python 3.12 --no-project -- python \"$work/scripts/rl_phase5/run_f2_target_command.py\" build-managed-host-runtime "
        "--uv \"$uv\" --source-bundle ./f2-source-bundle.tar.gz --source-inventory ./F2_SOURCE_INVENTORY.json "
        "--output \"$out/runtime-authority\" --report \"$out/build-report.json\" --emit-phase3-component\n"
    ).encode()
    manifest = {"schema_version": "bb.rl.f2.scratch-managed-host-runtime-phase3-payload.v1", "attempt_id": attempt_id, "source_tree_ref": inventory["tree_sha256"], "source_bundle_ref": "sha256:" + hashlib.sha256(bundle).hexdigest(), "uv_path": uv_path, "uv_ref": uv_ref, "shared_output": shared, "run_script_sha256": "sha256:" + hashlib.sha256(run_script).hexdigest(), "canonical_episode_allowed": False}
    with zipfile.ZipFile(output, "x") as archive:
        _member(archive, "f2-source-bundle.tar.gz", bundle, 0o600)
        _member(archive, "F2_SOURCE_INVENTORY.json", inventory_raw, 0o600)
        _member(archive, "F2_HOST_RUNTIME_PAYLOAD_MANIFEST.json", canonical(manifest), 0o600)
        _member(archive, "run.sh", run_script, 0o700)
    payload_raw = output.read_bytes()
    return {**manifest, "payload_ref": "sha256:" + hashlib.sha256(payload_raw).hexdigest(), "payload_size": len(payload_raw)}


def build_existing_host_runtime_payload(*, breadboard_root: Path, wrapper_root: Path, output: Path, attempt_id: str, python_path: str, python_ref: str) -> dict[str, object]:
    if not _ATTEMPT.fullmatch(attempt_id) or not _SHA.fullmatch(python_ref) or not re.fullmatch(r"/[A-Za-z0-9._/+:-]+", python_path):
        raise ValueError("managed Python payload authorities are invalid")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f2-existing-runtime-payload-") as temporary:
        bundle_path = Path(temporary) / "f2-source-bundle.tar.gz"
        inventory = build_bundle(breadboard_root, wrapper_root, bundle_path)
        bundle = bundle_path.read_bytes()
    shared = f"/shared/breadboard-f2/host-runtime/{str(inventory['tree_sha256']).removeprefix('sha256:')}"
    digest = python_ref.removeprefix("sha256:")
    run_script = (
        "#!/bin/sh\nset -eu\n"
        f"python={python_path}\n"
        f"printf '%s  %s\\n' {digest} \"$python\" | sha256sum -c -\n"
        "work=$(mktemp -d)\n"
        f"out={shared}\n"
        "test ! -e \"$out\"\nmkdir -p \"$out\"\n"
        "cleanup() { rc=$?; rm -rf \"$work\"; if [ \"$rc\" -ne 0 ]; then chmod -R u+w \"$out\" 2>/dev/null || true; rm -rf \"$out\"; fi; exit \"$rc\"; }\ntrap cleanup EXIT INT TERM\n"
        "tar -xzf ./f2-source-bundle.tar.gz -C \"$work\"\n"
        "\"$python\" \"$work/scripts/rl_phase5/run_f2_target_command.py\" build-host-runtime --python \"$python\" "
        "--source-bundle ./f2-source-bundle.tar.gz --source-inventory ./F2_SOURCE_INVENTORY.json "
        "--output \"$out/runtime\" --report \"$out/build-report.json\" --emit-phase3-component\n"
    ).encode()
    manifest = {"schema_version": "bb.rl.f2.scratch-host-runtime-phase3-payload.v1", "attempt_id": attempt_id, "source_tree_ref": inventory["tree_sha256"], "source_bundle_ref": "sha256:" + hashlib.sha256(bundle).hexdigest(), "builder_python_path": python_path, "builder_python_ref": python_ref, "shared_output": shared, "run_script_sha256": "sha256:" + hashlib.sha256(run_script).hexdigest(), "canonical_episode_allowed": False}
    with zipfile.ZipFile(output, "x") as archive:
        _member(archive, "f2-source-bundle.tar.gz", bundle, 0o600)
        _member(archive, "F2_SOURCE_INVENTORY.json", canonical(inventory), 0o600)
        _member(archive, "F2_HOST_RUNTIME_PAYLOAD_MANIFEST.json", canonical(manifest), 0o600)
        _member(archive, "run.sh", run_script, 0o700)
    raw = output.read_bytes()
    return {**manifest, "payload_ref": "sha256:" + hashlib.sha256(raw).hexdigest(), "payload_size": len(raw)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--breadboard-root", type=Path, required=True); parser.add_argument("--wrapper-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--scratch-image-build", action="store_true")
    parser.add_argument("--scratch-host-runtime-build", action="store_true")
    parser.add_argument("--uv-path")
    parser.add_argument("--uv-ref")
    parser.add_argument("--builder-python-path")
    parser.add_argument("--builder-python-ref")
    for name in ("f1-prerequisite-ref", "config-ref", "task-ref", "verifier-ref", "policy-ref"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    if args.scratch_image_build and args.scratch_host_runtime_build:
        parser.error("scratch build modes are mutually exclusive")
    if args.scratch_host_runtime_build:
        if args.builder_python_path is not None or args.builder_python_ref is not None:
            if args.builder_python_path is None or args.builder_python_ref is None:
                parser.error("host runtime build requires both builder Python path/ref")
            result = build_existing_host_runtime_payload(breadboard_root=args.breadboard_root, wrapper_root=args.wrapper_root, output=args.output, attempt_id=args.attempt_id, python_path=args.builder_python_path, python_ref=args.builder_python_ref)
        else:
            if args.uv_path is None or args.uv_ref is None:
                parser.error("host runtime build requires exact uv path/ref")
            result = build_host_runtime_payload(breadboard_root=args.breadboard_root, wrapper_root=args.wrapper_root, output=args.output, attempt_id=args.attempt_id, uv_path=args.uv_path, uv_ref=args.uv_ref)
    elif args.scratch_image_build:
        result = build_image_payload(breadboard_root=args.breadboard_root, wrapper_root=args.wrapper_root, output=args.output, attempt_id=args.attempt_id)
    else:
        missing = [name for name in ("f1_prerequisite_ref", "config_ref", "task_ref", "verifier_ref", "policy_ref") if getattr(args, name) is None]
        if missing:
            parser.error("canonical payload requires all immutable authority refs")
        result = build_payload(breadboard_root=args.breadboard_root, wrapper_root=args.wrapper_root, output=args.output, attempt_id=args.attempt_id, f1_prerequisite_ref=args.f1_prerequisite_ref, config_ref=args.config_ref, task_ref=args.task_ref, verifier_ref=args.verifier_ref, policy_ref=args.policy_ref)
    print(canonical(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
