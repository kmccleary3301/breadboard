#!/usr/bin/env python3
"""Run OpenHands packet cases through the BreadBoard Conductor public path.

This driver deliberately has no actor fallback.  DO-2 supplies ``--runner`` as
``breadboard.rl.harness.<module>:<callable>``; the callable must be a public
Conductor/service composition entrypoint accepting ``(case_id, case,
output_dir)`` and returning a mapping containing ``replay_trace``.  A result is
rejected unless it declares ``public_path == \"conductor\"``.  This makes a
direct OpenHandsActor replay fail closed instead of masquerading as a BB run.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from conformance.comparators.openhands_sdk import compare_cases


class ReplayDriverError(RuntimeError):
    pass


def _load_runner(spec: str) -> Callable[[str, Mapping[str, Any], Path], Mapping[str, Any]]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name.startswith("breadboard.rl.harness."):
        raise ReplayDriverError(
            "--runner must name a BreadBoard harness public-path callable"
        )
    module = importlib.import_module(module_name)
    runner = getattr(module, attribute, None)
    if not callable(runner):
        raise ReplayDriverError(f"runner is not callable: {spec}")
    return runner


def _load_cases(packet_root: Path) -> Mapping[str, Mapping[str, Any]]:
    path = packet_root / "kit" / "openhands_capture_cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value.get("cases") if isinstance(value, Mapping) else None
    if not isinstance(cases, Mapping) or not cases:
        raise ReplayDriverError(f"packet has no cases: {path}")
    return cases


def run(packet_root: Path, output_root: Path, runner_spec: str) -> int:
    runner = _load_runner(runner_spec)
    cases = _load_cases(packet_root)
    output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        if not isinstance(case, Mapping):
            raise ReplayDriverError(f"case is not an object: {case_id}")
        case_output = output_root / case_id
        case_output.mkdir(parents=True, exist_ok=True)
        result = runner(case_id, case, case_output)
        if not isinstance(result, Mapping) or result.get("public_path") != "conductor":
            raise ReplayDriverError(
                f"{case_id}: runner did not prove Conductor public path"
            )
        trace = result.get("replay_trace")
        if not isinstance(trace, Mapping):
            raise ReplayDriverError(f"{case_id}: runner returned no replay_trace")
        trace_path = case_output / "bb_replay_trace.json"
        trace_path.write_text(
            json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        report = compare_cases(packet_root / "captures" / case_id, trace_path)
        report_path = case_output / "comparator_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reports.append({"case_id": case_id, "ok": bool(report.get("ok")), "report": str(report_path)})
    summary = {"public_path": "conductor", "cases": reports, "ok": all(item["ok"] for item in reports)}
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--runner",
        default=os.environ.get("BB_OPENHANDS_CONDUCTOR_RUNNER", ""),
        help="module:function public Conductor entrypoint",
    )
    args = parser.parse_args()
    if not args.runner:
        raise SystemExit("refusing replay: --runner is required (no actor fallback)")
    try:
        return run(args.packet_root, args.output_root, args.runner)
    except ReplayDriverError as exc:
        raise SystemExit(f"refusing replay: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
