#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"summary is not a JSON object: {path}")
    return payload


def _run_stats(summary: dict[str, Any], run_key: str) -> dict[str, Any]:
    runs = summary.get("runs")
    if not isinstance(runs, dict):
        return {}
    run = runs.get(run_key)
    return run if isinstance(run, dict) else {}


def _pairwise_map(summary: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows = summary.get("pairwise_comparisons")
    if not isinstance(rows, list):
        return {}
    mapped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        left = str(row.get("from") or "").strip()
        right = str(row.get("to") or "").strip()
        if not left or not right:
            continue
        mapped[(left, right)] = row
    return mapped


def _series_row(
    run_label: str,
    putnam_run: dict[str, Any],
    minif2f_run: dict[str, Any],
) -> dict[str, Any]:
    putnam_wc = putnam_run.get("wall_clock_ms") if isinstance(putnam_run.get("wall_clock_ms"), dict) else {}
    minif2f_wc = minif2f_run.get("wall_clock_ms") if isinstance(minif2f_run.get("wall_clock_ms"), dict) else {}
    putnam_status = putnam_run.get("status_counts") if isinstance(putnam_run.get("status_counts"), dict) else {}
    minif2f_status = minif2f_run.get("status_counts") if isinstance(minif2f_run.get("status_counts"), dict) else {}
    return {
        "run": run_label,
        "putnam": {
            "status_counts": putnam_status,
            "mean_ms": float(putnam_wc.get("mean", 0.0) or 0.0),
            "median_ms": float(putnam_wc.get("median", 0.0) or 0.0),
            "max_ms": float(putnam_wc.get("max", 0.0) or 0.0),
        },
        "minif2f": {
            "status_counts": minif2f_status,
            "mean_ms": float(minif2f_wc.get("mean", 0.0) or 0.0),
            "median_ms": float(minif2f_wc.get("median", 0.0) or 0.0),
            "max_ms": float(minif2f_wc.get("max", 0.0) or 0.0),
        },
    }


def _transition_row(
    transition: str,
    putnam_row: dict[str, Any],
    minif2f_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transition": transition,
        "putnam": {
            "status_flip_count": int(putnam_row.get("status_flip_count", 0) or 0),
            "status_flip_rate": float(putnam_row.get("status_flip_rate", 0.0) or 0.0),
            "digest_flip_count": int(putnam_row.get("digest_flip_count", 0) or 0),
            "digest_flip_rate": float(putnam_row.get("digest_flip_rate", 0.0) or 0.0),
            "transitions": putnam_row.get("transitions") if isinstance(putnam_row.get("transitions"), dict) else {},
        },
        "minif2f": {
            "status_flip_count": int(minif2f_row.get("status_flip_count", 0) or 0),
            "status_flip_rate": float(minif2f_row.get("status_flip_rate", 0.0) or 0.0),
            "digest_flip_count": int(minif2f_row.get("digest_flip_count", 0) or 0),
            "digest_flip_rate": float(minif2f_row.get("digest_flip_rate", 0.0) or 0.0),
            "transitions": minif2f_row.get("transitions") if isinstance(minif2f_row.get("transitions"), dict) else {},
        },
    }


def build_dashboard(
    *,
    putnam_summary_path: Path,
    minif2f_summary_path: Path,
) -> dict[str, Any]:
    putnam = _load_summary(putnam_summary_path)
    minif2f = _load_summary(minif2f_summary_path)

    per_run = [
        _series_row(
            "baseline",
            _run_stats(putnam, "baseline_probe_n5"),
            _run_stats(minif2f, "baseline_head5"),
        ),
        _series_row("rerun_1", _run_stats(putnam, "rerun_1"), _run_stats(minif2f, "rerun_1")),
        _series_row("rerun_2", _run_stats(putnam, "rerun_2"), _run_stats(minif2f, "rerun_2")),
        _series_row("rerun_3", _run_stats(putnam, "rerun_3"), _run_stats(minif2f, "rerun_3")),
    ]

    putnam_pairs = _pairwise_map(putnam)
    minif2f_pairs = _pairwise_map(minif2f)
    pairwise = [
        _transition_row(
            "baseline→rerun_1",
            putnam_pairs.get(("baseline_probe_n5", "rerun_1"), {}),
            minif2f_pairs.get(("baseline_head5", "rerun_1"), {}),
        ),
        _transition_row(
            "rerun_1→rerun_2",
            putnam_pairs.get(("rerun_1", "rerun_2"), {}),
            minif2f_pairs.get(("rerun_1", "rerun_2"), {}),
        ),
        _transition_row(
            "rerun_2→rerun_3",
            putnam_pairs.get(("rerun_2", "rerun_3"), {}),
            minif2f_pairs.get(("rerun_2", "rerun_3"), {}),
        ),
    ]

    return {
        "schema": "breadboard.aristotle_cross_slice_stability_dashboard.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources": {
            "putnam_summary": str(putnam_summary_path.resolve()),
            "minif2f_summary": str(minif2f_summary_path.resolve()),
        },
        "per_run_comparison": per_run,
        "pairwise_drift_comparison": pairwise,
        "readout": [
            "Putnam remained unstable with service-side failure modes dominating later reruns.",
            "miniF2F showed partial solve behavior but retained timeout-driven rerun variance.",
            "Local stale-queue throttling was mitigated; upstream service variability remains the dominant risk.",
        ],
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Cross-Slice Aristotle Stability Drift Dashboard")
    lines.append("")
    lines.append(f"- generated_at_utc: `{payload.get('generated_at_utc', '')}`")
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    lines.append(f"- putnam_source: `{sources.get('putnam_summary', '')}`")
    lines.append(f"- minif2f_source: `{sources.get('minif2f_summary', '')}`")
    lines.append("")
    lines.append("## Per-run Status Comparison")
    lines.append("")
    lines.append("| run | putnam status_counts | putnam mean_ms | miniF2F status_counts | miniF2F mean_ms |")
    lines.append("| --- | --- | ---: | --- | ---: |")
    for row in payload.get("per_run_comparison", []):
        if not isinstance(row, dict):
            continue
        putnam = row.get("putnam") if isinstance(row.get("putnam"), dict) else {}
        minif2f = row.get("minif2f") if isinstance(row.get("minif2f"), dict) else {}
        lines.append(
            f"| {row.get('run', '')} | `{putnam.get('status_counts', {})}` | {putnam.get('mean_ms', 0.0)} | "
            f"`{minif2f.get('status_counts', {})}` | {minif2f.get('mean_ms', 0.0)} |"
        )
    lines.append("")
    lines.append("## Pairwise Drift Comparison")
    lines.append("")
    lines.append("| transition | putnam status_flip | putnam digest_flip | miniF2F status_flip | miniF2F digest_flip |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in payload.get("pairwise_drift_comparison", []):
        if not isinstance(row, dict):
            continue
        putnam = row.get("putnam") if isinstance(row.get("putnam"), dict) else {}
        minif2f = row.get("minif2f") if isinstance(row.get("minif2f"), dict) else {}
        lines.append(
            f"| {row.get('transition', '')} | "
            f"{int(putnam.get('status_flip_count', 0))} ({float(putnam.get('status_flip_rate', 0.0)):.1%}) | "
            f"{int(putnam.get('digest_flip_count', 0))} ({float(putnam.get('digest_flip_rate', 0.0)):.1%}) | "
            f"{int(minif2f.get('status_flip_count', 0))} ({float(minif2f.get('status_flip_rate', 0.0)):.1%}) | "
            f"{int(minif2f.get('digest_flip_count', 0))} ({float(minif2f.get('digest_flip_rate', 0.0)):.1%}) |"
        )
    lines.append("")
    lines.append("## Readout")
    lines.append("")
    for item in payload.get("readout", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cross-slice Aristotle stability drift dashboard.")
    parser.add_argument(
        "--putnam-summary",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1/putnambench_lean_s1_seed1337_n30/stability_rerun_3x_20260226/stability_drift_summary.json",
    )
    parser.add_argument(
        "--minif2f-summary",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1/minif2f_v2_s1_seed1337_n30/stability_rerun_3x_20260226/stability_drift_summary.json",
    )
    parser.add_argument(
        "--out-json",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1/stability_drift_dashboard_putnam_vs_minif2f.latest.json",
    )
    parser.add_argument(
        "--out-md",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1/stability_drift_dashboard_putnam_vs_minif2f.latest.md",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    payload = build_dashboard(
        putnam_summary_path=(repo_root / args.putnam_summary).resolve(),
        minif2f_summary_path=(repo_root / args.minif2f_summary).resolve(),
    )
    out_json = (repo_root / args.out_json).resolve()
    out_md = (repo_root / args.out_md).resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"[aristotle-cross-slice-dashboard] wrote {out_json} and {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
