from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f2_terminal import PARTITION, TARGET_ALIAS, TARGET_ARTIFACTS, canonical_json_bytes, parse_artifact_markers, validate_scratch
from scripts.rl_phase5.run_f2_target_command import _decode_envelope, _safe_extract, RESULT_PREFIX, RUNNER_PREFIX

_ATTEMPT = re.compile(r"^f2-[a-z0-9]+(?:-[a-z0-9]+)*$")


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def option(argv: list[str], name: str) -> str:
    indices = [i for i, value in enumerate(argv) if value == name]
    if len(indices) != 1 or indices[0] + 1 >= len(argv):
        raise ValueError(f"outer argv requires exactly one {name}")
    return argv[indices[0] + 1]


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def ingest(*, phase3_output: Path, attempt_id: str, target_run_id: str, payload_zip: Path, scratch_root: Path, secret_file: Path | None = None) -> Path:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid F2 attempt id")
    phase3_output = phase3_output.resolve(strict=True)
    payload_zip = payload_zip.resolve(strict=True)
    secret_material: tuple[bytes, ...] = ()
    if secret_file is not None:
        secret = secret_file.resolve(strict=True)
        if (secret.stat().st_mode & 0o777) != 0o400:
            raise PermissionError("secret file mode must be 0400")
        raw_secret = secret.read_bytes()
        if not raw_secret:
            raise ValueError("secret file is empty")
        secret_material = (raw_secret,)
    manifest_path = phase3_output / "phase3_command_log_manifest.json"
    manifest_raw = manifest_path.read_bytes(); manifest = json.loads(manifest_raw)
    rows = manifest.get("commands") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        raise ValueError("outer Phase3 command manifest is invalid")
    matches = [row for row in rows if isinstance(row, dict) and row.get("command_id") == attempt_id]
    if len(matches) != 1:
        raise ValueError("exactly one matching outer Phase3 row is required")
    row: dict[str, Any] = matches[0]
    if row.get("status") != "passed" or row.get("exit_code") != 0 or row.get("blocked_reason") not in (None, "") or row.get("component_failed_count") != 0 or row.get("component_passed") is not True:
        raise ValueError("outer Phase3 row did not pass with zero failed components")
    job, node = str(row.get("slurm_job_id") or ""), str(row.get("node") or "")
    if not job.isdigit() or not node or row.get("target_run_id") != target_run_id:
        raise ValueError("outer target-run/job/node identity mismatch")
    argv = row.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        raise ValueError("outer argv is invalid")
    expected = {"--ssh-alias": TARGET_ALIAS, "--partition": PARTITION, "--command-id": attempt_id, "--gres": "gpu:1", "--nodes": "1", "--ntasks": "1"}
    for name, value in expected.items():
        if option(argv, name) != value:
            raise ValueError(f"outer {name} mismatch")
    requested_target = option(argv, "--target-run-id")
    if not requested_target.endswith("-slurm-pending") or target_run_id != requested_target.removesuffix("pending") + job:
        raise ValueError("outer requested/final target-run/job join mismatch")
    argv_payload = Path(option(argv, "--payload-zip")).resolve()
    if argv_payload != payload_zip:
        raise ValueError("outer payload path mismatch")
    payload_ref = sha(payload_zip)
    relative = PurePosixPath(str(row.get("raw_log_path") or ""))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("outer raw log path is unsafe")
    raw_path = (phase3_output / Path(*relative.parts)).resolve(strict=True)
    if phase3_output not in raw_path.parents or sha(raw_path) != row.get("raw_log_sha256"):
        raise ValueError("outer raw log hash mismatch")
    raw_log = raw_path.read_bytes()
    precheck_raw_path = phase3_output / "f2_target_precheck.raw"
    precheck_report_path = phase3_output / "f2_target_precheck.json"
    precheck_raw = precheck_raw_path.read_bytes()
    precheck_report_raw = precheck_report_path.read_bytes()
    precheck = json.loads(precheck_report_raw)
    if type(precheck) is not dict or precheck.get("schema_version") != "bb.rl.f2.target-precheck.v1" or precheck.get("passed") is not True:
        raise ValueError("passing F2 target precheck is required")
    if precheck.get("raw_ref") != "sha256:" + hashlib.sha256(precheck_raw).hexdigest() or precheck.get("ssh_alias") != TARGET_ALIAS:
        raise ValueError("F2 target precheck raw identity mismatch")

    scratch_root = scratch_root.absolute()
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if scratch_root.is_symlink():
        raise ValueError("scratch root must not be a symlink")
    scratch_root = scratch_root.resolve(strict=True)
    destination = scratch_root / attempt_id
    if destination.parent != scratch_root:
        raise ValueError("attempt destination escapes scratch root")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{attempt_id}-transaction-", dir=scratch_root))
    if staging_parent.resolve().parent != scratch_root:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise ValueError("transaction staging escapes scratch root")
    staging = staging_parent / attempt_id; staging.mkdir(mode=0o700)
    try:
        runner_raw = _decode_envelope(raw_log, RUNNER_PREFIX)
        decoded_runner = staging_parent / "decoded-runner"; _safe_extract(runner_raw, decoded_runner)
        target_stdout = (decoded_runner / "target.stdout").read_bytes(); target_stderr = (decoded_runner / "target.stderr").read_bytes()
        result_raw = _decode_envelope(target_stdout, RESULT_PREFIX)
        artifacts = staging / "artifacts"; _safe_extract(result_raw, artifacts)
        runner = staging / "runner"; runner.mkdir(mode=0o700)
        markers = parse_artifact_markers(target_stdout, attempt_id)
        by_name = {marker["name"]: marker for marker in markers}
        for name, filename in TARGET_ARTIFACTS.items():
            artifact = artifacts / filename
            raw = artifact.read_bytes()
            marker = by_name[name]
            if marker["path"] != "artifacts/" + filename or marker["size"] != len(raw) or marker["sha256"] != "sha256:" + hashlib.sha256(raw).hexdigest():
                raise ValueError(f"target artifact marker mismatch: {name}")
        invocation = {"schema_version": "bb.rl.f2.runner-invocation.v1", "attempt_id": attempt_id, "target_run_id": target_run_id, "job_id": job, "node": node, "payload_ref": payload_ref}
        _write_json(runner / "invocation.json", invocation)
        (runner / "stdout.bin").write_bytes(target_stdout); (runner / "stderr.bin").write_bytes(target_stderr)
        _write_json(runner / "exit.json", {"schema_version": "bb.rl.f2.runner-exit.v1", "returncode": 0})
        outer = staging / "outer"; outer.mkdir(mode=0o700)
        _write_json(outer / "phase3_invocation.json", {"schema_version": "bb.rl.f2.phase3-invocation.v1", "argv": argv, "target_alias": TARGET_ALIAS, "partition": PARTITION, "command_id": attempt_id, "target_run_id": target_run_id, "job_id": job, "node": node, "payload_ref": payload_ref, "target_precheck": precheck, "target_precheck_raw_b64": __import__("base64").b64encode(precheck_raw).decode("ascii")})
        _write_json(outer / "transport.json", {"schema_version": "bb.rl.f2.phase3-transport.v1", "raw_log_ref": sha(raw_path), "manifest_ref": "sha256:" + hashlib.sha256(manifest_raw).hexdigest(), "runner_archive_ref": "sha256:" + hashlib.sha256(runner_raw).hexdigest(), "precheck_raw_ref": "sha256:" + hashlib.sha256(precheck_raw).hexdigest(), "precheck_report_ref": "sha256:" + hashlib.sha256(precheck_report_raw).hexdigest(), "component_failed_count": 0})
        _write_json(outer / "result_archive.json", {"schema_version": "bb.rl.f2.result-archive.v1", "attempt_id": attempt_id, "sha256": "sha256:" + hashlib.sha256(result_raw).hexdigest(), "size_bytes": len(result_raw)})
        (outer / "phase3-command.log").write_bytes(raw_log)
        (outer / "phase3-command-log-manifest.json").write_bytes(manifest_raw)
        _write_json(staging / "attempt.json", {"schema_version": "bb.rl.f2.attempt.v1", "attempt_id": attempt_id, "target_run_id": target_run_id, "command_id": attempt_id, "payload_ref": payload_ref})
        (staging / "target.stdout").write_bytes(target_stdout); (staging / "target.stderr").write_bytes(target_stderr)
        (staging / "exit_code").write_text("0\n", encoding="ascii")
        (staging / "result.tar.gz").write_bytes(result_raw); (staging / "runner-result.tar.gz").write_bytes(runner_raw)
        validate_scratch(staging, secret_material=secret_material)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    shutil.rmtree(staging_parent, ignore_errors=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and atomically ingest one passing F2 Phase3 attempt")
    parser.add_argument("--phase3-output", type=Path, required=True); parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--target-run-id", required=True); parser.add_argument("--payload-zip", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    args = parser.parse_args()
    result = ingest(phase3_output=args.phase3_output, attempt_id=args.attempt_id, target_run_id=args.target_run_id, payload_zip=args.payload_zip, scratch_root=args.scratch_root, secret_file=args.secret_file)
    print(canonical_json_bytes({"attempt_path": str(result)}).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
