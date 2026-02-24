#!/usr/bin/env python3
"""
Analyze frame-gap hotspots for replay scenarios.

This script reads index.jsonl from the latest run per scenario and reports
the largest inter-frame timestamp gaps to guide deterministic performance work.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

WAIT_ACTION_TYPES = {"wait_for_quiet", "wait_until", "sleep"}
BOUNDARY_ACTION_TYPES = {"key"}


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


def _discover_latest_run(run_root: Path, scenario: str) -> Path | None:
    slug = scenario.replace("phase4_replay/", "", 1)
    scenario_dir = run_root / slug
    if not scenario_dir.exists() or not scenario_dir.is_dir():
        return None
    manifests = sorted(scenario_dir.rglob("scenario_manifest.json"))
    if not manifests:
        return None
    return manifests[-1].parent


def _load_expected_waits(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = _load_json(path)
    scenarios = payload.get("scenarios", {})
    if not isinstance(scenarios, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in scenarios.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        actions = val.get("expected_wait_actions", [])
        if not isinstance(actions, list):
            actions = []
        out[key] = {
            "expected_wait_actions": [str(x) for x in actions if isinstance(x, str)],
            "note": str(val.get("note") or ""),
        }
    return out


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def analyze_hotspots(
    *,
    run_dir: Path,
    top_k: int,
    expected_waits_by_scenario: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest_path = run_dir / "scenario_manifest.json"
    index_path = run_dir / "index.jsonl"
    if not manifest_path.exists() or not index_path.exists():
        raise FileNotFoundError(f"run_dir missing manifest/index: {run_dir}")

    manifest = _load_json(manifest_path)
    rows = _load_jsonl(index_path)
    scenario = str(manifest.get("scenario") or "")
    expected_cfg = (expected_waits_by_scenario or {}).get(scenario, {})
    expected_wait_actions = (
        expected_cfg.get("expected_wait_actions", []) if isinstance(expected_cfg, dict) else []
    )
    if not isinstance(expected_wait_actions, list):
        expected_wait_actions = []
    expected_wait_set = {str(x) for x in expected_wait_actions if isinstance(x, str)}
    expected_wait_note = str(expected_cfg.get("note") or "") if isinstance(expected_cfg, dict) else ""
    executed_actions = manifest.get("executed_actions", [])
    if not isinstance(executed_actions, list):
        executed_actions = []

    def _overlap_actions(start_ts: float, end_ts: float) -> list[str]:
        out: list[str] = []
        for row in executed_actions:
            if not isinstance(row, dict):
                continue
            a = row.get("action", {})
            if not isinstance(a, dict):
                continue
            typ = str(a.get("type") or "")
            s = row.get("started_at")
            e = row.get("ended_at")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                continue
            # closed interval overlap
            if float(s) <= end_ts and float(e) >= start_ts:
                out.append(typ)
        return sorted(set(out))

    def _split_overlap_actions(actions: list[str]) -> tuple[list[str], list[str], bool]:
        wait = [a for a in actions if a in WAIT_ACTION_TYPES]
        non_wait = [a for a in actions if a not in WAIT_ACTION_TYPES and a not in BOUNDARY_ACTION_TYPES]
        wait_dominated = bool(wait) and len(non_wait) == 0
        return wait, non_wait, wait_dominated
    gaps: list[dict[str, Any]] = []
    last_ts: float | None = None
    last_frame: int | None = None
    for row in rows:
        ts = row.get("timestamp")
        frame = row.get("frame")
        if not isinstance(ts, (int, float)) or not isinstance(frame, int):
            continue
        if last_ts is not None and last_frame is not None:
            gap = float(ts) - float(last_ts)
            overlap_actions = _overlap_actions(float(last_ts), float(ts))
            wait_actions, non_wait_actions, wait_dominated = _split_overlap_actions(overlap_actions)
            unexpected_wait_actions = sorted([a for a in wait_actions if a not in expected_wait_set])
            wait_alignment = (
                "none"
                if not wait_actions
                else ("expected" if not unexpected_wait_actions else "unexpected")
            )
            gaps.append(
                {
                    "from_frame": int(last_frame),
                    "to_frame": int(frame),
                    "from_timestamp": float(last_ts),
                    "to_timestamp": float(ts),
                    "gap_seconds": gap,
                    "overlap_actions": overlap_actions,
                    "wait_actions": wait_actions,
                    "non_wait_actions": non_wait_actions,
                    "wait_overlap": bool(wait_actions),
                    "wait_dominated": wait_dominated,
                    "unexpected_wait_actions": unexpected_wait_actions,
                    "wait_alignment": wait_alignment,
                }
            )
        last_ts = float(ts)
        last_frame = int(frame)

    gap_values = sorted(float(g["gap_seconds"]) for g in gaps)
    top = sorted(gaps, key=lambda g: float(g["gap_seconds"]), reverse=True)[:top_k]
    return {
        "schema_version": "phase5_frame_gap_hotspots_v1",
        "scenario": scenario,
        "run_id": str(manifest.get("run_id") or run_dir.name),
        "run_dir": str(run_dir),
        "expected_wait_actions": sorted(expected_wait_set),
        "expected_wait_note": expected_wait_note,
        "gap_summary": {
            "count": len(gap_values),
            "mean": (sum(gap_values) / len(gap_values)) if gap_values else 0.0,
            "p95": _percentile(gap_values, 95.0),
            "max": gap_values[-1] if gap_values else 0.0,
            "top_wait_dominated_count": sum(1 for row in top if bool(row.get("wait_dominated"))),
            "top_expected_wait_count": sum(1 for row in top if str(row.get("wait_alignment")) == "expected"),
            "top_unexpected_wait_count": sum(
                1 for row in top if str(row.get("wait_alignment")) == "unexpected"
            ),
        },
        "top_gaps": top,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("gap_summary", {}) if isinstance(payload.get("gap_summary"), dict) else {}
    lines = [
        "# Phase5 Frame-Gap Hotspots",
        "",
        f"- scenario: `{payload.get('scenario')}`",
        f"- run_id: `{payload.get('run_id')}`",
        f"- run_dir: `{payload.get('run_dir')}`",
        f"- expected_wait_actions: `{', '.join(str(x) for x in (payload.get('expected_wait_actions') or []))}`",
        "",
        "| Gap Count | Mean | P95 | Max |",
        "|---:|---:|---:|---:|",
        f"| {int(summary.get('count') or 0)} | {float(summary.get('mean') or 0.0):.3f} | {float(summary.get('p95') or 0.0):.3f} | {float(summary.get('max') or 0.0):.3f} |",
        "",
        "## Top Gaps",
        "",
        "| Rank | From Frame | To Frame | Gap Seconds | Wait Dominated | Wait Alignment | Overlap Actions |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    top = payload.get("top_gaps", [])
    if isinstance(top, list):
        for idx, row in enumerate(top, start=1):
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {idx} | {int(row.get('from_frame') or 0)} | {int(row.get('to_frame') or 0)} | {float(row.get('gap_seconds') or 0.0):.3f} | {1 if bool(row.get('wait_dominated')) else 0} | {str(row.get('wait_alignment') or 'none')} | {', '.join(str(x) for x in (row.get('overlap_actions') or []))} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze frame-gap hotspots for Phase5 replay scenarios.")
    p.add_argument("--run-root", default="docs_tmp/tmux_captures/scenarios/phase4_replay")
    p.add_argument("--scenario", required=True)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--expected-waits-json")
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).expanduser().resolve()
    scenario = str(args.scenario)
    run_dir = _discover_latest_run(run_root, scenario)
    if run_dir is None:
        raise FileNotFoundError(f"latest run not found for scenario: {scenario}")

    expected_waits: dict[str, dict[str, Any]] = {}
    if args.expected_waits_json:
        expected_waits = _load_expected_waits(Path(args.expected_waits_json).expanduser().resolve())

    payload = analyze_hotspots(
        run_dir=run_dir,
        top_k=max(1, int(args.top_k)),
        expected_waits_by_scenario=expected_waits,
    )
    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")
    print(f"scenario={payload.get('scenario')}")
    print(f"max_gap={float((payload.get('gap_summary') or {}).get('max') or 0.0):.3f}")
    print(f"json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
