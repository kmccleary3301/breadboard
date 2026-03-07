#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _aristotle_compliance_gate import (
    DEFAULT_SCOPE_PATH,
    AristotleComplianceError,
    assert_aristotle_compliance,
)
from _cross_system_eval_v1 import dump_json, load_manifest
from breadboard_ext.atp.aristotle_adapter import (
    AristotleProjectAdapter,
    AristotleRunConfig,
    AristotleTaskInput,
    build_toolchain_id,
)


def _load_task_inputs(path: Path) -> list[AristotleTaskInput]:
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

    tasks: list[AristotleTaskInput] = []
    for index, row in enumerate(tasks_raw):
        if not isinstance(row, dict):
            raise ValueError(f"tasks[{index}] must be object")
        task_id = str(row.get("task_id") or "").strip()
        input_text = str(row.get("input_text") or "").strip()
        input_mode = str(row.get("input_mode") or "informal").strip()
        input_hash = str(row.get("input_hash") or "").strip() or None
        formal_input_context = str(row.get("formal_input_context") or "").strip() or None
        if not task_id:
            raise ValueError(f"tasks[{index}] missing task_id")
        if not input_text:
            raise ValueError(f"tasks[{index}] missing input_text")
        tasks.append(
            AristotleTaskInput(
                task_id=task_id,
                input_text=input_text,
                input_mode=input_mode,
                input_hash=input_hash,
                formal_input_context=formal_input_context,
            )
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


def _manifest_wall_clock_cap_s(manifest: dict[str, Any]) -> int:
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        return 300
    value = budget.get("wall_clock_cap_s")
    if isinstance(value, int) and value > 0:
        return value
    return 300


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


async def run_adapter_slice(
    *,
    manifest_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_path: Path,
    system_id: str,
    max_concurrency: int,
    poll_interval_seconds: float,
    wall_clock_cap_seconds: int | None,
    proof_output_dir: str | None,
    raw_output_dir: str | None,
    fail_fast: bool,
    limit: int | None,
    compliance_scope_path: Path | None = None,
    enforce_compliance: bool = True,
) -> dict[str, Any]:
    compliance_report: dict[str, Any] | None = None
    if enforce_compliance:
        compliance_report = assert_aristotle_compliance(
            scope_path=(compliance_scope_path.resolve() if compliance_scope_path else DEFAULT_SCOPE_PATH.resolve()),
            require_external_reporting=False,
        )

    manifest = load_manifest(manifest_path)
    tasks = _load_task_inputs(task_inputs_path)
    if limit is not None:
        tasks = tasks[: max(0, int(limit))]
    if not tasks:
        raise ValueError("no tasks to run after applying limit")

    run_config = AristotleRunConfig(
        run_id=str(manifest.get("run_id") or "").strip() or "cross-system-run",
        prover_system=str(system_id).strip(),
        toolchain_id=_manifest_toolchain_id(manifest),
        budget_class=_manifest_budget_class(manifest),
        wall_clock_cap_s=int(wall_clock_cap_seconds or _manifest_wall_clock_cap_s(manifest)),
        poll_interval_s=float(poll_interval_seconds),
    )
    proof_dir, raw_dir = _resolve_artifact_dirs(
        manifest,
        system_id=run_config.prover_system,
        proof_output_dir=proof_output_dir,
        raw_output_dir=raw_output_dir,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    adapter = AristotleProjectAdapter()
    rows = await adapter.run_tasks(
        tasks=tasks,
        run=run_config,
        max_concurrency=max_concurrency,
        proof_output_dir=proof_dir,
        fail_fast=fail_fast,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")

    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "UNKNOWN")
        status_counts[status] = int(status_counts.get(status, 0)) + 1

    payload = {
        "schema": "breadboard.aristotle_adapter_slice_run.v1",
        "ok": True,
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "result_path": str(out_path),
        "summary_generated_at": int(time.time()),
        "run_id": run_config.run_id,
        "prover_system": run_config.prover_system,
        "task_count": len(rows),
        "status_counts": status_counts,
        "proof_output_dir": str(proof_dir) if proof_dir is not None else None,
        "raw_output_dir": str(raw_dir),
        "compliance_gate_enforced": bool(enforce_compliance),
        "compliance_scope_path": str((compliance_scope_path or DEFAULT_SCOPE_PATH).resolve())
        if enforce_compliance
        else None,
        "compliance_status": str((compliance_report or {}).get("status") or "") if enforce_compliance else "skipped",
    }
    dump_json(summary_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument(
        "--out",
        default="artifacts/benchmarks/aristotle_adapter_normalized_results_v1.latest.jsonl",
    )
    parser.add_argument(
        "--summary-out",
        default="artifacts/benchmarks/aristotle_adapter_slice_summary_v1.latest.json",
    )
    parser.add_argument("--system-id", default="aristotle_api")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--wall-clock-cap-seconds", type=int, default=0)
    parser.add_argument("--proof-output-dir", default="")
    parser.add_argument("--raw-output-dir", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--compliance-scope",
        default=str(DEFAULT_SCOPE_PATH),
    )
    parser.add_argument("--skip-compliance-gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        summary = asyncio.run(
            run_adapter_slice(
                manifest_path=Path(args.manifest).resolve(),
                task_inputs_path=Path(args.task_inputs).resolve(),
                out_path=Path(args.out).resolve(),
                summary_path=Path(args.summary_out).resolve(),
                system_id=str(args.system_id).strip(),
                max_concurrency=max(1, int(args.max_concurrency)),
                poll_interval_seconds=max(0.1, float(args.poll_interval_seconds)),
                wall_clock_cap_seconds=(int(args.wall_clock_cap_seconds) if int(args.wall_clock_cap_seconds) > 0 else None),
                proof_output_dir=str(args.proof_output_dir).strip() or None,
                raw_output_dir=str(args.raw_output_dir).strip() or None,
                fail_fast=bool(args.fail_fast),
                limit=(int(args.limit) if int(args.limit) > 0 else None),
                compliance_scope_path=Path(args.compliance_scope).resolve(),
                enforce_compliance=not bool(args.skip_compliance_gate),
            )
        )
    except AristotleComplianceError as exc:
        print(f"[aristotle-adapter-slice-v1] ok=False compliance_error={exc}")
        return 2
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "[aristotle-adapter-slice-v1] "
            f"ok={summary['ok']} tasks={summary['task_count']} "
            f"result_path={summary['result_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
