#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _gate(ok: bool, *, reason: str, required: bool = True) -> dict[str, Any]:
    return {"ok": bool(ok), "required": bool(required), "reason": reason}


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else float("inf")
    return float(numerator) / float(denominator)


def _read_budget(report: dict[str, Any]) -> dict[str, float]:
    budget = ((report.get("instrumentation_digest") or {}).get("budget_usage") or {})
    return {
        "tokens": float(budget.get("tokens", 0.0)),
        "cost_usd": float(budget.get("cost_usd", 0.0)),
        "time_s": float(budget.get("time_s", 0.0)),
    }


def _load_diagnostics_count_by_class(report: dict[str, Any]) -> dict[str, int]:
    diagnostics_path = report.get("diagnostics_path")
    if not diagnostics_path:
        return {}
    path = Path(str(diagnostics_path))
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for row in payload:
        normalized = str((row or {}).get("normalized_class") or "unknown")
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _all_schema_valid(report: dict[str, Any]) -> bool:
    return (
        bool(report.get("decomposition_event_validation_ok", False))
        and bool(report.get("decomposition_trace_validation_ok", False))
        and bool(report.get("retrieval_snapshot_validation_ok", False))
        and bool(report.get("diagnostics_validation_ok", False))
        and bool(report.get("repair_policy_validation_ok", False))
        and bool(report.get("instrumentation_validation_ok", False))
    )


def _reduction_fraction(*, before: float, after: float) -> float:
    if before <= 0:
        return 1.0 if after <= 0 else 0.0
    return max(0.0, (float(before) - float(after)) / float(before))


