#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _cross_system_eval_v1 import (
    dump_json,
    exact_mcnemar_pvalue,
    load_jsonl,
    load_manifest,
    solve_bool,
    wilson_interval,
)


def _to_row_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        system_id = str(row.get("prover_system") or "").strip()
        if not task_id or not system_id:
            continue
        index.setdefault(task_id, {})[system_id] = row
    return index


def _build_report(
    *,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    baseline_system: str,
    candidate_system: str,
) -> dict[str, Any]:
    task_ids: list[str] = [str(item).strip() for item in (((manifest.get("benchmark") or {}).get("slice") or {}).get("task_ids") or [])]
    system_ids: list[str] = [
        str(item.get("system_id") or "").strip()
        for item in (manifest.get("systems") or [])
        if isinstance(item, dict) and str(item.get("system_id") or "").strip()
    ]
    index = _to_row_index(rows)

    per_system: dict[str, dict[str, Any]] = {}
    for system_id in system_ids:
        available = [index.get(task_id, {}).get(system_id) for task_id in task_ids]
        available_rows = [row for row in available if isinstance(row, dict)]
        solved_count = sum(1 for row in available_rows if solve_bool(row.get("status")))
        coverage = len(available_rows)
        ci = wilson_interval(solved_count, coverage)
        per_system[system_id] = {
            "coverage_count": coverage,
            "coverage_rate": (float(coverage) / float(len(task_ids))) if task_ids else 0.0,
            "solved_count": solved_count,
            "solve_rate": (float(solved_count) / float(coverage)) if coverage else 0.0,
            "solve_rate_ci95": ci,
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
            paired["unpaired_task_count"] = int(paired["unpaired_task_count"]) + 1
            continue
        paired["paired_task_count"] = int(paired["paired_task_count"]) + 1
        baseline_ok = solve_bool(baseline_row.get("status"))
        candidate_ok = solve_bool(candidate_row.get("status"))
        if baseline_ok and candidate_ok:
            paired["n11_both_solved"] = int(paired["n11_both_solved"]) + 1
        elif (not baseline_ok) and candidate_ok:
            paired["n10_candidate_only"] = int(paired["n10_candidate_only"]) + 1
        elif baseline_ok and (not candidate_ok):
            paired["n01_baseline_only"] = int(paired["n01_baseline_only"]) + 1
        else:
            paired["n00_both_unsolved"] = int(paired["n00_both_unsolved"]) + 1

    cand_wins = int(paired["n10_candidate_only"])
    base_wins = int(paired["n01_baseline_only"])
    discordant = cand_wins + base_wins
    pvalue = exact_mcnemar_pvalue(cand_wins, base_wins)

    report = {
        "schema": "breadboard.cross_system_pilot_report.v1",
        "run_id": str(manifest.get("run_id") or ""),
        "benchmark_name": str(((manifest.get("benchmark") or {}).get("name")) or ""),
        "baseline_system": baseline_system,
        "candidate_system": candidate_system,
        "task_count": len(task_ids),
        "system_metrics": per_system,
        "paired_outcomes": paired,
        "mcnemar": {
            "discordant_pairs": discordant,
            "candidate_wins": cand_wins,
            "baseline_wins": base_wins,
            "exact_two_sided_pvalue": pvalue,
        },
        "directional_summary": {
            "candidate_minus_baseline_solve_rate": float(
                per_system.get(candidate_system, {}).get("solve_rate", 0.0)
            )
            - float(per_system.get(baseline_system, {}).get("solve_rate", 0.0)),
            "candidate_advantage_discordant": cand_wins - base_wins,
        },
    }
    return report


def _to_markdown(report: dict[str, Any]) -> str:
    baseline = report["baseline_system"]
    candidate = report["candidate_system"]
    paired = report["paired_outcomes"]
    mcnemar = report["mcnemar"]
    directional = report["directional_summary"]

    lines: list[str] = []
    lines.append("# Cross-System Pilot Comparison Report v1")
    lines.append("")
    lines.append(f"- run_id: `{report.get('run_id', '')}`")
    lines.append(f"- benchmark: `{report.get('benchmark_name', '')}`")
    lines.append(f"- task_count: `{report.get('task_count', 0)}`")
    lines.append(f"- baseline: `{baseline}`")
    lines.append(f"- candidate: `{candidate}`")
    lines.append("")
    lines.append("## System Metrics")
    lines.append("")
    lines.append("| system | coverage | solve_rate | solved | ci95 |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for system_id in (baseline, candidate):
        metrics = report["system_metrics"].get(system_id, {})
        ci = metrics.get("solve_rate_ci95", {})
        ci_text = f"[{float(ci.get('low', 0.0)):.3f}, {float(ci.get('high', 0.0)):.3f}]"
        lines.append(
            f"| {system_id} | {int(metrics.get('coverage_count', 0))} | "
            f"{float(metrics.get('solve_rate', 0.0)):.4f} | {int(metrics.get('solved_count', 0))} | {ci_text} |"
        )
    lines.append("")
    lines.append("## Paired Outcomes")
    lines.append("")
    lines.append(f"- paired_task_count: `{paired.get('paired_task_count', 0)}`")
    lines.append(f"- unpaired_task_count: `{paired.get('unpaired_task_count', 0)}`")
    lines.append(f"- n11_both_solved: `{paired.get('n11_both_solved', 0)}`")
    lines.append(f"- n10_candidate_only: `{paired.get('n10_candidate_only', 0)}`")
    lines.append(f"- n01_baseline_only: `{paired.get('n01_baseline_only', 0)}`")
    lines.append(f"- n00_both_unsolved: `{paired.get('n00_both_unsolved', 0)}`")
    lines.append("")
    lines.append("## McNemar (Exact)")
    lines.append("")
    lines.append(f"- discordant_pairs: `{mcnemar.get('discordant_pairs', 0)}`")
    lines.append(f"- candidate_wins: `{mcnemar.get('candidate_wins', 0)}`")
    lines.append(f"- baseline_wins: `{mcnemar.get('baseline_wins', 0)}`")
    lines.append(f"- exact_two_sided_pvalue: `{float(mcnemar.get('exact_two_sided_pvalue', 1.0)):.6f}`")
    lines.append("")
    lines.append("## Directional Summary")
    lines.append("")
    lines.append(
        "- candidate_minus_baseline_solve_rate: "
        f"`{float(directional.get('candidate_minus_baseline_solve_rate', 0.0)):.4f}`"
    )
    lines.append(
        f"- candidate_advantage_discordant: `{int(directional.get('candidate_advantage_discordant', 0))}`"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="Path to cross-system run manifest (YAML or JSON).")
    parser.add_argument("--results", action="append", required=True, help="Path(s) to normalized result JSONL.")
    parser.add_argument("--baseline-system", required=True, help="System id used as baseline.")
    parser.add_argument("--candidate-system", required=True, help="System id used as candidate.")
    parser.add_argument(
        "--out-json",
        default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--out-md",
        default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.md",
        help="Output Markdown report path.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(Path(args.manifest).resolve())
    rows = load_jsonl([Path(item).resolve() for item in args.results])
    report = _build_report(
        manifest=manifest,
        rows=rows,
        baseline_system=str(args.baseline_system).strip(),
        candidate_system=str(args.candidate_system).strip(),
    )

    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, report)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "[cross-system-pilot-report-v1] "
            f"baseline={report['baseline_system']} candidate={report['candidate_system']} "
            f"tasks={report['task_count']} pvalue={report['mcnemar']['exact_two_sided_pvalue']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
