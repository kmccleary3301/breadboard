#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.runtime.kernel_emitter import JsonlKernelEmitter, primitive_emission_mode
from agentic_coder_prototype.state.session_state import SessionState

LIVE_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BREADBOARD_OPENAI_API_KEY",
    "BREADBOARD_ANTHROPIC_API_KEY",
    "BREADBOARD_PROVIDER_TOKEN",
)


def _clock() -> str:
    return "2026-07-07T00:00:00Z"

def _mode_default_without_env() -> str:
    previous = os.environ.pop("BREADBOARD_PRIMITIVES_MODE", None)
    try:
        return primitive_emission_mode("strict")
    finally:
        if previous is not None:
            os.environ["BREADBOARD_PRIMITIVES_MODE"] = previous


def _invalid_mode_falls_back_to_strict() -> bool:
    previous = os.environ.get("BREADBOARD_PRIMITIVES_MODE")
    os.environ["BREADBOARD_PRIMITIVES_MODE"] = "invalid"
    try:
        return primitive_emission_mode("strict") == "strict"
    finally:
        if previous is None:
            os.environ.pop("BREADBOARD_PRIMITIVES_MODE", None)
        else:
            os.environ["BREADBOARD_PRIMITIVES_MODE"] = previous



def _runtime_root(out_dir: Path, label: str) -> Path:
    path = out_dir / label
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tool_result(index: int, *, failed: bool = False, long_result: bool = False) -> dict[str, Any]:
    if failed:
        return {"error": f"simulated failure {index}", "exit_code": 2, "stderr": "boom", "stdout": ""}
    stdout = ("ok " * 2048).strip() if long_result else f"ok {index}"
    return {"stdout": stdout, "exit_code": 0, "content": stdout}


def _run_one_session(*, session_id: str, run_dir: Path | None, workload_index: int, provider_latency_ms: int) -> dict[str, Any]:
    emitter = JsonlKernelEmitter(
        run_dir,
        mode="strict",
        clock=_clock,
        require_payload_schema=True,
        require_tool_spec_ref=True,
    ) if run_dir else None
    state = SessionState(
        workspace="/tmp/breadboard-runtime-soak",
        image="mock-provider",
        kernel_emitter=emitter,
        clock=_clock,
    )
    state.provider_metadata["session_id"] = session_id
    state.provider_metadata["run_id"] = f"run-{session_id}"

    labels: list[str] = []
    turns = 2 if workload_index % 5 == 0 else 1
    for turn in range(turns):
        state.begin_turn(turn)
        user_message = {"role": "user", "content": f"request {workload_index}:{turn}"}
        state.add_message(user_message, to_provider=True)
        state.add_transcript_entry(user_message)
        if workload_index % 4 == 0:
            labels.append("native_tool_call")
            assistant_message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"native-{workload_index}-{turn}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": {"path": "README.md"}},
                    }
                ],
            }
            state.add_message(assistant_message, to_provider=True)
            state.add_transcript_entry(assistant_message)
        else:
            labels.append("text_dialect_tool_call")
            assistant_message = {"role": "assistant", "content": f"<tool_call id='text-{workload_index}-{turn}' />"}
            state.add_message(assistant_message, to_provider=False)
            state.add_transcript_entry(assistant_message)
            state.emit_tool_call_primitive(
                {"id": f"text-{workload_index}-{turn}", "function": "bash", "arguments": {"command": "true"}},
                "declared",
            )

        call = {"id": f"call-{workload_index}-{turn}", "function": "bash", "arguments": {"command": "true"}}
        state.emit_tool_call_primitive(call, "executing")
        failed = workload_index % 7 == 0 and turn == 0
        denied = workload_index % 11 == 0 and turn == 0
        long_result = workload_index % 13 == 0
        if denied:
            labels.append("tool_denial")
            result = {"error": "permission denied by soak policy", "denied": True}
            state.emit_tool_call_primitive(call, "denied", result=result)
            state.emit_tool_outcome_primitives(call, result)
        else:
            if failed:
                labels.append("tool_failure")
            result = _tool_result(workload_index, failed=failed, long_result=long_result)
            state.emit_tool_call_primitive(call, "failed" if failed else "completed", result=result)
            state.emit_tool_outcome_primitives(call, result)
        tool_message = {"role": "tool", "content": json.dumps(result), "tool_call_id": call["id"]}
        state.add_message(tool_message, to_provider=False)
        if workload_index % 6 == 0 and turn == 0:
            labels.append("compaction_triggered")
            state._record_ctree("compaction", {"reason": "soak", "index": workload_index}, turn=turn)
            state.emit_session_transcript_snapshot(reason="compaction_boundary")
        if provider_latency_ms > 0:
            time.sleep(provider_latency_ms / 1000)

    if workload_index % 3 == 0:
        labels.append("guardrail_event")
        state.record_guardrail_event("soak_guardrail", {"index": workload_index})
    state.emit_session_transcript_snapshot(reason="session_end")
    if emitter is not None:
        emitter.close()
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"quarantine_count": 0, "counts_by_schema": {}}
    if turns > 1:
        labels.append("multi_turn")
    if provider_latency_ms < 10:
        labels.append("sub_second")
    else:
        labels.append("provider_faithful_latency")
    return {
        "session_id": session_id,
        "workload_index": workload_index,
        "labels": sorted(set(labels)),
        "manifest": manifest,
    }


