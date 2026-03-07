#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(payload: Any) -> str:
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _normalize_input_hash(input_text: str) -> str:
    normalized = " ".join(str(input_text).split())
    return _sha256_bytes(normalized.encode("utf-8"))


def _seeded_order_key(*, task_id: str, seed: int) -> str:
    return _sha256_bytes(f"{int(seed)}:{task_id}".encode("utf-8"))


def _resolve_git_sha(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    value = result.stdout.strip()
    return value or "unknown"


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    input_mode: str
    input_text: str
    source_ref: str
    metadata: dict[str, Any]


def _load_putnambench_tasks(root: Path) -> tuple[list[TaskRecord], dict[str, Any]]:
    source_dir = (root / "lean4" / "src").resolve()
    if not source_dir.exists():
        raise ValueError(f"putnambench source dir missing: {source_dir}")
    tasks: list[TaskRecord] = []
    digest_rows: list[dict[str, str]] = []
    for lean_path in sorted(source_dir.glob("*.lean")):
        content = lean_path.read_text(encoding="utf-8")
        task_id = lean_path.stem
        source_ref = str(lean_path.relative_to(root))
        tasks.append(
            TaskRecord(
                task_id=task_id,
                input_mode="formal_lean",
                input_text=content,
                source_ref=source_ref,
                metadata={"source_ref": source_ref},
            )
        )
        digest_rows.append(
            {
                "task_id": task_id,
                "source_ref": source_ref,
                "content_sha256": _sha256_bytes(content.encode("utf-8")),
            }
        )
    if not tasks:
        raise ValueError(f"no PutnamBench Lean tasks found under {source_dir}")
    snapshot = {
        "benchmark_git_sha": _resolve_git_sha(root),
        "dataset_sha256": _canonical_sha256(digest_rows),
        "task_count": len(tasks),
    }
    return tasks, snapshot


def _coerce_formal_statement_to_sorry(formal_statement: str) -> str:
    statement = formal_statement.rstrip()
    lowered = statement.lower()
    if "sorry" in lowered:
        return statement + ("\n" if not statement.endswith("\n") else "")
    if statement.endswith(":= by"):
        return statement + "\n  sorry\n"
    if statement.endswith("by"):
        return statement + "\n  sorry\n"
    return statement + "\n:= by\n  sorry\n"


def _load_minif2f_tasks(dataset_json: Path) -> tuple[list[TaskRecord], dict[str, Any]]:
    if not dataset_json.exists():
        raise ValueError(f"miniF2F dataset json missing: {dataset_json}")
    payload = json.loads(dataset_json.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("miniF2F payload must be non-empty JSON array")
    tasks: list[TaskRecord] = []
    digest_rows: list[dict[str, str]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"miniF2F row {index} must be object")
        task_id = str(row.get("name") or "").strip()
        formal_statement = str(row.get("formal_statement") or "").strip()
        if not task_id or not formal_statement:
            continue
        header = str(row.get("header") or "")
        split = str(row.get("split") or "").strip() or "unknown"
        informal_statement = str(row.get("informal_statement") or "").strip()
        full_input = f"{header.rstrip()}\n\n{_coerce_formal_statement_to_sorry(formal_statement).rstrip()}\n"
        tasks.append(
            TaskRecord(
                task_id=task_id,
                input_mode="formal_lean",
                input_text=full_input,
                source_ref=str(dataset_json),
                metadata={
                    "split": split,
                    "informal_statement": informal_statement,
                },
            )
        )
        digest_rows.append(
            {
                "task_id": task_id,
                "split": split,
                "formal_statement_sha256": _sha256_bytes(formal_statement.encode("utf-8")),
                "header_sha256": _sha256_bytes(header.encode("utf-8")),
            }
        )
    if not tasks:
        raise ValueError(f"no miniF2F tasks found in {dataset_json}")
    snapshot = {
        "benchmark_git_sha": _resolve_git_sha(dataset_json.parent.parent),
        "dataset_sha256": _canonical_sha256(digest_rows),
        "task_count": len(tasks),
    }
    return tasks, snapshot


def _select_slice(*, tasks: list[TaskRecord], seed: int, n_tasks: int) -> list[TaskRecord]:
    unique_map = {task.task_id: task for task in tasks}
    if len(unique_map) != len(tasks):
        raise ValueError("task IDs must be unique")
    if n_tasks < 1 or n_tasks > len(unique_map):
        raise ValueError(f"requested n_tasks={n_tasks} out of range (1..{len(unique_map)})")
    selected_ids = sorted(
        unique_map.keys(),
        key=lambda task_id: _seeded_order_key(task_id=task_id, seed=seed),
    )[:n_tasks]
    return [unique_map[task_id] for task_id in selected_ids]


def _build_task_inputs_payload(
    *,
    benchmark_name: str,
    seed: int,
    selected: list[TaskRecord],
) -> dict[str, Any]:
    return {
        "schema": "breadboard.aristotle_task_inputs.v1",
        "benchmark": benchmark_name,
        "slice_method": "seeded_hash_sort_v1",
        "seed": int(seed),
        "n_tasks": len(selected),
        "tasks": [
            {
                "task_id": row.task_id,
                "input_mode": row.input_mode,
                "input_text": row.input_text,
                "input_hash": _normalize_input_hash(row.input_text),
            }
            for row in selected
        ],
    }


def _build_manifest_payload(
    *,
    run_id: str,
    owner: str,
    purpose: str,
    benchmark_name: str,
    benchmark_version: dict[str, Any],
    seed: int,
    selected: list[TaskRecord],
    lean_version: str,
    mathlib_commit: str,
    docker_image_digest: str,
    budget_class: str,
    max_candidates: int,
    max_repair_rounds: int,
    wall_clock_cap_s: int,
    cost_cap_usd: float,
    baseline_config_ref: str,
    candidate_config_ref: str,
    artifacts_root: Path,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "owner": owner,
        "purpose": purpose,
        "benchmark": {
            "name": benchmark_name,
            "version": benchmark_version,
            "slice": {
                "method": "seeded_hash_sort_v1",
                "seed": int(seed),
                "n_tasks": len(selected),
                "task_ids": [row.task_id for row in selected],
            },
        },
        "toolchain": {
            "lean_version": lean_version,
            "mathlib_commit": mathlib_commit,
            "docker_image_digest": docker_image_digest,
        },
        "budget": {
            "class": budget_class,
            "max_candidates": int(max_candidates),
            "max_repair_rounds": int(max_repair_rounds),
            "wall_clock_cap_s": int(wall_clock_cap_s),
            "cost_cap_usd": float(cost_cap_usd),
        },
        "systems": [
            {
                "system_id": "bb_atp",
                "config_ref": baseline_config_ref,
            },
            {
                "system_id": "aristotle_api",
                "config_ref": candidate_config_ref,
                "notes": "end_to_end_blackbox",
            },
        ],
        "artifacts": {
            "root_dir": str(artifacts_root),
            "store_proofs": True,
            "store_logs": True,
            "redact_secrets": True,
        },
        "acceptance": {
            "determinism_reruns": 2,
            "required_fields": [
                "verification_log_digest",
                "toolchain_id",
                "input_hash",
            ],
        },
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_bundle(
    *,
    benchmark_slug: str,
    benchmark_name: str,
    snapshot: dict[str, Any],
    source_tag: str,
    tasks: list[TaskRecord],
    seed: int,
    n_tasks: int,
    out_root: Path,
    owner: str,
    purpose: str,
    lean_version: str,
    mathlib_commit: str,
    docker_image_digest: str,
    budget_class: str,
    max_candidates: int,
    max_repair_rounds: int,
    wall_clock_cap_s: int,
    cost_cap_usd: float,
    baseline_config_ref: str,
    candidate_config_ref: str,
) -> dict[str, Any]:
    selected = _select_slice(tasks=tasks, seed=seed, n_tasks=n_tasks)
    bundle_dir = out_root / f"{benchmark_slug}_seed{int(seed)}_n{int(n_tasks)}"
    run_id = f"cross-system-{benchmark_slug}-seed{int(seed)}-n{int(n_tasks)}"
    manifest = _build_manifest_payload(
        run_id=run_id,
        owner=owner,
        purpose=purpose,
        benchmark_name=benchmark_name,
        benchmark_version={
            "benchmark_git_sha": str(snapshot["benchmark_git_sha"]),
            "dataset_sha256": str(snapshot["dataset_sha256"]),
        },
        seed=seed,
        selected=selected,
        lean_version=lean_version,
        mathlib_commit=mathlib_commit,
        docker_image_digest=docker_image_digest,
        budget_class=budget_class,
        max_candidates=max_candidates,
        max_repair_rounds=max_repair_rounds,
        wall_clock_cap_s=wall_clock_cap_s,
        cost_cap_usd=cost_cap_usd,
        baseline_config_ref=baseline_config_ref,
        candidate_config_ref=candidate_config_ref,
        artifacts_root=bundle_dir,
    )
    task_inputs = _build_task_inputs_payload(benchmark_name=benchmark_name, seed=seed, selected=selected)

    slice_index = {
        "schema": "breadboard.cross_system_slice_index.v1",
        "benchmark": benchmark_name,
        "benchmark_slug": benchmark_slug,
        "source_tag": source_tag,
        "seed": int(seed),
        "n_tasks": int(n_tasks),
        "selection_method": "seeded_hash_sort_v1",
        "selected_task_ids": [row.task_id for row in selected],
        "source_task_count": int(snapshot["task_count"]),
        "benchmark_git_sha": str(snapshot["benchmark_git_sha"]),
        "dataset_sha256": str(snapshot["dataset_sha256"]),
        "task_source_map": {row.task_id: row.source_ref for row in selected},
    }
    manifest_path = bundle_dir / "cross_system_manifest.json"
    task_inputs_path = bundle_dir / "aristotle_task_inputs.json"
    slice_index_path = bundle_dir / "slice_index.json"
    _write_json(manifest_path, manifest)
    _write_json(task_inputs_path, task_inputs)
    _write_json(slice_index_path, slice_index)
    return {
        "benchmark": benchmark_name,
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "slice_index_path": str(slice_index_path),
        "task_count": len(selected),
        "seed": int(seed),
        "dataset_sha256": str(snapshot["dataset_sha256"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--putnambench-root", default="/tmp/bb_putnambench_src")
    parser.add_argument("--minif2f-dataset-json", default="/tmp/bb_minif2f_v2_src/datasets/miniF2F_v2c.json")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--n-tasks", type=int, default=100)
    parser.add_argument("--out-root", default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100")
    parser.add_argument("--owner", default="atp-team")
    parser.add_argument("--purpose", default="pilot_comparison")
    parser.add_argument("--lean-version", default="4.12.0")
    parser.add_argument("--mathlib-commit", default="unknown")
    parser.add_argument("--docker-image-digest", default="local")
    parser.add_argument("--budget-class", default="B")
    parser.add_argument("--max-candidates", type=int, default=4)
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--wall-clock-cap-s", type=int, default=300)
    parser.add_argument("--cost-cap-usd", type=float, default=25.0)
    parser.add_argument("--baseline-config-ref", default="agent_configs/codex_cli_gpt51mini_e4_live.yaml")
    parser.add_argument("--candidate-config-ref", default="scripts/run_aristotle_adapter_slice_v1.py")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    out_root = Path(args.out_root).resolve()
    putnam_tasks, putnam_snapshot = _load_putnambench_tasks(Path(args.putnambench_root).resolve())
    mini_tasks, mini_snapshot = _load_minif2f_tasks(Path(args.minif2f_dataset_json).resolve())

    bundles = [
        _build_bundle(
            benchmark_slug="putnambench_lean_s1",
            benchmark_name="putnambench_lean",
            snapshot=putnam_snapshot,
            source_tag=str(Path(args.putnambench_root).resolve()),
            tasks=putnam_tasks,
            seed=int(args.seed),
            n_tasks=int(args.n_tasks),
            out_root=out_root,
            owner=str(args.owner).strip(),
            purpose=str(args.purpose).strip(),
            lean_version=str(args.lean_version).strip(),
            mathlib_commit=str(args.mathlib_commit).strip(),
            docker_image_digest=str(args.docker_image_digest).strip(),
            budget_class=str(args.budget_class).strip(),
            max_candidates=int(args.max_candidates),
            max_repair_rounds=int(args.max_repair_rounds),
            wall_clock_cap_s=int(args.wall_clock_cap_s),
            cost_cap_usd=float(args.cost_cap_usd),
            baseline_config_ref=str(args.baseline_config_ref).strip(),
            candidate_config_ref=str(args.candidate_config_ref).strip(),
        ),
        _build_bundle(
            benchmark_slug="minif2f_v2_s1",
            benchmark_name="minif2f_v2",
            snapshot=mini_snapshot,
            source_tag=str(Path(args.minif2f_dataset_json).resolve()),
            tasks=mini_tasks,
            seed=int(args.seed),
            n_tasks=int(args.n_tasks),
            out_root=out_root,
            owner=str(args.owner).strip(),
            purpose=str(args.purpose).strip(),
            lean_version=str(args.lean_version).strip(),
            mathlib_commit=str(args.mathlib_commit).strip(),
            docker_image_digest=str(args.docker_image_digest).strip(),
            budget_class=str(args.budget_class).strip(),
            max_candidates=int(args.max_candidates),
            max_repair_rounds=int(args.max_repair_rounds),
            wall_clock_cap_s=int(args.wall_clock_cap_s),
            cost_cap_usd=float(args.cost_cap_usd),
            baseline_config_ref=str(args.baseline_config_ref).strip(),
            candidate_config_ref=str(args.candidate_config_ref).strip(),
        ),
    ]
    summary = {
        "schema": "breadboard.cross_system_frozen_slice_bundle.v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "seed": int(args.seed),
        "n_tasks": int(args.n_tasks),
        "out_root": str(out_root),
        "bundles": bundles,
    }
    summary_path = out_root / "bundle_summary.json"
    _write_json(summary_path, summary)
    _write_json(out_root / "build_summary.latest.json", summary)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"[build-cross-system-frozen-slices-v1] bundles={len(bundles)} summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
