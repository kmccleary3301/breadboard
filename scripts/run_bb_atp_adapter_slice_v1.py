#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _cross_system_eval_v1 import dump_json, load_manifest

try:
    from breadboard_ext.atp.aristotle_adapter import build_toolchain_id, normalize_input_hash
except Exception:
    import hashlib

    def normalize_input_hash(input_text: str) -> str:
        normalized = " ".join(str(input_text).split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def build_toolchain_id(lean_version: str, mathlib_commit: str) -> str:
        version = str(lean_version).strip()
        commit = str(mathlib_commit).strip()
        return f"lean{version}_mathlib.{commit}"


def _load_task_inputs(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks_raw: list[Any]
    if isinstance(payload, dict):
        tasks_raw = payload.get("tasks") or []
    elif isinstance(payload, list):
        tasks_raw = payload
    else:
        raise ValueError("task inputs payload must be JSON object or array")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("task inputs must include non-empty `tasks` array")

    tasks: list[dict[str, str]] = []
    for index, row in enumerate(tasks_raw):
        if not isinstance(row, dict):
            raise ValueError(f"tasks[{index}] must be object")
        task_id = str(row.get("task_id") or "").strip()
        input_text = str(row.get("input_text") or "").strip()
        input_mode = str(row.get("input_mode") or "formal_lean").strip()
        input_hash = str(row.get("input_hash") or "").strip()
        if not task_id:
            raise ValueError(f"tasks[{index}] missing task_id")
        if not input_text:
            raise ValueError(f"tasks[{index}] missing input_text")
        tasks.append(
            {
                "task_id": task_id,
                "input_text": input_text,
                "input_mode": input_mode,
                "input_hash": input_hash,
            }
        )
    return tasks


def _manifest_toolchain_id(manifest: dict[str, Any]) -> str:
    toolchain = manifest.get("toolchain")
    if not isinstance(toolchain, dict):
        raise ValueError("manifest.toolchain missing")
    lean_version = str(toolchain.get("lean_version") or "").strip()
    mathlib_commit = str(toolchain.get("mathlib_commit") or "").strip()
    if not lean_version or not mathlib_commit:
        raise ValueError("manifest.toolchain requires lean_version + mathlib_commit")
    return build_toolchain_id(lean_version=lean_version, mathlib_commit=mathlib_commit)


def _manifest_budget_class(manifest: dict[str, Any]) -> str:
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("manifest.budget missing")
    budget_class = str(budget.get("class") or "").strip()
    if not budget_class:
        raise ValueError("manifest.budget.class missing")
    return budget_class


def _manifest_system_config_ref(manifest: dict[str, Any], *, system_id: str) -> str:
    systems = manifest.get("systems")
    if not isinstance(systems, list):
        return ""
    for row in systems:
        if not isinstance(row, dict):
            continue
        if str(row.get("system_id") or "").strip() != str(system_id).strip():
            continue
        return str(row.get("config_ref") or "").strip()
    return ""


def _heuristic_candidate(task: dict[str, str]) -> tuple[str | None, str | None]:
    task_id = str(task.get("task_id") or "").lower()
    text = str(task.get("input_text") or "").lower()
    rules: list[tuple[str, list[str], str]] = [
        ("nat.add_zero", ["n + 0 = n", "nat.add_zero", "add_zero"], "simpa using Nat.add_zero _"),
        ("nat.zero_add", ["0 + n = n", "nat.zero_add", "zero_add"], "simpa using Nat.zero_add _"),
        ("nat.mul_one", ["n * 1 = n", "nat.mul_one", "mul_one"], "simpa using Nat.mul_one _"),
        ("nat.one_mul", ["1 * n = n", "nat.one_mul", "one_mul"], "simpa using Nat.one_mul _"),
    ]
    for rule_id, patterns, proof in rules:
        if any(pattern in text for pattern in patterns) or any(pattern in task_id for pattern in patterns):
            return rule_id, proof
    return None, None


def _resolve_artifact_dirs(
    manifest: dict[str, Any],
    *,
    system_id: str,
    proof_output_dir: str | None,
    raw_output_dir: str | None,
) -> tuple[Path | None, Path]:
    artifacts_block = manifest.get("artifacts")
    root = None
    if isinstance(artifacts_block, dict):
        candidate = str(artifacts_block.get("root_dir") or "").strip()
        if candidate:
            root = Path(candidate).resolve()
    if root is None:
        root = Path("artifacts/benchmarks").resolve()

    effective_raw = Path(raw_output_dir).resolve() if raw_output_dir else root / "cross_system" / system_id / "raw"
    effective_proof = Path(proof_output_dir).resolve() if proof_output_dir else root / "cross_system" / system_id / "proofs"
    return effective_proof, effective_raw


def run_bb_slice(
    *,
    manifest_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_path: Path,
    system_id: str,
    allow_unverified_solved: bool,
    proof_output_dir: str | None,
    raw_output_dir: str | None,
    limit: int | None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    tasks = _load_task_inputs(task_inputs_path)
    if limit is not None:
        tasks = tasks[: max(0, int(limit))]
    if not tasks:
        raise ValueError("no tasks to run after applying limit")

    run_id = str(manifest.get("run_id") or "").strip() or "cross-system-run"
    toolchain_id = _manifest_toolchain_id(manifest)
    budget_class = _manifest_budget_class(manifest)
    config_ref = _manifest_system_config_ref(manifest, system_id=system_id)
    proof_dir, raw_dir = _resolve_artifact_dirs(
        manifest,
        system_id=system_id,
        proof_output_dir=proof_output_dir,
        raw_output_dir=raw_output_dir,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    if proof_dir is not None:
        proof_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        started = time.monotonic()
        task_id = str(task["task_id"])
        input_text = str(task["input_text"])
        task_input_hash = str(task.get("input_hash") or "").strip() or normalize_input_hash(input_text)
        matched_rule, candidate_proof = _heuristic_candidate(task)
        solved = bool(matched_rule and allow_unverified_solved)
        status = "SOLVED" if solved else "UNSOLVED"

        proof_artifact_ref: str | None = None
        if solved and candidate_proof and proof_dir is not None:
            proof_path = proof_dir / f"{task_id}.lean"
            proof_path.write_text(candidate_proof + "\n", encoding="utf-8")
            proof_artifact_ref = str(proof_path)

        diagnostic_payload = {
            "task_id": task_id,
            "mode": "heuristic_stub_v1",
            "matched_rule": matched_rule or "",
            "allow_unverified_solved": bool(allow_unverified_solved),
            "config_ref": config_ref,
            "status": status,
        }
        diagnostic_path = raw_dir / f"{task_id}.json"
        diagnostic_path.write_text(json.dumps(diagnostic_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verification_log_digest = normalize_input_hash(json.dumps(diagnostic_payload, sort_keys=True))

        row: dict[str, Any] = {
            "task_id": task_id,
            "toolchain_id": toolchain_id,
            "input_hash": task_input_hash,
            "prover_system": str(system_id).strip(),
            "budget_class": budget_class,
            "status": status,
            "verification_log_digest": verification_log_digest,
            "run_id": run_id,
            "attempts": 1,
            "repair_rounds_used": 0,
            "wall_clock_ms": max(0, int((time.monotonic() - started) * 1000.0)),
        }
        if proof_artifact_ref:
            row["proof_artifact_ref"] = proof_artifact_ref
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")

    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "UNKNOWN")
        status_counts[status] = int(status_counts.get(status, 0)) + 1

    payload = {
        "schema": "breadboard.bb_atp_adapter_slice_run.v1",
        "ok": True,
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "result_path": str(out_path),
        "summary_generated_at": int(time.time()),
        "run_id": run_id,
        "prover_system": str(system_id).strip(),
        "task_count": len(rows),
        "status_counts": status_counts,
        "allow_unverified_solved": bool(allow_unverified_solved),
        "config_ref": config_ref,
        "proof_output_dir": str(proof_dir) if proof_dir is not None else None,
        "raw_output_dir": str(raw_dir),
    }
    dump_json(summary_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument(
        "--out",
        default="artifacts/benchmarks/bb_atp_adapter_normalized_results_v1.latest.jsonl",
    )
    parser.add_argument(
        "--summary-out",
        default="artifacts/benchmarks/bb_atp_adapter_slice_summary_v1.latest.json",
    )
    parser.add_argument("--system-id", default="bb_atp")
    parser.add_argument("--allow-unverified-solved", action="store_true")
    parser.add_argument("--proof-output-dir", default="")
    parser.add_argument("--raw-output-dir", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_bb_slice(
        manifest_path=Path(args.manifest).resolve(),
        task_inputs_path=Path(args.task_inputs).resolve(),
        out_path=Path(args.out).resolve(),
        summary_path=Path(args.summary_out).resolve(),
        system_id=str(args.system_id).strip(),
        allow_unverified_solved=bool(args.allow_unverified_solved),
        proof_output_dir=str(args.proof_output_dir).strip() or None,
        raw_output_dir=str(args.raw_output_dir).strip() or None,
        limit=(int(args.limit) if int(args.limit) > 0 else None),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "[bb-atp-adapter-slice-v1] "
            f"ok={summary['ok']} tasks={summary['task_count']} "
            f"result_path={summary['result_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
