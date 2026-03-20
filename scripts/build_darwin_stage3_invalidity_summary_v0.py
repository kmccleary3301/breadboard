from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARISONS = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "matched_budget_comparisons_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "invalidity_summary_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_invalidity_summary() -> dict:
    comparisons = _load_json(COMPARISONS)
    counter = Counter(row.get("invalid_reason") or "valid" for row in comparisons.get("rows") or [])
    payload = {
        "schema": "breadboard.darwin.stage3.invalidity_summary.v0",
        "total_rows": len(comparisons.get("rows") or []),
        "reason_counts": dict(counter),
    }
    _write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "reason_count": len(payload["reason_counts"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 invalidity summary.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_invalidity_summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"invalidity_summary={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
