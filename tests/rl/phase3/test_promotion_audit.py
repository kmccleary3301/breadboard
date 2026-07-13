from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

from breadboard.rl.phase3.final_report import PHASE3_CORE_CLAIM_BOUNDARY, PHASE3_CORE_READINESS_SCHEMA, PHASE3_FINAL_CLAIM_BOUNDARY, PHASE3_FINAL_REPORT_ID, PHASE3_MILESTONES
from breadboard.rl.phase3.promotion_audit import build_phase3_promotion_audit, validate_phase3_core_promotion_audit, validate_phase3_promotion_audit


def _scorecard(points: int) -> dict:
    return {"current_verified_points": points, "total_points": 1000}


def test_promotion_audit_surfaces_final_report_blockers() -> None:
    target = "20260624T040000Z-slurm-243958"
    final_report = {
        "validation_errors": ["P3-M7: passed must be true", "P3-M11: passed must be true"],
        "milestone_summaries": [
            {"milestone_id": "P3-M0", "passed": True},
            {"milestone_id": "P3-M7", "passed": False},
            {"milestone_id": "P3-M11", "passed": False},
        ],
    }

    audit = build_phase3_promotion_audit(
        target_run_id=target,
        final_report=final_report,
        scorecard=_scorecard(0),
        claim_ledger_text="",
        bd_epic_closed=False,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["completed_milestones"] == ["P3-M0"]
    assert audit["blocked_milestones"] == ["P3-M7", "P3-M11"]
    assert audit["final_report_validation_errors"] == final_report["validation_errors"]
    errors = validate_phase3_promotion_audit(audit)
    assert "promotion_review_ready must be true" in errors
    assert "every P3 milestone must be completed" in errors


def test_promotion_audit_ready_requires_all_phase3_gates() -> None:
    target = "20260624T040000Z-slurm-243958"
    active_milestones = list(PHASE3_MILESTONES)
    final_report = {
        "validation_errors": [],
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        "core_readiness": {
            "schema_version": PHASE3_CORE_READINESS_SCHEMA,
            "claim_boundary": PHASE3_CORE_CLAIM_BOUNDARY,
            "ready": True,
            "scorecard_update_allowed": False,
            "active_milestones": active_milestones,
            "core_milestones": active_milestones,
            "deferred_milestones": [],
            "blocked_active_milestones": [],
            "blocked_core_milestones": [],
            "core_raw_points_verified": 0,
            "core_raw_points_total": 0,
            "original_scorecard_total_points": 1000,
        },
    }
    ledger = f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n{PHASE3_CORE_CLAIM_BOUNDARY}\n"

    audit = build_phase3_promotion_audit(
        target_run_id=target,
        final_report=final_report,
        scorecard=_scorecard(1000),
        claim_ledger_text=ledger,
        bd_epic_closed=True,
    )

    assert "P3-M11" in PHASE3_MILESTONES
    assert "P3-M11" in final_report["core_readiness"]["core_milestones"]
    assert audit["promotion_review_ready"] is True
    assert audit["active_review_ready"] is True
    assert audit["active_artifact_audit_clean"] is True
    assert audit["core_artifact_audit_clean"] is True
    assert audit["active_completed_milestones"] == active_milestones
    assert audit["blocked_milestones"] == []
    assert audit["scorecard_update_allowed"] is False
    assert audit["final_report_validation_errors"] == []
    assert audit["core_validation_errors"] == []
    assert validate_phase3_promotion_audit(audit) == []


def test_active_audit_ready_but_promotion_gated_until_epic_closed() -> None:
    target = "20260624T040000Z-slurm-243958"
    active_milestones = list(PHASE3_MILESTONES)
    final_report = {
        "validation_errors": [],
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        "core_readiness": {
            "schema_version": PHASE3_CORE_READINESS_SCHEMA,
            "claim_boundary": PHASE3_CORE_CLAIM_BOUNDARY,
            "ready": True,
            "scorecard_update_allowed": False,
            "active_milestones": active_milestones,
            "core_milestones": active_milestones,
            "deferred_milestones": [],
            "blocked_active_milestones": [],
            "blocked_core_milestones": [],
            "core_raw_points_verified": 0,
            "core_raw_points_total": 0,
            "original_scorecard_total_points": 1000,
        },
    }
    ledger = f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n{PHASE3_CORE_CLAIM_BOUNDARY}\n"

    audit = build_phase3_promotion_audit(
        target_run_id=target,
        final_report=final_report,
        scorecard=_scorecard(0),
        claim_ledger_text=ledger,
        bd_epic_closed=False,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["active_review_ready"] is True
    assert audit["active_artifact_audit_clean"] is True
    assert audit["active_review_ready_meaning"] == "Artifact-audit boundary is clean for the promoted exact-scope Phase 3 claim; broader successor claims remain separately gated."
    assert audit["completed_milestones"] == active_milestones
    assert audit["blocked_milestones"] == []
    assert audit["scorecard_update_allowed"] is False
    assert audit["core_scorecard_update_allowed"] is False
    assert audit["active_completed_milestones"] == active_milestones
    assert audit["core_blocked_milestones"] == []
    assert audit["core_claim_ledger_anchored"] is True
    assert audit["core_validation_errors"] == []
    assert validate_phase3_core_promotion_audit(audit) == []
    full_errors = validate_phase3_promotion_audit(audit)
    assert "bd epic must be closed" in full_errors

    unanchored = build_phase3_promotion_audit(
        target_run_id=target,
        final_report=final_report,
        scorecard=_scorecard(0),
        claim_ledger_text=f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=False,
    )
    assert unanchored["core_review_ready"] is False
    assert "active claim boundary must be anchored in claim ledger" in unanchored["core_validation_errors"]

def test_promotion_audit_rejects_missing_target_run_id() -> None:
    audit = {
        "schema_version": "bb.rl.phase3.promotion_audit.v1",
        "report_id": "bb_zyphra_rl_phase3_promotion_audit_v1",
        "claim_boundary": "phase3_promotion_review_only_not_scorecard_update",
        "target_run_id": "",
        "completed_milestones": list(PHASE3_MILESTONES),
        "blocked_milestones": [],
        "final_report_validation_errors": [],
        "bd_epic_closed": True,
        "promotion_review_ready": True,
        "scorecard_update_allowed": False,
        "core_scorecard_update_allowed": False,
    }

    assert "target_run_id must match Phase 3 Slurm target run id pattern" in validate_phase3_promotion_audit(audit)

    final_report = {
        "validation_errors": [],
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
    }
    audit_from_builder = build_phase3_promotion_audit(
        target_run_id="",
        final_report=final_report,
        scorecard=_scorecard(1000),
        claim_ledger_text=f"{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )
    assert audit_from_builder["promotion_review_ready"] is False


def test_promotion_audit_non_string_target_run_id_blocks_without_crashing() -> None:
    final_report = {
        "validation_errors": [],
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
    }

    audit = build_phase3_promotion_audit(
        target_run_id=12345,  # type: ignore[arg-type]
        final_report=final_report,
        scorecard=_scorecard(1000),
        claim_ledger_text=f"12345\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False
    assert "target_run_id must match Phase 3 Slurm target run id pattern" in validate_phase3_promotion_audit(audit)


def test_promotion_audit_rejects_ready_with_stale_blockers() -> None:
    audit = {
        "schema_version": "bb.rl.phase3.promotion_audit.v1",
        "report_id": "bb_zyphra_rl_phase3_promotion_audit_v1",
        "claim_boundary": "phase3_promotion_review_only_not_scorecard_update",
        "target_run_id": "20260624T040000Z-slurm-243958",
        "completed_milestones": list(PHASE3_MILESTONES),
        "blocked_milestones": ["P3-M7"],
        "final_report_validation_errors": ["P3-M7: passed must be true"],
        "bd_epic_closed": True,
        "promotion_review_ready": True,
        "scorecard_update_allowed": False,
        "core_scorecard_update_allowed": False,
    }

    errors = validate_phase3_promotion_audit(audit)
    assert "ready promotion audit must not list blocked_milestones" in errors
    assert "ready promotion audit must not list final_report_validation_errors" in errors


def test_promotion_audit_rejects_wrong_schema_version() -> None:
    audit = {
        "schema_version": "wrong",
        "report_id": "bb_zyphra_rl_phase3_promotion_audit_v1",
        "claim_boundary": "phase3_promotion_review_only_not_scorecard_update",
        "target_run_id": "20260624T040000Z-slurm-243958",
        "completed_milestones": list(PHASE3_MILESTONES),
        "blocked_milestones": [],
        "final_report_validation_errors": [],
        "bd_epic_closed": True,
        "promotion_review_ready": True,
        "scorecard_update_allowed": False,
        "core_scorecard_update_allowed": False,
    }

    assert "schema_version must be Phase 3 promotion audit schema" in validate_phase3_promotion_audit(audit)


def test_promotion_audit_malformed_final_report_shape_blocks_without_crashing() -> None:
    audit = build_phase3_promotion_audit(
        target_run_id="20260624T040000Z-slurm-243958",
        final_report={"validation_errors": "stale error", "milestone_summaries": {"P3-M0": {"passed": True}}},
        scorecard=_scorecard(1000),
        claim_ledger_text=f"20260624T040000Z-slurm-243958\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["completed_milestones"] == []
    assert audit["final_report_validation_errors"] == ["stale error"]


def test_promotion_audit_malformed_scorecard_blocks_without_crashing() -> None:
    final_report = {
        "validation_errors": [],
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
    }

    audit = build_phase3_promotion_audit(
        target_run_id="20260624T040000Z-slurm-243958",
        final_report=final_report,
        scorecard={"current_verified_points": "oops", "total_points": "also-oops"},
        claim_ledger_text=f"20260624T040000Z-slurm-243958\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is True


def test_promotion_audit_non_string_claim_ledger_blocks_without_crashing() -> None:
    non_string_ledger = {
        "text": "20260624T040000Z-slurm-243958\nbb_zyphra_rl_phase3_final_report_v1\nphase3_final_report_claim_boundary"
    }
    audit = build_phase3_promotion_audit(
        target_run_id="20260624T040000Z-slurm-243958",
        final_report={
            "validation_errors": [],
            "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        },
        scorecard=_scorecard(1000),
        claim_ledger_text=non_string_ledger,  # type: ignore[arg-type]
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False


def test_promotion_audit_duplicate_failed_summary_blocks_readiness() -> None:
    target = "20260624T040000Z-slurm-243958"
    summaries = [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES]
    summaries.append({"milestone_id": "P3-M7", "passed": False})

    audit = build_phase3_promotion_audit(
        target_run_id=target,
        final_report={"validation_errors": [], "milestone_summaries": summaries},
        scorecard=_scorecard(1000),
        claim_ledger_text=f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["blocked_milestones"] == ["P3-M7"]
    assert "ready promotion audit must not list blocked_milestones" not in validate_phase3_promotion_audit(audit)




def test_promotion_audit_duplicate_passed_summary_blocks_readiness() -> None:
    target = "20260624T040000Z-slurm-243958"
    summaries = [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES]
    summaries.append({"milestone_id": "P3-M0", "passed": True})

    audit = build_phase3_promotion_audit(
        target_run_id=target,
        final_report={"validation_errors": [], "milestone_summaries": summaries},
        scorecard=_scorecard(1000),
        claim_ledger_text=f"{target}\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["completed_milestones"].count("P3-M0") == 2
    assert "every P3 milestone must be completed" in validate_phase3_promotion_audit(audit)




def test_promotion_audit_validator_rejects_malformed_ready_lists() -> None:
    audit = {
        "schema_version": "bb.rl.phase3.promotion_audit.v1",
        "report_id": "bb_zyphra_rl_phase3_promotion_audit_v1",
        "claim_boundary": "phase3_promotion_review_only_not_scorecard_update",
        "target_run_id": "20260624T040000Z-slurm-243958",
        "completed_milestones": [*PHASE3_MILESTONES, {"bad": 1}],
        "blocked_milestones": {},
        "final_report_validation_errors": "",
        "bd_epic_closed": True,
        "promotion_review_ready": True,
        "scorecard_update_allowed": False,
        "core_scorecard_update_allowed": False,
    }

    errors = validate_phase3_promotion_audit(audit)
    assert "blocked_milestones must be a list of strings" in errors
    assert "final_report_validation_errors must be a list of strings" in errors
    assert "every P3 milestone must be completed" in errors


def test_promotion_audit_validator_handles_unhashable_completed_milestones() -> None:
    audit = {
        "schema_version": "bb.rl.phase3.promotion_audit.v1",
        "report_id": "bb_zyphra_rl_phase3_promotion_audit_v1",
        "claim_boundary": "phase3_promotion_review_only_not_scorecard_update",
        "target_run_id": "20260624T040000Z-slurm-243958",
        "completed_milestones": [{"bad": 1}],
        "blocked_milestones": [],
        "final_report_validation_errors": [],
        "bd_epic_closed": True,
        "promotion_review_ready": True,
        "scorecard_update_allowed": False,
        "core_scorecard_update_allowed": False,
    }

    assert "every P3 milestone must be completed" in validate_phase3_promotion_audit(audit)

def test_promotion_audit_unhashable_milestone_ids_block_without_crashing() -> None:
    audit = build_phase3_promotion_audit(
        target_run_id="20260624T040000Z-slurm-243958",
        final_report={
            "validation_errors": [],
            "milestone_summaries": [{"milestone_id": {"bad": 1}, "passed": True}],
        },
        scorecard=_scorecard(1000),
        claim_ledger_text=f"20260624T040000Z-slurm-243958\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n",
        bd_epic_closed=True,
    )

    assert audit["promotion_review_ready"] is False
    assert audit["completed_milestones"] == []
    assert "every P3 milestone must be completed" in validate_phase3_promotion_audit(audit)

def test_audit_cli_revalidates_stale_final_report_errors(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    final_report = {
        "report_id": PHASE3_FINAL_REPORT_ID,
        "claim_boundary": PHASE3_FINAL_CLAIM_BOUNDARY,
        "target_run_id": "20260624T040000Z-slurm-243958",
        "validation_errors": [],
        "scorecard_update_allowed": False,
        "command_log_manifest": {},
        "milestone_reports": {},
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        "scorecard": {"current_verified_points": 1000, "total_points": 1000},
        "claim_ledger_text": "20260624T040000Z-slurm-243958\nphase3_final_report_claim_boundary\n",
    }
    (runs / "p3_m12_final_report.json").write_text(json.dumps(final_report))
    (phase_dir / "BB_ZYPHRA_RL_PHASE_3_CLAIM_LEDGER.md").write_text(
        f"20260624T040000Z-slurm-243958\n{PHASE3_FINAL_REPORT_ID}\n{PHASE3_FINAL_CLAIM_BOUNDARY}\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "commands must contain at least one command row" in audit["final_report_validation_errors"]


def test_audit_cli_blocks_malformed_final_report_json(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "p3_m12_final_report.json").write_text("{not json")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "JSONDecodeError" in audit["final_report_read_error"]
    assert audit["final_report_validation_errors"][0].startswith("final report is not readable JSON: JSONDecodeError")


def test_audit_cli_blocks_non_object_final_report_json(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "p3_m12_final_report.json").write_text(json.dumps([]))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert audit["final_report_validation_errors"] == ["final report must be a JSON object"]


def test_audit_cli_blocks_null_final_report_json(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "p3_m12_final_report.json").write_text("null")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert audit["final_report_validation_errors"] == ["final report must be a JSON object"]


def test_audit_cli_blocks_unreadable_claim_ledger(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    final_report = {
        "report_id": PHASE3_FINAL_REPORT_ID,
        "claim_boundary": PHASE3_FINAL_CLAIM_BOUNDARY,
        "target_run_id": "20260624T040000Z-slurm-243958",
        "validation_errors": [],
        "scorecard_update_allowed": False,
        "command_log_manifest": {},
        "milestone_reports": {},
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        "scorecard": {"current_verified_points": 1000, "total_points": 1000},
        "claim_ledger_text": "20260624T040000Z-slurm-243958\nphase3_final_report_claim_boundary\n",
    }
    (runs / "p3_m12_final_report.json").write_text(json.dumps(final_report))
    (phase_dir / "BB_ZYPHRA_RL_PHASE_3_CLAIM_LEDGER.md").mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "IsADirectoryError" in audit["claim_ledger_read_error"]


def test_audit_cli_revalidates_empty_final_report_object(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    (runs / "p3_m12_final_report.json").write_text("{}")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "report_id must be Phase 3 final report id" in audit["final_report_validation_errors"]


def test_audit_cli_blocks_missing_final_report(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    (phase_dir / "runs").mkdir(parents=True)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((phase_dir / "runs" / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "FileNotFoundError" in audit["final_report_read_error"]
    assert audit["final_report_validation_errors"][0].startswith("final report is missing: FileNotFoundError")


def test_audit_cli_blocks_missing_claim_ledger(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    runs = phase_dir / "runs"
    runs.mkdir(parents=True)
    final_report = {
        "report_id": PHASE3_FINAL_REPORT_ID,
        "claim_boundary": PHASE3_FINAL_CLAIM_BOUNDARY,
        "target_run_id": "20260624T040000Z-slurm-243958",
        "validation_errors": [],
        "scorecard_update_allowed": False,
        "command_log_manifest": {},
        "milestone_reports": {},
        "milestone_summaries": [{"milestone_id": milestone, "passed": True} for milestone in PHASE3_MILESTONES],
        "scorecard": {"current_verified_points": 1000, "total_points": 1000},
        "claim_ledger_text": "20260624T040000Z-slurm-243958\nphase3_final_report_claim_boundary\n",
    }
    (runs / "p3_m12_final_report.json").write_text(json.dumps(final_report))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/audit_phase3_promotion.py",
            "--phase-dir",
            str(phase_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    audit = json.loads((runs / "p3_m12_promotion_audit.json").read_text())
    assert audit["promotion_review_ready"] is False
    assert "FileNotFoundError" in audit["claim_ledger_read_error"]
