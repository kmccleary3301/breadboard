from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "search" / "invalid_comparison_ledger_v1.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_invalid_comparison_ledger() -> dict:
    payload = _load_json(SEARCH_SUMMARY)
    invalid_rows = []
    for row in payload.get("lanes") or []:
        if row.get("baseline_primary_score") is None or row.get("best_mutated_score") is None:
            invalid_rows.append(
                {
                    "lane_id": row["lane_id"],
                    "reason": "missing_baseline_or_mutated_score",
                }
            )
    return {
        "schema": "breadboard.darwin.invalid_comparison_ledger.v1",
        "invalid_count": len(invalid_rows),
        "rows": invalid_rows,
    }


def write_invalid_comparison_ledger() -> dict:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = build_invalid_comparison_ledger()
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(OUT_PATH), "invalid_count": payload["invalid_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit DARWIN invalid-comparison ledger for current search-smoke artifacts.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_invalid_comparison_ledger()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"invalid_comparison_ledger={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
