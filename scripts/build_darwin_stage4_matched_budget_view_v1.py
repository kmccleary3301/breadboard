from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_darwin_stage4_live_economics_pilot_v0 import build_stage4_live_comparisons  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics"
RUNS_PATH = OUT_DIR / "campaign_runs_v0.json"
COMPARISONS_PATH = OUT_DIR / "matched_budget_comparisons_v0.json"
SUMMARY_PATH = OUT_DIR / "live_economics_pilot_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_stage4_matched_budget_view() -> dict[str, object]:
    runs_payload = _load_json(RUNS_PATH)
    summary_payload = _load_json(SUMMARY_PATH)
    run_rows = list(runs_payload.get("runs") or [])
    comparison_rows = build_stage4_live_comparisons(run_rows)
    _write_json(
        COMPARISONS_PATH,
        {
            "schema": "breadboard.darwin.stage4.matched_budget_comparisons.v0",
            "row_count": len(comparison_rows),
            "rows": comparison_rows,
        },
    )
    summary_payload["comparison_count"] = len(comparison_rows)
    summary_payload["claim_eligible_comparison_count"] = sum(1 for row in comparison_rows if row["claim_eligible"])
    _write_json(SUMMARY_PATH, summary_payload)
    return {
        "comparison_count": len(comparison_rows),
        "claim_eligible_comparison_count": summary_payload["claim_eligible_comparison_count"],
        "summary_path": str(SUMMARY_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Stage-4 matched-budget comparisons from existing campaign runs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_matched_budget_view()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_matched_budget_view={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
