from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import (  # noqa: E402
    summarize_m12_final_report_remediations,
    validate_m12_final_report,
    validate_m12_final_report_remediation_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize missing M12 final-report gates by target action.")
    parser.add_argument(
        "--final-report",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = json.loads(args.final_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"invalid_m12_final_report_input: {exc.__class__.__name__}: {exc}") from exc
    if not isinstance(report, dict):
        raise SystemExit("invalid_m12_final_report_input: expected JSON object")
    errors = validate_m12_final_report(report)
    if errors:
        raise SystemExit("invalid_m12_final_report: " + "; ".join(errors))

    summary = summarize_m12_final_report_remediations(report)
    summary_errors = validate_m12_final_report_remediation_summary(summary)
    if summary_errors:
        raise SystemExit("invalid_m12_remediation_summary: " + "; ".join(summary_errors))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    next_actions = ",".join(item["target_action_id"] for item in summary["next_target_actions"]) or "none"
    print(
        "summary="
        + summary["summary_id"]
        + f" score_eligible={summary['m12_score_eligible']}"
        + f" missing_gates={summary['missing_gate_count']}"
        + f" remediations={summary['remediation_count']}"
        + f" next_actions={next_actions}"
    )


if __name__ == "__main__":
    main()
