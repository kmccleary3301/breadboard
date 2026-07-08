from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from breadboard.rl.phase3.final_report import validate_phase3_final_report
from breadboard.rl.phase3.promotion_audit import build_phase3_promotion_audit, validate_phase3_promotion_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    final_path = args.phase_dir / "runs" / "p3_m12_final_report.json"
    final_report_read_error = ""
    if not final_path.exists():
        final_report_read_error = f"FileNotFoundError: {final_path} is missing"
        final_report = {"validation_errors": [f"final report is missing: {final_report_read_error}"]}
    else:
        try:
            final_report = json.loads(final_path.read_text())
        except Exception as exc:
            final_report_read_error = f"{exc.__class__.__name__}: {exc}"
            final_report = {"validation_errors": [f"final report is not readable JSON: {final_report_read_error}"]}
    if not isinstance(final_report, dict):
        final_report_read_error = "TypeError: final report must be a JSON object"
        final_report = {"validation_errors": ["final report must be a JSON object"]}
    if not final_report_read_error and isinstance(final_report, dict):
        final_report["validation_errors"] = validate_phase3_final_report(final_report, repo_root=Path.cwd(), evidence_root=args.phase_dir.parents[1])
    claim_ledger_read_error = ""
    ledger_path = args.phase_dir / "BB_ZYPHRA_RL_PHASE_3_CLAIM_LEDGER.md"
    if not ledger_path.exists():
        claim_ledger_read_error = f"FileNotFoundError: {ledger_path} is missing"
        ledger = ""
    else:
        try:
            ledger = ledger_path.read_text()
        except Exception as exc:
            claim_ledger_read_error = f"{exc.__class__.__name__}: {exc}"
            ledger = ""
    scorecard = final_report.get("scorecard", {}) if isinstance(final_report, dict) else {}
    bd_closed = False
    try:
        result = subprocess.run(["bd", "show", "bb-5v6", "--json"], check=False, text=True, capture_output=True)
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            record = payload[0] if isinstance(payload, list) else payload
            bd_closed = isinstance(record, dict) and str(record.get("status", "")).lower() == "closed"
    except Exception:
        bd_closed = False
    target_run_id = final_report.get("target_run_id", "") if isinstance(final_report, dict) else ""
    audit = build_phase3_promotion_audit(target_run_id=target_run_id, final_report=final_report, scorecard=scorecard, claim_ledger_text=ledger, bd_epic_closed=bd_closed)
    if final_report_read_error:
        audit["final_report_read_error"] = final_report_read_error
    if claim_ledger_read_error:
        audit["claim_ledger_read_error"] = claim_ledger_read_error
    errors = validate_phase3_promotion_audit(audit)
    audit["validation_errors"] = errors
    output = args.phase_dir / "runs" / "p3_m12_promotion_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"audit": str(output), "validation_errors": errors, "promotion_review_ready": audit.get("promotion_review_ready")}, sort_keys=True))
    return 0 if not args.require_ready or not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