def _build_markdown(payload: dict[str, Any]) -> str:
    gates = payload.get("gates") or {}
    deltas = payload.get("delta") or {}
    candidate = payload.get("candidate_summary") or {}
    baseline = payload.get("baseline_summary") or {}
    lines: list[str] = []
    lines.append("# ATP Proficiency PV1.2 Gate Report")
    lines.append("")
    lines.append(f"- overall_ok: `{payload.get('overall_ok', False)}`")
    lines.append(f"- generated_at_unix: `{payload.get('generated_at_unix', 0)}`")
    lines.append("")
    lines.append("## Gate Matrix")
    lines.append("")
    lines.append("| gate | required | ok | reason |")
    lines.append("| --- | ---: | ---: | --- |")
    for key in sorted(gates.keys()):
        row = gates[key] or {}
        lines.append(
            f"| {key} | {str(bool(row.get('required', False))).lower()} | {str(bool(row.get('ok', False))).lower()} | "
            f"{str(row.get('reason', '')).replace('|', '\\|')} |"
        )
    lines.append("")
    lines.append("## Key Metrics")
    lines.append("")
    lines.append(
        "- candidate: "
        f"solved=`{bool(candidate.get('solved', False))}` "
        f"unknown_count=`{int(candidate.get('unknown_count', 0))}` "
        f"tactic_failure_count=`{int(candidate.get('tactic_failure_count', 0))}`"
    )
    lines.append(
        "- baseline: "
        f"solved=`{bool(baseline.get('solved', False))}` "
        f"unknown_count=`{int(baseline.get('unknown_count', 0))}` "
        f"tactic_failure_count=`{int(baseline.get('tactic_failure_count', 0))}`"
    )
    lines.append(
        "- deltas: "
        f"unknown_reduction=`{float(deltas.get('unknown_reduction_fraction', 0.0)):.4f}` "
        f"cost_ratio=`{float(deltas.get('cost_ratio', 0.0)):.4f}` "
        f"tokens_ratio=`{float(deltas.get('tokens_ratio', 0.0)):.4f}` "
        f"time_ratio=`{float(deltas.get('time_ratio', 0.0)):.4f}`"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--candidate-report-json", default="artifacts/proficiency_v1/pv11_repair_on.latest.json")
    parser.add_argument("--baseline-report-json", default="artifacts/proficiency_v1/pv11_repair_off.latest.json")
    parser.add_argument("--out-json", default="artifacts/proficiency_v1/pv12_gate.latest.json")
    parser.add_argument("--out-md", default="artifacts/proficiency_v1/pv12_gate.latest.md")
    parser.add_argument("--min-unknown-reduction", type=float, default=0.25)
    parser.add_argument("--max-cost-ratio", type=float, default=1.2)
    parser.add_argument("--max-token-ratio", type=float, default=1.2)
    parser.add_argument("--max-time-ratio", type=float, default=1.2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    candidate_path = (repo_root / args.candidate_report_json).resolve()
    baseline_path = (repo_root / args.baseline_report_json).resolve()
    out_json = (repo_root / args.out_json).resolve()
    out_md = (repo_root / args.out_md).resolve()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    candidate_report = _load_json(candidate_path)
    baseline_report = _load_json(baseline_path)

    candidate_budget = _read_budget(candidate_report)
    baseline_budget = _read_budget(baseline_report)
    candidate_diag_counts = _load_diagnostics_count_by_class(candidate_report)
    baseline_diag_counts = _load_diagnostics_count_by_class(baseline_report)
    candidate_unknown = int(candidate_report.get("diagnostics_unknown_count", 0))
    baseline_unknown = int(baseline_report.get("diagnostics_unknown_count", 0))
    candidate_tactic_fail = int(candidate_diag_counts.get("tactic_failure", 0))
    baseline_tactic_fail = int(baseline_diag_counts.get("tactic_failure", 0))

    unknown_reduction_fraction = _reduction_fraction(before=baseline_unknown, after=candidate_unknown)
    tactic_failure_reduction_fraction = _reduction_fraction(before=baseline_tactic_fail, after=candidate_tactic_fail)
    cost_ratio = _safe_ratio(candidate_budget["cost_usd"], baseline_budget["cost_usd"])
    tokens_ratio = _safe_ratio(candidate_budget["tokens"], baseline_budget["tokens"])
    time_ratio = _safe_ratio(candidate_budget["time_s"], baseline_budget["time_s"])

    gates = {
        "candidate_schema_ok": _gate(
            str(candidate_report.get("schema", "")) == "breadboard.atp.retrieval_decomposition_report.v1",
            reason="candidate schema must match breadboard.atp.retrieval_decomposition_report.v1",
        ),
        "baseline_schema_ok": _gate(
            str(baseline_report.get("schema", "")) == "breadboard.atp.retrieval_decomposition_report.v1",
            reason="baseline schema must match breadboard.atp.retrieval_decomposition_report.v1",
        ),
        "candidate_validations_ok": _gate(
            _all_schema_valid(candidate_report),
            reason="candidate decomposition/snapshot/diagnostic/repair/instrumentation validations must all be true",
        ),
        "baseline_validations_ok": _gate(
            _all_schema_valid(baseline_report),
            reason="baseline decomposition/snapshot/diagnostic/repair/instrumentation validations must all be true",
        ),
        "replay_ok_both": _gate(
            bool(candidate_report.get("replay_ok", False)) and bool(baseline_report.get("replay_ok", False)),
            reason="candidate and baseline replay checks must both be true",
        ),
        "unknown_identifier_reduction": _gate(
            unknown_reduction_fraction >= float(args.min_unknown_reduction),
            reason=f"unknown reduction must be >= {float(args.min_unknown_reduction):.2f} against baseline",
        ),
        "tactic_failure_not_worse": _gate(
            candidate_tactic_fail <= baseline_tactic_fail,
            reason="candidate tactic_failure count must not exceed baseline",
        ),
        "solve_not_worse": _gate(
            int(bool(candidate_report.get("solved", False))) >= int(bool(baseline_report.get("solved", False))),
            reason="candidate solved flag must be at least baseline solved flag",
        ),
        "cost_growth_bounded": _gate(
            cost_ratio <= float(args.max_cost_ratio),
            reason=f"candidate cost ratio must be <= {float(args.max_cost_ratio):.2f}",
        ),
        "token_growth_bounded": _gate(
            tokens_ratio <= float(args.max_token_ratio),
            reason=f"candidate token ratio must be <= {float(args.max_token_ratio):.2f}",
        ),
        "time_growth_bounded": _gate(
            time_ratio <= float(args.max_time_ratio),
            reason=f"candidate time ratio must be <= {float(args.max_time_ratio):.2f}",
        ),
    }

    required_gate_ids = [key for key, row in gates.items() if bool(row.get("required", False))]
    overall_ok = all(bool(gates[key]["ok"]) for key in required_gate_ids)
    payload = {
        "schema": "breadboard.atp_proficiency_pv12_gate.v1",
        "generated_at_unix": int(time.time()),
        "overall_ok": overall_ok,
        "required_gate_ids": required_gate_ids,
        "gates": gates,
        "candidate_report_path": str(candidate_path),
        "baseline_report_path": str(baseline_path),
        "candidate_summary": {
            "solved": bool(candidate_report.get("solved", False)),
            "unknown_count": candidate_unknown,
            "tactic_failure_count": candidate_tactic_fail,
            "replay_ok": bool(candidate_report.get("replay_ok", False)),
            "budget_usage": candidate_budget,
        },
        "baseline_summary": {
            "solved": bool(baseline_report.get("solved", False)),
            "unknown_count": baseline_unknown,
            "tactic_failure_count": baseline_tactic_fail,
            "replay_ok": bool(baseline_report.get("replay_ok", False)),
            "budget_usage": baseline_budget,
        },
        "delta": {
            "unknown_reduction_fraction": unknown_reduction_fraction,
            "tactic_failure_reduction_fraction": tactic_failure_reduction_fraction,
            "cost_ratio": cost_ratio,
            "tokens_ratio": tokens_ratio,
            "time_ratio": time_ratio,
        },
    }

    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_build_markdown(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "[atp-proficiency-pv12-gate] "
            f"overall_ok={overall_ok} unknown_reduction={unknown_reduction_fraction:.4f} "
            f"cost_ratio={cost_ratio:.4f} out={out_json}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
