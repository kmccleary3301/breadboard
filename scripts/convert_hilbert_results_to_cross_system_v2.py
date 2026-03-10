#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json, load_manifest


def _normalize_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_task_hashes(path: Path) -> Dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    mapping = {}
    for task in tasks:
        if isinstance(task, dict):
            mapping[str(task["task_id"])] = str(task.get("input_hash") or "")
    return mapping


def convert(
    manifest_path: Path,
    hilbert_result_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_out: Path,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    task_hashes = _load_task_hashes(task_inputs_path)
    payload = json.loads(hilbert_result_path.read_text(encoding="utf-8"))
    successes = payload.get("successes", {})
    proofs = payload.get("proofs", {})
    formal_statements = payload.get("formal_statements", {})
    if not successes and isinstance(payload.get("results"), dict):
        successes = payload.get("results", {})
    problem_ids = payload.get("problem_ids", [])
    if not problem_ids and isinstance(successes, dict):
        problem_ids = list(successes.keys())
    budget_class = manifest["budget"]["class"]
    toolchain = manifest["toolchain"]
    run_id = str(manifest.get("run_id") or "hilbert-pack")
    rows: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    for idx, task_id in enumerate(problem_ids):
        task_id = str(task_id)
        success = bool(
            successes.get(task_id, successes.get(str(idx), successes.get(idx, False)))
        )
        proof_text = proofs.get(task_id, proofs.get(str(idx), proofs.get(idx)))
        formal_statement = formal_statements.get(task_id, formal_statements.get(str(idx), formal_statements.get(idx, "")))
        status = "SOLVED" if success else "UNSOLVED"
        status_counts[status] = status_counts.get(status, 0) + 1
        rows.append(
            {
                "task_id": task_id,
                "toolchain_id": f"lean-{toolchain['lean_version']}__mathlib-{toolchain['mathlib_commit']}",
                "input_hash": task_hashes.get(task_id) or _normalize_hash(formal_statement),
                "prover_system": "hilbert_roselab",
                "budget_class": budget_class,
                "status": status,
                "verification_log_digest": _normalize_hash(json.dumps({"success": success, "proof_present": bool(proof_text)}, sort_keys=True)),
                "run_id": run_id,
                "attempts": 1,
                "repair_rounds_used": 0,
                "wall_clock_ms": 0,
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    summary = {"ok": True, "task_count": len(rows), "status_counts": status_counts, "result_path": str(out_path)}
    dump_json(summary_out, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hilbert-results", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = convert(
        manifest_path=Path(args.manifest).resolve(),
        hilbert_result_path=Path(args.hilbert_results).resolve(),
        task_inputs_path=Path(args.task_inputs).resolve(),
        out_path=Path(args.out).resolve(),
        summary_out=Path(args.summary_out).resolve(),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"[convert-hilbert-v2] ok={summary['ok']} tasks={summary['task_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
