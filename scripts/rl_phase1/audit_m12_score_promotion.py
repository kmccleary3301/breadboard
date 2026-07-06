from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import validate_m12_promotion_audit, write_m12_promotion_audit  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the non-scoring M12 score-promotion audit.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"),
    )
    parser.add_argument(
        "--final-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    )
    parser.add_argument(
        "--scorecard",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"),
    )
    parser.add_argument(
        "--claim-ledger",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"),
    )
    parser.add_argument(
        "--command-log-manifest",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"),
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit nonzero unless the audit says the target evidence is ready for separate scorecard review.",
    )
    args = parser.parse_args()

    audit = write_m12_promotion_audit(
        output_path=args.output,
        final_report_path=args.final_report,
        scorecard_path=args.scorecard,
        claim_ledger_path=args.claim_ledger,
        command_log_manifest_path=args.command_log_manifest,
    )
    errors = validate_m12_promotion_audit(audit)
    if errors:
        raise SystemExit("invalid_m12_promotion_audit: " + "; ".join(errors))
    print(
        "audit="
        + audit["audit_id"]
        + f" promotion_review_ready={audit['promotion_review_ready']} "
        + f"scorecard_update_allowed={audit['scorecard_update_allowed']} "
        + "missing_requirements="
        + (",".join(audit["missing_requirements"]) or "none")
    )
    if args.require_ready and not audit["promotion_review_ready"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
