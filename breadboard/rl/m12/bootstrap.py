from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from breadboard.rl.m12.transfer import validate_m12_transfer_overlay_report


BOOTSTRAP_DRY_RUN_ID = "bb_zyphra_rl_phase1_m12_bootstrap_dry_run_report_v1"
BOOTSTRAP_DRY_RUN_CLAIM_BOUNDARY = "target_bootstrap_dry_run_not_m12_validation"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _input_hash_errors(report: dict[str, Any], input_hashes: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ["bootstrap_script", "transfer_manifest", "archive_manifest", "overlay_dry_run_report"]:
        recorded = str(input_hashes.get(field) or "")
        raw_path = report.get(field)
        if not recorded.startswith("sha256:"):
            errors.append(f"input_hashes.{field} must start with sha256:")
            continue
        if not raw_path:
            errors.append(f"{field} path must be recorded")
            continue
        actual = _sha256_file(Path(str(raw_path)))
        if actual is None:
            errors.append(f"{field} file missing for input hash validation")
        elif actual != recorded:
            errors.append(f"input_hashes.{field} does not match current file")
    return errors


def _overlay_file_errors(report: dict[str, Any], overlay_summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    raw_path = report.get("overlay_dry_run_report")
    if not raw_path:
        return ["overlay_dry_run_report path must be recorded"]
    path = Path(str(raw_path))
    if not path.is_file():
        return ["overlay_dry_run_report file missing for validation"]
    try:
        overlay_report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"overlay_dry_run_report is not readable JSON: {type(exc).__name__}: {exc}"]

    errors.extend(f"overlay_dry_run_report.{error}" for error in validate_m12_transfer_overlay_report(overlay_report))
    for field in [
        "status",
        "dry_run",
        "would_write_count",
        "written_count",
        "existing_destination_count",
        "scorecard_update_allowed",
        "m12_points_awarded",
    ]:
        if overlay_summary.get(field) != overlay_report.get(field):
            errors.append(f"overlay.{field} must match overlay_dry_run_report")
    return errors


def build_m12_bootstrap_dry_run_report(
    *,
    repo_root: Path,
    transfer_prep_dir: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    transfer_prep_dir = transfer_prep_dir.resolve()
    workspace_root = (workspace_root or repo_root.parent).resolve()
    bootstrap_script = transfer_prep_dir / "m12_target_bootstrap.sh"
    transfer_manifest = transfer_prep_dir / "m12_transfer_manifest.json"
    archive_manifest = transfer_prep_dir / "m12_transfer_archive_manifest.json"
    overlay_report_path = transfer_prep_dir / "m12_overlay_apply_dry_run_report.json"
    bootstrap_script_sha256 = _sha256_file(bootstrap_script)
    transfer_manifest_sha256 = _sha256_file(transfer_manifest)
    archive_manifest_sha256 = _sha256_file(archive_manifest)

    env = os.environ.copy()
    env.update(
        {
            "REPO_ROOT": str(repo_root),
            "WORKSPACE_ROOT": str(workspace_root),
            "BOOTSTRAP_DRY_RUN_ONLY": "1",
            "ALLOW_M12_DIRTY_CHECKOUT": "1",
        }
    )
    result = subprocess.run(
        ["bash", str(bootstrap_script)],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    overlay_report = _read_json_if_present(overlay_report_path)
    overlay_report_sha256 = _sha256_file(overlay_report_path)
    stdout = result.stdout
    stderr = result.stderr
    repo_head_verified = "repo_head_verified=true" in stdout
    dirty_checkout_check_observed = "repo_dirty_check=" in stdout
    dirty_checkout_mode = "override" if "repo_dirty_check=override" in stdout else "clean" if "repo_dirty_check=clean" in stdout else None
    dirty_checkout_override_used = dirty_checkout_mode == "override"
    target_commands_skipped = "bootstrap_dry_run_only=true" in stdout
    errors: list[str] = []
    if result.returncode != 0:
        errors.append(f"bootstrap exited nonzero: {result.returncode}")
    if not repo_head_verified:
        errors.append("bootstrap stdout did not confirm repo_head_verified=true")
    if not dirty_checkout_check_observed:
        errors.append("bootstrap stdout did not confirm repo_dirty_check")
    if not target_commands_skipped:
        errors.append("bootstrap stdout did not confirm bootstrap_dry_run_only=true")
    if overlay_report is None:
        errors.append("overlay dry-run report was not written")
    else:
        overlay_validation_errors = validate_m12_transfer_overlay_report(overlay_report)
        errors.extend(f"overlay dry-run report invalid: {error}" for error in overlay_validation_errors)
        if overlay_report_sha256 is None:
            errors.append("overlay dry-run report sha256 was not recorded")
        if overlay_report.get("status") != "passed":
            errors.append("overlay dry-run report status was not passed")
        if overlay_report.get("dry_run") is not True:
            errors.append("overlay dry-run report did not record dry_run=true")
        if overlay_report.get("written_count") != 0:
            errors.append("overlay dry-run report wrote files")
        if overlay_report.get("scorecard_update_allowed") is not False:
            errors.append("overlay dry-run report allowed scorecard update")
        if overlay_report.get("m12_points_awarded") is not False:
            errors.append("overlay dry-run report awarded M12 points")

    return {
        "report_id": BOOTSTRAP_DRY_RUN_ID,
        "claim_boundary": BOOTSTRAP_DRY_RUN_CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "transfer_prep_dir": str(transfer_prep_dir),
        "bootstrap_script": str(bootstrap_script),
        "transfer_manifest": str(transfer_manifest),
        "archive_manifest": str(archive_manifest),
        "overlay_dry_run_report": str(overlay_report_path),
        "input_hashes": {
            "bootstrap_script": bootstrap_script_sha256,
            "transfer_manifest": transfer_manifest_sha256,
            "archive_manifest": archive_manifest_sha256,
            "overlay_dry_run_report": overlay_report_sha256,
        },
        "exit_code": result.returncode,
        "repo_head_verified": repo_head_verified,
        "dirty_checkout_check_observed": dirty_checkout_check_observed,
        "dirty_checkout_mode": dirty_checkout_mode,
        "dirty_checkout_override_used": dirty_checkout_override_used,
        "target_commands_skipped": target_commands_skipped,
        "stdout_sha256": _sha256_text(stdout),
        "stderr_sha256": _sha256_text(stderr),
        "stdout": stdout,
        "stderr": stderr,
        "overlay": {
            "status": None if overlay_report is None else overlay_report.get("status"),
            "dry_run": None if overlay_report is None else overlay_report.get("dry_run"),
            "would_write_count": None if overlay_report is None else overlay_report.get("would_write_count"),
            "written_count": None if overlay_report is None else overlay_report.get("written_count"),
            "existing_destination_count": None
            if overlay_report is None
            else overlay_report.get("existing_destination_count"),
            "scorecard_update_allowed": None
            if overlay_report is None
            else overlay_report.get("scorecard_update_allowed"),
            "m12_points_awarded": None if overlay_report is None else overlay_report.get("m12_points_awarded"),
        },
    }


def validate_m12_bootstrap_dry_run_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != BOOTSTRAP_DRY_RUN_ID:
        errors.append("report_id must be bb_zyphra_rl_phase1_m12_bootstrap_dry_run_report_v1")
    if report.get("claim_boundary") != BOOTSTRAP_DRY_RUN_CLAIM_BOUNDARY:
        errors.append("claim_boundary must remain target_bootstrap_dry_run_not_m12_validation")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if report.get("status") not in {"passed", "failed"}:
        errors.append("status must be passed or failed")
    if report.get("status") == "passed" and report.get("errors") != []:
        errors.append("passed report must have no errors")
    if report.get("exit_code") != 0 and report.get("status") == "passed":
        errors.append("passed report must have exit_code=0")
    if report.get("repo_head_verified") is not True and report.get("status") == "passed":
        errors.append("passed report must verify repo head")
    if report.get("dirty_checkout_check_observed") is not True and report.get("status") == "passed":
        errors.append("passed report must observe dirty checkout check")
    if report.get("dirty_checkout_mode") not in {"clean", "override"} and report.get("status") == "passed":
        errors.append("passed report must record dirty_checkout_mode clean or override")
    if report.get("target_commands_skipped") is not True and report.get("status") == "passed":
        errors.append("passed report must skip target commands")
    input_hashes = report.get("input_hashes")
    if not isinstance(input_hashes, dict):
        errors.append("input_hashes must be an object")
        input_hashes = {}
    errors.extend(_input_hash_errors(report, input_hashes))
    overlay = report.get("overlay")
    if not isinstance(overlay, dict):
        errors.append("overlay must be an object")
        overlay = {}
    else:
        errors.extend(_overlay_file_errors(report, overlay))
    if report.get("status") == "passed":
        if overlay.get("status") != "passed":
            errors.append("passed report requires overlay.status=passed")
        if overlay.get("dry_run") is not True:
            errors.append("passed report requires overlay.dry_run=true")
        if overlay.get("written_count") != 0:
            errors.append("passed report requires overlay.written_count=0")
        if overlay.get("scorecard_update_allowed") is not False:
            errors.append("passed report requires overlay.scorecard_update_allowed=false")
        if overlay.get("m12_points_awarded") is not False:
            errors.append("passed report requires overlay.m12_points_awarded=false")
    for field in ["stdout_sha256", "stderr_sha256"]:
        if not str(report.get(field) or "").startswith("sha256:"):
            errors.append(f"{field} must start with sha256:")
    return errors


def write_m12_bootstrap_dry_run_report(
    *,
    repo_root: Path,
    transfer_prep_dir: Path,
    output_path: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    report = build_m12_bootstrap_dry_run_report(
        repo_root=repo_root,
        transfer_prep_dir=transfer_prep_dir,
        workspace_root=workspace_root,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
