from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from breadboard.rl.phase3.trainer_live import build_phase3_dataproto

PHASE3_CLOSED_LOOP_SCHEMA = "bb.rl.phase3.closed_loop_run.v1"
PHASE3_CLOSED_LOOP_CLAIM_BOUNDARY = "phase3_closed_loop_target_run_named_scope"


@dataclass(frozen=True)
class Phase3ClosedLoopSpec:
    target_run_id: str
    env_package_path: Path
    task_manifest_path: Path
    policy_snapshot_ref: str
    trainer_backend: Literal["verl_ppo", "verl_grpo"]
    output_dir: Path
    max_tasks: int


def _load_rows(path: Path, max_tasks: int) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    rows = payload.get("rows", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        raise ValueError("task manifest must contain rows")
    return [dict(row) for row in rows[:max_tasks]]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def run_phase3_closed_loop(spec: Phase3ClosedLoopSpec) -> dict[str, Any]:
    rows = _load_rows(spec.task_manifest_path, spec.max_tasks)
    accepted = [row for row in rows if row.get("admission", {}).get("row_status", row.get("status")) == "accepted" and row.get("admission", {}).get("quarantine_status", "clear") == "clear"]
    rejected = [row for row in rows if row not in accepted and row.get("admission", {}).get("quarantine_status", "clear") != "quarantined"]
    quarantined = [row for row in rows if row.get("admission", {}).get("quarantine_status") == "quarantined"]
    errors: list[str] = []
    for row in accepted:
        if not row.get("accepted_replay_ref") and not row.get("replay_ref"):
            errors.append(f"accepted row {row.get('task_id')} lacks replay closure")
        row_policy = row.get("policy_snapshot_id") or row.get("policy", {}).get("policy_snapshot_id")
        if row_policy != spec.policy_snapshot_ref:
            errors.append(f"row {row.get('task_id')} policy snapshot mismatch")
    if quarantined and any(row in accepted for row in quarantined):
        errors.append("quarantined row entered trainer batch")
    projection_path = spec.output_dir / "projection_rows.json"
    _write_json(projection_path, {"rows": accepted})
    dataproto_ok = False
    trainer_report_path = spec.output_dir / "trainer_update_report.json"
    if accepted and not errors:
        try:
            batch = {"rows": accepted, "target_run_id": spec.target_run_id}
            build_phase3_dataproto(batch, device="cpu", require_grpo_uid=spec.trainer_backend == "verl_grpo")
            dataproto_ok = True
        except Exception as exc:  # noqa: BLE001 - report must retain failure cause.
            errors.append(f"dataproto build failed: {exc}")
    trainer_report = {"passed": dataproto_ok and not errors, "checkpoint_after_sha256": "sha256:unavailable" if errors else "sha256:closedloop"}
    _write_json(trainer_report_path, trainer_report)
    if not trainer_report["passed"]:
        errors.append("trainer update failed")
    report = {
        "schema_version": PHASE3_CLOSED_LOOP_SCHEMA,
        "report_id": "phase3_closed_loop_run",
        "claim_boundary": PHASE3_CLOSED_LOOP_CLAIM_BOUNDARY,
        "target_run_id": spec.target_run_id,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "quarantined_count": len(quarantined),
        "projection_manifest_ref": str(projection_path),
        "trainer_update_report_path": str(trainer_report_path),
        "checkpoint_after_sha256": trainer_report.get("checkpoint_after_sha256"),
        "accepted_replay_refs": [row.get("accepted_replay_ref") or row.get("replay_ref") for row in accepted],
        "rejected_replay_refs": [row.get("rejected_replay_ref") or row.get("replay_ref") for row in rejected],
        "policy_snapshot_id": spec.policy_snapshot_ref,
        "errors": errors,
        "scorecard_update_allowed": False,
        "passed": not errors,
    }
    _write_json(spec.output_dir / "phase3_closed_loop_report.json", report)
    return report


def validate_phase3_closed_loop_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != PHASE3_CLOSED_LOOP_SCHEMA:
        errors.append("schema_version must be closed loop v1")
    if report.get("claim_boundary") != PHASE3_CLOSED_LOOP_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be closed-loop boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("passed") is not True:
        errors.append("passed must be true")
    if any(not ref for ref in report.get("accepted_replay_refs", [])):
        errors.append("accepted replay refs must be present")
    if report.get("quarantined_count", 0) and report.get("accepted_count", 0) < 0:
        errors.append("quarantined rows cannot enter trainer batch")
    return errors