def _run_workload(*, out_dir: Path, sessions: int, enabled: bool, provider_latency_ms: int) -> tuple[float, list[dict[str, Any]]]:
    label = "strict_on" if enabled else "flag_off"
    root = _runtime_root(out_dir, label)
    previous = os.environ.get("BREADBOARD_EMIT_PRIMITIVES")
    try:
        if enabled:
            os.environ["BREADBOARD_EMIT_PRIMITIVES"] = "1"
        else:
            os.environ.pop("BREADBOARD_EMIT_PRIMITIVES", None)
        started = time.perf_counter()
        rows = []
        for index in range(sessions):
            rows.append(
                _run_one_session(
                    session_id=f"soak-{label}-{index:03d}",
                    run_dir=(root / f"soak-{index:03d}") if enabled else None,
                    workload_index=index,
                    provider_latency_ms=provider_latency_ms,
                )
            )
        elapsed = time.perf_counter() - started
        return elapsed, rows
    finally:
        if previous is None:
            os.environ.pop("BREADBOARD_EMIT_PRIMITIVES", None)
        else:
            os.environ["BREADBOARD_EMIT_PRIMITIVES"] = previous


def _payload_policy_assertions(out_dir: Path) -> dict[str, Any]:
    kernel_total = 0
    kernel_missing_payload_schema = 0
    tool_total = 0
    tool_missing_spec_ref = 0
    for session_dir in (out_dir / "strict_on").glob("soak-*"):
        kernel_path = session_dir / "records" / "bb.kernel_event.v2.jsonl"
        if kernel_path.is_file():
            for line in kernel_path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                kernel_total += 1
                record = json.loads(line)
                if not record.get("payload_schema_version"):
                    kernel_missing_payload_schema += 1
        tool_path = session_dir / "records" / "bb.tool_call.v2.jsonl"
        if tool_path.is_file():
            for line in tool_path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                tool_total += 1
                record = json.loads(line)
                if not record.get("tool_spec_ref"):
                    tool_missing_spec_ref += 1
    return {
        "kernel_event_total": kernel_total,
        "kernel_event_missing_payload_schema": kernel_missing_payload_schema,
        "kernel_event_payload_schema_passed": kernel_total > 0 and kernel_missing_payload_schema == 0,
        "tool_call_total": tool_total,
        "tool_call_missing_tool_spec_ref": tool_missing_spec_ref,
        "tool_call_spec_ref_passed": tool_total > 0 and tool_missing_spec_ref == 0,
    }


def build_soak_report(*, out_dir: Path, sessions: int, provider_latency_ms: int) -> dict[str, Any]:
    strict_seconds, strict_rows = _run_workload(
        out_dir=out_dir,
        sessions=sessions,
        enabled=True,
        provider_latency_ms=provider_latency_ms,
    )
    off_seconds, off_rows = _run_workload(
        out_dir=out_dir,
        sessions=sessions,
        enabled=False,
        provider_latency_ms=provider_latency_ms,
    )
    quarantine_count = sum(int(row["manifest"].get("quarantine_count") or 0) for row in strict_rows)
    counts_by_schema: dict[str, int] = {}
    all_labels: set[str] = set()
    for row in strict_rows:
        all_labels.update(row["labels"])
        for schema_version, count in (row["manifest"].get("counts_by_schema") or {}).items():
            counts_by_schema[str(schema_version)] = counts_by_schema.get(str(schema_version), 0) + int(count)
    overhead_percent = ((strict_seconds - off_seconds) / off_seconds * 100.0) if off_seconds > 0 else 0.0
    payload_policy = _payload_policy_assertions(out_dir)
    live_provider_available = any(bool(os.environ.get(name)) for name in LIVE_PROVIDER_ENV_VARS)
    mode_default_without_env = _mode_default_without_env()
    invalid_mode_fallback_passed = _invalid_mode_falls_back_to_strict()
    report = {
        "schema_version": "bb.ns.runtime_soak.v1",
        "generated_at_utc": _clock(),
        "sessions": sessions,
        "strict_seconds": strict_seconds,
        "flag_off_seconds": off_seconds,
        "overhead_percent": overhead_percent,
        "overhead_passed": overhead_percent < 5.0,
        "quarantine_count": quarantine_count,
        "quarantine_passed": quarantine_count == 0,
        "counts_by_schema": dict(sorted(counts_by_schema.items())),
        "varied_workload_labels": sorted(all_labels),
        "strict_rows": strict_rows,
        "flag_off_rows": off_rows,
        "live_provider_available": live_provider_available,
        "provider_substitution": None if live_provider_available else "mock provider with deterministic provider-latency sleep and native/text tool shapes",
        "mode_default_without_env": mode_default_without_env,
        "dev_ci_default_mode": mode_default_without_env,
        "default_mode_passed": mode_default_without_env == "strict",
        "invalid_mode_fallback_passed": invalid_mode_fallback_passed,
        "payload_policy": payload_policy,
        "accepted": sessions >= 25 and quarantine_count == 0 and overhead_percent < 5.0 and mode_default_without_env == "strict" and invalid_mode_fallback_passed and payload_policy["kernel_event_payload_schema_passed"] and payload_policy["tool_call_spec_ref_passed"],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the BB-NS WS-C strict runtime-emission soak.")
    parser.add_argument("--out", type=Path, default=Path("../docs_tmp/phase_17/runtime_soak"))
    parser.add_argument("--sessions", type=int, default=25)
    parser.add_argument("--provider-latency-ms", type=int, default=500)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.sessions < 1:
        raise SystemExit("--sessions must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    report = build_soak_report(out_dir=args.out, sessions=args.sessions, provider_latency_ms=args.provider_latency_ms)
    json_out = args.json_out or (args.out / "runtime_emission_soak_report.json")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"json_out": str(json_out), "accepted": report["accepted"], "overhead_percent": report["overhead_percent"], "quarantine_count": report["quarantine_count"]}, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
