from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_SUMMARY = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "bounded_inference_campaign_v0.json"
COMPARISONS = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "matched_budget_comparisons_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "operator_ev_report_v0.json"
OUT_MD = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "operator_ev_report_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_operator_ev_report() -> dict:
    _ = _load_json(CAMPAIGN_SUMMARY)
    comparisons = _load_json(COMPARISONS)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in comparisons.get("rows") or []:
        buckets[(row["lane_id"], row["operator_id"])].append(row)
    rows = []
    lines = ["# Stage-3 Operator EV Report", ""]
    for (lane_id, operator_id), entries in sorted(buckets.items()):
        valid = [row for row in entries if row["comparison_valid"]]
        invalid = [row for row in entries if not row["comparison_valid"]]
        avg_delta = round(sum(float(row["delta_score"]) for row in valid) / len(valid), 6) if valid else 0.0
        runtime_delta = round(sum(int(row["delta_runtime_ms"]) for row in valid) / len(valid), 3) if valid else 0.0
        positive = sum(1 for row in valid if row["positive_signal"])
        degraded = sum(1 for row in valid if float(row["delta_score"]) < 0)
        noop = sum(1 for row in valid if not row["positive_signal"] and float(row["delta_score"]) == 0.0 and int(row["delta_runtime_ms"]) >= 0)
        row = {
            "lane_id": lane_id,
            "operator_id": operator_id,
            "trial_count": len(entries),
            "valid_comparison_count": len(valid),
            "invalid_comparison_count": len(invalid),
            "improvement_count": positive,
            "degradation_count": degraded,
            "noop_count": noop,
            "average_delta": avg_delta,
            "average_runtime_delta_ms": runtime_delta,
            "matched_budget_retention_rate": round(len(valid) / len(entries), 6) if entries else 0.0,
        }
        rows.append(row)
        lines.append(
            f"- `{lane_id}` / `{operator_id}`: trials=`{row['trial_count']}`, valid=`{row['valid_comparison_count']}`, "
            f"positive=`{row['improvement_count']}`, avg_delta=`{row['average_delta']}`, avg_runtime_delta_ms=`{row['average_runtime_delta_ms']}`"
        )
    payload = {
        "schema": "breadboard.darwin.stage3.operator_ev_report.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    _write_json(OUT_JSON, payload)
    _write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 operator expected-value report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_operator_ev_report()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"operator_ev_report={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
