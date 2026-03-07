#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "solver_primary_success",
        "problem_ref": "problem://toy/algebra/001",
        "primary_solver": "geometry_stub",
        "primary_ok": True,
        "fallback_solver": "general_stub",
        "fallback_ok": True,
    },
    {
        "id": "solver_primary_fail_fallback_success",
        "problem_ref": "problem://toy/algebra/002",
        "primary_solver": "geometry_stub",
        "primary_ok": False,
        "fallback_solver": "general_stub",
        "fallback_ok": True,
    },
    {
        "id": "solver_primary_fail_fallback_fail",
        "problem_ref": "problem://toy/algebra/003",
        "primary_solver": "geometry_stub",
        "primary_ok": False,
        "fallback_solver": "general_stub",
        "fallback_ok": False,
    },
    {
        "id": "solver_algebra_primary_success",
        "problem_ref": "problem://toy/algebra/004",
        "primary_solver": "algebra_stub",
        "primary_ok": True,
        "fallback_solver": "general_stub",
        "fallback_ok": True,
    },
    {
        "id": "solver_number_theory_fail_fallback_algebra_success",
        "problem_ref": "problem://toy/number_theory/005",
        "primary_solver": "number_theory_stub",
        "primary_ok": False,
        "fallback_solver": "algebra_stub",
        "fallback_ok": True,
    },
    {
        "id": "solver_smt_fail_fallback_general_success",
        "problem_ref": "problem://toy/smt/006",
        "primary_solver": "smt_stub",
        "primary_ok": False,
        "fallback_solver": "general_stub",
        "fallback_ok": True,
    },
]


def _load_trace_validator(schema_dir: Path) -> Draft202012Validator:
    path = schema_dir / "specialist_solver_trace_v1.schema.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


def _make_trace(
    *,
    scenario: dict[str, Any],
    randomizer: random.Random,
    replay_id: str,
    seed: int,
) -> tuple[dict[str, Any], bool]:
    calls: list[dict[str, Any]] = []
    primary_ok = bool(scenario["primary_ok"])
    calls.append(
        {
            "call_index": 1,
            "solver_type": str(scenario["primary_solver"]),
            "ok": primary_ok,
            "fallback_triggered": not primary_ok,
            "result_ref": f"artifact://solver/{scenario['id']}/call1.json",
            "diagnostic_code": "ok" if primary_ok else "primary_failed",
            "latency_ms": int(90 + randomizer.randint(0, 120)),
        }
    )
    fallback_used = not primary_ok
    if fallback_used:
        fallback_ok = bool(scenario["fallback_ok"])
        calls.append(
            {
                "call_index": 2,
                "solver_type": str(scenario["fallback_solver"]),
                "ok": fallback_ok,
                "fallback_triggered": False,
                "result_ref": f"artifact://solver/{scenario['id']}/call2.json",
                "diagnostic_code": "ok" if fallback_ok else "fallback_failed",
                "latency_ms": int(100 + randomizer.randint(0, 160)),
            }
        )
        final_ok = fallback_ok
    else:
        final_ok = primary_ok

    trace = {
        "schema_version": "atp_specialist_solver_trace_v1",
        "problem_ref": str(scenario["problem_ref"]),
        "calls": calls,
        "final_ok": final_ok,
        "fallback_used": fallback_used,
        "replay_id": replay_id,
        "seed": int(seed),
    }
    return trace, final_ok


