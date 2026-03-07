from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.evolake.tools import summarize_batch_result


SCHEMA = "breadboard.atp.replay_eval_fixture.v1"


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON payload: {path}")
    return payload


def _avg(values: List[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _candidate_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    success = sum(1 for row in rows if bool(row.get("success")))
    error_count = sum(int(row.get("error_count") or 0) for row in rows)
    repl_values = [float(row["repl_ms_avg"]) for row in rows if isinstance(row.get("repl_ms_avg"), (int, float))]
    restore_values = [float(row["restore_ms_avg"]) for row in rows if isinstance(row.get("restore_ms_avg"), (int, float))]
    quality_score = max(0.0, 100.0 - (10.0 * error_count))
    return {
        "rows_total": total,
        "rows_success": success,
        "rows_error": total - success,
        "success_rate": (float(success) / float(total)) if total else 0.0,
        "error_events": float(error_count),
        "warning_events": 0.0,
        "quality_score": float(round(quality_score, 6)),
        "repl_ms_avg": _avg(repl_values),
        "restore_ms_avg": _avg(restore_values),
    }


def build_fixture_bundle(
    *,
    fixture_id: str,
    round_index: int,
    batch_payload: Dict[str, Any],
    rows: List[Dict[str, Any]],
    source_path: str,
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "fixture_id": fixture_id,
        "round_index": int(round_index),
        "source_batch_json": source_path,
        "row_count": len(rows),
        "result_count": len(batch_payload.get("results") or []),
        "metrics": _candidate_metrics(rows),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic ATP replay/eval fixture bundle.")
    parser.add_argument("--batch-json", required=True, help="Path to ATP batch response JSON payload.")
    parser.add_argument("--out-dir", required=True, help="Output directory for generated fixture files.")
    parser.add_argument("--fixture-id", default="", help="Optional fixture id (defaults to batch JSON stem).")
    parser.add_argument("--round-index", type=int, default=0, help="Round index used in derived rows.")
    parser.add_argument(
        "--timestamp",
        type=float,
        default=None,
        help="Optional fixed timestamp applied to all derived rows for deterministic fixtures.",
    )
    args = parser.parse_args(argv)

    batch_path = Path(args.batch_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    fixture_id = (args.fixture_id or batch_path.stem).strip() or batch_path.stem
    round_index = int(args.round_index)

    payload = _load_json(batch_path)
    rows = summarize_batch_result(payload, round_index=round_index)
    rows = sorted(rows, key=lambda row: (int(row.get("request_index") or 0), str(row.get("request_id") or "")))
    if args.timestamp is not None:
        for row in rows:
            row["ts"] = float(args.timestamp)

    bundle = build_fixture_bundle(
        fixture_id=fixture_id,
        round_index=round_index,
        batch_payload=payload,
        rows=rows,
        source_path=str(batch_path),
    )
    candidates = {
        "schema": "breadboard.atp.replay_eval_candidates.v1",
        "fixture_id": fixture_id,
        "candidates": [{"name": fixture_id, "metrics": dict(bundle.get("metrics") or {})}],
    }

    _write_json(out_dir / "atp_batch_response.json", payload)
    _write_jsonl(out_dir / "atp_anchor_rows.jsonl", rows)
    _write_json(out_dir / "replay_eval_candidates.json", candidates)
    _write_json(out_dir / "fixture_manifest.json", bundle)

    print(json.dumps({"ok": True, "schema": SCHEMA, "fixture_id": fixture_id, "out_dir": str(out_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
