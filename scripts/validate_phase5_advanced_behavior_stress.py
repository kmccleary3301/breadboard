#!/usr/bin/env python3
"""
Validate Phase5 advanced-behavior stress scenarios from replay artifacts.

This checker stays replay-first (no live model calls) and is intended to harden
Phase G acceptance around:
- advanced thinking lifecycles
- stress/overlay interaction scenarios
- high-concurrency subagent strips
- streaming continuity baseline

Checks per scenario:
1) run-level health signals from scenario_manifest.json
2) frame timeline smoothness (monotonic timestamps + max inter-frame gap)
3) frame activity (non-zero PNG deltas + minimum non-background coverage)
4) strict text-contract parity via validate_phase4_claude_parity.validate_run
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from validate_phase4_claude_parity import DEFAULT_CONTRACT_FILE, validate_run

ADVANCED_SCENARIOS: list[str] = [
    "phase4_replay/streaming_v1_fullpane_v8",
    "phase4_replay/thinking_preview_v1",
    "phase4_replay/thinking_reasoning_only_v1",
    "phase4_replay/thinking_tool_interleaved_v1",
    "phase4_replay/thinking_multiturn_lifecycle_v1",
    "phase4_replay/thinking_lifecycle_expiration_v1",
    "phase4_replay/subagents_concurrency_20_v1",
    "phase4_replay/subagents_strip_churn_v1",
    "phase4_replay/large_output_artifact_v1",
    "phase4_replay/large_diff_artifact_v1",
    "phase4_replay/alt_buffer_enter_exit_v1",
    "phase4_replay/resize_overlay_interaction_v1",
]

MIN_FRAME_COUNT_BY_SCENARIO: dict[str, int] = {
    "phase4_replay/streaming_v1_fullpane_v8": 7,
    "phase4_replay/thinking_preview_v1": 8,
    "phase4_replay/thinking_reasoning_only_v1": 7,
    "phase4_replay/thinking_tool_interleaved_v1": 8,
    "phase4_replay/thinking_multiturn_lifecycle_v1": 8,
    "phase4_replay/thinking_lifecycle_expiration_v1": 8,
    "phase4_replay/subagents_concurrency_20_v1": 8,
    "phase4_replay/subagents_strip_churn_v1": 7,
    "phase4_replay/large_output_artifact_v1": 6,
    "phase4_replay/large_diff_artifact_v1": 6,
    "phase4_replay/alt_buffer_enter_exit_v1": 6,
    "phase4_replay/resize_overlay_interaction_v1": 7,
}


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


def _scenario_slug(scenario: str) -> str:
    if scenario.startswith("phase4_replay/"):
        return scenario.split("/", 1)[1]
    return scenario


def _discover_latest_runs_by_scenario(run_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not run_root.exists() or not run_root.is_dir():
        return out
    for scenario_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
        manifests = sorted(scenario_dir.rglob("scenario_manifest.json"))
        if not manifests:
            continue
        manifest_path = manifests[-1]
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        scenario = str(manifest.get("scenario") or "")
        if not scenario:
            continue
        out[scenario] = manifest_path.parent
    return out


def _compute_frame_motion(
    run_dir: Path,
    index_rows: list[dict[str, Any]],
    *,
    delta_epsilon: float,
) -> dict[str, Any]:
    images: list[np.ndarray] = []
    for row in index_rows:
        rel = row.get("png")
        if not isinstance(rel, str) or not rel:
            continue
        path = run_dir / rel
        if not path.exists():
            continue
        arr = np.asarray(Image.open(path).convert("RGB"))
        images.append(arr)

    if len(images) < 2:
        return {
            "frame_png_count": len(images),
            "nonzero_delta_count": 0,
            "max_changed_ratio": 0.0,
            "max_frozen_delta_streak": 0,
            "max_active_coverage": 0.0,
        }

    coverages: list[float] = []
    deltas: list[float] = []
    for arr in images:
        bg = arr[0, 0]
        active = np.any(arr != bg, axis=2)
        coverages.append(float(active.mean()))

    for prev, cur in zip(images, images[1:]):
        changed = np.any(prev != cur, axis=2)
        deltas.append(float(changed.mean()))

    nonzero_delta_count = sum(1 for x in deltas if x > delta_epsilon)
    max_changed_ratio = max(deltas) if deltas else 0.0
    frozen = 0
    max_frozen = 0
    for x in deltas:
        if x <= delta_epsilon:
            frozen += 1
        else:
            frozen = 0
        max_frozen = max(max_frozen, frozen)

    return {
        "frame_png_count": len(images),
        "nonzero_delta_count": nonzero_delta_count,
        "max_changed_ratio": max_changed_ratio,
        "max_frozen_delta_streak": max_frozen,
        "max_active_coverage": max(coverages) if coverages else 0.0,
    }


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    report: dict[str, Any]


def _append_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    passed: bool,
    expected: Any,
    observed: Any,
) -> None:
    checks.append(
        {
            "name": name,
            "pass": passed,
            "expected": expected,
            "observed": observed,
        }
    )


def _validate_single_run(
    *,
    scenario: str,
    run_dir: Path,
    contract_file: Path,
    max_frame_gap_seconds: float,
    max_unchanged_streak_seconds: float,
    min_active_coverage: float,
    min_nonzero_deltas: int,
    delta_epsilon: float,
) -> dict[str, Any]:
    manifest = _load_json(run_dir / "scenario_manifest.json")
    index_rows = _load_jsonl(run_dir / "index.jsonl")
    checks: list[dict[str, Any]] = []

    min_frames = MIN_FRAME_COUNT_BY_SCENARIO.get(scenario, 6)
    scenario_result = str(manifest.get("scenario_result") or "")
    _append_check(
        checks,
        name="scenario_result",
        passed=scenario_result == "pass",
        expected="pass",
        observed=scenario_result,
    )
    _append_check(
        checks,
        name="execution_error",
        passed=not bool(manifest.get("execution_error")),
        expected=False,
        observed=bool(manifest.get("execution_error")),
    )
    _append_check(
        checks,
        name="action_error",
        passed=not bool(manifest.get("action_error")),
        expected=False,
        observed=bool(manifest.get("action_error")),
    )
    _append_check(
        checks,
        name="frame_stall_error",
        passed=not bool(manifest.get("frame_stall_error")),
        expected=False,
        observed=bool(manifest.get("frame_stall_error")),
    )

    fv = manifest.get("frame_validation") if isinstance(manifest.get("frame_validation"), dict) else {}
    frame_count = int(fv.get("frame_count") or len(index_rows))
    _append_check(
        checks,
        name="frame_count_min",
        passed=frame_count >= min_frames,
        expected=f">={min_frames}",
        observed=frame_count,
    )
    _append_check(
        checks,
        name="frames_dir_exists",
        passed=bool(fv.get("frames_dir_exists", True)),
        expected=True,
        observed=bool(fv.get("frames_dir_exists", True)),
    )
    missing_artifacts = fv.get("missing_artifacts") if isinstance(fv.get("missing_artifacts"), list) else []
    _append_check(
        checks,
        name="missing_artifacts",
        passed=len(missing_artifacts) == 0,
        expected=0,
        observed=len(missing_artifacts),
    )
    missing_indices = fv.get("missing_frame_indices") if isinstance(fv.get("missing_frame_indices"), list) else []
    _append_check(
        checks,
        name="missing_frame_indices",
        passed=len(missing_indices) == 0,
        expected=0,
        observed=len(missing_indices),
    )
    max_streak_sec = float(fv.get("max_unchanged_streak_seconds_estimate") or 0.0)
    _append_check(
        checks,
        name="max_unchanged_streak_seconds",
        passed=max_streak_sec <= max_unchanged_streak_seconds,
        expected=f"<={max_unchanged_streak_seconds}",
        observed=max_streak_sec,
    )

    timestamps = [
        float(row["timestamp"])
        for row in index_rows
        if isinstance(row.get("timestamp"), (int, float))
    ]
    monotonic = all(b >= a for a, b in zip(timestamps, timestamps[1:])) if timestamps else False
    _append_check(
        checks,
        name="timestamps_monotonic",
        passed=monotonic,
        expected=True,
        observed=monotonic,
    )
    max_gap = max((b - a) for a, b in zip(timestamps, timestamps[1:])) if len(timestamps) >= 2 else 0.0
    _append_check(
        checks,
        name="max_frame_gap_seconds",
        passed=max_gap <= max_frame_gap_seconds,
        expected=f"<={max_frame_gap_seconds}",
        observed=max_gap,
    )

    motion = _compute_frame_motion(run_dir, index_rows, delta_epsilon=delta_epsilon)
    _append_check(
        checks,
        name="nonzero_frame_delta_count",
        passed=int(motion["nonzero_delta_count"]) >= min_nonzero_deltas,
        expected=f">={min_nonzero_deltas}",
        observed=int(motion["nonzero_delta_count"]),
    )
    _append_check(
        checks,
        name="max_active_coverage",
        passed=float(motion["max_active_coverage"]) >= min_active_coverage,
        expected=f">={min_active_coverage}",
        observed=float(motion["max_active_coverage"]),
    )

    parity = validate_run(run_dir, strict=True, contract_file=contract_file)
    parity_errors = list(getattr(parity, "errors", []))
    _append_check(
        checks,
        name="strict_text_contract_parity",
        passed=bool(getattr(parity, "ok", False)),
        expected=True,
        observed=parity_errors[0] if parity_errors else "ok",
    )

    passed = all(bool(row["pass"]) for row in checks)
    return {
        "scenario": scenario,
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "checks": checks,
        "passed": passed,
        "frame_motion": motion,
    }


def validate_advanced_stress(
    *,
    run_root: Path,
    scenarios: list[str],
    contract_file: Path,
    max_frame_gap_seconds: float,
    max_unchanged_streak_seconds: float,
    min_active_coverage: float,
    min_nonzero_deltas: int,
    delta_epsilon: float,
    fail_on_missing_scenarios: bool,
) -> ValidationResult:
    latest = _discover_latest_runs_by_scenario(run_root)
    errors: list[str] = []
    records: list[dict[str, Any]] = []

    missing: list[str] = []
    for scenario in scenarios:
        run_dir = latest.get(scenario)
        if run_dir is None:
            missing.append(scenario)
            continue
        if not (run_dir / "scenario_manifest.json").exists() or not (run_dir / "index.jsonl").exists():
            missing.append(scenario)
            continue
        record = _validate_single_run(
            scenario=scenario,
            run_dir=run_dir,
            contract_file=contract_file,
            max_frame_gap_seconds=max_frame_gap_seconds,
            max_unchanged_streak_seconds=max_unchanged_streak_seconds,
            min_active_coverage=min_active_coverage,
            min_nonzero_deltas=min_nonzero_deltas,
            delta_epsilon=delta_epsilon,
        )
        records.append(record)
        if not bool(record["passed"]):
            errors.append(f"{scenario}: advanced stress checks failed")

    if fail_on_missing_scenarios and missing:
        errors.append("missing scenarios: " + ", ".join(sorted(missing)))

    report = {
        "schema_version": "phase5_advanced_behavior_stress_v1",
        "run_root": str(run_root),
        "contract_file": str(contract_file),
        "scenario_count": len(scenarios),
        "validated_count": len(records),
        "missing_scenarios": sorted(missing),
        "overall_ok": len(errors) == 0,
        "records": records,
    }
    return ValidationResult(ok=len(errors) == 0, errors=errors, report=report)


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Phase5 Advanced Behavior Stress Report",
        "",
        f"- overall_ok: `{payload.get('overall_ok')}`",
        f"- scenario_count: `{payload.get('scenario_count')}`",
        f"- validated_count: `{payload.get('validated_count')}`",
        f"- missing_scenarios: `{len(payload.get('missing_scenarios') or [])}`",
        "",
    ]
    missing = payload.get("missing_scenarios") or []
    if missing:
        lines.append("## Missing")
        lines.append("")
        for scenario in missing:
            lines.append(f"- `{scenario}`")
        lines.append("")

    lines.append("## Records")
    lines.append("")
    for record in payload.get("records", []):
        scenario = record.get("scenario")
        lines.append(f"### `{scenario}`")
        lines.append("")
        lines.append(f"- run: `{record.get('run_id')}`")
        lines.append(f"- passed: `{record.get('passed')}`")
        motion = record.get("frame_motion", {})
        lines.append(
            "- motion: "
            f"png={motion.get('frame_png_count')} "
            f"nonzero_deltas={motion.get('nonzero_delta_count')} "
            f"max_changed_ratio={motion.get('max_changed_ratio')} "
            f"max_active_coverage={motion.get('max_active_coverage')}"
        )
        failed = [row for row in record.get("checks", []) if not row.get("pass")]
        if failed:
            lines.append("- failed_checks:")
            for row in failed:
                lines.append(
                    f"  - `{row.get('name')}` expected `{row.get('expected')}` observed `{row.get('observed')}`"
                )
        else:
            lines.append("- failed_checks: none")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate phase5 advanced-behavior replay stress scenarios.")
    parser.add_argument(
        "--run-root",
        default="docs_tmp/tmux_captures/scenarios/phase4_replay",
        help="Replay scenario root directory.",
    )
    parser.add_argument(
        "--contract-file",
        default=str(DEFAULT_CONTRACT_FILE),
        help="Text contract file for strict parity validation.",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=ADVANCED_SCENARIOS,
        help="Scenario list to validate.",
    )
    parser.add_argument("--max-frame-gap-seconds", type=float, default=3.2)
    parser.add_argument("--max-unchanged-streak-seconds", type=float, default=2.2)
    parser.add_argument("--min-active-coverage", type=float, default=0.01)
    parser.add_argument("--min-nonzero-deltas", type=int, default=1)
    parser.add_argument("--delta-epsilon", type=float, default=1e-6)
    parser.add_argument("--fail-on-missing-scenarios", action="store_true")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root)
    contract_file = Path(args.contract_file)
    result = validate_advanced_stress(
        run_root=run_root,
        scenarios=[str(x) for x in args.scenarios if str(x).strip()],
        contract_file=contract_file,
        max_frame_gap_seconds=float(args.max_frame_gap_seconds),
        max_unchanged_streak_seconds=float(args.max_unchanged_streak_seconds),
        min_active_coverage=float(args.min_active_coverage),
        min_nonzero_deltas=int(args.min_nonzero_deltas),
        delta_epsilon=float(args.delta_epsilon),
        fail_on_missing_scenarios=bool(args.fail_on_missing_scenarios),
    )
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        out_md = Path(args.output_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        _write_markdown(out_md, result.report)
    if not result.ok:
        for err in result.errors:
            print(err)
        return 1
    print("phase5 advanced behavior stress: pass")
    print(f"validated: {result.report.get('validated_count')} scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
