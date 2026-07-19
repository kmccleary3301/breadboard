from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from typing import Any

_MANIFEST_MEMBER = "payload_manifest.json"
_MANIFEST_SCHEMA = "bb.rl.phase5.transport-smoke-payload.v1"
_REPORT_SCHEMA = "bb.rl.phase3.transport_smoke.v1"
_REPORT_ID = "transport-smoke-fixed-v1"
_COMPONENT = "transport_smoke"
_NONCE = bytes.fromhex(
    "8f4b4fdc5f2ec82df80b23a5e0a34ebd0ef2734d8e9636f49e2b7df7f6fd5812"
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")
_PENDING_TARGET_SUFFIX = "-slurm-pending"
_COMPONENT_INPUT_KEYS = {
    "command_id",
    "fixed_nonce_sha256",
    "requested_target_run_id",
    "runner_source_sha256",
    "runner_test_sha256",
}
_NONCLAIMS = (
    "outer transport authority",
    "campaign admission",
    "target execution, scheduling, or placement authority",
    "restart, retry, fetch, or cleanup",
    "F2/F3/F4/F5/F7/F8",
    "GPU or topology beyond the observed one-node/one-task cardinality",
    "model, reward, training, or checkpoint",
    "score, promotion, or external acceptance",
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
exec /usr/bin/python3 -B -- "$script_dir/transport_smoke.py"
"""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validated_component_input(component_input: dict[str, str]) -> dict[str, str]:
    if type(component_input) is not dict or set(component_input) != _COMPONENT_INPUT_KEYS:
        raise ValueError("transport smoke component input keys mismatch")
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
    for label in ("runner_source_sha256", "runner_test_sha256"):
        digest = component_input.get(label)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"invalid {label.removesuffix('_sha256').replace('_', ' ')} SHA-256")
    if component_input.get("fixed_nonce_sha256") != _sha256(_NONCE):
        raise ValueError("transport smoke fixed nonce digest mismatch")
    return dict(component_input)


def _smoke_source(
    *, component_input: dict[str, str], component_input_sha256: str
) -> bytes:
    command_id = json.dumps(component_input["command_id"])
    requested_target_run_id = json.dumps(
        component_input["requested_target_run_id"]
    )
    runner_source_sha256 = json.dumps(component_input["runner_source_sha256"])
    runner_test_sha256 = json.dumps(component_input["runner_test_sha256"])
    fixed_nonce_sha256 = json.dumps(component_input["fixed_nonce_sha256"])
    input_sha256 = json.dumps(component_input_sha256)
    nonclaims = json.dumps(list(_NONCLAIMS), separators=(",", ":"))
    safe_node_name = json.dumps(_SAFE_NODE_NAME.pattern)
    return f'''from __future__ import annotations

import hashlib
import json
import os
import platform
import re

NONCE = bytes.fromhex("{_NONCE.hex()}")
REPORT_ID = "{_REPORT_ID}"
REPORT_SCHEMA = "{_REPORT_SCHEMA}"
COMPONENT = "{_COMPONENT}"
EXPECTED_COMMAND_ID = {command_id}
EXPECTED_REQUESTED_TARGET_RUN_ID = {requested_target_run_id}
EXPECTED_RUNNER_SOURCE_SHA256 = {runner_source_sha256}
EXPECTED_RUNNER_TEST_SHA256 = {runner_test_sha256}
EXPECTED_FIXED_NONCE_SHA256 = {fixed_nonce_sha256}
EXPECTED_COMPONENT_INPUT_SHA256 = {input_sha256}
NONCLAIMS = {nonclaims}
SHA256 = r"sha256:[0-9a-f]{{64}}"
SAFE_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}"
SAFE_FINAL_TARGET = r"[A-Za-z0-9][A-Za-z0-9._-]{{0,159}}"
SAFE_NODE_NAME = {safe_node_name}
NUMERIC_JOB_ID = r"[0-9]+"
MINIMUM_PYTHON_VERSION = (3, 8, 0)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n").encode()


def sha256(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("missing required environment: " + name)
    return value


def main():
    if len(os.sys.argv) != 1:
        raise RuntimeError("transport smoke accepts no arguments")
    if os.sys.version_info < MINIMUM_PYTHON_VERSION:
        raise RuntimeError("Python 3.8.0 or newer is required")
    if re.fullmatch(SAFE_IDENTIFIER, EXPECTED_COMMAND_ID) is None:
        raise RuntimeError("embedded command identity is invalid")
    if (
        re.fullmatch(SAFE_IDENTIFIER, EXPECTED_REQUESTED_TARGET_RUN_ID) is None
        or not EXPECTED_REQUESTED_TARGET_RUN_ID.endswith("-slurm-pending")
    ):
        raise RuntimeError("embedded requested target identity is invalid")
    for digest in (
        EXPECTED_RUNNER_SOURCE_SHA256,
        EXPECTED_RUNNER_TEST_SHA256,
        EXPECTED_FIXED_NONCE_SHA256,
        EXPECTED_COMPONENT_INPUT_SHA256,
    ):
        if re.fullmatch(SHA256, digest) is None:
            raise RuntimeError("embedded SHA-256 identity is invalid")

    nonce_sha256 = sha256(NONCE)
    if nonce_sha256 != EXPECTED_FIXED_NONCE_SHA256:
        raise RuntimeError("embedded fixed nonce digest mismatch")
    component_input = {{
        "command_id": EXPECTED_COMMAND_ID,
        "fixed_nonce_sha256": EXPECTED_FIXED_NONCE_SHA256,
        "requested_target_run_id": EXPECTED_REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": EXPECTED_RUNNER_SOURCE_SHA256,
        "runner_test_sha256": EXPECTED_RUNNER_TEST_SHA256,
    }}
    if sha256(canonical(component_input)) != EXPECTED_COMPONENT_INPUT_SHA256:
        raise RuntimeError("embedded component input digest mismatch")

    command_id = required("PHASE3_COMMAND_ID")
    if re.fullmatch(SAFE_IDENTIFIER, command_id) is None:
        raise RuntimeError("invalid PHASE3_COMMAND_ID")
    if command_id != EXPECTED_COMMAND_ID:
        raise RuntimeError("PHASE3_COMMAND_ID mismatch")
    phase3_job_id = required("PHASE3_SLURM_JOB_ID")
    slurm_job_id = required("SLURM_JOB_ID")
    if (
        re.fullmatch(NUMERIC_JOB_ID, phase3_job_id) is None
        or re.fullmatch(NUMERIC_JOB_ID, slurm_job_id) is None
        or phase3_job_id != slurm_job_id
    ):
        raise RuntimeError("numeric Phase 3 and Slurm job IDs must match")
    target_run_id = required("PHASE3_TARGET_RUN_ID")
    expected_target_run_id = (
        EXPECTED_REQUESTED_TARGET_RUN_ID[:-len("pending")] + slurm_job_id
    )
    if re.fullmatch(SAFE_FINAL_TARGET, target_run_id) is None:
        raise RuntimeError("invalid PHASE3_TARGET_RUN_ID")
    if target_run_id != expected_target_run_id:
        raise RuntimeError("PHASE3_TARGET_RUN_ID mismatch")
    payload_zip_sha256 = required("PHASE3_PAYLOAD_ZIP_SHA256")
    if re.fullmatch(SHA256, payload_zip_sha256) is None:
        raise RuntimeError("invalid PHASE3_PAYLOAD_ZIP_SHA256")
    if required("SLURM_NNODES") != "1":
        raise RuntimeError("SLURM_NNODES must equal 1")
    if required("SLURM_NTASKS") != "1":
        raise RuntimeError("SLURM_NTASKS must equal 1")
    slurmd_nodename = required("SLURMD_NODENAME")
    if re.fullmatch(SAFE_NODE_NAME, slurmd_nodename) is None:
        raise RuntimeError("invalid SLURMD_NODENAME")
    expected_identity = {{
        "command_id": EXPECTED_COMMAND_ID,
        "requested_target_run_id": EXPECTED_REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": EXPECTED_RUNNER_SOURCE_SHA256,
        "runner_test_sha256": EXPECTED_RUNNER_TEST_SHA256,
        "target_run_id": expected_target_run_id,
    }}
    observed_identity = {{
        "command_id": command_id,
        "payload_zip_sha256": payload_zip_sha256,
        "phase3_slurm_job_id": phase3_job_id,
        "slurm_job_id": slurm_job_id,
        "target_run_id": target_run_id,
    }}
    node_observation = {{
        "slurm_nnodes": 1,
        "slurm_ntasks": 1,
        "slurmd_nodename": slurmd_nodename,
    }}
    observation_output = {{
        "component_input_sha256": EXPECTED_COMPONENT_INPUT_SHA256,
        "node_observation": node_observation,
        "nonce_sha256": nonce_sha256,
        "observed_identity": observed_identity,
    }}
    observation_output_sha256 = sha256(canonical(observation_output))
    report = {{
        "authoritative": False,
        "claim_boundary": "raw_harmless_nonce_observation_only",
        "component": COMPONENT,
        "command_id": command_id,
        "component_input_digest": EXPECTED_COMPONENT_INPUT_SHA256,
        "payload_zip_sha256": payload_zip_sha256,
        "slurm_job_id": slurm_job_id,
        "target_run_id": target_run_id,
        "expected_identity": expected_identity,
        "node_observation": node_observation,
        "nonclaims": NONCLAIMS,
        "nonce_sha256": nonce_sha256,
        "observation_kind": "raw_harmless_nonce_observation",
        "observation_output": observation_output,
        "observation_output_sha256": observation_output_sha256,
        "observed_identity": observed_identity,
        "outer_runner_authority_required": True,
        "passed": True,
        "report_id": REPORT_ID,
        "runtime": {{
            "python_executable": os.sys.executable,
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        }},
        "schema_version": REPORT_SCHEMA,
    }}
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    print("TRANSPORT_SMOKE_NONCE_SHA256=" + nonce_sha256, flush=True)
    print("PHASE3_COMPONENT_REPORT_JSON=" + encoded, flush=True)
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


def construct_transport_smoke_payload(
    component_input: dict[str, str],
) -> tuple[bytes, bytes]:
    """Construct the exact reviewed payload ZIP and its canonical manifest."""

    component_input = _validated_component_input(component_input)
    component_input_sha256 = _sha256(_canonical(component_input))
    members = {
        "run.sh": (_RUN_SH, 0o500),
        "transport_smoke.py": (
            _smoke_source(
                component_input=component_input,
                component_input_sha256=component_input_sha256,
            ),
            0o400,
        ),
    }
    manifest = {
        "command_id": component_input["command_id"],
        "component": _COMPONENT,
        "component_input": component_input,
        "component_input_sha256": component_input_sha256,
        "execution_contract": {
            "argv": [],
            "minimum_python_version": "3.8.0",
            "python_bytecode_writes": False,
            "required_executables": ["/bin/bash", "/usr/bin/python3"],
        },
        "fixed_nonce_sha256": component_input["fixed_nonce_sha256"],
        "members": [
            {
                "mode": f"{mode:04o}",
                "path": name,
                "sha256": _sha256(raw),
                "size_bytes": len(raw),
            }
            for name, (raw, mode) in sorted(members.items())
        ],
        "nonclaims": list(_NONCLAIMS),
        "report_id": _REPORT_ID,
        "report_schema_version": _REPORT_SCHEMA,
        "requested_target_run_id": component_input["requested_target_run_id"],
        "resources": {
            "deadline_seconds": 300,
            "gpus": 0,
            "nodes": 1,
            "tasks": 1,
        },
        "runner_source_sha256": component_input["runner_source_sha256"],
        "runner_test_sha256": component_input["runner_test_sha256"],
        "schema_version": _MANIFEST_SCHEMA,
    }
    manifest_raw = _canonical(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = b""
        for name, (raw, mode) in sorted(members.items()):
            _member(archive, name, raw, mode)
        _member(archive, _MANIFEST_MEMBER, manifest_raw, 0o400)
    return buffer.getvalue(), manifest_raw
