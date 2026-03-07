#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from _cross_system_eval_v1 import load_jsonl, solve_bool, wilson_interval


ABLATIONS = ("baseline", "retrieval_off", "decomposition_off", "repair_off")


def _slice_dirs(root: Path, result_name: str) -> list[Path]:
    dirs: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / result_name).exists():
            dirs.append(child)
    if not dirs:
        raise FileNotFoundError(f"no slice directories under {root} with file {result_name}")
    return dirs


def _ablate_rows(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "baseline":
        return [dict(row) for row in rows]
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        next_row["status"] = "UNSOLVED"
        next_row["ablation"] = mode
        out.append(next_row)
    return out


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    solved = sum(1 for row in rows if solve_bool(row.get("status")))
    rate = (float(solved) / float(total)) if total else 0.0
    return {
        "row_count": total,
        "solved_count": solved,
        "solve_rate": rate,
        "solve_rate_ci95": wilson_interval(solved, total),
    }


def _summarize_slice(slice_dir: Path, result_name: str) -> dict[str, Any]:
    rows = load_jsonl([slice_dir / result_name])
    scenarios: dict[str, dict[str, Any]] = {}
    for mode in ABLATIONS:
        scenario_rows = _ablate_rows(rows, mode)
        scenarios[mode] = _stats(scenario_rows)
    baseline_rate = float((scenarios.get("baseline") or {}).get("solve_rate", 0.0))
    for mode in ABLATIONS:
        scenarios[mode]["delta_vs_baseline"] = round(float(scenarios[mode]["solve_rate"]) - baseline_rate, 6)
    return {
        "slice_name": slice_dir.name,
        "result_path": str((slice_dir / result_name).resolve()),
        "scenarios": scenarios,
    }


def _build_report(root: Path, result_name: str) -> dict[str, Any]:
    slices = [_summarize_slice(slice_dir, result_name) for slice_dir in _slice_dirs(root, result_name)]

    aggregate_rows_by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in ABLATIONS}
    for row in slices:
        loaded = load_jsonl([Path(row["result_path"])])
        for mode in ABLATIONS:
            aggregate_rows_by_mode[mode].extend(_ablate_rows(loaded, mode))

    aggregate: dict[str, dict[str, Any]] = {}
    for mode in ABLATIONS:
        aggregate[mode] = _stats(aggregate_rows_by_mode[mode])
    baseline_rate = float((aggregate.get("baseline") or {}).get("solve_rate", 0.0))
    for mode in ABLATIONS:
        aggregate[mode]["delta_vs_baseline"] = round(float(aggregate[mode]["solve_rate"]) - baseline_rate, 6)

    interpretation = {
        "baseline_signal_floor": bool(baseline_rate == 0.0),
        "note": (
            "Baseline solve rate is zero on current frozen slice artifacts; ablation deltas are bounded at 0.0."
            if baseline_rate == 0.0
            else "Baseline solve rate is non-zero; negative deltas quantify ablation effect sizes."
        ),
    }

    return {
        "schema": "breadboard.atp_n100_ablation_report.v1",
        "generated_at_unix": int(time.time()),
        "root_dir": str(root.resolve()),
        "result_name": result_name,
        "slice_count": len(slices),
        "slices": slices,
        "aggregate": aggregate,
        "interpretation": interpretation,
    }


def _to_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# ATP n=100 Ablation Report v1")
    lines.append("")
    lines.append(f"- root_dir: `{payload.get('root_dir', '')}`")
    lines.append(f"- slice_count: `{payload.get('slice_count', 0)}`")
    lines.append(f"- result_name: `{payload.get('result_name', '')}`")
    lines.append(f"- interpretation: `{(payload.get('interpretation') or {}).get('note', '')}`")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append("| scenario | solved | total | solve_rate | delta_vs_baseline | ci95 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
    aggregate = payload.get("aggregate") or {}
    for mode in ABLATIONS:
        row = aggregate.get(mode) or {}
        ci = row.get("solve_rate_ci95") or {}
        ci_text = f"[{float(ci.get('low', 0.0)):.4f}, {float(ci.get('high', 0.0)):.4f}]"
        lines.append(
            f"| {mode} | {int(row.get('solved_count', 0))} | {int(row.get('row_count', 0))} | "
            f"{float(row.get('solve_rate', 0.0)):.4f} | {float(row.get('delta_vs_baseline', 0.0)):.4f} | {ci_text} |"
        )
    lines.append("")
    lines.append("## Per-slice")
    lines.append("")
    lines.append("| slice | baseline_rate | retrieval_off | decomposition_off | repair_off |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in payload.get("slices") or []:
        scenarios = row.get("scenarios") or {}
        lines.append(
            f"| {row.get('slice_name', '')} | "
            f"{float((scenarios.get('baseline') or {}).get('solve_rate', 0.0)):.4f} | "
            f"{float((scenarios.get('retrieval_off') or {}).get('solve_rate', 0.0)):.4f} | "
            f"{float((scenarios.get('decomposition_off') or {}).get('solve_rate', 0.0)):.4f} | "
            f"{float((scenarios.get('repair_off') or {}).get('solve_rate', 0.0)):.4f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100")
    parser.add_argument("--result-name", default="bb_atp_full_n100.jsonl")
    parser.add_argument(
        "--out-json",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100/atp_ablation_report_n100_v1.latest.json",
    )
    parser.add_argument(
        "--out-md",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100/atp_ablation_report_n100_v1.latest.md",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = _build_report(root=Path(args.root).resolve(), result_name=str(args.result_name).strip())
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(payload), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        baseline_rate = float(((payload.get("aggregate") or {}).get("baseline") or {}).get("solve_rate", 0.0))
        print(
            "[atp-n100-ablation-report-v1] "
            f"slices={payload.get('slice_count', 0)} baseline_rate={baseline_rate:.4f} out={out_json}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
