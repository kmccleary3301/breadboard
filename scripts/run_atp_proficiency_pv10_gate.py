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


def _build_markdown(payload: dict[str, Any]) -> str:
    gates = payload.get("gates") or {}
    lines: list[str] = []
    lines.append("# ATP Proficiency PV1.0 Gate Report")
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
    toy = payload.get("toy_report") or {}
    retrieval = payload.get("retrieval_report") or {}
    lines.append("## Key Metrics")
    lines.append("")
    lines.append(f"- toy_solved_count: `{toy.get('solved_count', 0)}` / `{toy.get('problem_count', 0)}`")
    lines.append(f"- toy_solve_rate: `{float(toy.get('solve_rate', 0.0)):.4f}`")
    lines.append(f"- retrieval_solved: `{bool(retrieval.get('solved', False))}`")
    lines.append(f"- diagnostics_unknown_count: `{int(retrieval.get('diagnostics_unknown_count', 0))}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--fixture", default="tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json")
    parser.add_argument("--schema-dir", default="docs/contracts/atp/schemas")
    parser.add_argument("--toy-out-json", default="artifacts/atp_toy_loop_v1/atp_toy_loop_report.latest.json")
    parser.add_argument("--toy-artifacts-dir", default="artifacts/atp_toy_loop_v1/artifacts")
    parser.add_argument(
        "--retrieval-out-json",
        default="artifacts/atp_retrieval_decomposition_v1/atp_retrieval_decomposition_report.latest.json",
    )
    parser.add_argument("--retrieval-artifacts-dir", default="artifacts/atp_retrieval_decomposition_v1/artifacts")
    parser.add_argument("--ops-digest-json", default="artifacts/atp_ops_digest.latest.json")
    parser.add_argument("--out-json", default="artifacts/proficiency_v1/pv10_gate.latest.json")
    parser.add_argument("--out-md", default="artifacts/proficiency_v1/pv10_gate.latest.md")
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    toy_out_json = (repo_root / args.toy_out_json).resolve()
    toy_artifacts_dir = (repo_root / args.toy_artifacts_dir).resolve()
    retrieval_out_json = (repo_root / args.retrieval_out_json).resolve()
    retrieval_artifacts_dir = (repo_root / args.retrieval_artifacts_dir).resolve()
    fixture = (repo_root / args.fixture).resolve()
    schema_dir = (repo_root / args.schema_dir).resolve()
    ops_digest_json = (repo_root / args.ops_digest_json).resolve()
    out_json = (repo_root / args.out_json).resolve()
    out_md = (repo_root / args.out_md).resolve()

    toy_out_json.parent.mkdir(parents=True, exist_ok=True)
    toy_artifacts_dir.mkdir(parents=True, exist_ok=True)
    retrieval_out_json.parent.mkdir(parents=True, exist_ok=True)
    retrieval_artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    if not args.reuse_existing:
        _run(
            [
                sys.executable,
                str((repo_root / "scripts" / "run_atp_toy_loop_v1.py").resolve()),
                "--out",
                str(toy_out_json),
                "--artifacts-dir",
                str(toy_artifacts_dir),
            ],
            cwd=repo_root,
        )
        _run(
            [
                sys.executable,
                str((repo_root / "scripts" / "run_atp_retrieval_decomposition_loop_v1.py").resolve()),
                "--fixture",
                str(fixture),
                "--schema-dir",
                str(schema_dir),
                "--artifacts-dir",
                str(retrieval_artifacts_dir),
                "--out",
                str(retrieval_out_json),
            ],
            cwd=repo_root,
        )

    toy_report = _load_json(toy_out_json)
    retrieval_report = _load_json(retrieval_out_json)
    ops_digest = _load_json(ops_digest_json) if ops_digest_json.exists() else {}

    gates = {
        "toy_schema_ok": _gate(
            str(toy_report.get("schema", "")) == "breadboard.atp.toy_loop_report.v1",
            reason="toy report schema must match breadboard.atp.toy_loop_report.v1",
        ),
        "toy_solve_nonzero": _gate(
            int(toy_report.get("solved_count", 0)) > 0,
            reason="toy solved_count must be > 0",
        ),
        "retrieval_schema_ok": _gate(
            str(retrieval_report.get("schema", "")) == "breadboard.atp.retrieval_decomposition_report.v1",
            reason="retrieval report schema must match breadboard.atp.retrieval_decomposition_report.v1",
        ),
        "retrieval_validation_ok": _gate(
            bool(retrieval_report.get("decomposition_event_validation_ok", False))
            and bool(retrieval_report.get("decomposition_trace_validation_ok", False))
            and bool(retrieval_report.get("retrieval_snapshot_validation_ok", False))
            and bool(retrieval_report.get("diagnostics_validation_ok", False))
            and bool(retrieval_report.get("repair_policy_validation_ok", False))
            and bool(retrieval_report.get("instrumentation_validation_ok", False)),
            reason="retrieval/decomposition validations must all be true",
        ),
        "diagnostics_unknown_zero": _gate(
            int(retrieval_report.get("diagnostics_unknown_count", 1)) == 0,
            reason="diagnostics_unknown_count must be 0",
        ),
        "replay_ok": _gate(
            int(toy_report.get("replay_ok_count", 0)) == int(toy_report.get("problem_count", 0))
            and bool(retrieval_report.get("replay_ok", False)),
            reason="replay checks must be true for toy and retrieval loops",
        ),
        "ops_digest_green": _gate(
            bool(ops_digest.get("overall_ok", False)) and str(ops_digest.get("decision_state", "")) == "green",
            required=False,
            reason="optional advisory gate from atp_ops_digest (overall_ok=true and decision_state=green)",
        ),
    }

    required_gate_ids = [key for key, row in gates.items() if bool(row.get("required", False))]
    overall_ok = all(bool(gates[key]["ok"]) for key in required_gate_ids)
    payload = {
        "schema": "breadboard.atp_proficiency_pv10_gate.v1",
        "generated_at_unix": int(time.time()),
        "duration_s": round(time.time() - started, 3),
        "overall_ok": overall_ok,
        "required_gate_ids": required_gate_ids,
        "gates": gates,
        "toy_report_path": str(toy_out_json),
        "retrieval_report_path": str(retrieval_out_json),
        "ops_digest_path": str(ops_digest_json),
        "toy_report": {
            "schema": toy_report.get("schema"),
            "problem_count": int(toy_report.get("problem_count", 0)),
            "solved_count": int(toy_report.get("solved_count", 0)),
            "solve_rate": float(toy_report.get("solve_rate", 0.0)),
            "replay_ok_count": int(toy_report.get("replay_ok_count", 0)),
        },
        "retrieval_report": {
            "schema": retrieval_report.get("schema"),
            "solved": bool(retrieval_report.get("solved", False)),
            "repair_off": bool(retrieval_report.get("repair_off", False)),
            "repair_attempt_count": int(retrieval_report.get("repair_attempt_count", 0)),
            "repair_success_count": int(retrieval_report.get("repair_success_count", 0)),
            "diagnostics_unknown_count": int(retrieval_report.get("diagnostics_unknown_count", 0)),
            "replay_ok": bool(retrieval_report.get("replay_ok", False)),
            "decomposition_event_validation_ok": bool(retrieval_report.get("decomposition_event_validation_ok", False)),
            "decomposition_trace_validation_ok": bool(retrieval_report.get("decomposition_trace_validation_ok", False)),
            "retrieval_snapshot_validation_ok": bool(retrieval_report.get("retrieval_snapshot_validation_ok", False)),
            "diagnostics_validation_ok": bool(retrieval_report.get("diagnostics_validation_ok", False)),
            "repair_policy_validation_ok": bool(retrieval_report.get("repair_policy_validation_ok", False)),
            "instrumentation_validation_ok": bool(retrieval_report.get("instrumentation_validation_ok", False)),
        },
        "ops_digest_summary": {
            "present": ops_digest_json.exists(),
            "overall_ok": bool(ops_digest.get("overall_ok", False)),
            "decision_state": str(ops_digest.get("decision_state", "missing")),
        },
    }

    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_build_markdown(payload), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "[atp-proficiency-pv10-gate] "
            f"overall_ok={overall_ok} toy_solved={payload['toy_report']['solved_count']}/"
            f"{payload['toy_report']['problem_count']} retrieval_solved={payload['retrieval_report']['solved']} "
            f"out={out_json}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
