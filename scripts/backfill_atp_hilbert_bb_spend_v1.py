#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "canonical_baseline_index_v1.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "bb_spend_backfill_v1.json"

RUNNER_PATH = REPO_ROOT / "scripts" / "run_bb_formal_pack_v1.py"
sys.path.insert(0, str(RUNNER_PATH.parent))
_runner_spec = importlib.util.spec_from_file_location("run_bb_formal_pack_v1", RUNNER_PATH)
assert _runner_spec and _runner_spec.loader
runner = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(runner)


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload: {path}")
    return payload


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _extract_status_pack_ids(text: str) -> List[str]:
    return sorted(set(re.findall(r"\bpack_[a-z0-9_]+_minif2f_v\d+\b", text)))


def _extract_tmp_dirs(text: str) -> List[Path]:
    rels = sorted(set(re.findall(r"tmp/[A-Za-z0-9_./-]+", text)))
    return [(REPO_ROOT / rel).resolve() for rel in rels]


def _candidate_raw_dirs(entry: Dict[str, Any]) -> List[Path]:
    pack_dir = (REPO_ROOT / str(entry["report"])).resolve().parent
    status_doc = (REPO_ROOT / str(entry["status_doc"])).resolve()
    text = status_doc.read_text(encoding="utf-8")
    raw_dirs: List[Path] = []
    for path in sorted(pack_dir.glob("bb_hilbert_like_raw_v*")):
        if path.is_dir():
            raw_dirs.append(path.resolve())
    for pack_id in _extract_status_pack_ids(text):
        candidate_pack_dir = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / pack_id
        for path in sorted(candidate_pack_dir.glob("bb_hilbert_like_raw_v*")):
            if path.is_dir():
                raw_dirs.append(path.resolve())
    for tmp_dir in _extract_tmp_dirs(text):
        for path in sorted(tmp_dir.glob("bb_hilbert_like_raw_v*")):
            if path.is_dir():
                raw_dirs.append(path.resolve())
    seen = set()
    ordered: List[Path] = []
    for path in raw_dirs:
        key = str(path)
        if key not in seen and path.exists():
            seen.add(key)
            ordered.append(path)
    return ordered


def _raw_task_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw_dir in _candidate_raw_dirs(entry):
        for raw_path in sorted(raw_dir.glob("*.json")):
            try:
                payload = _load_json(raw_path)
            except Exception:
                continue
            task_id = str(payload.get("task_id") or raw_path.stem).strip()
            run_dir = str(payload.get("run_dir") or "").strip()
            if not task_id or not run_dir:
                continue
            rows.append(
                {
                    "task_id": task_id,
                    "raw_path": str(raw_path.relative_to(REPO_ROOT)),
                    "run_dir": run_dir,
                    "mtime_ns": raw_path.stat().st_mtime_ns,
                }
            )
    return rows


def _latest_task_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in _raw_task_rows(entry):
        task_id = row["task_id"]
        prev = latest.get(task_id)
        if prev is None or int(row["mtime_ns"]) >= int(prev["mtime_ns"]):
            latest[task_id] = row
    return [latest[key] for key in sorted(latest)]


def _build_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    task_rows = _latest_task_rows(entry)
    usage_rows: List[Dict[str, Any]] = []
    prompt_total = 0
    completion_total = 0
    cost_total = 0.0
    for row in task_rows:
        run_dir = (REPO_ROOT / row["run_dir"]).resolve() if not Path(row["run_dir"]).is_absolute() else Path(row["run_dir"]).resolve()
        usage = runner._usage_ledger_from_run_dir(run_dir)
        usage_rows.append(
            {
                "task_id": row["task_id"],
                "raw_path": row["raw_path"],
                **usage,
            }
        )
        prompt_total += int(usage.get("prompt_tokens") or 0)
        completion_total += int(usage.get("completion_tokens") or 0)
        cost_total += float(usage.get("estimated_cost_usd") or 0.0)
    return {
        "pack_id": entry["pack_id"],
        "task_count": int(entry["task_count"]),
        "task_rows_found": len(task_rows),
        "prompt_tokens_total": prompt_total,
        "completion_tokens_total": completion_total,
        "total_tokens": prompt_total + completion_total,
        "estimated_total_cost_usd": round(cost_total, 6),
        "rows": usage_rows,
    }


def build_payload(index_path: Path) -> Dict[str, Any]:
    canonical_index = _load_json(index_path)
    entries = [_build_entry(entry) for entry in (canonical_index.get("entries") or [])]
    return {
        "schema": "breadboard.atp_hilbert_bb_spend_backfill.v1",
        "index_path": _display_path(index_path),
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-json", default=str(DEFAULT_INDEX))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = build_payload(Path(args.index_json).resolve())
    out_json = Path(args.out_json).resolve()
    dump_json(out_json, payload)
    print(f"[atp-hilbert-bb-spend-backfill-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
