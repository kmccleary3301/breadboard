from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from breadboard.rl.phase3.evidence import PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, PHASE3_TARGET_RUN_ID_PATTERN, sha256_file
from breadboard.rl.phase3.security_enforcement import enforce_command_request


def _inline_report_passed(report: object) -> bool:
    return isinstance(report, dict) and report.get("passed") is True


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_manifest(path: Path, target_run_id: str) -> dict:
    empty = {"schema_version": PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, "target_run_id": target_run_id, "commands": []}
    if not path.exists():
        return empty
    manifest = json.loads(path.read_text())
    if manifest.get("target_run_id") != target_run_id:
        return empty
    return manifest


def _load_attempts_manifest(path: Path, target_run_id: str) -> dict:
    empty = {
        "schema_version": "bb.rl.phase3.command_attempts_manifest.v1",
        "target_run_id": target_run_id,
        "attempts": [],
    }
    if not path.exists():
        return empty
    manifest = json.loads(path.read_text())
    if manifest.get("target_run_id") != target_run_id:
        return empty
    return manifest

def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

def _build_remote_command(
    *,
    target_run_id: str,
    command_id: str,
    remote_zip: str,
    partition: str,
    job_name: str,
    gres: str = "gpu:8",
    mem: str | None = None,
    nodelist: str | None = None,
    constraint: str | None = None,
    reservation: str | None = None,
    qos: str | None = None,
) -> str:
    slurm_payload = "echo PHASE3_NODE=$(hostname); echo PHASE3_SLURM_JOB_ID=${SLURM_JOB_ID:-}; ./run.sh"
    srun_args = [
        f"--partition={shlex.quote(partition)}",
        f"--job-name={shlex.quote(job_name)}",
        f"--gres={shlex.quote(gres)}",
    ]
    if mem:
        srun_args.append(f"--mem={shlex.quote(mem)}")
    for flag, value in (
        ("--nodelist", nodelist),
        ("--constraint", constraint),
        ("--reservation", reservation),
        ("--qos", qos),
    ):
        if value:
            srun_args.append(f"{flag}={shlex.quote(value)}")
    return (
        "set -euo pipefail; "
        f"export PHASE3_TARGET_RUN_ID={shlex.quote(target_run_id)}; "
        f"export PHASE3_COMMAND_ID={shlex.quote(command_id)}; "
        f"mkdir -p /shared/bb-p3-${{USER:-root}}; "
        f"WORK=$(mktemp -d /shared/bb-p3-${{USER:-root}}/{shlex.quote(command_id)}.XXXXXX); "
        f"unzip -q {shlex.quote(remote_zip)} -d \"$WORK\"; "
        "cd \"$WORK\"; "
        "test -x ./run.sh; "
        f"srun {' '.join(srun_args)} bash -lc {shlex.quote(slurm_payload)}"
    )


def _build_ssh_command(*, ssh_alias: str, remote_command: str) -> list[str]:
    return ["ssh", ssh_alias, f"bash -lc {shlex.quote(remote_command)}"]

def _safe_artifact_name(value: object, *, fallback: str) -> str:
    raw = str(value or fallback)
    stem = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw).strip("._-") or fallback
    if stem == raw:
        return stem
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{stem}-{digest}"


def _valid_requested_target_run_id(target_run_id: str) -> bool:
    return bool(
        re.match(PHASE3_TARGET_RUN_ID_PATTERN, target_run_id)
        or re.match(r"^\d{8}T\d{6}Z-slurm-pending$", target_run_id)
    )



