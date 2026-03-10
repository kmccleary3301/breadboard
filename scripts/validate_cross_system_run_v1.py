#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _cross_system_eval_v1 import (
    VALID_RESULT_STATUSES,
    dump_json,
    load_jsonl,
    load_manifest,
    required_result_fields,
    validate_manifest_shape,
)


def validate_results(
    manifest: Dict[str, Any],
    rows: List[Dict[str, Any]],
    *,
    allow_incomplete_matrix: bool,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    task_ids = set(str(item).strip() for item in (((manifest.get("benchmark") or {}).get("slice") or {}).get("task_ids") or []))
    system_ids = set(
        str(item.get("system_id") or "").strip()
        for item in (manifest.get("systems") or [])
        if isinstance(item, dict)
    )
    required_fields = required_result_fields(manifest)
    seen_pairs: set[tuple[str, str]] = set()
    rows_per_system: Dict[str, int] = {system_id: 0 for system_id in system_ids}
    status_counts: Dict[str, int] = {status: 0 for status in VALID_RESULT_STATUSES}
    for index, row in enumerate(rows):
        source = f"{row.get('_source_path')}:{row.get('_source_line')}"
        for field in required_fields:
            value = row.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"rows[{index}] {source} missing required field: {field}")
        task_id = str(row.get("task_id") or "").strip()
        system_id = str(row.get("prover_system") or "").strip()
        status = str(row.get("status") or "").strip().upper()
        if task_id and task_ids and task_id not in task_ids:
            errors.append(f"rows[{index}] {source} task_id not in manifest slice: {task_id}")
        if system_id and system_ids and system_id not in system_ids:
            errors.append(f"rows[{index}] {source} prover_system not in manifest.systems: {system_id}")
        if status not in VALID_RESULT_STATUSES:
            errors.append(f"rows[{index}] {source} invalid status={status!r}")
        else:
            status_counts[status] = int(status_counts.get(status, 0)) + 1
        if task_id and system_id:
            pair = (task_id, system_id)
            if pair in seen_pairs:
                errors.append(f"duplicate task/system pair: {task_id}/{system_id}")
            seen_pairs.add(pair)
            if system_id in rows_per_system:
                rows_per_system[system_id] += 1
    expected_pairs = len(task_ids) * len(system_ids)
    actual_pairs = len(seen_pairs)
    if expected_pairs > 0 and actual_pairs < expected_pairs:
        message = f"incomplete matrix coverage: expected_pairs={expected_pairs} actual_pairs={actual_pairs}"
        if allow_incomplete_matrix:
            warnings.append(message)
        else:
            errors.append(message)
    summary = {
        "row_count": len(rows),
        "expected_pairs": expected_pairs,
        "actual_pairs": actual_pairs,
        "rows_per_system": rows_per_system,
        "status_counts": status_counts,
        "required_result_fields": required_fields,
    }
    return errors, warnings, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--results", action="append", required=True)
    parser.add_argument("--json-out", default="artifacts/benchmarks/cross_system_validation_report_v1.latest.json")
    parser.add_argument("--allow-incomplete-matrix", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    result_paths = [Path(item).resolve() for item in args.results]
    manifest = load_manifest(manifest_path)
    rows = load_jsonl(result_paths)
    errors = validate_manifest_shape(manifest)
    result_errors, warnings, summary = validate_results(manifest, rows, allow_incomplete_matrix=bool(args.allow_incomplete_matrix))
    errors.extend(result_errors)
    payload = {
        "schema": "breadboard.cross_system_validation_report.v1",
        "ok": len(errors) == 0,
        "manifest_path": str(manifest_path),
        "result_paths": [str(path) for path in result_paths],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }
    out_path = Path(args.json_out).resolve()
    dump_json(out_path, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"[cross-system-validate-v1] ok={payload['ok']} errors={payload['error_count']} warnings={payload['warning_count']} rows={summary['row_count']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
