#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_BACKENDS = [
    "geometry_stub",
    "algebra_stub",
    "number_theory_stub",
    "smt_stub",
    "general_stub",
]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload: {path}")
    return payload


def _collect_backends(matrix_payload: dict[str, Any]) -> set[str]:
    metrics = matrix_payload.get("metrics")
    if isinstance(metrics, dict):
        from_metrics = metrics.get("solver_backends_seen")
        if isinstance(from_metrics, list):
            collected = {str(item).strip() for item in from_metrics if str(item).strip()}
            if collected:
                return collected

    collected: set[str] = set()
    rows = matrix_payload.get("rows")
    if not isinstance(rows, list):
        return collected
    for row in rows:
        if not isinstance(row, dict):
            continue
        trace_path = row.get("trace_path")
        if not isinstance(trace_path, str) or not trace_path.strip():
            continue
        path = Path(trace_path).resolve()
        if not path.exists():
            continue
        trace = _load_json(path)
        calls = trace.get("calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            solver_type = str(call.get("solver_type") or "").strip()
            if solver_type:
                collected.add(solver_type)
    return collected


def build_report(
    *,
    matrix_payload: dict[str, Any],
    matrix_report_path: Path,
    required_backends: list[str],
) -> dict[str, Any]:
    seen = _collect_backends(matrix_payload)
    required = [str(item).strip() for item in required_backends if str(item).strip()]
    missing = sorted(set(required) - seen)
    coverage_rate = float(len(set(required) & seen)) / float(len(set(required))) if required else 1.0
    metrics = matrix_payload.get("metrics")
    fallback_coverage_rate = 0.0
    if isinstance(metrics, dict):
        try:
            fallback_coverage_rate = float(metrics.get("fallback_coverage_rate", 0.0))
        except Exception:
            fallback_coverage_rate = 0.0
    trace_validation = matrix_payload.get("trace_validation")
    trace_validation_ok = bool(trace_validation.get("ok")) if isinstance(trace_validation, dict) else False
    return {
        "schema": "breadboard.atp.solver_backend_breadth_report.v1",
        "generated_at": time.time(),
        "matrix_report_path": str(matrix_report_path.resolve()),
        "required_backends": sorted(set(required)),
        "observed_backends": sorted(seen),
        "missing_backends": missing,
        "required_backend_coverage_rate": coverage_rate,
        "fallback_coverage_rate": fallback_coverage_rate,
        "trace_validation_ok": trace_validation_ok,
        "ok": trace_validation_ok and len(missing) == 0,
    }


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ATP Solver Backend Breadth Report v1",
        "",
        f"- Status: `{'PASS' if report['ok'] else 'FAIL'}`",
        f"- required_backend_coverage_rate: `{report['required_backend_coverage_rate']}`",
        f"- fallback_coverage_rate: `{report['fallback_coverage_rate']}`",
        f"- trace_validation_ok: `{report['trace_validation_ok']}`",
        "",
        "## Backends",
        "",
        f"- Required: `{report['required_backends']}`",
        f"- Observed: `{report['observed_backends']}`",
        f"- Missing: `{report['missing_backends']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix-report",
        default="artifacts/atp_specialist_solver_fallback_matrix_v1/solver_fallback_matrix_report.latest.json",
    )
    parser.add_argument("--required-backend", action="append", default=[])
    parser.add_argument(
        "--json-out",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100/atp_solver_backend_breadth_report_v1.latest.json",
    )
    parser.add_argument(
        "--md-out",
        default="artifacts/benchmarks/cross_system_frozen_slices_v1_n100/atp_solver_backend_breadth_report_v1.latest.md",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    required_backends = list(args.required_backend) if args.required_backend else list(DEFAULT_REQUIRED_BACKENDS)
    matrix_path = Path(args.matrix_report).resolve()
    report = build_report(
        matrix_payload=_load_json(matrix_path),
        matrix_report_path=matrix_path,
        required_backends=required_backends,
    )
    json_out = Path(args.json_out).resolve()
    md_out = Path(args.md_out).resolve()
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_out.write_text(_to_markdown(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "[atp-solver-backend-breadth] "
            f"status={'PASS' if report['ok'] else 'FAIL'} "
            f"coverage={report['required_backend_coverage_rate']:.3f}"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
