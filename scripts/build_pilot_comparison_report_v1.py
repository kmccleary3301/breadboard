#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import (
    dump_json,
    exact_mcnemar_pvalue,
    load_jsonl,
    load_manifest,
    solve_bool,
    wilson_interval,
)


def _to_row_index(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        system_id = str(row.get("prover_system") or "").strip()
        if task_id and system_id:
            index.setdefault(task_id, {})[system_id] = row
    return index


def _build_report(manifest: Dict[str, Any], rows: List[Dict[str, Any]], baseline_system: str, candidate_system: str) -> Dict[str, Any]:
    task_ids = [str(item).strip() for item in (((manifest.get("benchmark") or {}).get("slice") or {}).get("task_ids") or [])]
    system_ids = [
        str(item.get("system_id") or "").strip()
        for item in (manifest.get("systems") or [])
        if isinstance(item, dict) and str(item.get("system_id") or "").strip()
    ]
    index = _to_row_index(rows)
    per_system: Dict[str, Dict[str, Any]] = {}
    for system_id in system_ids:
        available_rows = [index.get(task_id, {}).get(system_id) for task_id in task_ids]
        available_rows = [row for row in available_rows if isinstance(row, dict)]
        solved_count = sum(1 for row in available_rows if solve_bool(row.get("status")))
        coverage = len(available_rows)
        per_system[system_id] = {
            "coverage_count": coverage,
            "coverage_rate": (float(coverage) / float(len(task_ids))) if task_ids else 0.0,
            "solved_count": solved_count,
            "solve_rate": (float(solved_count) / float(coverage)) if coverage else 0.0,
            "solve_rate_ci95": wilson_interval(solved_count, coverage),
        }
    paired = {
        "n11_both_solved": 0,
        "n10_candidate_only": 0,
        "n01_baseline_only": 0,
        "n00_both_unsolved": 0,
        "paired_task_count": 0,
        "unpaired_task_count": 0,
    }
    for task_id in task_ids:
        baseline_row = index.get(task_id, {}).get(baseline_system)
        candidate_row = index.get(task_id, {}).get(candidate_system)
        if baseline_row is None or candidate_row is None:
            paired["unpaired_task_count"] += 1
            continue
        paired["paired_task_count"] += 1
        baseline_ok = solve_bool(baseline_row.get("status"))
        candidate_ok = solve_bool(candidate_row.get("status"))
        if baseline_ok and candidate_ok:
            paired["n11_both_solved"] += 1
        elif (not baseline_ok) and candidate_ok:
            paired["n10_candidate_only"] += 1
        elif baseline_ok and (not candidate_ok):
            paired["n01_baseline_only"] += 1
        else:
            paired["n00_both_unsolved"] += 1
    cand_wins = paired["n10_candidate_only"]
    base_wins = paired["n01_baseline_only"]
    return {
        "schema": "breadboard.cross_system_pilot_report.v1",
        "run_id": str(manifest.get("run_id") or ""),
        "benchmark_name": str((manifest.get("benchmark") or {}).get("name") or ""),
        "baseline_system": baseline_system,
        "candidate_system": candidate_system,
        "task_count": len(task_ids),
        "system_metrics": per_system,
        "paired_outcomes": paired,
        "mcnemar": {
            "discordant_pairs": cand_wins + base_wins,
            "candidate_wins": cand_wins,
            "baseline_wins": base_wins,
            "exact_two_sided_pvalue": exact_mcnemar_pvalue(cand_wins, base_wins),
        },
        "directional_summary": {
            "candidate_minus_baseline_solve_rate": float(per_system.get(candidate_system, {}).get("solve_rate", 0.0))
            - float(per_system.get(baseline_system, {}).get("solve_rate", 0.0)),
            "candidate_advantage_discordant": cand_wins - base_wins,
        },
    }


def _to_markdown(report: Dict[str, Any]) -> str:
    baseline = report["baseline_system"]
    candidate = report["candidate_system"]
    paired = report["paired_outcomes"]
    mcnemar = report["mcnemar"]
    lines = [
        "# Cross-System Pilot Comparison Report v1",
        "",
        f"- run_id: `{report.get('run_id', '')}`",
        f"- benchmark: `{report.get('benchmark_name', '')}`",
        f"- task_count: `{report.get('task_count', 0)}`",
        f"- baseline: `{baseline}`",
        f"- candidate: `{candidate}`",
        "",
        "## System Metrics",
        "",
        "| system | coverage | solve_rate | solved | ci95 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for system_id in (baseline, candidate):
        metrics = report["system_metrics"].get(system_id, {})
        ci = metrics.get("solve_rate_ci95", {})
        lines.append(
            f"| {system_id} | {int(metrics.get('coverage_count', 0))} | {float(metrics.get('solve_rate', 0.0)):.4f} | {int(metrics.get('solved_count', 0))} | [{float(ci.get('low', 0.0)):.3f}, {float(ci.get('high', 0.0)):.3f}] |"
        )
    lines.extend([
        "",
        "## Paired Outcomes",
        "",
        f"- paired_task_count: `{paired.get('paired_task_count', 0)}`",
        f"- unpaired_task_count: `{paired.get('unpaired_task_count', 0)}`",
        f"- n11_both_solved: `{paired.get('n11_both_solved', 0)}`",
        f"- n10_candidate_only: `{paired.get('n10_candidate_only', 0)}`",
        f"- n01_baseline_only: `{paired.get('n01_baseline_only', 0)}`",
        f"- n00_both_unsolved: `{paired.get('n00_both_unsolved', 0)}`",
        "",
        "## McNemar (Exact)",
        "",
        f"- discordant_pairs: `{mcnemar.get('discordant_pairs', 0)}`",
        f"- candidate_wins: `{mcnemar.get('candidate_wins', 0)}`",
        f"- baseline_wins: `{mcnemar.get('baseline_wins', 0)}`",
        f"- exact_two_sided_pvalue: `{float(mcnemar.get('exact_two_sided_pvalue', 1.0)):.6f}`",
        "",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--results", action="append", required=True)
    parser.add_argument("--baseline-system", required=True)
    parser.add_argument("--candidate-system", required=True)
    parser.add_argument("--out-json", default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.json")
    parser.add_argument("--out-md", default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.md")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(Path(args.manifest).resolve())
    rows = load_jsonl([Path(item).resolve() for item in args.results])
    report = _build_report(manifest, rows, str(args.baseline_system).strip(), str(args.candidate_system).strip())
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, report)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"[cross-system-pilot-report-v1] baseline={report['baseline_system']} candidate={report['candidate_system']} tasks={report['task_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
