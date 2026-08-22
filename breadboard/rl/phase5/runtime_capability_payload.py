from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile

MANIFEST_MEMBER = "payload_manifest.json"
MANIFEST_SCHEMA = "bb.rl.phase5.runtime-preflight-capability-payload.v1"
REPORT_SCHEMA = "bb.rl.phase5.runtime-preflight-capability.v1"
REPORT_ID = "rc4-linux-capability-v1"
COMPONENT = "runtime_preflight_capability"
FIXED_NONCE = bytes.fromhex(
    "8f4b4fdc5f2ec82df80b23a5e0a34ebd0ef2734d8e9636f49e2b7df7f6fd5812"
)
FIXED_NONCE_SHA256 = "sha256:" + hashlib.sha256(FIXED_NONCE).hexdigest()
COMPONENT_INPUT_KEYS = {
    "command_id",
    "fixed_nonce_sha256",
    "requested_target_run_id",
    "runner_source_sha256",
    "runner_test_sha256",
    "runtime_source_sha256",
    "runtime_test_sha256",
}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PENDING_TARGET_SUFFIX = "-slurm-pending"
_NONCLAIMS = (
    "campaign admission or target runtime preflight receipt",
    "clean staged repository, Beads store, candidate, migration, or selector",
    "quiescence, lease, cutover, score, completion, promotion, or external acceptance",
    "installed tool suitability beyond the recorded path, digest, and version result",
)
_RUN_SH = b"""#!/bin/bash
set -euo pipefail
umask 077
test "$#" -eq 0
export PYTHONDONTWRITEBYTECODE=1
script_source=${BASH_SOURCE[0]}
case "$script_source" in
  */*) script_dir=${script_source%/*} ;;
  *) script_dir=. ;;
esac
script_dir=$(CDPATH= cd -- "$script_dir" && pwd -P)
exec /usr/bin/python3 -B -- "$script_dir/runtime_capability_probe.py"
"""


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validated_component_input(component_input: dict[str, str]) -> dict[str, str]:
    if type(component_input) is not dict or set(component_input) != COMPONENT_INPUT_KEYS:
        raise ValueError("runtime capability component input keys mismatch")
    command_id = component_input.get("command_id")
    requested_target_run_id = component_input.get("requested_target_run_id")
    if not isinstance(command_id, str) or _SAFE_IDENTIFIER.fullmatch(command_id) is None:
        raise ValueError("invalid command_id")
    if (
        not isinstance(requested_target_run_id, str)
        or _SAFE_IDENTIFIER.fullmatch(requested_target_run_id) is None
        or not requested_target_run_id.endswith(_PENDING_TARGET_SUFFIX)
    ):
        raise ValueError(
            "invalid requested_target_run_id: expected a safe identifier ending "
            "in -slurm-pending"
        )
    for label in (
        "runner_source_sha256",
        "runner_test_sha256",
        "runtime_source_sha256",
        "runtime_test_sha256",
    ):
        digest = component_input.get(label)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"invalid {label.removesuffix('_sha256').replace('_', ' ')} SHA-256")
    if component_input.get("fixed_nonce_sha256") != FIXED_NONCE_SHA256:
        raise ValueError("runtime capability fixed nonce digest mismatch")
    return dict(component_input)


