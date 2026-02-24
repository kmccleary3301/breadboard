#!/usr/bin/env python3
"""
Build a replay-first Phase5 latency trend report from latest scenario runs.

The report is deterministic and uses only local replay artifacts:
- scenario_manifest.json
- index.jsonl

It can optionally compare against a prior trend report to surface drift.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from validate_phase4_claude_parity import DEFAULT_CONTRACT_FILE


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _contract_scenario_set(contract_file: Path, selected: str) -> set[str]:
    payload = _load_json(contract_file)
    scenario_sets = payload.get("scenario_sets", {})
    if not isinstance(scenario_sets, dict):
        return set()
    if selected == "all":
        out: set[str] = set()
        for value in scenario_sets.values():
            if isinstance(value, list):
                out.update(str(v) for v in value if str(v).strip())
        return out
    rows = scenario_sets.get(selected, [])
    if not isinstance(rows, list):
        return set()
    return {str(v) for v in rows if str(v).strip()}


def _discover_latest_runs(run_root: Path) -> list[Path]:
    if not run_root.exists() or not run_root.is_dir():
        return []
    out: list[Path] = []
    for scenario_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
        manifests = sorted(scenario_dir.rglob("scenario_manifest.json"))
        if manifests:
            out.append(manifests[-1].parent)
    return out


def _extract_replay_send_end(manifest: dict[str, Any]) -> float | None:
    executed = manifest.get("executed_actions", [])
    if not isinstance(executed, list):
        return None
    replay_send_end: float | None = None
    for row in executed:
        if not isinstance(row, dict):
            continue
        action = row.get("action")
        if not isinstance(action, dict):
            continue
        if action.get("type") != "send":
            continue
        text = str(action.get("text") or "")
        if not text.startswith("replay:"):
            continue
        ended_at = row.get("ended_at")
        if isinstance(ended_at, (int, float)):
            replay_send_end = float(ended_at)
    return replay_send_end


def _interval_overlap_actions(executed_actions: list[dict[str, Any]], start_ts: float, end_ts: float) -> list[str]:
    out: set[str] = set()
    for row in executed_actions:
        if not isinstance(row, dict):
            continue
        action = row.get("action")
        if not isinstance(action, dict):
            continue
        typ = str(action.get("type") or "").strip()
        if not typ:
            continue
        started = row.get("started_at")
        ended = row.get("ended_at")
        if not isinstance(started, (int, float)) or not isinstance(ended, (int, float)):
            continue
        if float(started) <= end_ts and float(ended) >= start_ts:
            out.add(typ)
    return sorted(out)


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def _metric_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "mean": 0.0, "p95": 0.0, "max": 0.0}
    vals = sorted(values)
    return {
        "min": vals[0],
        "mean": sum(vals) / len(vals),
        "p95": _percentile(vals, 95.0),
        "max": vals[-1],
    }


@dataclass
class TrendResult:
    payload: dict[str, Any]


def build_latency_trend(
    *,
    run_root: Path,
    scenario_set: str,
    contract_file: Path,
    max_first_frame_seconds: float,
    max_replay_first_frame_seconds: float,
    max_duration_seconds: float,
    max_stall_seconds: float,
    max_frame_gap_seconds: float,
    near_threshold_ratio: float,
    baseline_payload: dict[str, Any] | None,
) -> TrendResult:
    selected = _contract_scenario_set(contract_file, scenario_set)
    records: list[dict[str, Any]] = []
    missing_scenarios: list[str] = []

    for run_dir in _discover_latest_runs(run_root):
        manifest_path = run_dir / "scenario_manifest.json"
        index_path = run_dir / "index.jsonl"
        if not manifest_path.exists() or not index_path.exists():
            continue
        manifest = _load_json(manifest_path)
        scenario = str(manifest.get("scenario") or "")
        if selected and scenario not in selected:
            continue
        rows = _load_jsonl(index_path)
        executed_actions = manifest.get("executed_actions", [])
        if not isinstance(executed_actions, list):
            executed_actions = []
        timestamps = [float(r["timestamp"]) for r in rows if isinstance(r.get("timestamp"), (int, float))]
        started_at = manifest.get("started_at")
        first_frame = None
        if isinstance(started_at, (int, float)) and timestamps:
            first_frame = min(timestamps) - float(started_at)

        replay_first = None
        replay_send_end = _extract_replay_send_end(manifest)
        if replay_send_end is not None and timestamps:
            replay_ts = [ts for ts in timestamps if ts >= replay_send_end]
            if replay_ts:
                replay_first = min(replay_ts) - replay_send_end

        gap_rows: list[dict[str, Any]] = []
        wait_types = {"wait_for_quiet", "wait_until", "sleep"}
        for a, b in zip(timestamps, timestamps[1:]):
            overlap_actions = _interval_overlap_actions(executed_actions, float(a), float(b))
            wait_overlap = any(action in wait_types for action in overlap_actions)
            gap_rows.append(
                {
                    "gap_seconds": float(b) - float(a),
                    "overlap_actions": overlap_actions,
                    "wait_overlap": wait_overlap,
                }
            )
        max_gap_row = max(gap_rows, key=lambda row: float(row.get("gap_seconds") or 0.0)) if gap_rows else {}
        max_gap = float(max_gap_row.get("gap_seconds") or 0.0)
        max_gap_overlap_actions = (
            max_gap_row.get("overlap_actions") if isinstance(max_gap_row.get("overlap_actions"), list) else []
        )
        max_gap_wait_overlap = bool(max_gap_row.get("wait_overlap"))
        non_wait_gaps = [float(row.get("gap_seconds") or 0.0) for row in gap_rows if not bool(row.get("wait_overlap"))]
        max_non_wait_gap = max(non_wait_gaps) if non_wait_gaps else 0.0
        frame_validation = manifest.get("frame_validation", {})
        max_streak = float(frame_validation.get("max_unchanged_streak_seconds_estimate") or 0.0) if isinstance(frame_validation, dict) else 0.0
        duration = float(manifest.get("duration_seconds") or 0.0)

        checks = {
            "first_frame_seconds": bool(first_frame is not None and first_frame <= max_first_frame_seconds),
            "replay_first_frame_seconds": bool(replay_first is not None and replay_first <= max_replay_first_frame_seconds),
            "duration_seconds": duration <= max_duration_seconds,
            "max_stall_seconds": max_streak <= max_stall_seconds,
            "max_frame_gap_seconds": max_gap <= max_frame_gap_seconds,
            "max_non_wait_frame_gap_seconds": max_non_wait_gap <= max_frame_gap_seconds,
        }

        records.append(
            {
                "scenario": scenario,
                "run_id": run_dir.name,
                "run_dir": str(run_dir),
                "metrics": {
                    "first_frame_seconds": first_frame,
                    "replay_first_frame_seconds": replay_first,
                    "duration_seconds": duration,
                    "max_stall_seconds": max_streak,
                    "max_frame_gap_seconds": max_gap,
                    "max_non_wait_frame_gap_seconds": max_non_wait_gap,
                    "max_frame_gap_overlap_actions": max_gap_overlap_actions,
                    "max_frame_gap_wait_overlap": max_gap_wait_overlap,
                },
                "checks": checks,
                "ok": all(checks.values()),
            }
        )

    if selected:
        seen = {str(r.get("scenario") or "") for r in records}
        missing_scenarios = sorted(s for s in selected if s not in seen)

    first_frames = [float(r["metrics"]["first_frame_seconds"]) for r in records if isinstance(r["metrics"].get("first_frame_seconds"), (int, float))]
    replay_firsts = [float(r["metrics"]["replay_first_frame_seconds"]) for r in records if isinstance(r["metrics"].get("replay_first_frame_seconds"), (int, float))]
    durations = [float(r["metrics"]["duration_seconds"]) for r in records]
    stalls = [float(r["metrics"]["max_stall_seconds"]) for r in records]
    gaps = [float(r["metrics"]["max_frame_gap_seconds"]) for r in records]
    non_wait_gaps = [float(r["metrics"]["max_non_wait_frame_gap_seconds"]) for r in records]

    summary = {
        "first_frame_seconds": _metric_summary(first_frames),
        "replay_first_frame_seconds": _metric_summary(replay_firsts),
        "duration_seconds": _metric_summary(durations),
        "max_stall_seconds": _metric_summary(stalls),
        "max_frame_gap_seconds": _metric_summary(gaps),
        "max_non_wait_frame_gap_seconds": _metric_summary(non_wait_gaps),
        "ok_count": sum(1 for r in records if bool(r.get("ok"))),
        "record_count": len(records),
        "missing_count": len(missing_scenarios),
    }

    metric_thresholds = {
        "first_frame_seconds": max_first_frame_seconds,
        "replay_first_frame_seconds": max_replay_first_frame_seconds,
        "duration_seconds": max_duration_seconds,
        "max_stall_seconds": max_stall_seconds,
        "max_frame_gap_seconds": max_frame_gap_seconds,
        "max_non_wait_frame_gap_seconds": max_frame_gap_seconds,
    }
    near_threshold_contributors: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        scenario = str(record.get("scenario") or "unknown")
        metrics = record.get("metrics", {}) if isinstance(record.get("metrics"), dict) else {}
        for metric, limit in metric_thresholds.items():
            observed = metrics.get(metric)
            if not isinstance(observed, (int, float)) or limit <= 0:
                continue
            ratio = float(observed) / float(limit)
            if ratio >= near_threshold_ratio:
                contributor: dict[str, Any] = {
                    "scenario": scenario,
                    "metric": metric,
                    "observed": float(observed),
                    "threshold": float(limit),
                    "ratio": ratio,
                }
                if metric == "max_frame_gap_seconds":
                    contributor["overlap_actions"] = metrics.get("max_frame_gap_overlap_actions", [])
                    contributor["wait_overlap"] = bool(metrics.get("max_frame_gap_wait_overlap"))
                near_threshold_contributors.append(contributor)
    near_threshold_contributors.sort(key=lambda row: float(row.get("ratio") or 0.0), reverse=True)

    delta_summary: dict[str, Any] = {}
    if baseline_payload:
        baseline_summary = (
            baseline_payload.get("summary", {})
            if isinstance(baseline_payload.get("summary"), dict)
            else {}
        )
        for key in (
            "first_frame_seconds",
            "replay_first_frame_seconds",
            "duration_seconds",
            "max_stall_seconds",
            "max_frame_gap_seconds",
            "max_non_wait_frame_gap_seconds",
        ):
            cur = summary.get(key, {})
            base = baseline_summary.get(key, {})
            if isinstance(cur, dict) and isinstance(base, dict):
                delta_summary[key] = {
                    "mean_delta": float(cur.get("mean", 0.0)) - float(base.get("mean", 0.0)),
                    "p95_delta": float(cur.get("p95", 0.0)) - float(base.get("p95", 0.0)),
                    "max_delta": float(cur.get("max", 0.0)) - float(base.get("max", 0.0)),
                }

    payload = {
        "schema_version": "phase5_latency_trend_report_v1",
        "run_root": str(run_root),
        "scenario_set": scenario_set,
        "contract_file": str(contract_file),
        "thresholds": {
            "max_first_frame_seconds": max_first_frame_seconds,
            "max_replay_first_frame_seconds": max_replay_first_frame_seconds,
            "max_duration_seconds": max_duration_seconds,
            "max_stall_seconds": max_stall_seconds,
            "max_frame_gap_seconds": max_frame_gap_seconds,
            "max_non_wait_frame_gap_seconds": max_frame_gap_seconds,
        },
        "near_threshold": {
            "ratio_floor": near_threshold_ratio,
            "count": len(near_threshold_contributors),
            "top": near_threshold_contributors[:20],
        },
        "summary": summary,
        "baseline_present": bool(baseline_payload),
        "delta_summary": delta_summary,
        "missing_scenarios": missing_scenarios,
        "records": records,
        "overall_ok": summary["missing_count"] == 0 and summary["ok_count"] == summary["record_count"],
    }
    return TrendResult(payload=payload)


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Phase5 Latency Trend Report",
        "",
        f"- overall_ok: `{payload.get('overall_ok')}`",
        f"- scenario_set: `{payload.get('scenario_set')}`",
        f"- record_count: `{summary.get('record_count')}`",
        f"- ok_count: `{summary.get('ok_count')}`",
        f"- missing_count: `{summary.get('missing_count')}`",
        "",
        "| Metric | Min | Mean | P95 | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in (
        "first_frame_seconds",
        "replay_first_frame_seconds",
        "duration_seconds",
        "max_stall_seconds",
        "max_frame_gap_seconds",
        "max_non_wait_frame_gap_seconds",
    ):
        row = summary.get(key, {}) if isinstance(summary.get(key), dict) else {}
        lines.append(
            f"| {key} | {float(row.get('min', 0.0)):.3f} | {float(row.get('mean', 0.0)):.3f} | {float(row.get('p95', 0.0)):.3f} | {float(row.get('max', 0.0)):.3f} |"
        )

    delta_summary = payload.get("delta_summary", {})
    if isinstance(delta_summary, dict) and delta_summary:
        lines.extend(
            [
                "",
                "## Baseline Deltas",
                "",
                "| Metric | Mean Δ | P95 Δ | Max Δ |",
                "|---|---:|---:|---:|",
            ]
        )
        for key in (
            "first_frame_seconds",
            "replay_first_frame_seconds",
            "duration_seconds",
            "max_stall_seconds",
            "max_frame_gap_seconds",
            "max_non_wait_frame_gap_seconds",
        ):
            row = delta_summary.get(key, {}) if isinstance(delta_summary.get(key), dict) else {}
            lines.append(
                f"| {key} | {float(row.get('mean_delta', 0.0)):+.3f} | {float(row.get('p95_delta', 0.0)):+.3f} | {float(row.get('max_delta', 0.0)):+.3f} |"
            )

    if payload.get("missing_scenarios"):
        lines.extend(["", "## Missing Scenarios", ""])
        for scenario in payload["missing_scenarios"]:
            lines.append(f"- `{scenario}`")

    near = payload.get("near_threshold", {})
    if isinstance(near, dict):
        top = near.get("top", [])
        ratio_floor = float(near.get("ratio_floor", 0.0))
        if isinstance(top, list) and top:
            lines.extend(
                [
                    "",
                    "## Near-Threshold Contributors",
                    "",
                    f"- ratio_floor: `{ratio_floor:.3f}`",
                    "",
                    "| Scenario | Metric | Observed | Threshold | Ratio |",
                    "|---|---|---:|---:|---:|",
                ]
            )
            for row in top:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    "| {scenario} | {metric} | {observed:.3f} | {threshold:.3f} | {ratio:.3f} |".format(
                        scenario=str(row.get("scenario") or ""),
                        metric=str(row.get("metric") or ""),
                        observed=float(row.get("observed") or 0.0),
                        threshold=float(row.get("threshold") or 0.0),
                        ratio=float(row.get("ratio") or 0.0),
                    )
                )
                overlap_actions = row.get("overlap_actions")
                if isinstance(overlap_actions, list) and overlap_actions:
                    lines.append(
                        f"  - overlap_actions: `{', '.join(str(x) for x in overlap_actions)}`; wait_overlap=`{bool(row.get('wait_overlap'))}`"
                    )

    lines.extend(["", "## Scenario Records", "", "| Scenario | OK | First Frame | Replay→First | Duration | Stall | Max Gap |", "|---|---:|---:|---:|---:|---:|---:|"])
    records = payload.get("records", [])
    if isinstance(records, list):
        for record in sorted(records, key=lambda x: str(x.get("scenario") or "")):
            metrics = record.get("metrics", {}) if isinstance(record.get("metrics"), dict) else {}
            lines.append(
                "| {scenario} | {ok} | {ff} | {rf} | {dur} | {stall} | {gap} |".format(
                    scenario=str(record.get("scenario") or ""),
                    ok="PASS" if bool(record.get("ok")) else "FAIL",
                    ff=f"{float(metrics.get('first_frame_seconds')):.3f}" if isinstance(metrics.get("first_frame_seconds"), (int, float)) else "n/a",
                    rf=f"{float(metrics.get('replay_first_frame_seconds')):.3f}" if isinstance(metrics.get("replay_first_frame_seconds"), (int, float)) else "n/a",
                    dur=f"{float(metrics.get('duration_seconds')):.3f}",
                    stall=f"{float(metrics.get('max_stall_seconds')):.3f}",
                    gap=f"{float(metrics.get('max_frame_gap_seconds')):.3f}",
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Phase5 replay latency trend report.")
    p.add_argument("--run-root", default="docs_tmp/tmux_captures/scenarios/phase4_replay")
    p.add_argument("--scenario-set", choices=("hard_gate", "nightly", "all"), default="all")
    p.add_argument("--contract-file", default=str(DEFAULT_CONTRACT_FILE))
    p.add_argument("--baseline-json", default="")
    p.add_argument("--max-first-frame-seconds", type=float, default=3.0)
    p.add_argument("--max-replay-first-frame-seconds", type=float, default=2.0)
    p.add_argument("--max-duration-seconds", type=float, default=25.0)
    p.add_argument("--max-stall-seconds", type=float, default=3.0)
    p.add_argument("--max-frame-gap-seconds", type=float, default=3.2)
    p.add_argument("--near-threshold-ratio", type=float, default=0.85)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).expanduser().resolve()
    contract_file = Path(args.contract_file).expanduser().resolve()
    baseline_payload = None
    if args.baseline_json:
        baseline_path = Path(args.baseline_json).expanduser().resolve()
        if baseline_path.exists():
            baseline_payload = _load_json(baseline_path)

    result = build_latency_trend(
        run_root=run_root,
        scenario_set=str(args.scenario_set),
        contract_file=contract_file,
        max_first_frame_seconds=float(args.max_first_frame_seconds),
        max_replay_first_frame_seconds=float(args.max_replay_first_frame_seconds),
        max_duration_seconds=float(args.max_duration_seconds),
        max_stall_seconds=float(args.max_stall_seconds),
        max_frame_gap_seconds=float(args.max_frame_gap_seconds),
        near_threshold_ratio=float(args.near_threshold_ratio),
        baseline_payload=baseline_payload,
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result.payload, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(result.payload), encoding="utf-8")
    if result.payload.get("overall_ok"):
        print("phase5 latency trend: pass")
        print(f"records={result.payload.get('summary', {}).get('record_count')}")
        print(f"json={out_json}")
        return 0
    print("phase5 latency trend: fail")
    print(f"json={out_json}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
