from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARISONS = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "matched_budget_comparisons_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "topology_ev_report_v0.json"
OUT_MD = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "topology_ev_report_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_topology_ev_report() -> dict:
    comparisons = _load_json(COMPARISONS)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in comparisons.get("rows") or []:
        buckets[(row["lane_id"], row["topology_id"])].append(row)
    rows = []
    lines = ["# Stage-3 Topology EV Report", ""]
    for (lane_id, topology_id), entries in sorted(buckets.items()):
        valid = [row for row in entries if row["comparison_valid"]]
        invalid = [row for row in entries if not row["comparison_valid"]]
        avg_delta = round(sum(float(row["delta_score"]) for row in valid) / len(valid), 6) if valid else 0.0
        row = {
            "lane_id": lane_id,
            "topology_id": topology_id,
            "trial_count": len(entries),
            "valid_comparison_count": len(valid),
            "invalid_comparison_count": len(invalid),
            "positive_signal_count": sum(1 for row in valid if row["positive_signal"]),
            "average_delta": avg_delta,
        }
        rows.append(row)
        lines.append(
            f"- `{lane_id}` / `{topology_id}`: trials=`{row['trial_count']}`, valid=`{row['valid_comparison_count']}`, "
            f"positive=`{row['positive_signal_count']}`, avg_delta=`{row['average_delta']}`"
        )
    payload = {"schema": "breadboard.darwin.stage3.topology_ev_report.v0", "row_count": len(rows), "rows": rows}
    _write_json(OUT_JSON, payload)
    _write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 topology expected-value report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_topology_ev_report()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"topology_ev_report={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
