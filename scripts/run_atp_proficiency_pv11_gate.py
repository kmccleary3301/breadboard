#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _run(cmd: list[str], *, cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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


def _all_schema_valid(report: dict[str, Any]) -> bool:
    return (
        bool(report.get("decomposition_event_validation_ok", False))
        and bool(report.get("decomposition_trace_validation_ok", False))
        and bool(report.get("retrieval_snapshot_validation_ok", False))
        and bool(report.get("diagnostics_validation_ok", False))
        and bool(report.get("repair_policy_validation_ok", False))
        and bool(report.get("instrumentation_validation_ok", False))
    )


def _build_markdown(payload: dict[str, Any]) -> str:
    gates = payload.get("gates") or {}
    on_summary = payload.get("repair_on_summary") or {}
    off_summary = payload.get("repair_off_summary") or {}
    budget_delta = payload.get("budget_delta") or {}
    lines: list[str] = []
    lines.append("# ATP Proficiency PV1.1 Gate Report")
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
        "- repair_on: "
        f"solved=`{bool(on_summary.get('solved', False))}` "
        f"repair_attempt_count=`{int(on_summary.get('repair_attempt_count', 0))}` "
        f"diagnostics_unknown_count=`{int(on_summary.get('diagnostics_unknown_count', 0))}`"
    )
    lines.append(
        "- repair_off: "
        f"solved=`{bool(off_summary.get('solved', False))}` "
        f"repair_attempt_count=`{int(off_summary.get('repair_attempt_count', 0))}` "
        f"diagnostics_unknown_count=`{int(off_summary.get('diagnostics_unknown_count', 0))}`"
    )
    lines.append(
        "- budget_ratio: "
        f"tokens=`{float(budget_delta.get('tokens_ratio', 0.0)):.4f}` "
        f"cost_usd=`{float(budget_delta.get('cost_ratio', 0.0)):.4f}` "
        f"time_s=`{float(budget_delta.get('time_ratio', 0.0)):.4f}`"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--fixture", default="tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json")
    parser.add_argument("--schema-dir", default="docs/contracts/atp/schemas")
    parser.add_argument("--repair-on-out-json", default="artifacts/proficiency_v1/pv11_repair_on.latest.json")
    parser.add_argument("--repair-on-artifacts-dir", default="artifacts/proficiency_v1/pv11_repair_on_artifacts")
    parser.add_argument("--repair-off-out-json", default="artifacts/proficiency_v1/pv11_repair_off.latest.json")
    parser.add_argument("--repair-off-artifacts-dir", default="artifacts/proficiency_v1/pv11_repair_off_artifacts")
    parser.add_argument("--out-json", default="artifacts/proficiency_v1/pv11_gate.latest.json")
    parser.add_argument("--out-md", default="artifacts/proficiency_v1/pv11_gate.latest.md")
    parser.add_argument("--max-repair-attempts", type=int, default=3)
    parser.add_argument("--max-token-ratio", type=float, default=2.0)
    parser.add_argument("--max-cost-ratio", type=float, default=2.0)
    parser.add_argument("--max-time-ratio", type=float, default=2.0)
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    fixture = (repo_root / args.fixture).resolve()
    schema_dir = (repo_root / args.schema_dir).resolve()
    repair_on_out_json = (repo_root / args.repair_on_out_json).resolve()
    repair_on_artifacts_dir = (repo_root / args.repair_on_artifacts_dir).resolve()
    repair_off_out_json = (repo_root / args.repair_off_out_json).resolve()
    repair_off_artifacts_dir = (repo_root / args.repair_off_artifacts_dir).resolve()
    out_json = (repo_root / args.out_json).resolve()
    out_md = (repo_root / args.out_md).resolve()

    repair_on_out_json.parent.mkdir(parents=True, exist_ok=True)
    repair_on_artifacts_dir.mkdir(parents=True, exist_ok=True)
    repair_off_out_json.parent.mkdir(parents=True, exist_ok=True)
    repair_off_artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    if not args.reuse_existing:
        script_path = str((repo_root / "scripts" / "run_atp_retrieval_decomposition_loop_v1.py").resolve())
        _run(
            [
                sys.executable,
                script_path,
                "--fixture",
                str(fixture),
                "--schema-dir",
                str(schema_dir),
                "--artifacts-dir",
                str(repair_on_artifacts_dir),
                "--out",
                str(repair_on_out_json),
            ],
            cwd=repo_root,
        )
        _run(
            [
                sys.executable,
                script_path,
                "--fixture",
                str(fixture),
                "--schema-dir",
                str(schema_dir),
                "--artifacts-dir",
                str(repair_off_artifacts_dir),
                "--out",
                str(repair_off_out_json),
                "--repair-off",
            ],
            cwd=repo_root,
        )

    repair_on_report = _load_json(repair_on_out_json)
    repair_off_report = _load_json(repair_off_out_json)
    on_budget = _read_budget(repair_on_report)
    off_budget = _read_budget(repair_off_report)
    token_ratio = _safe_ratio(on_budget["tokens"], off_budget["tokens"])
    cost_ratio = _safe_ratio(on_budget["cost_usd"], off_budget["cost_usd"])
    time_ratio = _safe_ratio(on_budget["time_s"], off_budget["time_s"])

    gates = {
        "repair_on_schema_ok": _gate(
            str(repair_on_report.get("schema", "")) == "breadboard.atp.retrieval_decomposition_report.v1",
            reason="repair_on report schema must match breadboard.atp.retrieval_decomposition_report.v1",
        ),
        "repair_off_schema_ok": _gate(
            str(repair_off_report.get("schema", "")) == "breadboard.atp.retrieval_decomposition_report.v1",
            reason="repair_off report schema must match breadboard.atp.retrieval_decomposition_report.v1",
        ),
        "repair_on_validations_ok": _gate(
            _all_schema_valid(repair_on_report),
            reason="repair_on decomposition/snapshot/diagnostic/repair/instrumentation validations must all be true",
        ),
        "repair_off_validations_ok": _gate(
            _all_schema_valid(repair_off_report),
            reason="repair_off decomposition/snapshot/diagnostic/repair/instrumentation validations must all be true",
        ),
        "replay_ok_both": _gate(
            bool(repair_on_report.get("replay_ok", False)) and bool(repair_off_report.get("replay_ok", False)),
            reason="repair_on and repair_off replay checks must both be true",
        ),
        "diagnostics_unknown_zero_both": _gate(
            int(repair_on_report.get("diagnostics_unknown_count", 1)) == 0
            and int(repair_off_report.get("diagnostics_unknown_count", 999)) <= 1,
            reason="diagnostics_unknown_count must be 0 for repair_on and <= 1 for repair_off",
        ),
        "bounded_repair_policy": _gate(
            int(repair_on_report.get("repair_attempt_count", 0)) > 0
            and int(repair_on_report.get("repair_attempt_count", 0)) <= int(args.max_repair_attempts)
            and int(repair_off_report.get("repair_attempt_count", 1)) == 0,
            reason=f"repair_on attempts must be within 1..{int(args.max_repair_attempts)} and repair_off attempts must be 0",
        ),
        "repair_improves_outcome": _gate(
            bool(repair_on_report.get("solved", False))
            and not bool(repair_off_report.get("solved", True))
            and int(repair_on_report.get("repair_success_count", 0)) >= 1,
            reason="repair_on must solve while repair_off fails, with at least one repair success",
        ),
        "budget_blowup_bounded": _gate(
            token_ratio <= float(args.max_token_ratio)
            and cost_ratio <= float(args.max_cost_ratio)
            and time_ratio <= float(args.max_time_ratio),
            reason=(
                f"repair_on budget ratios must remain <= thresholds "
                f"(tokens<={args.max_token_ratio}, cost<={args.max_cost_ratio}, time<={args.max_time_ratio})"
            ),
        ),
    }

    required_gate_ids = [key for key, value in gates.items() if bool(value.get("required", False))]
    overall_ok = all(bool(gates[key]["ok"]) for key in required_gate_ids)
    payload = {
        "schema": "breadboard.atp_proficiency_pv11_gate.v1",
        "generated_at_unix": int(time.time()),
        "duration_s": round(time.time() - started, 3),
        "overall_ok": overall_ok,
        "required_gate_ids": required_gate_ids,
        "gates": gates,
        "repair_on_report_path": str(repair_on_out_json),
        "repair_off_report_path": str(repair_off_out_json),
        "repair_on_summary": {
            "solved": bool(repair_on_report.get("solved", False)),
            "repair_off": bool(repair_on_report.get("repair_off", False)),
            "repair_attempt_count": int(repair_on_report.get("repair_attempt_count", 0)),
            "repair_success_count": int(repair_on_report.get("repair_success_count", 0)),
            "diagnostics_unknown_count": int(repair_on_report.get("diagnostics_unknown_count", 0)),
            "replay_ok": bool(repair_on_report.get("replay_ok", False)),
        },
        "repair_off_summary": {
            "solved": bool(repair_off_report.get("solved", False)),
            "repair_off": bool(repair_off_report.get("repair_off", False)),
            "repair_attempt_count": int(repair_off_report.get("repair_attempt_count", 0)),
            "repair_success_count": int(repair_off_report.get("repair_success_count", 0)),
            "diagnostics_unknown_count": int(repair_off_report.get("diagnostics_unknown_count", 0)),
            "replay_ok": bool(repair_off_report.get("replay_ok", False)),
        },
        "budget_delta": {
            "repair_on": on_budget,
            "repair_off": off_budget,
            "tokens_ratio": token_ratio,
            "cost_ratio": cost_ratio,
            "time_ratio": time_ratio,
        },
    }

    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_build_markdown(payload), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "[atp-proficiency-pv11-gate] "
            f"overall_ok={overall_ok} repair_on_solved={payload['repair_on_summary']['solved']} "
            f"repair_off_solved={payload['repair_off_summary']['solved']} out={out_json}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
