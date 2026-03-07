#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from _cross_system_eval_v1 import dump_json, load_jsonl, load_manifest, solve_bool, wilson_interval


def _task_ids(manifest: dict[str, Any]) -> list[str]:
    benchmark = manifest.get("benchmark")
    if not isinstance(benchmark, dict):
        return []
    slice_block = benchmark.get("slice")
    if not isinstance(slice_block, dict):
        return []
    raw = slice_block.get("task_ids")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "UNKNOWN").strip() or "UNKNOWN"
        counts[status] = int(counts.get(status, 0)) + 1
    return counts


def _slice_summary(*, manifest_path: Path, result_path: Path, system_id: str) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    all_rows = load_jsonl([result_path])
    rows = [row for row in all_rows if str(row.get("prover_system") or "").strip() == system_id]
    task_ids = _task_ids(manifest)
    expected_tasks = len(task_ids)
    coverage_count = len(rows)
    solved_count = sum(1 for row in rows if solve_bool(row.get("status")))
    solve_rate = (float(solved_count) / float(coverage_count)) if coverage_count else 0.0
    ci95 = wilson_interval(solved_count, coverage_count)
    benchmark_name = str((manifest.get("benchmark") or {}).get("name") or "").strip()
    run_id = str(manifest.get("run_id") or "").strip()

    return {
        "slice_dir": str(manifest_path.parent),
        "slice_name": manifest_path.parent.name,
        "manifest_path": str(manifest_path),
        "result_path": str(result_path),
        "benchmark_name": benchmark_name,
        "run_id": run_id,
        "expected_tasks": expected_tasks,
        "coverage_count": coverage_count,
        "missing_count": max(0, expected_tasks - coverage_count),
        "solved_count": solved_count,
        "solve_rate": solve_rate,
        "solve_rate_ci95": ci95,
        "status_counts": _status_counts(rows),
    }


def _discover_slice_entries(root: Path, result_name: str) -> list[tuple[Path, Path]]:
    entries: list[tuple[Path, Path]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "cross_system_manifest.json"
        result_path = child / result_name
        if manifest_path.exists() and result_path.exists():
            entries.append((manifest_path, result_path))
    if not entries:
        raise FileNotFoundError(
            f"no slices found under {root} with cross_system_manifest.json and {result_name}"
        )
    return entries


def _build_report(*, root: Path, system_id: str, result_name: str) -> dict[str, Any]:
    entries = _discover_slice_entries(root=root, result_name=result_name)
    slices = [
        _slice_summary(manifest_path=manifest_path, result_path=result_path, system_id=system_id)
        for manifest_path, result_path in entries
    ]

    total_expected = sum(int(item["expected_tasks"]) for item in slices)
    total_coverage = sum(int(item["coverage_count"]) for item in slices)
    total_solved = sum(int(item["solved_count"]) for item in slices)
    macro_rate = (sum(float(item["solve_rate"]) for item in slices) / float(len(slices))) if slices else 0.0
    micro_rate = (float(total_solved) / float(total_coverage)) if total_coverage else 0.0

    return {
        "schema": "breadboard.bb_atp_baseline_slice_report.v1",
        "generated_at_unix": int(time.time()),
        "root_dir": str(root),
        "system_id": system_id,
        "result_name": result_name,
        "slice_count": len(slices),
        "aggregate": {
            "expected_tasks": total_expected,
            "coverage_count": total_coverage,
            "missing_count": max(0, total_expected - total_coverage),
            "solved_count": total_solved,
            "solve_rate_micro": micro_rate,
            "solve_rate_micro_ci95": wilson_interval(total_solved, total_coverage),
            "solve_rate_macro": macro_rate,
        },
        "slices": slices,
    }


def _to_markdown(payload: dict[str, Any]) -> str:
    agg = payload.get("aggregate") or {}
    lines: list[str] = []
    lines.append("# BB ATP Baseline Slice Report v1")
    lines.append("")
    lines.append(f"- root_dir: `{payload.get('root_dir', '')}`")
    lines.append(f"- system_id: `{payload.get('system_id', '')}`")
    lines.append(f"- result_name: `{payload.get('result_name', '')}`")
    lines.append(f"- slice_count: `{payload.get('slice_count', 0)}`")
    lines.append(
        f"- aggregate: coverage `{agg.get('coverage_count', 0)}` / expected `{agg.get('expected_tasks', 0)}`, solved `{agg.get('solved_count', 0)}`"
    )
    lines.append(
        "- micro solve rate: "
        f"`{float(agg.get('solve_rate_micro', 0.0)):.4f}` "
        f"(ci95: [{float((agg.get('solve_rate_micro_ci95') or {}).get('low', 0.0)):.4f}, "
        f"{float((agg.get('solve_rate_micro_ci95') or {}).get('high', 0.0)):.4f}])"
    )
    lines.append(f"- macro solve rate: `{float(agg.get('solve_rate_macro', 0.0)):.4f}`")
    lines.append("")
    lines.append("## Per-slice metrics")
    lines.append("")
    lines.append("| slice | benchmark | coverage | solved | solve_rate | ci95 | status_counts |")
    lines.append("| --- | --- | ---: | ---: | ---: | --- | --- |")
    for row in payload.get("slices") or []:
        ci = row.get("solve_rate_ci95") or {}
        ci_text = f"[{float(ci.get('low', 0.0)):.4f}, {float(ci.get('high', 0.0)):.4f}]"
        counts = json.dumps(row.get("status_counts") or {}, sort_keys=True)
        lines.append(
            f"| {row.get('slice_name', '')} | {row.get('benchmark_name', '')} | "
            f"{int(row.get('coverage_count', 0))}/{int(row.get('expected_tasks', 0))} | "
            f"{int(row.get('solved_count', 0))} | {float(row.get('solve_rate', 0.0)):.4f} | {ci_text} | `{counts}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--system-id", default="bb_atp")
    parser.add_argument("--result-name", default="bb_atp_full_n100.jsonl")
    parser.add_argument(
        "--out-json",
        default="artifacts/benchmarks/bb_atp_baseline_slice_report_v1.latest.json",
    )
    parser.add_argument(
        "--out-md",
        default="artifacts/benchmarks/bb_atp_baseline_slice_report_v1.latest.md",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = _build_report(
        root=Path(args.root).resolve(),
        system_id=str(args.system_id).strip(),
        result_name=str(args.result_name).strip(),
    )
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        agg = payload.get("aggregate") or {}
        print(
            "[bb-baseline-slice-report-v1] "
            f"slices={payload.get('slice_count', 0)} "
            f"coverage={agg.get('coverage_count', 0)}/{agg.get('expected_tasks', 0)} "
            f"solved={agg.get('solved_count', 0)} "
            f"micro={float(agg.get('solve_rate_micro', 0.0)):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
