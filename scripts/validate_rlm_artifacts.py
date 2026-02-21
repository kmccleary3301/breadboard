#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _require_keys(rows: Iterable[Dict[str, Any]], required: List[str], *, label: str) -> None:
    for idx, row in enumerate(rows):
        for key in required:
            if key not in row:
                raise ValueError(f"{label}[{idx}] missing required key: {key}")


def validate(run_dir: Path) -> Dict[str, Any]:
    meta = run_dir / "meta"
    subcalls_path = meta / "rlm_subcalls.jsonl"
    batch_subcalls_path = meta / "rlm_batch_subcalls.jsonl"
    batch_summary_path = meta / "rlm_batch_summary.json"
    blobs_path = meta / "rlm_blobs_manifest.jsonl"
    branches_path = meta / "rlm_branches.json"

    subcalls = _read_jsonl(subcalls_path)
    batch_subcalls = _read_jsonl(batch_subcalls_path)
    blobs = _read_jsonl(blobs_path)
    branches_payload: Dict[str, Any] = {}
    batch_summary_payload: Dict[str, Any] = {}
    if branches_path.exists():
        loaded = json.loads(branches_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            branches_payload = loaded
    if batch_summary_path.exists():
        loaded = json.loads(batch_summary_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            batch_summary_payload = loaded

    _require_keys(
        subcalls,
        [
            "event",
            "call_id",
            "branch_id",
            "route_id",
            "resolved_model",
            "prompt_hash",
            "consumed_blobs",
            "created_blobs",
        ],
        label="rlm_subcalls",
    )
    _require_keys(
        blobs,
        ["event", "blob_id", "created_blobs", "consumed_blobs"],
        label="rlm_blobs_manifest",
    )
    _require_keys(
        batch_subcalls,
        [
            "event",
            "batch_id",
            "request_index",
            "call_id",
            "branch_id",
            "status",
            "consumed_blobs",
            "created_blobs",
        ],
        label="rlm_batch_subcalls",
    )

    if branches_payload:
        if "schema_version" not in branches_payload:
            raise ValueError("rlm_branches.json missing schema_version")
        if "branches" not in branches_payload:
            raise ValueError("rlm_branches.json missing branches")
        if "events" not in branches_payload:
            raise ValueError("rlm_branches.json missing events")
    if batch_summary_payload:
        for key in ("batch_count", "batch_item_count", "batch_failures", "status_counts"):
            if key not in batch_summary_payload:
                raise ValueError(f"rlm_batch_summary.json missing {key}")

    return {
        "ok": True,
        "subcalls": len(subcalls),
        "batch_subcalls": len(batch_subcalls),
        "blobs": len(blobs),
        "has_branch_ledger": bool(branches_payload),
        "has_batch_summary": bool(batch_summary_payload),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate RLM run artifacts.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing meta/* artifacts.")
    args = parser.parse_args()
    payload = validate(Path(args.run_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
