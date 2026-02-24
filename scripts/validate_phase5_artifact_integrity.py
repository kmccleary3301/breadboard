#!/usr/bin/env python3
"""
Validate Phase5 replay artifact integrity with frame-level localization.

This checker complements reliability by explicitly reporting where integrity
violations happen (frame ids, missing files, seam-row indices).
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


def _isolated_low_edge_rows(render_lock: dict[str, Any], *, ratio_ceiling: float = 0.02) -> list[int]:
    occ = render_lock.get("row_occupancy", {})
    if not isinstance(occ, dict):
        return []
    diagnostics = occ.get("edge_row_diagnostics", [])
    if not isinstance(diagnostics, list):
        return []
    declared_rows = int(occ.get("declared_rows") or 0)
    if declared_rows <= 0:
        return []
    edge_ratio_by_row: dict[int, float] = {}
    for row in diagnostics:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("row") or 0)
        if idx <= 0:
            continue
        ratio = float(row.get("edge_ratio") or 0.0)
        edge_ratio_by_row[idx] = ratio

    isolated: list[int] = []
    start_row = max(2, declared_rows // 2)
    for idx in range(start_row, declared_rows):
        current = edge_ratio_by_row.get(idx, 0.0)
        prev_ratio = edge_ratio_by_row.get(idx - 1, 0.0)
        next_ratio = edge_ratio_by_row.get(idx + 1, 0.0)
        if current <= 0.0 or current > ratio_ceiling:
            continue
        if prev_ratio == 0.0 and next_ratio == 0.0:
            isolated.append(idx)
    return isolated


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    report: dict[str, Any]


def _scenario_budget(scenario: str, *, default_budget: int, overrides: dict[str, int]) -> int:
    if scenario in overrides:
        return int(overrides[scenario])
    return int(default_budget)


def validate_artifact_integrity(
    *,
    run_root: Path,
    scenario_set: str,
    contract_file: Path,
    expected_render_profile: str,
    default_max_isolated_edge_rows_total: int,
    scenario_max_isolated_edge_rows_overrides: dict[str, int],
    max_line_gap_nonzero_frames: int,
    fail_on_missing_scenarios: bool,
) -> ValidationResult:
    selected = _contract_scenario_set(contract_file, scenario_set)
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for run_dir in _discover_latest_runs(run_root):
        manifest_path = run_dir / "scenario_manifest.json"
        index_path = run_dir / "index.jsonl"
        if not manifest_path.exists() or not index_path.exists():
            continue
        manifest = _load_json(manifest_path)
        scenario = str(manifest.get("scenario") or "")
        if selected and scenario not in selected:
            continue
        seen.add(scenario)
        rows = _load_jsonl(index_path)

        frame_numbers = [int(r.get("frame")) for r in rows if isinstance(r.get("frame"), int)]
        expected_sequence = list(range(1, len(frame_numbers) + 1))
        frame_sequence_ok = frame_numbers == expected_sequence

        timestamps = [float(r["timestamp"]) for r in rows if isinstance(r.get("timestamp"), (int, float))]
        timestamps_monotonic = all(b >= a for a, b in zip(timestamps, timestamps[1:])) if timestamps else False

        missing_artifacts: list[dict[str, Any]] = []
        row_parity_violations = 0
        row_parity_details: list[dict[str, Any]] = []
        render_profile_mismatch_frames: list[int] = []
        line_gap_nonzero_frames: list[dict[str, Any]] = []
        isolated_rows_by_frame: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            frame_idx = int(row.get("frame") or 0)
            rel_text = str(row.get("text") or "")
            rel_ansi = str(row.get("ansi") or "")
            rel_png = str(row.get("png") or "")
            text_path = run_dir / rel_text if rel_text else None
            ansi_path = run_dir / rel_ansi if rel_ansi else None
            png_path = run_dir / rel_png if rel_png else None

            for kind, p in (("text", text_path), ("ansi", ansi_path), ("png", png_path)):
                if p is None or not p.exists():
                    missing_artifacts.append({"frame": frame_idx, "kind": kind, "path": str(p) if p else ""})

            base = text_path.stem if text_path is not None else f"frame_{frame_idx:04d}"
            frame_parent = text_path.parent if text_path is not None else (run_dir / "frames")
            parity_path = frame_parent / f"{base}.row_parity.json"
            lock_path = frame_parent / f"{base}.render_lock.json"
            for kind, p in (("row_parity", parity_path), ("render_lock", lock_path)):
                if not p.exists():
                    missing_artifacts.append({"frame": frame_idx, "kind": kind, "path": str(p)})

            if parity_path.exists():
                parity_payload = _load_json(parity_path)
                parity = parity_payload.get("parity", {})
                if not isinstance(parity, dict):
                    row_parity_violations += 1
                    row_parity_details.append({"frame": frame_idx, "error": "missing parity object"})
                else:
                    missing = int(parity.get("missing_count") or 0)
                    extra = int(parity.get("extra_count") or 0)
                    span_delta = int(parity.get("row_span_delta") or 0)
                    mismatch_rows = parity.get("mismatch_rows", [])
                    mismatch_count = len(mismatch_rows) if isinstance(mismatch_rows, list) else 0
                    viol = missing + extra + abs(span_delta) + mismatch_count
                    if viol > 0:
                        row_parity_violations += viol
                        row_parity_details.append(
                            {
                                "frame": frame_idx,
                                "missing_count": missing,
                                "extra_count": extra,
                                "row_span_delta": span_delta,
                                "mismatch_rows": mismatch_rows if isinstance(mismatch_rows, list) else [],
                            }
                        )

            if lock_path.exists():
                lock_payload = _load_json(lock_path)
                profile = str(lock_payload.get("render_profile") or "")
                if expected_render_profile and profile != expected_render_profile:
                    render_profile_mismatch_frames.append(frame_idx)
                geometry = lock_payload.get("geometry", {})
                if isinstance(geometry, dict):
                    line_gap = int(geometry.get("line_gap") or 0)
                    if line_gap != 0:
                        line_gap_nonzero_frames.append({"frame": frame_idx, "line_gap": line_gap})
                isolated_rows = _isolated_low_edge_rows(lock_payload)
                if isolated_rows:
                    isolated_rows_by_frame.append({"frame": frame_idx, "rows": isolated_rows, "count": len(isolated_rows)})

        isolated_total = sum(int(row.get("count") or 0) for row in isolated_rows_by_frame)
        isolated_budget = _scenario_budget(
            scenario,
            default_budget=default_max_isolated_edge_rows_total,
            overrides=scenario_max_isolated_edge_rows_overrides,
        )

        checks = {
            "frame_sequence_contiguous": frame_sequence_ok,
            "timestamps_monotonic": timestamps_monotonic,
            "missing_artifacts": len(missing_artifacts) == 0,
            "row_parity_violations": row_parity_violations == 0,
            "render_profile_mismatch_frames": len(render_profile_mismatch_frames) == 0,
            "line_gap_nonzero_frames": len(line_gap_nonzero_frames) <= int(max_line_gap_nonzero_frames),
            "isolated_low_edge_rows_total": isolated_total <= isolated_budget,
        }
        ok = all(checks.values())
        if not ok:
            errors.append(f"{scenario}: artifact integrity checks failed")

        records.append(
            {
                "scenario": scenario,
                "run_id": run_dir.name,
                "run_dir": str(run_dir),
                "ok": ok,
                "checks": checks,
                "metrics": {
                    "missing_artifacts_count": len(missing_artifacts),
                    "row_parity_violations": row_parity_violations,
                    "render_profile_mismatch_count": len(render_profile_mismatch_frames),
                    "line_gap_nonzero_count": len(line_gap_nonzero_frames),
                    "isolated_low_edge_rows_total": isolated_total,
                    "isolated_low_edge_rows_budget": isolated_budget,
                },
                "localization": {
                    "missing_artifacts": missing_artifacts,
                    "row_parity_violations": row_parity_details,
                    "render_profile_mismatch_frames": render_profile_mismatch_frames,
                    "line_gap_nonzero_frames": line_gap_nonzero_frames,
                    "isolated_low_edge_rows_by_frame": isolated_rows_by_frame,
                },
            }
        )

    missing_scenarios: list[str] = []
    if selected:
        for scenario in sorted(selected):
            if scenario not in seen:
                missing_scenarios.append(scenario)
        if missing_scenarios and fail_on_missing_scenarios:
            errors.append("missing scenarios for selected set: " + ", ".join(missing_scenarios))

    report = {
        "schema_version": "phase5_artifact_integrity_report_v1",
        "overall_ok": len(errors) == 0,
        "run_root": str(run_root),
        "scenario_set": scenario_set,
        "contract_file": str(contract_file),
        "expected_render_profile": expected_render_profile,
        "default_max_isolated_edge_rows_total": int(default_max_isolated_edge_rows_total),
        "scenario_max_isolated_edge_rows_overrides": scenario_max_isolated_edge_rows_overrides,
        "records": records,
        "missing_scenarios": missing_scenarios,
        "errors": errors,
    }
    return ValidationResult(ok=(len(errors) == 0), errors=errors, report=report)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase5 Artifact Integrity Report",
        "",
        f"- overall_ok: `{report.get('overall_ok')}`",
        f"- scenario_set: `{report.get('scenario_set')}`",
        f"- expected_render_profile: `{report.get('expected_render_profile')}`",
        "",
        "| Scenario | OK | Missing Artifacts | Parity Violations | Line Gap Frames | Isolated Edge Rows (budget) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for record in report.get("records", []):
        if not isinstance(record, dict):
            continue
        metrics = record.get("metrics", {}) if isinstance(record.get("metrics"), dict) else {}
        lines.append(
            "| {scenario} | {ok} | {missing} | {parity} | {line_gap} | {isolated}/{budget} |".format(
                scenario=str(record.get("scenario") or ""),
                ok="PASS" if bool(record.get("ok")) else "FAIL",
                missing=int(metrics.get("missing_artifacts_count") or 0),
                parity=int(metrics.get("row_parity_violations") or 0),
                line_gap=int(metrics.get("line_gap_nonzero_count") or 0),
                isolated=int(metrics.get("isolated_low_edge_rows_total") or 0),
                budget=int(metrics.get("isolated_low_edge_rows_budget") or 0),
            )
        )
    if report.get("missing_scenarios"):
        lines.extend(["", "## Missing Scenarios", ""])
        for scenario in report["missing_scenarios"]:
            lines.append(f"- `{scenario}`")
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for err in report["errors"]:
            lines.append(f"- {err}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate phase5 replay artifact integrity.")
    p.add_argument("--run-root", default="docs_tmp/tmux_captures/scenarios/phase4_replay")
    p.add_argument("--scenario-set", choices=("hard_gate", "nightly", "all"), default="all")
    p.add_argument("--contract-file", default=str(DEFAULT_CONTRACT_FILE))
    p.add_argument("--expected-render-profile", default="phase4_locked_v5")
    p.add_argument("--default-max-isolated-edge-rows-total", type=int, default=1)
    p.add_argument("--scenario-isolated-edge-budgets-json", default="")
    p.add_argument("--max-line-gap-nonzero-frames", type=int, default=0)
    p.add_argument("--fail-on-missing-scenarios", action="store_true")
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    overrides: dict[str, int] = {}
    if args.scenario_isolated_edge_budgets_json:
        path = Path(args.scenario_isolated_edge_budgets_json).expanduser().resolve()
        if path.exists():
            raw = _load_json(path)
            if isinstance(raw, dict):
                overrides = {str(k): int(v) for k, v in raw.items()}

    result = validate_artifact_integrity(
        run_root=Path(args.run_root).expanduser().resolve(),
        scenario_set=str(args.scenario_set),
        contract_file=Path(args.contract_file).expanduser().resolve(),
        expected_render_profile=str(args.expected_render_profile),
        default_max_isolated_edge_rows_total=int(args.default_max_isolated_edge_rows_total),
        scenario_max_isolated_edge_rows_overrides=overrides,
        max_line_gap_nonzero_frames=int(args.max_line_gap_nonzero_frames),
        fail_on_missing_scenarios=bool(args.fail_on_missing_scenarios),
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(result.report), encoding="utf-8")
    if result.ok:
        print("phase5 artifact integrity: pass")
        print(f"records={len(result.report.get('records', []))}")
        print(f"json={out_json}")
        return 0
    print("phase5 artifact integrity: fail")
    for err in result.errors:
        print(f"- {err}")
    print(f"json={out_json}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
