#!/usr/bin/env python3
"""
Validate replay-first Phase5 reliability signals (latency + artifact safety).

This validator operates only on existing tmux replay artifacts (no live model calls):
- scenario_manifest.json
- index.jsonl
- frames/*.row_parity.json
- frames/*.render_lock.json

Checks:
1) Latency/timing budgets (first frame, replay->first frame, total run duration, stall).
2) Row-parity safety (no missing/extra/mismatch/span-delta regressions).
3) Render-lock safety (expected profile, zero line_gap, no isolated low-edge seam rows).
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


def _discover_latest_runs(run_root: Path) -> list[Path]:
    if not run_root.exists() or not run_root.is_dir():
        return []
    out: list[Path] = []
    for scenario_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
        manifests = sorted(scenario_dir.rglob("scenario_manifest.json"))
        if manifests:
            out.append(manifests[-1].parent)
    return out


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


def _extract_first_frame_timestamps(index_rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not index_rows:
        return None, None
    timestamps = [
        float(row["timestamp"])
        for row in index_rows
        if isinstance(row.get("timestamp"), (int, float))
    ]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _count_row_parity_violations(run_dir: Path) -> tuple[int, list[str]]:
    violations = 0
    details: list[str] = []
    for path in sorted((run_dir / "frames").glob("*.row_parity.json")):
        payload = _load_json(path)
        parity = payload.get("parity", {})
        if not isinstance(parity, dict):
            violations += 1
            details.append(f"{path.name}: missing parity object")
            continue
        missing = int(parity.get("missing_count") or 0)
        extra = int(parity.get("extra_count") or 0)
        span_delta = int(parity.get("row_span_delta") or 0)
        mismatch_rows = parity.get("mismatch_rows", [])
        mismatch_count = len(mismatch_rows) if isinstance(mismatch_rows, list) else 0
        row_violations = 0
        if missing > 0:
            row_violations += missing
        if extra > 0:
            row_violations += extra
        if span_delta != 0:
            row_violations += abs(span_delta)
        if mismatch_count > 0:
            row_violations += mismatch_count
        if row_violations > 0:
            violations += row_violations
            details.append(
                f"{path.name}: missing={missing} extra={extra} span_delta={span_delta} mismatch_rows={mismatch_count}"
            )
    return violations, details


def _count_isolated_low_edge_rows(render_lock: dict[str, Any], *, ratio_ceiling: float = 0.02) -> int:
    occ = render_lock.get("row_occupancy", {})
    if not isinstance(occ, dict):
        return 0
    diagnostics = occ.get("edge_row_diagnostics", [])
    if not isinstance(diagnostics, list):
        return 0
    declared_rows = int(occ.get("declared_rows") or 0)
    if declared_rows <= 0:
        return 0
    edge_ratio_by_row: dict[int, float] = {}
    for row in diagnostics:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("row") or 0)
        if idx <= 0:
            continue
        ratio = float(row.get("edge_ratio") or 0.0)
        edge_ratio_by_row[idx] = ratio

    isolated = 0
    start_row = max(2, declared_rows // 2)
    for idx in range(start_row, declared_rows):
        current = edge_ratio_by_row.get(idx, 0.0)
        prev_ratio = edge_ratio_by_row.get(idx - 1, 0.0)
        next_ratio = edge_ratio_by_row.get(idx + 1, 0.0)
        if current <= 0.0 or current > ratio_ceiling:
            continue
        if prev_ratio == 0.0 and next_ratio == 0.0:
            isolated += 1
    return isolated


def _collect_render_lock_signals(run_dir: Path, *, expected_profile: str) -> dict[str, Any]:
    locks = sorted((run_dir / "frames").glob("*.render_lock.json"))
    profile_mismatch = 0
    line_gap_nonzero = 0
    isolated_rows_total = 0
    missing_occupancy = 0
    for path in locks:
        payload = _load_json(path)
        profile = str(payload.get("render_profile") or "")
        if expected_profile and profile != expected_profile:
            profile_mismatch += 1
        geometry = payload.get("geometry", {})
        line_gap = None
        if isinstance(geometry, dict):
            line_gap = int(geometry.get("line_gap") or 0)
        if line_gap and line_gap != 0:
            line_gap_nonzero += 1
        occ = payload.get("row_occupancy")
        if not isinstance(occ, dict):
            missing_occupancy += 1
            continue
        isolated_rows_total += _count_isolated_low_edge_rows(payload)
    return {
        "render_lock_count": len(locks),
        "render_profile_mismatch_frames": profile_mismatch,
        "line_gap_nonzero_frames": line_gap_nonzero,
        "isolated_low_edge_rows_total": isolated_rows_total,
        "missing_row_occupancy_frames": missing_occupancy,
    }


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    report: dict[str, Any]


def validate_reliability(
    *,
    run_root: Path,
    scenario_set_name: str,
    contract_file: Path,
    expected_render_profile: str,
    max_first_frame_seconds: float,
    max_replay_first_frame_seconds: float,
    max_duration_seconds: float,
    max_stall_seconds: float,
    max_isolated_low_edge_rows_total: int,
    fail_on_missing_scenarios: bool,
) -> ValidationResult:
    selected = _contract_scenario_set(contract_file, scenario_set_name)
    run_dirs = _discover_latest_runs(run_root)
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    seen_scenarios: set[str] = set()

    for run_dir in run_dirs:
        manifest_path = run_dir / "scenario_manifest.json"
        index_path = run_dir / "index.jsonl"
        if not manifest_path.exists() or not index_path.exists():
            continue
        manifest = _load_json(manifest_path)
        scenario = str(manifest.get("scenario") or "")
        if selected and scenario not in selected:
            continue
        seen_scenarios.add(scenario)
        index_rows = _load_jsonl(index_path)
        started_at = manifest.get("started_at")
        duration_seconds = float(manifest.get("duration_seconds") or 0.0)
        first_ts, _last_ts = _extract_first_frame_timestamps(index_rows)
        first_frame_seconds = None
        if isinstance(started_at, (int, float)) and first_ts is not None:
            first_frame_seconds = float(first_ts - float(started_at))

        replay_send_end = _extract_replay_send_end(manifest)
        replay_first_frame_seconds = None
        if replay_send_end is not None:
            replay_frames = [
                float(row["timestamp"])
                for row in index_rows
                if isinstance(row.get("timestamp"), (int, float)) and float(row["timestamp"]) >= replay_send_end
            ]
            if replay_frames:
                replay_first_frame_seconds = min(replay_frames) - replay_send_end

        frame_validation = manifest.get("frame_validation", {})
        max_stall = 0.0
        missing_artifacts_count = 0
        if isinstance(frame_validation, dict):
            max_stall = float(frame_validation.get("max_unchanged_streak_seconds_estimate") or 0.0)
            missing_artifacts = frame_validation.get("missing_artifacts", [])
            missing_artifacts_count = len(missing_artifacts) if isinstance(missing_artifacts, list) else 0

        row_parity_violations, row_parity_details = _count_row_parity_violations(run_dir)
        render_lock_signals = _collect_render_lock_signals(
            run_dir,
            expected_profile=expected_render_profile,
        )

        check_rows = [
            ("first_frame_seconds", first_frame_seconds is not None and first_frame_seconds <= max_first_frame_seconds),
            (
                "replay_first_frame_seconds",
                replay_first_frame_seconds is not None and replay_first_frame_seconds <= max_replay_first_frame_seconds,
            ),
            ("duration_seconds", duration_seconds <= max_duration_seconds),
            ("max_stall_seconds", max_stall <= max_stall_seconds),
            ("missing_artifacts", missing_artifacts_count == 0),
            ("row_parity_violations", row_parity_violations == 0),
            ("render_profile_mismatch_frames", int(render_lock_signals["render_profile_mismatch_frames"]) == 0),
            ("line_gap_nonzero_frames", int(render_lock_signals["line_gap_nonzero_frames"]) == 0),
            (
                "isolated_low_edge_rows_total",
                int(render_lock_signals["isolated_low_edge_rows_total"]) <= max_isolated_low_edge_rows_total,
            ),
            ("missing_row_occupancy_frames", int(render_lock_signals["missing_row_occupancy_frames"]) == 0),
        ]
        ok = all(flag for _, flag in check_rows)
        if not ok:
            errors.append(f"{scenario}: reliability checks failed")

        records.append(
            {
                "scenario": scenario,
                "run_dir": str(run_dir),
                "ok": ok,
                "metrics": {
                    "first_frame_seconds": first_frame_seconds,
                    "replay_first_frame_seconds": replay_first_frame_seconds,
                    "duration_seconds": duration_seconds,
                    "max_stall_seconds": max_stall,
                    "missing_artifacts_count": missing_artifacts_count,
                    "row_parity_violations": row_parity_violations,
                    "row_parity_violation_details": row_parity_details,
                    **render_lock_signals,
                },
                "checks": [
                    {"name": name, "pass": flag}
                    for name, flag in check_rows
                ],
            }
        )

    missing_scenarios: list[str] = []
    if selected:
        for scenario in sorted(selected):
            if scenario not in seen_scenarios:
                missing_scenarios.append(scenario)
        if missing_scenarios and fail_on_missing_scenarios:
            errors.append(
                "missing scenarios for selected set: "
                + ", ".join(missing_scenarios)
            )

    report = {
        "schema_version": "phase5_replay_reliability_report_v1",
        "overall_ok": len(errors) == 0,
        "run_root": str(run_root),
        "scenario_set": scenario_set_name,
        "contract_file": str(contract_file),
        "expected_render_profile": expected_render_profile,
        "thresholds": {
            "max_first_frame_seconds": max_first_frame_seconds,
            "max_replay_first_frame_seconds": max_replay_first_frame_seconds,
            "max_duration_seconds": max_duration_seconds,
            "max_stall_seconds": max_stall_seconds,
            "max_isolated_low_edge_rows_total": max_isolated_low_edge_rows_total,
        },
        "records": records,
        "missing_scenarios": missing_scenarios,
        "errors": errors,
    }
    return ValidationResult(ok=(len(errors) == 0), errors=errors, report=report)


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase5 Replay Reliability Report")
    lines.append("")
    lines.append(f"- overall_ok: `{report.get('overall_ok')}`")
    lines.append(f"- run_root: `{report.get('run_root')}`")
    lines.append(f"- scenario_set: `{report.get('scenario_set')}`")
    lines.append(f"- expected_render_profile: `{report.get('expected_render_profile')}`")
    lines.append("")
    lines.append("| Scenario | OK | First Frame (s) | Replay→First Frame (s) | Duration (s) | Stall (s) | Parity Violations | Isolated Edge Rows |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in report.get("records", []):
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics", {}) if isinstance(row.get("metrics"), dict) else {}
        lines.append(
            "| {scenario} | {ok} | {ff} | {rf} | {dur} | {stall} | {parity} | {iso} |".format(
                scenario=str(row.get("scenario") or ""),
                ok="PASS" if bool(row.get("ok")) else "FAIL",
                ff=f"{float(metrics.get('first_frame_seconds')):.3f}" if isinstance(metrics.get("first_frame_seconds"), (int, float)) else "n/a",
                rf=f"{float(metrics.get('replay_first_frame_seconds')):.3f}" if isinstance(metrics.get("replay_first_frame_seconds"), (int, float)) else "n/a",
                dur=f"{float(metrics.get('duration_seconds')):.3f}" if isinstance(metrics.get("duration_seconds"), (int, float)) else "n/a",
                stall=f"{float(metrics.get('max_stall_seconds')):.3f}" if isinstance(metrics.get("max_stall_seconds"), (int, float)) else "n/a",
                parity=int(metrics.get("row_parity_violations") or 0),
                iso=int(metrics.get("isolated_low_edge_rows_total") or 0),
            )
        )
    if report.get("missing_scenarios"):
        lines.append("")
        lines.append("## Missing Scenarios")
        lines.append("")
        for scenario in report["missing_scenarios"]:
            lines.append(f"- `{scenario}`")
    if report.get("errors"):
        lines.append("")
        lines.append("## Errors")
        lines.append("")
        for err in report["errors"]:
            lines.append(f"- {err}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Phase5 replay reliability from existing artifacts.")
    p.add_argument(
        "--run-root",
        default="docs_tmp/tmux_captures/scenarios/phase4_replay",
        help="Phase4 replay scenario root directory",
    )
    p.add_argument(
        "--scenario-set",
        choices=("hard_gate", "nightly", "all"),
        default="all",
        help="Contract scenario set to validate",
    )
    p.add_argument(
        "--contract-file",
        default=str(DEFAULT_CONTRACT_FILE),
        help="Contract file containing scenario sets",
    )
    p.add_argument("--expected-render-profile", default="phase4_locked_v5")
    p.add_argument("--max-first-frame-seconds", type=float, default=3.0)
    p.add_argument("--max-replay-first-frame-seconds", type=float, default=2.0)
    p.add_argument("--max-duration-seconds", type=float, default=25.0)
    p.add_argument("--max-stall-seconds", type=float, default=3.0)
    # A single isolated low-edge row can occur in stable captures; >1 is treated as suspicious.
    p.add_argument("--max-isolated-low-edge-rows-total", type=int, default=1)
    p.add_argument("--fail-on-missing-scenarios", action="store_true")
    p.add_argument("--output-json", default="")
    p.add_argument("--output-md", default="")
    return p.parse_args()


def main() -> int:
    try:
        args = parse_args()
        run_root = Path(args.run_root).expanduser().resolve()
        contract_file = Path(args.contract_file).expanduser().resolve()
        result = validate_reliability(
            run_root=run_root,
            scenario_set_name=str(args.scenario_set),
            contract_file=contract_file,
            expected_render_profile=str(args.expected_render_profile),
            max_first_frame_seconds=float(args.max_first_frame_seconds),
            max_replay_first_frame_seconds=float(args.max_replay_first_frame_seconds),
            max_duration_seconds=float(args.max_duration_seconds),
            max_stall_seconds=float(args.max_stall_seconds),
            max_isolated_low_edge_rows_total=int(args.max_isolated_low_edge_rows_total),
            fail_on_missing_scenarios=bool(args.fail_on_missing_scenarios),
        )

        out_json = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else Path.cwd() / "phase5_replay_reliability_report.json"
        )
        out_md = (
            Path(args.output_md).expanduser().resolve()
            if args.output_md
            else out_json.with_suffix(".md")
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
        out_md.write_text(_render_markdown(result.report), encoding="utf-8")

        if result.ok:
            print(f"[phase5-replay-reliability] pass: {len(result.report.get('records', []))} runs")
            print(f"[phase5-replay-reliability] report_json={out_json}")
            print(f"[phase5-replay-reliability] report_md={out_md}")
            return 0

        print(f"[phase5-replay-reliability] fail: {len(result.errors)} error(s)")
        for err in result.errors:
            print(f"- {err}")
        print(f"[phase5-replay-reliability] report_json={out_json}")
        print(f"[phase5-replay-reliability] report_md={out_md}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase5-replay-reliability] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