def _validated_slurm_option(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:,=+\-\[\]]{1,256}", value):
        raise ValueError(f"--{name} contains unsupported characters")
    return value

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-alias", required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--payload-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--gres", default="gpu:8")
    parser.add_argument("--mem")
    parser.add_argument("--nodelist")
    parser.add_argument("--constraint")
    parser.add_argument("--reservation")
    parser.add_argument("--qos")
    parser.add_argument("--scp-timeout-seconds", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        gres = _validated_slurm_option(args.gres, name="gres")
        nodelist = _validated_slurm_option(args.nodelist, name="nodelist")
        mem = _validated_slurm_option(args.mem, name="mem")
        constraint = _validated_slurm_option(args.constraint, name="constraint")
        reservation = _validated_slurm_option(args.reservation, name="reservation")
        qos = _validated_slurm_option(args.qos, name="qos")
    except ValueError as exc:
        parser.error(str(exc))
    if args.scp_timeout_seconds < 1:
        parser.error("--scp-timeout-seconds must be positive")
    if not _valid_requested_target_run_id(args.target_run_id):
        parser.error("--target-run-id must be concrete Phase 3 id or end in -slurm-pending")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    safe_command_id = _safe_artifact_name(args.command_id, fallback="phase3_command")
    log_dir = args.output_dir / "command_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = log_dir / f"{safe_command_id}.log"
    started_at = _iso()
    remote_zip = f"/tmp/{safe_command_id}.zip"
    remote_command = _build_remote_command(
        target_run_id=args.target_run_id,
        command_id=safe_command_id,
        remote_zip=remote_zip,
        partition=args.partition,
        job_name=args.job_name,
        gres=gres,
        mem=mem,
        nodelist=nodelist,
        constraint=constraint,
        reservation=reservation,
        qos=qos,
    )
    command = _build_ssh_command(ssh_alias=args.ssh_alias, remote_command=remote_command)
    exit_code = 1
    slurm_job_id = ""
    node = ""
    final_target_run_id = args.target_run_id
    blocked_reason = ""
    inline_reports: list[dict] = []
    try:
        enforce_command_request(command, workspace_relative_path="workspace/slurm", workspace_id="workspace")
        if not args.payload_zip.exists():
            raise FileNotFoundError(args.payload_zip)
        scp = subprocess.run(["scp", str(args.payload_zip), f"{args.ssh_alias}:{remote_zip}"], check=False, text=True, capture_output=True, timeout=args.scp_timeout_seconds)
        if scp.returncode != 0:
            raw_log_path.write_text((scp.stdout or "") + (scp.stderr or ""))
            exit_code = scp.returncode
        else:
            run_env = {**os.environ, "PHASE3_TARGET_RUN_ID": args.target_run_id}
            result = subprocess.run(command, check=False, text=True, capture_output=True, env=run_env, timeout=3600)
            raw_log_path.write_text((result.stdout or "") + (result.stderr or ""))
            exit_code = result.returncode
            for line in (result.stdout or "").splitlines():
                if line.startswith("PHASE3_NODE="):
                    node = line.split("=", 1)[1].strip()
                if line.startswith("PHASE3_SLURM_JOB_ID="):
                    slurm_job_id = line.split("=", 1)[1].strip()
                if line.startswith("PHASE3_INTROSPECTION_REPORT=") or line.startswith("PHASE3_COMPONENT_REPORT_JSON="):
                    try:
                        inline_reports.append(json.loads(line.split("=", 1)[1]))
                    except json.JSONDecodeError:
                        blocked_reason = "invalid_inline_report"
            if slurm_job_id and args.target_run_id.endswith("-pending"):
                final_target_run_id = args.target_run_id.removesuffix("pending") + slurm_job_id
    except subprocess.TimeoutExpired as exc:
        blocked_reason = "target_unreachable"
        raw_log_path.write_text(f"TimeoutExpired: {exc}\n")
        exit_code = 124
    except Exception as exc:  # noqa: BLE001
        blocked_reason = exc.__class__.__name__
        raw_log_path.write_text(f"{exc.__class__.__name__}: {exc}\n")
        exit_code = 1
    completed_at = _iso()
    manifest_path = args.output_dir / "phase3_command_log_manifest.json"
    attempts_path = args.output_dir / "phase3_command_attempts_manifest.json"
    component_failed_count = sum(1 for report in inline_reports if not _inline_report_passed(report))
    component_blocked_reasons = [
        str(report.get("blocked_reason") or "inline_component_not_passed")
        for report in inline_reports
        if not _inline_report_passed(report) and isinstance(report, dict)
    ]
    component_passed = component_failed_count == 0
    effective_blocked_reason = blocked_reason or ("inline_component_failed" if component_failed_count else "")
    passed = exit_code == 0 and bool(slurm_job_id) and bool(node) and not effective_blocked_reason and component_passed
    row = {
        "command_id": args.command_id,
        "argv": sys.argv if argv is None else argv,
        "raw_log_path": str(raw_log_path.relative_to(args.output_dir)),
        "raw_log_sha256": sha256_file(raw_log_path),
        "slurm_job_id": slurm_job_id,
        "target_run_id": final_target_run_id,
        "node": node,
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
        "status": "passed" if passed else "failed",
        "blocked_reason": effective_blocked_reason,
        "component_passed": component_passed,
        "component_failed_count": component_failed_count,
        "component_blocked_reasons": component_blocked_reasons,
    }
    if not blocked_reason:
        for report in inline_reports:
            report["target_run_id"] = final_target_run_id
            report_id = str(report.get("report_id") or args.command_id)
            safe_report_id = _safe_artifact_name(report_id, fallback=safe_command_id)
            component_dir = _safe_artifact_name(report.get("component"), fallback=safe_report_id)
            report_dir = args.output_dir / component_dir
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{safe_report_id}.json"
            artifact_paths = report.setdefault("artifact_paths", {})
            if isinstance(artifact_paths, dict):
                artifact_paths.setdefault("component_report_json", str(report_path.resolve()))
                artifact_paths.setdefault("command_log", str(raw_log_path.resolve()))
            report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    if blocked_reason == "target_unreachable":
        blocked_report = {
            "schema_version": "bb.rl.phase3.target_command_blocked.v1",
            "report_id": f"{args.command_id}_blocked",
            "target_run_id": final_target_run_id,
            "blocked_reason": "target_unreachable",
            "scorecard_update_allowed": False,
            "passed": False,
        }
        (args.output_dir / f"{safe_command_id}_blocked.json").write_text(json.dumps(blocked_report, sort_keys=True, indent=2) + "\n")
    if passed:
        manifest = _load_manifest(manifest_path, final_target_run_id)
        manifest["target_run_id"] = final_target_run_id
        manifest["commands"] = [existing for existing in manifest.get("commands", []) if existing.get("command_id") != args.command_id]
        manifest["commands"].append(row)
        _write_manifest(manifest_path, manifest)
        if attempts_path.exists():
            attempts = _load_attempts_manifest(attempts_path, final_target_run_id)
            attempts["target_run_id"] = final_target_run_id
            attempts["attempts"] = [existing for existing in attempts.get("attempts", []) if existing.get("command_id") != args.command_id]
            if attempts["attempts"]:
                _write_manifest(attempts_path, attempts)
            else:
                attempts_path.unlink()
    else:
        if manifest_path.exists():
            manifest = _load_manifest(manifest_path, final_target_run_id)
            manifest["commands"] = [existing for existing in manifest.get("commands", []) if existing.get("command_id") != args.command_id]
            if manifest["commands"]:
                _write_manifest(manifest_path, manifest)
            else:
                manifest_path.unlink()
        attempts = _load_attempts_manifest(attempts_path, final_target_run_id)
        attempts["target_run_id"] = final_target_run_id
        attempts["attempts"] = [existing for existing in attempts.get("attempts", []) if existing.get("command_id") != args.command_id]
        attempts["attempts"].append(row)
        _write_manifest(attempts_path, attempts)
    return 0 if passed else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
