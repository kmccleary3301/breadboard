#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload at {path}")
    return payload


def evaluate(
    *,
    contract_validation: dict[str, Any],
    retrieval_loop_report: dict[str, Any],
    specialist_fallback_report: dict[str, Any],
    kpi_snapshot: dict[str, Any],
) -> dict[str, Any]:
    always_failures: list[str] = []
    usually_failures: list[str] = []

    if not bool(contract_validation.get("ok")):
        always_failures.append("contract_validation_not_ok")
    if not bool(retrieval_loop_report.get("decomposition_event_validation_ok")):
        always_failures.append("decomposition_event_validation_not_ok")
    if not bool(retrieval_loop_report.get("decomposition_trace_validation_ok")):
        always_failures.append("decomposition_trace_validation_not_ok")
    if not bool(retrieval_loop_report.get("replay_ok")):
        always_failures.append("retrieval_loop_replay_not_ok")
    if not bool((specialist_fallback_report.get("trace_validation") or {}).get("ok")):
        always_failures.append("specialist_trace_validation_not_ok")

    atp = kpi_snapshot.get("atp") if isinstance(kpi_snapshot.get("atp"), dict) else {}
    if float(atp.get("retrieval_subset_solve_rate", 0.0)) < 0.7:
        usually_failures.append("retrieval_subset_solve_rate_below_0_7")
    if float(atp.get("specialist_fallback_coverage_rate", 0.0)) <= 0.0:
        usually_failures.append("specialist_fallback_coverage_not_positive")
    if float(atp.get("retrieval_falsification_max_delta", 0.0)) > -0.1:
        usually_failures.append("retrieval_falsification_signal_too_weak")

    always_total = 5
    always_rate = float(len(always_failures)) / float(always_total)
    usually_total = 3
    usually_rate = float(len(usually_failures)) / float(usually_total)
    return {
        "schema": "breadboard.atp.reliability_lane_report.v1",
        "generated_at": time.time(),
        "ok": (len(always_failures) == 0) and (len(usually_failures) == 0),
        "lanes": {
            "ALWAYS_PASSES": {
                "total_checks": always_total,
                "failed_checks": len(always_failures),
                "failure_rate": always_rate,
                "failures": always_failures,
            },
            "USUALLY_PASSES": {
                "total_checks": usually_total,
                "failed_checks": len(usually_failures),
                "failure_rate": usually_rate,
                "failures": usually_failures,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-validation", default="artifacts/atp_capabilities/contract_validation_report.latest.json")
    parser.add_argument("--retrieval-loop-report", default="artifacts/atp_retrieval_decomposition_v1/atp_retrieval_decomposition_report.latest.json")
    parser.add_argument("--specialist-fallback-report", default="artifacts/atp_specialist_solver_fallback_matrix_v1/solver_fallback_matrix_report.latest.json")
    parser.add_argument("--kpi-snapshot", default="artifacts/kpi/p2_kpi_snapshot.latest.json")
    parser.add_argument("--json-out", default="artifacts/atp_reliability/atp_reliability_lane_report.latest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate(
        contract_validation=_load(Path(args.contract_validation).resolve()),
        retrieval_loop_report=_load(Path(args.retrieval_loop_report).resolve()),
        specialist_fallback_report=_load(Path(args.specialist_fallback_report).resolve()),
        kpi_snapshot=_load(Path(args.kpi_snapshot).resolve()),
    )
    out_path = Path(args.json_out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"[atp-reliability-lanes] ok={report['ok']}")
        if not report["ok"]:
            for lane_name, lane in report["lanes"].items():
                for failure in lane.get("failures", []):
                    print(f"- {lane_name}: {failure}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
