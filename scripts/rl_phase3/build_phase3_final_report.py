from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.final_report import PHASE3_MILESTONES, build_phase3_final_report, validate_phase3_final_report


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(_read_text(path))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _collect_milestone_reports(runs: Path) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    canonical = runs / "milestone_reports"
    roots = [canonical] if canonical.exists() else []
    roots.append(runs)
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            if path.name in {"p3_m12_final_report.json", "p3_m12_promotion_audit.json"}:
                continue
            payload = _load_json(path)
            if not payload:
                continue
            milestone = payload.get("milestone_id")
            if not isinstance(milestone, str) or milestone not in PHASE3_MILESTONES:
                continue
            if root == canonical or milestone not in reports:
                reports[milestone] = payload
    return reports


def _scorecard(phase_dir: Path) -> dict:
    scorecard_path = phase_dir / "BB_ZYPHRA_RL_PHASE_3_SCORECARD.yaml"
    scorecard_text = _read_text(scorecard_path)
    current_match = re.search(r"^current_verified_points:\s*(\d+)", scorecard_text, re.MULTILINE)
    total_match = re.search(r"^total_points:\s*(\d+)", scorecard_text, re.MULTILINE)
    reviewed_match = re.search(r"^reviewed_final_report_id:\s*(\S+)", scorecard_text, re.MULTILINE)
    return {
        "raw": scorecard_text,
        "current_verified_points": int(current_match.group(1)) if current_match else 0,
        "total_points": int(total_match.group(1)) if total_match else 0,
        "reviewed_final_report_id": None if not reviewed_match or reviewed_match.group(1) == "null" else reviewed_match.group(1),
    }


def _require_ready_errors(report: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    active_scope = report.get("active_scope")
    if not isinstance(active_scope, Mapping):
        errors.append("active_scope must be present for --require-ready")
        return errors
    if active_scope.get("ready") is not True:
        errors.append("active_scope.ready must be true for --require-ready")
    verified = active_scope.get("core_raw_points_verified")
    total = active_scope.get("core_raw_points_total")
    if not isinstance(verified, int) or not isinstance(total, int) or verified != total:
        errors.append("core_raw_points_verified must equal core_raw_points_total for --require-ready")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    runs = args.phase_dir / "runs"
    manifest = _load_json(runs / "phase3_command_log_manifest.json")
    target_run_id = manifest.get("target_run_id", "")
    ledger_path = args.phase_dir / "BB_ZYPHRA_RL_PHASE_3_CLAIM_LEDGER.md"
    report = build_phase3_final_report(
        target_run_id=target_run_id,
        milestone_reports=_collect_milestone_reports(runs),
        command_log_manifest=manifest,
        scorecard=_scorecard(args.phase_dir),
        claim_ledger_text=_read_text(ledger_path),
    )
    errors = validate_phase3_final_report(report, repo_root=Path.cwd(), evidence_root=args.phase_dir.parents[1])
    readiness_errors = _require_ready_errors(report) if args.require_ready else []
    report["validation_errors"] = errors
    output = runs / "p3_m12_final_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"report": str(output), "validation_errors": errors, "readiness_errors": readiness_errors}, sort_keys=True))
    return 0 if not args.require_ready or (not errors and not readiness_errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
