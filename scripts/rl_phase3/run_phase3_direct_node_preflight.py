from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.evidence import sha256_file
from breadboard.rl.phase3.security_enforcement import enforce_command_request
from scripts.rl_phase3.run_phase3_target_command import _build_ssh_command, _safe_artifact_name

DIRECT_PREFLIGHT_SCHEMA = "bb.rl.phase4.direct_node_preflight.v1"
DIRECT_CLAIM_BOUNDARY = "phase4_direct_node_dev_preflight_only_not_phase3_promotion"
DEFAULT_IMAGE = "vllm/vllm-openai-rocm:nightly"
DEFAULT_REMOTE_ROOT = "/shared/bb-p3-root"
ENDPOINT_ENV_VARS = (
    "BREADBOARD_ORS_BASE_URL",
    "BREADBOARD_ORS_TOKEN",
    "BREADBOARD_OPENREWARD_BASE_URL",
    "BREADBOARD_OPENREWARD_TOKEN",
    "BREADBOARD_BENCHFLOW_BASE_URL",
    "BREADBOARD_BENCHFLOW_TOKEN",
    "BREADBOARD_HARBOR_BASE_URL",
    "BREADBOARD_HARBOR_TOKEN",
    "BREADBOARD_VERIFIER_BASE_URL",
    "BREADBOARD_VERIFIER_TOKEN",
    "BREADBOARD_OBJECT_STORE_BASE_URL",
    "BREADBOARD_OBJECT_STORE_BUCKET",
    "BREADBOARD_OBJECT_STORE_TOKEN",
    "BREADBOARD_SCHEDULER_BASE_URL",
    "BREADBOARD_SCHEDULER_TOKEN",
    "HF_HOME",
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _presence_shell(var_names: tuple[str, ...] = ENDPOINT_ENV_VARS) -> str:
    rows = []
    for name in var_names:
        quoted = shlex.quote(name)
        rows.append(f"if [ -n \"${{{quoted}:-}}\" ]; then echo ENV_{quoted}=present; else echo ENV_{quoted}=absent; fi")
    return "; ".join(rows)


def _remote_precheck_command(*, direct_run_id: str, command_id: str, remote_root: str, image: str, hip_visible_devices: str, create_remote_root: bool) -> str:
    mkdir = f"mkdir -p {shlex.quote(remote_root)} {shlex.quote(remote_root)}/hf_home {shlex.quote(remote_root)}/direct_node_runs; " if create_remote_root else ""
    return (
        "set -u; "
        f"export PHASE4_DIRECT_RUN_ID={shlex.quote(direct_run_id)}; "
        f"export PHASE3_COMMAND_ID={shlex.quote(command_id)}; "
        f"export HIP_VISIBLE_DEVICES={shlex.quote(hip_visible_devices)}; "
        f"{mkdir}"
        "echo PHASE4_DIRECT_NODE=$(hostname 2>/dev/null || true); "
        "echo PHASE4_DIRECT_USER=$(whoami 2>/dev/null || true); "
        "echo PHASE4_DIRECT_MODE=precheck; "
        "echo PHASE4_DIRECT_SCHEDULER=none_direct_ssh; "
        "echo PHASE4_DIRECT_GPU_MASK=$HIP_VISIBLE_DEVICES; "
        "for cmd in docker rocm-smi python3 unzip; do if command -v $cmd >/dev/null 2>&1; then echo CMD_${cmd}=present:$(command -v $cmd); else echo CMD_${cmd}=absent; fi; done; "
        "if [ -e /dev/kfd ]; then echo DEVICE_KFD=present; else echo DEVICE_KFD=absent; fi; "
        "if [ -d /dev/dri ]; then echo DEVICE_DRI=present; else echo DEVICE_DRI=absent; fi; "
        f"if [ -d {shlex.quote(remote_root)} ]; then echo REMOTE_ROOT=present; else echo REMOTE_ROOT=absent; fi; "
        f"if [ -f {shlex.quote(remote_root)}/phase3_vllm_verl_py312/bin/activate ]; then echo RUNTIME_VENV=present; else echo RUNTIME_VENV=absent; fi; "
        f"if command -v docker >/dev/null 2>&1 && docker image inspect {shlex.quote(image)} >/dev/null 2>&1; then echo RUNTIME_IMAGE=present; docker image inspect --format='IMAGE_ID={{{{.Id}}}}' {shlex.quote(image)} 2>/dev/null || true; docker image inspect --format='IMAGE_REPODIGESTS={{{{json .RepoDigests}}}}' {shlex.quote(image)} 2>/dev/null || true; else echo RUNTIME_IMAGE=absent; fi; "
        "if command -v rocm-smi >/dev/null 2>&1; then echo ROCM_SMI_BEGIN; rocm-smi --showproductname --showmeminfo vram 2>&1 | head -160; echo ROCM_SMI_END; fi; "
        + _presence_shell()
    )


def _remote_run_command(*, direct_run_id: str, command_id: str, remote_zip: str, remote_root: str, image: str, hip_visible_devices: str) -> str:
    safe_remote_root = shlex.quote(remote_root)
    safe_command = shlex.quote(command_id)
    return (
        "set -euo pipefail; "
        f"export PHASE4_DIRECT_RUN_ID={shlex.quote(direct_run_id)}; "
        "export PHASE3_TARGET_RUN_ID=; "
        f"export PHASE3_COMMAND_ID={shlex.quote(command_id)}; "
        f"export HIP_VISIBLE_DEVICES={shlex.quote(hip_visible_devices)}; "
        "echo PHASE4_DIRECT_NODE=$(hostname); "
        "echo PHASE4_DIRECT_MODE=run; "
        "echo PHASE4_DIRECT_SCHEDULER=none_direct_ssh; "
        "echo PHASE4_DIRECT_GPU_MASK=$HIP_VISIBLE_DEVICES; "
        f"test -d {safe_remote_root} || (echo PHASE4_BLOCKED_REASON=direct_runtime_root_missing; exit 92); "
        f"test -f {safe_remote_root}/phase3_vllm_verl_py312/bin/activate || (echo PHASE4_BLOCKED_REASON=direct_runtime_venv_missing; exit 94); "
        f"docker image inspect {shlex.quote(image)} >/dev/null 2>&1 || (echo PHASE4_BLOCKED_REASON=direct_runtime_image_missing; exit 93); "
        f"WORK=$(mktemp -d {safe_remote_root}/direct_node_runs/{safe_command}.XXXXXX); "
        f"echo PHASE4_DIRECT_WORKDIR=$WORK; python3 -m zipfile -e {shlex.quote(remote_zip)} \"$WORK\"; cd \"$WORK\"; test -f ./run.sh; bash ./run.sh"
    )


def _parse_key_values(log_text: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    prefixes = ("PHASE4_", "PHASE3_", "CMD_", "DEVICE_", "REMOTE_", "RUNTIME_", "IMAGE_", "ENV_")
    for line in log_text.splitlines():
        if "=" not in line or not line.startswith(prefixes):
            continue
        key, value = line.split("=", 1)
        keys[key] = value.strip()
    return keys


def _inline_component_reports(log_text: str) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        if not line.startswith("PHASE3_COMPONENT_REPORT_JSON="):
            continue
        payload = line.split("=", 1)[1]
        try:
            report = json.loads(payload)
            reports.append(report if isinstance(report, dict) else {"passed": False, "blocked_reason": "inline_report_not_object"})
        except json.JSONDecodeError:
            reports.append({"passed": False, "blocked_reason": "invalid_inline_report"})
    return reports


def _env_presence(keys: dict[str, str]) -> dict[str, bool]:
    prefix = "ENV_"
    return {key.removeprefix(prefix): value == "present" for key, value in keys.items() if key.startswith(prefix)}


def _assessment_payload(
    *,
    argv: list[str],
    mode: str,
    ssh_alias: str,
    command_id: str,
    safe_command_id: str,
    direct_run_id: str,
    payload_zip: Path,
    output_dir: Path,
    raw_log_path: Path,
    started_at: str,
    completed_at: str,
    exit_code: int,
    keys: dict[str, str],
    blocked_reason: str,
    component_reports: list[dict[str, Any]],
    hip_visible_devices: str,
    remote_root: str,
    image: str,
) -> dict[str, Any]:
    component_failed_count = sum(1 for report in component_reports if report.get("passed") is not True)
    component_blocked_reasons = [str(report.get("blocked_reason") or "inline_component_not_passed") for report in component_reports if report.get("passed") is not True]
    component_failure_reason = ";".join(component_blocked_reasons)
    effective_blocked_reason = blocked_reason or component_failure_reason
    if mode == "run" and exit_code == 0 and not component_reports and not effective_blocked_reason:
        effective_blocked_reason = "payload_evidence_missing"
    precheck_ready = mode == "precheck" and exit_code == 0 and keys.get("RUNTIME_IMAGE") == "present" and keys.get("RUNTIME_VENV") == "present" and keys.get("REMOTE_ROOT") == "present"
    run_passed = mode == "run" and exit_code == 0 and bool(component_reports) and not effective_blocked_reason and component_failed_count == 0
    raw_hash = sha256_file(raw_log_path)
    return {
        "schema_version": DIRECT_PREFLIGHT_SCHEMA,
        "report_id": safe_command_id,
        "claim_boundary": DIRECT_CLAIM_BOUNDARY,
        "promotional": False,
        "scorecard_update_allowed": False,
        "canonical_phase3_command_log_manifest_eligible": False,
        "canonical_phase3_command_log_manifest_reason": "not_slurm_direct_ssh_preflight",
        "scheduler": "none_direct_ssh",
        "slurm_job_id_present": False,
        "target_run_id": None,
        "direct_run_id": direct_run_id,
        "mode": mode,
        "ssh_alias": ssh_alias,
        "node": keys.get("PHASE4_DIRECT_NODE", ""),
        "command_id": command_id,
        "safe_command_id": safe_command_id,
        "argv": argv,
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
        "status": "passed" if (precheck_ready or run_passed) else "blocked" if effective_blocked_reason or exit_code != 0 else "not_ready",
        "passed": bool(precheck_ready or run_passed),
        "blocked_reason": effective_blocked_reason,
        "hip_visible_devices": hip_visible_devices,
        "remote_root": remote_root,
        "image": image,
        "image_id": keys.get("IMAGE_ID", ""),
        "image_repo_digests": keys.get("IMAGE_REPODIGESTS", ""),
        "runtime": {
            "remote_root": keys.get("REMOTE_ROOT", ""),
            "venv": keys.get("RUNTIME_VENV", ""),
            "image": keys.get("RUNTIME_IMAGE", ""),
        },
        "commands": {key: value for key, value in keys.items() if key.startswith("CMD_")},
        "devices": {key: value for key, value in keys.items() if key.startswith("DEVICE_")},
        "endpoint_env_presence": _env_presence(keys),
        "component_reports": component_reports,
        "component_failed_count": component_failed_count,
        "component_blocked_reasons": component_blocked_reasons,
        "raw_log_path": str(raw_log_path.relative_to(output_dir)),
        "raw_log_sha256": raw_hash,
        "input_hashes": {
            "payload_zip": sha256_file(payload_zip),
            "raw_log": raw_hash,
            "direct_command_key_values": _sha256_text(json.dumps(keys, sort_keys=True)),
        },
        "artifact_paths": {
            "raw_log": str(raw_log_path),
            "assessment": str(output_dir / f"{safe_command_id}_direct_node_preflight.json"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-alias", required=True)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--direct-run-id", required=True)
    parser.add_argument("--payload-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("precheck", "run"), default="precheck")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--hip-visible-devices", default="0")
    parser.add_argument("--scp-timeout-seconds", type=int, default=120)
    parser.add_argument("--command-timeout-seconds", type=int, default=900)
    parser.add_argument("--create-remote-root", action="store_true")
    args = parser.parse_args(argv)
    if args.scp_timeout_seconds < 1:
        parser.error("--scp-timeout-seconds must be positive")
    if args.command_timeout_seconds < 1:
        parser.error("--command-timeout-seconds must be positive")
    if any(part in args.direct_run_id for part in ("/", "\\", "..")):
        parser.error("--direct-run-id must be an identifier, not a path")
    if not args.payload_zip.exists():
        raise FileNotFoundError(args.payload_zip)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    safe_command_id = _safe_artifact_name(args.command_id, fallback="phase4_direct_node_preflight")
    log_dir = args.output_dir / "command_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = log_dir / f"{safe_command_id}.log"
    assessment_path = args.output_dir / f"{safe_command_id}_direct_node_preflight.json"
    remote_zip = f"/tmp/{safe_command_id}.zip"
    started_at = _iso()
    exit_code = 1
    blocked_reason = ""
    raw_log = ""

    try:
        if args.mode == "run":
            remote_command = _remote_run_command(direct_run_id=args.direct_run_id, command_id=safe_command_id, remote_zip=remote_zip, remote_root=args.remote_root, image=args.image, hip_visible_devices=args.hip_visible_devices)
            command = _build_ssh_command(ssh_alias=args.ssh_alias, remote_command=remote_command)
            enforce_command_request(command, workspace_relative_path="workspace/direct-node", workspace_id="workspace")
            scp = subprocess.run(["scp", str(args.payload_zip), f"{args.ssh_alias}:{remote_zip}"], check=False, text=True, capture_output=True, timeout=args.scp_timeout_seconds)
            if scp.returncode != 0:
                raw_log = (scp.stdout or "") + (scp.stderr or "")
                exit_code = scp.returncode
                blocked_reason = "payload_transfer_failed"
            else:
                result = subprocess.run(command, check=False, text=True, capture_output=True, env={**os.environ, "PHASE4_DIRECT_RUN_ID": args.direct_run_id}, timeout=args.command_timeout_seconds)
                raw_log = (result.stdout or "") + (result.stderr or "")
                exit_code = result.returncode
        else:
            remote_command = _remote_precheck_command(direct_run_id=args.direct_run_id, command_id=safe_command_id, remote_root=args.remote_root, image=args.image, hip_visible_devices=args.hip_visible_devices, create_remote_root=args.create_remote_root)
            command = _build_ssh_command(ssh_alias=args.ssh_alias, remote_command=remote_command)
            enforce_command_request(command, workspace_relative_path="workspace/direct-node", workspace_id="workspace")
            result = subprocess.run(command, check=False, text=True, capture_output=True, env={**os.environ, "PHASE4_DIRECT_RUN_ID": args.direct_run_id}, timeout=args.command_timeout_seconds)
            raw_log = (result.stdout or "") + (result.stderr or "")
            exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        raw_log = f"TimeoutExpired: {exc}\n"
        exit_code = 124
        blocked_reason = "target_unreachable"
    except Exception as exc:  # noqa: BLE001
        raw_log = f"{exc.__class__.__name__}: {exc}\n"
        exit_code = 1
        blocked_reason = exc.__class__.__name__

    raw_log_path.write_text(raw_log)
    keys = _parse_key_values(raw_log)
    if not blocked_reason:
        blocked_reason = keys.get("PHASE4_BLOCKED_REASON", "")
    component_reports = _inline_component_reports(raw_log)
    if not blocked_reason and exit_code != 0 and not component_reports:
        blocked_reason = "remote_command_failed"
    completed_at = _iso()
    assessment = _assessment_payload(
        argv=sys.argv if argv is None else argv,
        mode=args.mode,
        ssh_alias=args.ssh_alias,
        command_id=args.command_id,
        safe_command_id=safe_command_id,
        direct_run_id=args.direct_run_id,
        payload_zip=args.payload_zip,
        output_dir=args.output_dir,
        raw_log_path=raw_log_path,
        started_at=started_at,
        completed_at=completed_at,
        exit_code=exit_code,
        keys=keys,
        blocked_reason=blocked_reason,
        component_reports=component_reports,
        hip_visible_devices=args.hip_visible_devices,
        remote_root=args.remote_root,
        image=args.image,
    )
    _write_json(assessment_path, assessment)
    print(json.dumps({"assessment": str(assessment_path), "passed": assessment["passed"], "status": assessment["status"], "blocked_reason": assessment["blocked_reason"]}, sort_keys=True))
    return 0 if assessment["passed"] else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
