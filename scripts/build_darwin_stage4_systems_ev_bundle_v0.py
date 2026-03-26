from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_darwin_stage4_operator_ev_report_v0 import build_stage4_operator_ev_report  # noqa: E402
from scripts.build_darwin_stage4_topology_ev_report_v0 import build_stage4_topology_ev_report  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import build_stage4_live_comparisons  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "systems_live"
RUNS_PATH = OUT_DIR / "campaign_runs_v0.json"
COMPARISONS_PATH = OUT_DIR / "matched_budget_comparisons_v0.json"
SUMMARY_PATH = OUT_DIR / "live_economics_pilot_v0.json"
OPERATOR_JSON = OUT_DIR / "operator_ev_report_v0.json"
OPERATOR_MD = OUT_DIR / "operator_ev_report_v0.md"
TOPOLOGY_JSON = OUT_DIR / "topology_ev_report_v0.json"
TOPOLOGY_MD = OUT_DIR / "topology_ev_report_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_stage4_systems_ev_bundle() -> dict[str, object]:
    runs_payload = _load_json(RUNS_PATH)
    summary_payload = _load_json(SUMMARY_PATH)
    comparison_rows = build_stage4_live_comparisons(list(runs_payload.get("runs") or []))
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
    summary_payload["positive_power_signal_count"] = sum(1 for row in comparison_rows if row["positive_power_signal"])
    _write_json(SUMMARY_PATH, summary_payload)
    operator_summary = build_stage4_operator_ev_report(
        comparisons_path=COMPARISONS_PATH,
        out_json=OPERATOR_JSON,
        out_md=OPERATOR_MD,
    )
    topology_summary = build_stage4_topology_ev_report(
        comparisons_path=COMPARISONS_PATH,
        out_json=TOPOLOGY_JSON,
        out_md=TOPOLOGY_MD,
    )
    return {
        "summary_path": str(SUMMARY_PATH),
        "comparison_count": len(comparison_rows),
        "claim_eligible_comparison_count": summary_payload["claim_eligible_comparison_count"],
        "positive_power_signal_count": summary_payload["positive_power_signal_count"],
        "operator_row_count": operator_summary["row_count"],
        "topology_row_count": topology_summary["row_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 systems EV bundle from existing live pilot runs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_systems_ev_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_systems_ev_bundle={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
