#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from _aristotle_compliance_gate import (
    DEFAULT_SCOPE_PATH,
    AristotleComplianceError,
    assert_aristotle_compliance,
)
from _cross_system_eval_v1 import dump_json, load_jsonl, load_manifest, validate_manifest_shape
from build_pilot_comparison_report_v1 import _build_report, _to_markdown
from run_aristotle_adapter_slice_v1 import run_adapter_slice
from validate_cross_system_run_v1 import validate_results


def _normalize_paths(items: list[str]) -> list[Path]:
    result: list[Path] = []
    for item in items:
        value = str(item).strip()
        if value:
            result.append(Path(value).resolve())
    return result


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _validate_system_ids(manifest: dict[str, Any], baseline_system: str, candidate_system: str) -> None:
    systems = manifest.get("systems")
    if not isinstance(systems, list):
        raise ValueError("manifest.systems missing")
    known = {
        str(row.get("system_id") or "").strip()
        for row in systems
        if isinstance(row, dict) and str(row.get("system_id") or "").strip()
    }
    missing: list[str] = []
    for system_id in (baseline_system, candidate_system):
        if system_id not in known:
            missing.append(system_id)
    if missing:
        raise ValueError(f"manifest missing system ids: {', '.join(missing)}")


async def _run(
    *,
    manifest_path: Path,
    baseline_results: list[Path],
    candidate_results: list[Path],
    aristotle_task_inputs: Path | None,
    aristotle_result_out: Path,
    aristotle_summary_out: Path,
    baseline_system: str,
    candidate_system: str,
    allow_incomplete_matrix: bool,
    validation_out: Path,
    report_json_out: Path,
    report_md_out: Path,
    max_concurrency: int,
    poll_interval_seconds: float,
    wall_clock_cap_seconds: int | None,
    fail_fast: bool,
    compliance_scope_path: Path | None,
    enforce_compliance: bool,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    _validate_system_ids(manifest, baseline_system=baseline_system, candidate_system=candidate_system)
    manifest_errors = validate_manifest_shape(manifest)
    if enforce_compliance and "aristotle" in candidate_system.lower():
        assert_aristotle_compliance(
            scope_path=(compliance_scope_path.resolve() if compliance_scope_path else DEFAULT_SCOPE_PATH.resolve()),
            require_external_reporting=False,
        )

    produced_candidate_path: Path | None = None
    if aristotle_task_inputs is not None:
        await run_adapter_slice(
            manifest_path=manifest_path,
            task_inputs_path=aristotle_task_inputs,
            out_path=aristotle_result_out,
            summary_path=aristotle_summary_out,
            system_id=candidate_system,
            max_concurrency=max_concurrency,
            poll_interval_seconds=poll_interval_seconds,
            wall_clock_cap_seconds=wall_clock_cap_seconds,
            proof_output_dir=None,
            raw_output_dir=None,
            fail_fast=fail_fast,
            limit=None,
            compliance_scope_path=compliance_scope_path.resolve() if compliance_scope_path else DEFAULT_SCOPE_PATH.resolve(),
            enforce_compliance=enforce_compliance,
        )
        produced_candidate_path = aristotle_result_out

    all_result_paths = list(baseline_results) + list(candidate_results)
    if produced_candidate_path is not None:
        all_result_paths.append(produced_candidate_path)
    if not all_result_paths:
        raise ValueError("no result files provided")

    rows = load_jsonl(all_result_paths)
    validation_errors, warnings, validation_summary = validate_results(
        manifest,
        rows,
        allow_incomplete_matrix=allow_incomplete_matrix,
    )
    errors = manifest_errors + validation_errors
    validation_payload = {
        "schema": "breadboard.cross_system_validation_report.v1",
        "ok": len(errors) == 0,
        "manifest_path": str(manifest_path),
        "result_paths": [str(path) for path in all_result_paths],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "summary": validation_summary,
    }
    dump_json(validation_out, validation_payload)

    report = _build_report(
        manifest=manifest,
        rows=rows,
        baseline_system=baseline_system,
        candidate_system=candidate_system,
    )
    dump_json(report_json_out, report)
    _write_markdown(report_md_out, _to_markdown(report))

    return {
        "schema": "breadboard.cross_system_pilot_bundle_run.v1",
        "ok": validation_payload["ok"],
        "manifest_path": str(manifest_path),
        "baseline_system": baseline_system,
        "candidate_system": candidate_system,
        "validation_path": str(validation_out),
        "report_json_path": str(report_json_out),
        "report_md_path": str(report_md_out),
        "generated_candidate_results_path": str(produced_candidate_path) if produced_candidate_path else None,
        "result_paths": [str(path) for path in all_result_paths],
        "error_count": int(validation_payload["error_count"]),
        "warning_count": int(validation_payload["warning_count"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--baseline-results", action="append", default=[])
    parser.add_argument("--candidate-results", action="append", default=[])
    parser.add_argument("--aristotle-task-inputs", default="")
    parser.add_argument(
        "--aristotle-result-out",
        default="artifacts/benchmarks/aristotle_adapter_normalized_results_v1.latest.jsonl",
    )
    parser.add_argument(
        "--aristotle-summary-out",
        default="artifacts/benchmarks/aristotle_adapter_slice_summary_v1.latest.json",
    )
    parser.add_argument("--baseline-system", default="bb_atp")
    parser.add_argument("--candidate-system", default="aristotle_api")
    parser.add_argument("--allow-incomplete-matrix", action="store_true")
    parser.add_argument(
        "--validation-out",
        default="artifacts/benchmarks/cross_system_validation_report_v1.latest.json",
    )
    parser.add_argument(
        "--report-json-out",
        default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.json",
    )
    parser.add_argument(
        "--report-md-out",
        default="artifacts/benchmarks/cross_system_pilot_report_v1.latest.md",
    )
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--wall-clock-cap-seconds", type=int, default=0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--compliance-scope",
        default=str(DEFAULT_SCOPE_PATH),
    )
    parser.add_argument("--skip-compliance-gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    baseline_results = _normalize_paths(list(args.baseline_results or []))
    candidate_results = _normalize_paths(list(args.candidate_results or []))
    aristotle_task_inputs = Path(args.aristotle_task_inputs).resolve() if str(args.aristotle_task_inputs).strip() else None

    try:
        payload = asyncio.run(
            _run(
                manifest_path=Path(args.manifest).resolve(),
                baseline_results=baseline_results,
                candidate_results=candidate_results,
                aristotle_task_inputs=aristotle_task_inputs,
                aristotle_result_out=Path(args.aristotle_result_out).resolve(),
                aristotle_summary_out=Path(args.aristotle_summary_out).resolve(),
                baseline_system=str(args.baseline_system).strip(),
                candidate_system=str(args.candidate_system).strip(),
                allow_incomplete_matrix=bool(args.allow_incomplete_matrix),
                validation_out=Path(args.validation_out).resolve(),
                report_json_out=Path(args.report_json_out).resolve(),
                report_md_out=Path(args.report_md_out).resolve(),
                max_concurrency=max(1, int(args.max_concurrency)),
                poll_interval_seconds=max(0.1, float(args.poll_interval_seconds)),
                wall_clock_cap_seconds=(int(args.wall_clock_cap_seconds) if int(args.wall_clock_cap_seconds) > 0 else None),
                fail_fast=bool(args.fail_fast),
                compliance_scope_path=Path(args.compliance_scope).resolve(),
                enforce_compliance=not bool(args.skip_compliance_gate),
            )
        )
    except AristotleComplianceError as exc:
        print(f"[cross-system-pilot-bundle-v1] ok=False compliance_error={exc}")
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "[cross-system-pilot-bundle-v1] "
            f"ok={payload['ok']} errors={payload['error_count']} warnings={payload['warning_count']} "
            f"report={payload['report_json_path']}"
        )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