def run_matrix(*, schema_dir: Path, seed: int, artifacts_dir: Path) -> dict[str, Any]:
    started = time.time()
    randomizer = random.Random(int(seed))
    manifest_id = f"atp-specialist-fallback-{uuid.uuid4().hex[:12]}"
    replay_id = f"atp-specialist-fallback-seed-{int(seed)}"

    validator = _load_trace_validator(schema_dir)
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    errors: list[str] = []

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        trace, final_ok = _make_trace(
            scenario=scenario,
            randomizer=randomizer,
            replay_id=replay_id,
            seed=seed,
        )
        for err in validator.iter_errors(trace):
            loc = ".".join(str(part) for part in err.path)
            prefix = f"{scenario['id']}"
            errors.append(f"{prefix}{'.' + loc if loc else ''}: {err.message}")
        trace_path = artifacts_dir / f"{scenario['id']}_trace.json"
        trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        traces.append(trace)
        rows.append(
            {
                "scenario_id": str(scenario["id"]),
                "problem_id": str(scenario["id"]),
                "problem_ref": str(scenario["problem_ref"]),
                "status": "pass" if final_ok else "fail",
                "fallback_used": bool(trace["fallback_used"]),
                "trace_calls": len(trace["calls"]),
                "replay_id": replay_id,
                "seed": int(seed),
                "trace_path": str(trace_path.resolve()),
            }
        )

    pass_count = sum(1 for row in rows if row["status"] == "pass")
    fallback_covered = sum(1 for row in rows if row["fallback_used"] is True)
    all_backends_seen: set[str] = set()
    primary_backends_seen: set[str] = set()
    fallback_backends_seen: set[str] = set()
    for trace in traces:
        calls = trace.get("calls")
        if not isinstance(calls, list):
            continue
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            solver_type = str(call.get("solver_type") or "").strip()
            if not solver_type:
                continue
            all_backends_seen.add(solver_type)
            if index == 0:
                primary_backends_seen.add(solver_type)
            else:
                fallback_backends_seen.add(solver_type)
    primary_only_pass = sum(
        1
        for row, scenario in zip(rows, SCENARIOS)
        if bool(scenario.get("primary_ok")) and row["status"] == "pass"
    )
    primary_only_solve_rate = (float(primary_only_pass) / float(len(rows))) if rows else 0.0
    post_fallback_solve_rate = (float(pass_count) / float(len(rows))) if rows else 0.0
    fallback_gain = max(0.0, post_fallback_solve_rate - primary_only_solve_rate)
    masking_detector_ok = fallback_gain <= 0.6
    report = {
        "schema": "breadboard.atp.specialist_solver_fallback_matrix_report.v1",
        "generated_at": time.time(),
        "duration_s": round(time.time() - started, 3),
        "run_manifest": {
            "schema_version": "benchmark_run_manifest_v1",
            "manifest_id": manifest_id,
            "benchmark": "atp_specialist_solver_fallback_matrix_v1",
            "dataset": "atp_specialist_solver",
            "subset_id": "fallback_matrix_v1",
            "seed": int(seed),
            "replay_id": replay_id,
            "budget": {"max_iterations": 64, "max_seconds": 600, "max_cost_usd": 2.0},
        },
        "rows": rows,
        "metrics": {
            "scenarios_total": len(rows),
            "scenarios_passed": pass_count,
            "fallback_covered": fallback_covered,
            "fallback_coverage_rate": (float(fallback_covered) / float(len(rows))) if rows else 0.0,
            "solve_rate": (float(pass_count) / float(len(rows))) if rows else 0.0,
            "primary_only_solve_rate": primary_only_solve_rate,
            "post_fallback_solve_rate": post_fallback_solve_rate,
            "fallback_gain": fallback_gain,
            "masking_detector_ok": masking_detector_ok,
            "solver_backends_seen": sorted(all_backends_seen),
            "solver_backend_count": len(all_backends_seen),
            "primary_backends_seen": sorted(primary_backends_seen),
            "fallback_backends_seen": sorted(fallback_backends_seen),
        },
        "trace_validation": {
            "ok": len(errors) == 0,
            "error_count": len(errors),
            "errors": errors,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-dir", default="docs/contracts/atp/schemas")
    parser.add_argument("--seed", type=int, default=20260223)
    parser.add_argument("--artifacts-dir", default="artifacts/atp_specialist_solver_fallback_matrix_v1")
    parser.add_argument("--out", default="artifacts/atp_specialist_solver_fallback_matrix_v1/solver_fallback_matrix_report.latest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run_matrix(
        schema_dir=Path(args.schema_dir).resolve(),
        seed=int(args.seed),
        artifacts_dir=Path(args.artifacts_dir).resolve(),
    )
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "[atp-specialist-fallback-matrix] pass="
            f"{report['metrics']['scenarios_passed']}/{report['metrics']['scenarios_total']} "
            f"fallback_covered={report['metrics']['fallback_covered']} out={out_path}"
        )
    return 0 if report["trace_validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