def _probe_source(
    *, component_input: dict[str, str], component_input_sha256: str
) -> bytes:
    encoded_input = json.dumps(component_input, sort_keys=True, separators=(",", ":"))
    encoded_input_sha256 = json.dumps(component_input_sha256)
    nonclaims = json.dumps(list(_NONCLAIMS), separators=(",", ":"))
    return f'''from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import resource
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

COMPONENT = {COMPONENT!r}
REPORT_ID = {REPORT_ID!r}
REPORT_SCHEMA = {REPORT_SCHEMA!r}
COMPONENT_INPUT = {encoded_input}
COMPONENT_INPUT_SHA256 = {encoded_input_sha256}
NONCLAIMS = {nonclaims}
SHA256 = re.compile(r"sha256:[0-9a-f]{{64}}")
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{{0,159}}")
MAX_VERSION_OUTPUT_BYTES = 16_384


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n").encode()


def sha256_bytes(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptor_snapshot(descriptor):
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or stat.S_IMODE(before.st_mode) & 0o111 == 0
    ):
        raise RuntimeError("retained binary is not a nonempty regular executable")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
        if not chunk:
            raise RuntimeError("retained binary changed while hashing")
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if after_identity != before_identity:
        raise RuntimeError("retained binary identity changed while hashing")
    return after, "sha256:" + digest.hexdigest(), after_identity


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("missing required environment: " + name)
    return value


def bounded_output(handle):
    handle.seek(0)
    raw = handle.read(MAX_VERSION_OUTPUT_BYTES + 1)
    if len(raw) > MAX_VERSION_OUTPUT_BYTES:
        raise RuntimeError("version command output exceeds bound")
    return raw


def limit_child_output_files():
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (MAX_VERSION_OUTPUT_BYTES, MAX_VERSION_OUTPUT_BYTES),
    )


def binary_observation(name, argv, cwd, env):
    requested = shutil.which(name, path=env["PATH"])
    if requested is None:
        return {{"name": name, "present": False}}
    try:
        requested_path = Path(requested)
        resolved = requested_path.resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if type(nofollow) is not int or nofollow == 0:
            raise RuntimeError("descriptor-bound binary observation requires O_NOFOLLOW")
        descriptor = os.open(resolved, flags | nofollow)
        try:
            metadata, executable_sha256, executable_identity = descriptor_snapshot(descriptor)
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                completed = subprocess.run(
                    [f"/proc/self/fd/{{descriptor}}", *argv],
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    check=False,
                    pass_fds=(descriptor,),
                    preexec_fn=limit_child_output_files,
                    timeout=20,
                )
                stdout_raw = bounded_output(stdout)
                stderr_raw = bounded_output(stderr)
            _, after_sha256, after_identity = descriptor_snapshot(descriptor)
            if after_identity != executable_identity or after_sha256 != executable_sha256:
                raise RuntimeError("retained binary changed across descriptor execution")
        finally:
            os.close(descriptor)
        return {{
            "name": name,
            "present": True,
            "requested_path": str(requested_path),
            "resolved_path": str(resolved),
            "sha256": executable_sha256,
            "size_bytes": metadata.st_size,
            "mode": format(metadata.st_mode & 0o7777, "04o"),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "execution_path": "retained_proc_self_fd",
            "version_argv": argv,
            "version_exit_code": completed.returncode,
            "version_stdout_sha256": sha256_bytes(stdout_raw),
            "version_stdout_utf8": stdout_raw.decode("utf-8", errors="replace"),
            "version_stderr_sha256": sha256_bytes(stderr_raw),
            "version_stderr_utf8": stderr_raw.decode("utf-8", errors="replace"),
        }}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {{
            "name": name,
            "present": True,
            "usable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }}


def descriptor_exec_observation(cwd, env):
    try:
        executable = Path("/bin/true").resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if type(nofollow) is not int or nofollow == 0:
            raise RuntimeError("descriptor execution requires O_NOFOLLOW")
        descriptor = os.open(executable, flags | nofollow)
        try:
            metadata, executable_sha256, executable_identity = descriptor_snapshot(descriptor)
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                completed = subprocess.run(
                    [f"/proc/self/fd/{{descriptor}}"],
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    check=False,
                    pass_fds=(descriptor,),
                    preexec_fn=limit_child_output_files,
                    timeout=20,
                )
                stdout_raw = bounded_output(stdout)
                stderr_raw = bounded_output(stderr)
            _, after_sha256, after_identity = descriptor_snapshot(descriptor)
            if after_identity != executable_identity or after_sha256 != executable_sha256:
                raise RuntimeError("descriptor executable changed across execution")
        finally:
            os.close(descriptor)
        return {{
            "executable": str(executable),
            "executable_sha256": executable_sha256,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "exit_code": completed.returncode,
            "stdout_sha256": sha256_bytes(stdout_raw),
            "stderr_sha256": sha256_bytes(stderr_raw),
            "passed": completed.returncode == 0 and not stdout_raw and not stderr_raw,
        }}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {{
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }}


def main():
    if len(os.sys.argv) != 1:
        raise RuntimeError("runtime capability probe accepts no arguments")
    if sha256_bytes(canonical(COMPONENT_INPUT)) != COMPONENT_INPUT_SHA256:
        raise RuntimeError("component input digest mismatch")
    command_id = required("PHASE3_COMMAND_ID")
    if command_id != COMPONENT_INPUT["command_id"] or SAFE_IDENTIFIER.fullmatch(command_id) is None:
        raise RuntimeError("command identity mismatch")
    phase3_job_id = required("PHASE3_SLURM_JOB_ID")
    slurm_job_id = required("SLURM_JOB_ID")
    if not phase3_job_id.isdigit() or phase3_job_id != slurm_job_id:
        raise RuntimeError("Slurm identity mismatch")
    target_run_id = required("PHASE3_TARGET_RUN_ID")
    expected_target_run_id = COMPONENT_INPUT["requested_target_run_id"][:-len("pending")] + slurm_job_id
    if target_run_id != expected_target_run_id or SAFE_IDENTIFIER.fullmatch(target_run_id) is None:
        raise RuntimeError("target run identity mismatch")
    payload_sha256 = required("PHASE3_PAYLOAD_ZIP_SHA256")
    if SHA256.fullmatch(payload_sha256) is None:
        raise RuntimeError("payload digest is invalid")
    if required("SLURM_NNODES") != "1" or required("SLURM_NTASKS") != "1":
        raise RuntimeError("probe requires one node and one task")

    with tempfile.TemporaryDirectory(prefix="bb-p5-runtime-capability-") as temporary:
        cwd = Path(temporary)
        env = {{
            "HOME": str(cwd),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        }}
        descriptor = descriptor_exec_observation(cwd, env)
        bd = binary_observation("bd", ["--version"], cwd, env)
        dolt = binary_observation("dolt", ["version"], cwd, env)

    linux = platform.system() == "Linux"
    proc_fd = Path("/proc/self/fd").is_dir()
    blocked_reasons = []
    if not linux:
        blocked_reasons.append("platform_not_linux")
    if not proc_fd:
        blocked_reasons.append("proc_self_fd_missing")
    if not descriptor.get("passed"):
        blocked_reasons.append("descriptor_execution_failed")
    for name, observed in (("bd", bd), ("dolt", dolt)):
        if not observed.get("present"):
            blocked_reasons.append(name + "_missing")
        elif (
            observed.get("version_exit_code") != 0
            or not isinstance(observed.get("version_stdout_utf8"), str)
            or not observed["version_stdout_utf8"].startswith(name + " version ")
        ):
            blocked_reasons.append(name + "_version_identity_failed")
    capability_ready = not blocked_reasons
    observation = {{
        "linux": linux,
        "proc_self_fd_present": proc_fd,
        "descriptor_bound_execution": descriptor,
        "bd": bd,
        "dolt": dolt,
        "platform": {{
            "architecture": platform.machine(),
            "os": platform.system().lower(),
            "os_release": platform.release(),
            "python_executable": os.sys.executable,
            "python_version": platform.python_version(),
        }},
        "runtime_source_sha256": COMPONENT_INPUT["runtime_source_sha256"],
        "runtime_test_sha256": COMPONENT_INPUT["runtime_test_sha256"],
    }}
    report = {{
        "authoritative": False,
        "blocked_reasons": blocked_reasons,
        "capability_ready": capability_ready,
        "claim_boundary": "non_mutating_linux_runtime_capability_observation_only",
        "command_id": command_id,
        "component": COMPONENT,
        "component_input_digest": COMPONENT_INPUT_SHA256,
        "nonclaims": NONCLAIMS,
        "observation": observation,
        "observation_sha256": sha256_bytes(canonical(observation)),
        "passed": capability_ready,
        "payload_zip_sha256": payload_sha256,
        "report_id": REPORT_ID,
        "schema_version": REPORT_SCHEMA,
        "slurm_job_id": slurm_job_id,
        "target_run_id": target_run_id,
    }}
    print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(report, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.encode()


def _member(archive: zipfile.ZipFile, name: str, raw: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.extra = b""
    info.comment = b""
    archive.writestr(info, raw)


def construct_runtime_capability_payload(
    component_input: dict[str, str],
) -> tuple[bytes, bytes]:
    """Construct the exact typed capability ZIP and canonical manifest."""

    component_input = _validated_component_input(component_input)
    component_input_sha256 = sha256_bytes(canonical_json_bytes(component_input))
    members = {
        "run.sh": (_RUN_SH, 0o500),
        "runtime_capability_probe.py": (
            _probe_source(
                component_input=component_input,
                component_input_sha256=component_input_sha256,
            ),
            0o400,
        ),
    }
    manifest = {
        "command_id": component_input["command_id"],
        "component": COMPONENT,
        "component_input": component_input,
        "component_input_sha256": component_input_sha256,
        "execution_contract": {
            "argv": [],
            "minimum_python_version": "3.10.0",
            "python_bytecode_writes": False,
            "required_executables": ["/bin/bash", "/bin/true", "/usr/bin/python3"],
        },
        "fixed_nonce_sha256": component_input["fixed_nonce_sha256"],
        "members": [
            {
                "mode": f"{mode:04o}",
                "path": name,
                "sha256": sha256_bytes(raw),
                "size_bytes": len(raw),
            }
            for name, (raw, mode) in sorted(members.items())
        ],
        "nonclaims": list(_NONCLAIMS),
        "report_id": REPORT_ID,
        "report_schema_version": REPORT_SCHEMA,
        "requested_target_run_id": component_input["requested_target_run_id"],
        "resources": {
            "deadline_seconds": 120,
            "gpus": 0,
            "nodes": 1,
            "tasks": 1,
        },
        "runner_source_sha256": component_input["runner_source_sha256"],
        "runner_test_sha256": component_input["runner_test_sha256"],
        "runtime_source_sha256": component_input["runtime_source_sha256"],
        "runtime_test_sha256": component_input["runtime_test_sha256"],
        "schema_version": MANIFEST_SCHEMA,
    }
    manifest_raw = canonical_json_bytes(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = b""
        for name, (raw, mode) in sorted(members.items()):
            _member(archive, name, raw, mode)
        _member(archive, MANIFEST_MEMBER, manifest_raw, 0o400)
    return buffer.getvalue(), manifest_raw
