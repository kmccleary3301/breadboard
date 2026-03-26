from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARISONS = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "matched_budget_comparisons_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "topology_ev_report_v0.json"
OUT_MD = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "topology_ev_report_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_stage4_topology_ev_report(
    *,
    comparisons_path: Path = COMPARISONS,
    out_json: Path = OUT_JSON,
    out_md: Path = OUT_MD,
) -> dict[str, object]:
    comparisons = _load_json(comparisons_path)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in comparisons.get("rows") or []:
        buckets[(str(row["lane_id"]), str(row["topology_id"]))].append(row)
    rows: list[dict] = []
    lines = ["# Stage-4 Topology EV Report", ""]
    for (lane_id, topology_id), entries in sorted(buckets.items()):
        valid = [row for row in entries if row["comparison_valid"]]
        positive = [row for row in valid if row["positive_power_signal"]]
        row = {
            "lane_id": lane_id,
            "topology_id": topology_id,
            "trial_count": len(entries),
            "valid_comparison_count": len(valid),
            "positive_power_signal_count": len(positive),
            "average_delta_score": round(sum(float(row["delta_score"]) for row in valid) / len(valid), 6) if valid else 0.0,
            "average_delta_runtime_ms": round(sum(int(row["delta_runtime_ms"]) for row in valid) / len(valid), 3) if valid else 0.0,
            "average_delta_cost_usd": round(sum(float(row["delta_cost_usd"]) for row in valid) / len(valid), 8) if valid else 0.0,
        }
        rows.append(row)
        lines.append(
            f"- `{lane_id}` / `{topology_id}`: valid=`{row['valid_comparison_count']}`, "
            f"positive_power=`{row['positive_power_signal_count']}`, avg_delta_runtime_ms=`{row['average_delta_runtime_ms']}`"
        )
    payload = {
        "schema": "breadboard.darwin.stage4.topology_ev_report.v0",
        "row_count": len(rows),
        "rows": rows,
    }
    _write_json(out_json, payload)
    _write_text(out_md, "\n".join(lines) + "\n")
    return {"out_json": str(out_json), "out_md": str(out_md), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 topology expected-value report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_topology_ev_report()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_topology_ev_report={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
